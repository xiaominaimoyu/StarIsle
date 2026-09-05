"""幂等记录生命周期:保留期清理、批次边界、审计旁路与重放去重不受影响。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config import settings  # noqa: E402
from app.db import get_conn, init_db  # noqa: E402
from app.security import iso, sha256_hex  # noqa: E402
from app.services import idempotency  # noqa: E402

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def cleanup_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = tmp_path / "data"
    monkeypatch.setattr(settings, "data_root", root)
    monkeypatch.setattr(settings, "db_path", root / "db" / "star-isle.db")
    monkeypatch.setattr(settings, "objects_root", root / "objects")
    monkeypatch.setattr(settings, "originals_dir", root / "objects" / "originals")
    monkeypatch.setattr(settings, "thumbnails_dir", root / "objects" / "thumbnails")
    monkeypatch.setattr(settings, "mail_attachments_dir", root / "objects" / "mail-attachments")
    monkeypatch.setattr(settings, "mail_raw_dir", root / "objects" / "mail-raw")
    monkeypatch.setattr(settings, "idempotency_retention_days", 30)
    init_db()
    return root


def _insert_record(conn, key: str, *, created_at: datetime, resource_type: str = "asset") -> None:
    conn.execute(
        "INSERT INTO idempotency (key_hash, resource_id, resource_type, created_at) VALUES (?, ?, ?, ?)",
        (sha256_hex(key.encode()), json.dumps(f"res-{key}"), resource_type, iso(created_at)),
    )


def _count_rows() -> int:
    with get_conn() as conn:
        return int(conn.execute("SELECT COUNT(*) AS c FROM idempotency").fetchone()["c"])


def test_purge_deletes_only_expired_records(cleanup_env):
    with get_conn() as conn:
        _insert_record(conn, "old-1", created_at=NOW - timedelta(days=31))
        _insert_record(conn, "old-2", created_at=NOW - timedelta(days=30, seconds=1))
        _insert_record(conn, "edge", created_at=NOW - timedelta(days=30))
        _insert_record(conn, "fresh", created_at=NOW - timedelta(days=1))

    deleted = idempotency.run_idempotency_cleanup(now=NOW)

    assert deleted == 2
    with get_conn() as conn:
        remaining = {
            row["key_hash"]
            for row in conn.execute("SELECT key_hash FROM idempotency")
        }
    assert remaining == {sha256_hex(b"edge"), sha256_hex(b"fresh")}


def test_purge_respects_batch_bound_and_is_repeatable(cleanup_env):
    with get_conn() as conn:
        for i in range(5):
            _insert_record(conn, f"old-{i}", created_at=NOW - timedelta(days=40 + i))

    first = idempotency.run_idempotency_cleanup(now=NOW, batch_size=2)
    assert first == 2
    assert _count_rows() == 3

    second = idempotency.run_idempotency_cleanup(now=NOW, batch_size=2)
    assert second == 2
    assert _count_rows() == 1

    third = idempotency.run_idempotency_cleanup(now=NOW, batch_size=2)
    assert third == 1
    assert _count_rows() == 0

    # 空表重复执行安全
    assert idempotency.run_idempotency_cleanup(now=NOW) == 0


def test_purge_batch_size_is_bounded(cleanup_env):
    with get_conn() as conn:
        for i in range(3):
            _insert_record(conn, f"old-{i}", created_at=NOW - timedelta(days=40))

    # 超大与非法批次分别被 clamp 与拒绝
    deleted = idempotency.run_idempotency_cleanup(now=NOW, batch_size=10**9)
    assert deleted == 3
    with pytest.raises(ValueError):
        idempotency.run_idempotency_cleanup(now=NOW, batch_size="2")


def test_purge_writes_audit_sideband(cleanup_env):
    with get_conn() as conn:
        _insert_record(conn, "old", created_at=NOW - timedelta(days=60))
        before = conn.execute("SELECT COUNT(*) AS c FROM audit_event").fetchone()["c"]

    idempotency.run_idempotency_cleanup(now=NOW)

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT action, resource_type, result FROM audit_event ORDER BY created_at DESC, id DESC"
        ).fetchall()
    assert len(rows) > before
    latest = rows[0]
    assert latest["action"] == "idempotency_cleanup"
    assert latest["resource_type"] == "idempotency"
    assert latest["result"] == "deleted_rows=1"


def test_retention_days_must_be_positive(cleanup_env, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "idempotency_retention_days", 0)
    with pytest.raises(ValueError):
        idempotency.run_idempotency_cleanup(now=NOW)


def test_replay_dedup_within_retention_window(cleanup_env):
    calls: list[str] = []

    def producer() -> str:
        calls.append("produced")
        return "asset_first"

    first = idempotency.with_idempotency("retry-key", "asset", producer)
    second = idempotency.with_idempotency("retry-key", "asset", producer)
    assert first == second == "asset_first"
    assert len(calls) == 1

    # 保留期内清理不得误删新记录
    deleted = idempotency.run_idempotency_cleanup(now=datetime.now(timezone.utc))
    assert deleted == 0
    third = idempotency.with_idempotency("retry-key", "asset", producer)
    assert third == "asset_first"
    assert len(calls) == 1
