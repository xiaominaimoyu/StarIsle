"""审计事件保留期:过期清理、软容量、登录限速窗口保护与审计旁路。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config import settings  # noqa: E402
from app.db import get_conn, init_db  # noqa: E402
from app.security import iso  # noqa: E402
from app.services import audit_retention  # noqa: E402

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def retention_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = tmp_path / "data"
    monkeypatch.setattr(settings, "data_root", root)
    monkeypatch.setattr(settings, "db_path", root / "db" / "star-isle.db")
    monkeypatch.setattr(settings, "objects_root", root / "objects")
    monkeypatch.setattr(settings, "originals_dir", root / "objects" / "originals")
    monkeypatch.setattr(settings, "thumbnails_dir", root / "objects" / "thumbnails")
    monkeypatch.setattr(settings, "mail_attachments_dir", root / "objects" / "mail-attachments")
    monkeypatch.setattr(settings, "mail_raw_dir", root / "objects" / "mail-raw")
    monkeypatch.setattr(settings, "audit_retention_days", 90)
    monkeypatch.setattr(settings, "audit_soft_cap_rows", 100000)
    init_db()
    return root


def _insert_event(conn, event_id: str, *, created_at: datetime, action: str = "file_upload") -> None:
    conn.execute(
        "INSERT INTO audit_event (id, actor, action, resource_type, resource_id, request_id, result, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (event_id, "admin", action, "asset", None, None, "ok", iso(created_at)),
    )


def _count(where: str = "", params: tuple = ()) -> int:
    """业务行计数;维护自身写入的审计旁路行不计入。"""
    clause = "WHERE action <> 'audit_retention_maintenance'"
    if where:
        clause += f" AND ({where.removeprefix('WHERE ')})"
    with get_conn() as conn:
        return int(conn.execute(f"SELECT COUNT(*) AS c FROM audit_event {clause}", params).fetchone()["c"])


def test_expired_rows_deleted_boundary_kept(retention_env):
    with get_conn() as conn:
        _insert_event(conn, "old", created_at=NOW - timedelta(days=91))
        _insert_event(conn, "edge", created_at=NOW - timedelta(days=90))
        _insert_event(conn, "fresh", created_at=NOW - timedelta(days=1))

    result = audit_retention.run_audit_retention(now=NOW)

    assert result.deleted_expired == 1
    assert result.deleted_capped == 0
    assert result.deferred_rows == 0
    assert _count() == 2
    assert _count("WHERE id='old'") == 0


def test_batch_bound_converges_over_rounds(retention_env):
    with get_conn() as conn:
        for i in range(5):
            _insert_event(conn, f"old-{i}", created_at=NOW - timedelta(days=100 + i))

    first = audit_retention.run_audit_retention(now=NOW, batch_size=2)
    assert first.deleted_expired == 2
    second = audit_retention.run_audit_retention(now=NOW, batch_size=2)
    assert second.deleted_expired == 2
    third = audit_retention.run_audit_retention(now=NOW, batch_size=2)
    assert third.deleted_expired == 1
    assert _count() == 0
    # 空表重复执行安全
    empty = audit_retention.run_audit_retention(now=NOW)
    assert (empty.deleted_expired, empty.deleted_capped, empty.deferred_rows) == (0, 0, 0)


def test_soft_cap_deletes_oldest_beyond_cap(retention_env, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "audit_soft_cap_rows", 10)
    with get_conn() as conn:
        for i in range(15):
            _insert_event(conn, f"e-{i:02d}", created_at=NOW - timedelta(days=i + 1))

    result = audit_retention.run_audit_retention(now=NOW)

    # 全部在保留期内,只由软容量驱动删除最旧的 5 行
    assert result.deleted_expired == 0
    assert result.deleted_capped == 5
    assert result.deferred_rows == 0
    assert _count() == 10
    # 最旧的 5 行被删除,较新的 10 行保留
    assert _count("WHERE id IN ('e-14','e-13','e-12','e-11','e-10')") == 0
    assert _count("WHERE id IN ('e-09','e-00')") == 2


def test_soft_cap_never_deletes_login_limiter_window(retention_env, monkeypatch: pytest.MonkeyPatch):
    """攻击场景:软容量被 5 分钟窗口内的行占满时,一行也不删,差量记为 deferred。"""
    monkeypatch.setattr(settings, "audit_soft_cap_rows", 10)
    with get_conn() as conn:
        for i in range(12):
            _insert_event(
                conn,
                f"attack-{i:02d}",
                created_at=NOW - timedelta(seconds=20 * i),
                action="login_fail",
            )

    result = audit_retention.run_audit_retention(now=NOW)

    assert result.deleted_expired == 0
    assert result.deleted_capped == 0
    assert result.deferred_rows == 2
    assert _count() == 12


def test_soft_cap_partial_window_mix(retention_env, monkeypatch: pytest.MonkeyPatch):
    """窗口外可删量小于超出量时,删除不越过窗口线,余量 deferred。"""
    monkeypatch.setattr(settings, "audit_soft_cap_rows", 10)
    with get_conn() as conn:
        for i in range(8):
            _insert_event(conn, f"old-{i}", created_at=NOW - timedelta(hours=1 + i))
        for i in range(7):
            _insert_event(conn, f"recent-{i}", created_at=NOW - timedelta(seconds=10 * (i + 1)))

    result = audit_retention.run_audit_retention(now=NOW)

    # 超出 5 行,窗口外有 8 行可删,本轮批次内删 5 行使总量回到软容量
    assert result.deleted_capped == 5
    assert result.deferred_rows == 0
    assert _count() == 10
    assert _count("WHERE id LIKE 'recent-%'") == 7


def test_recent_login_fail_rows_always_survive(retention_env, monkeypatch: pytest.MonkeyPatch):
    """与 auth.py 登录限速器耦合:近 5 分钟 login_fail 行在任何清理下都保留。"""
    monkeypatch.setattr(settings, "audit_soft_cap_rows", 1)
    with get_conn() as conn:
        _insert_event(conn, "recent-fail", created_at=NOW - timedelta(minutes=2), action="login_fail")
        _insert_event(conn, "old-1", created_at=NOW - timedelta(hours=2))
        _insert_event(conn, "old-2", created_at=NOW - timedelta(hours=3))

    result = audit_retention.run_audit_retention(now=NOW)

    assert _count("WHERE id='recent-fail'") == 1
    assert result.deleted_capped + result.deferred_rows >= 1


def test_retention_writes_audit_sideband(retention_env):
    with get_conn() as conn:
        _insert_event(conn, "old", created_at=NOW - timedelta(days=120))

    audit_retention.run_audit_retention(now=NOW)

    with get_conn() as conn:
        row = conn.execute(
            "SELECT action, resource_type, result FROM audit_event ORDER BY created_at DESC, id DESC LIMIT 1"
        ).fetchone()
    assert row["action"] == "audit_retention_maintenance"
    assert row["resource_type"] == "audit"
    assert row["result"] == "expired=1;capped=0;deferred=0"


def test_retention_config_validation(retention_env, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "audit_retention_days", 0)
    with pytest.raises(ValueError):
        audit_retention.run_audit_retention(now=NOW)
    monkeypatch.setattr(settings, "audit_retention_days", 90)
    monkeypatch.setattr(settings, "audit_soft_cap_rows", 0)
    with pytest.raises(ValueError):
        audit_retention.run_audit_retention(now=NOW)
    with pytest.raises(ValueError):
        audit_retention.run_audit_retention(now=NOW, batch_size="10")
