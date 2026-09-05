"""v5 通用 asset GC：保守迁移、共享保护、重试与目录隔离。"""

from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event

import pytest

from app import worker_runner  # noqa: E402
from app import deps  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import (  # noqa: E402
    MIGRATION_V2,
    MIGRATION_V3,
    MIGRATION_V4,
    SCHEMA_V1,
    SCHEMA_V3,
    SCHEMA_V4,
    SchemaVersionError,
    get_conn,
    init_db,
)
from app.routers import files  # noqa: E402
from app.security import iso  # noqa: E402
from app.services import asset_gc  # noqa: E402
from app.services.jobs import enqueue_job  # noqa: E402
from app.storage import StorageAdapter  # noqa: E402


NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def _set_data_root(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr(settings, "data_root", root)
    monkeypatch.setattr(settings, "db_path", root / "db" / "star-isle.db")
    monkeypatch.setattr(settings, "objects_root", root / "objects")
    monkeypatch.setattr(settings, "originals_dir", root / "objects" / "originals")
    monkeypatch.setattr(settings, "thumbnails_dir", root / "objects" / "thumbnails")
    monkeypatch.setattr(settings, "mail_attachments_dir", root / "objects" / "mail-attachments")
    monkeypatch.setattr(settings, "mail_raw_dir", root / "objects" / "mail-raw")


@pytest.fixture
def gc_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = tmp_path / "data"
    _set_data_root(monkeypatch, root)
    monkeypatch.setattr(settings, "storage_quota_bytes", 10 * 1024 * 1024)
    adapter = StorageAdapter(root / "objects")
    monkeypatch.setattr(asset_gc, "storage", adapter)
    monkeypatch.setattr(worker_runner, "storage", adapter)
    init_db()
    return root, adapter


def _insert_asset(
    conn,
    adapter: StorageAdapter,
    asset_id: str,
    storage_class: str,
    payload: bytes,
    *,
    status: str = "active",
    pending: bool = False,
    digest: str | None = None,
    key: str | None = None,
) -> str:
    subdir = asset_gc.ASSET_STORAGE_CLASS_TO_SUBDIR.get(storage_class, "originals")
    object_key = key or adapter.save(payload, subdir, ".bin")
    deleted_at = iso(NOW) if status == "deleted" else None
    next_run_at = iso(NOW) if pending else None
    conn.execute(
        """
        INSERT INTO asset (
          id, original_name, mime_type, size, object_key, sha256, storage_class,
          status, created_at, deleted_at, object_purged_at, gc_next_run_at,
          gc_attempts, gc_error_code
        ) VALUES (?, 'fixture.bin', 'application/octet-stream', ?, ?, ?, ?, ?, ?, ?, NULL, ?, 0, NULL)
        """,
        (
            asset_id,
            len(payload),
            object_key,
            digest or hashlib.sha256(payload).hexdigest(),
            storage_class,
            status,
            iso(NOW),
            deleted_at,
            next_run_at,
        ),
    )
    return object_key


def _asset(asset_id: str):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM asset WHERE id=?", (asset_id,)).fetchone()


def _asset_gc_state(asset_id: str, *, conn=None) -> tuple[object, ...]:
    row = (
        conn.execute("SELECT * FROM asset WHERE id=?", (asset_id,)).fetchone()
        if conn is not None
        else _asset(asset_id)
    )
    assert row is not None
    return tuple(
        row[column]
        for column in ("status", "deleted_at", "object_purged_at", "gc_next_run_at", "gc_attempts", "gc_error_code")
    )


def _delete_photo_for_test(monkeypatch: pytest.MonkeyPatch, photo_id: str) -> None:
    """直调端点以覆盖照片删除后的角色类别校验，不依赖鉴权夹具。"""
    monkeypatch.setattr(deps, "require_csrf", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(files, "audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(files, "get_request_id", lambda _request: "fixture")
    monkeypatch.setattr(files, "now_utc", lambda: NOW)
    response = files.delete_photo(photo_id, object(), payload={"sub": "fixture"})
    assert response.status_code == 204


@pytest.mark.parametrize("legacy_version", (1, 2, 3, 4))
def test_known_schema_versions_migrate_to_v5(gc_env, monkeypatch: pytest.MonkeyPatch, legacy_version: int):
    """每个已发布版本均通过真实的逐步迁移到 v5，而不是伪造版本号。"""
    root, _ = gc_env
    legacy_root = root.parent / f"legacy-v{legacy_version}"
    _set_data_root(monkeypatch, legacy_root)
    legacy_root.joinpath("db").mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path)
    try:
        if legacy_version == 1:
            conn.executescript(SCHEMA_V1)
        elif legacy_version == 2:
            conn.executescript(SCHEMA_V1)
            for statement in MIGRATION_V2:
                conn.execute(statement)
        elif legacy_version == 3:
            conn.executescript(SCHEMA_V3)
            for statement in MIGRATION_V2 + MIGRATION_V3[1:]:
                conn.execute(statement)
        else:
            conn.executescript(SCHEMA_V4)
            for statement in MIGRATION_V2 + MIGRATION_V3[1:] + MIGRATION_V4[1:]:
                conn.execute(statement)
        conn.execute(
            "CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO schema_version VALUES (1, ?, ?)", (legacy_version, iso(NOW)))
        conn.commit()
    finally:
        conn.close()

    init_db()
    with get_conn() as migrated:
        assert migrated.execute("SELECT version FROM schema_version WHERE id=1").fetchone()[0] == 5
        columns = {row["name"] for row in migrated.execute("PRAGMA table_info(asset)")}
    assert {"storage_class", "object_purged_at", "gc_next_run_at", "gc_attempts", "gc_error_code"} <= columns


def test_fresh_v5_enforces_gc_columns_and_rejects_future_version(gc_env):
    del gc_env
    with get_conn() as conn:
        assert conn.execute("SELECT version FROM schema_version WHERE id=1").fetchone()[0] == 5
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO asset (id, original_name, mime_type, size, object_key, sha256, storage_class, created_at) "
                "VALUES ('bad-class', 'x', 'text/plain', 0, 'x', ?, 'not-approved', ?)",
                ("a" * 64, iso(NOW)),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO asset (id, original_name, mime_type, size, object_key, sha256, storage_class, gc_attempts, created_at) "
                "VALUES ('bad-attempts', 'x', 'text/plain', 0, 'x', ?, 'file', -1, ?)",
                ("a" * 64, iso(NOW)),
            )
        conn.execute("UPDATE schema_version SET version=6 WHERE id=1")
    with pytest.raises(SchemaVersionError, match="高于当前程序"):
        init_db()


def test_v5_layout_rejects_missing_gc_column(gc_env):
    del gc_env
    with get_conn() as conn:
        conn.execute("ALTER TABLE asset DROP COLUMN gc_error_code")
    with pytest.raises(SchemaVersionError, match="asset GC 字段"):
        init_db()


@pytest.mark.parametrize("column", ("object_purged_at", "gc_next_run_at", "gc_error_code"))
def test_v5_layout_rejects_nonnullable_default_gc_fields(gc_env, column: str):
    """PRAGMA 必须拒绝 nullable GC 字段被收紧为 NOT NULL DEFAULT ''。"""
    del gc_env
    with get_conn() as conn:
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='asset'").fetchone()
        assert row is not None
        expected = f"{column} TEXT"
        weakened = str(row["sql"]).replace(expected, f"{expected} NOT NULL DEFAULT ''", 1)
        assert weakened != row["sql"]
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute("UPDATE sqlite_master SET sql=? WHERE type='table' AND name='asset'", (weakened,))
        conn.execute("PRAGMA writable_schema=OFF")

    with pytest.raises(SchemaVersionError, match="asset GC 字段"):
        init_db()


def test_v5_layout_rejects_weakened_gc_check(gc_env):
    del gc_env
    with get_conn() as conn:
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='asset'").fetchone()
        assert row is not None
        weakened = str(row["sql"]).replace("CHECK(gc_attempts>=0)", "")
        assert weakened != row["sql"]
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute("UPDATE sqlite_master SET sql=? WHERE type='table' AND name='asset'", (weakened,))
        conn.execute("PRAGMA writable_schema=OFF")
    with pytest.raises(SchemaVersionError, match="GC 约束"):
        init_db()


def test_v4_to_v5_classifies_conservatively_and_queues_only_proven_tombstones(
    gc_env, monkeypatch: pytest.MonkeyPatch
):
    root, _ = gc_env
    root = root.parent / "legacy-v4"
    _set_data_root(monkeypatch, root)
    root.joinpath("db").mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.executescript(SCHEMA_V4)
        for statement in MIGRATION_V2 + MIGRATION_V3[1:] + MIGRATION_V4[1:]:
            conn.execute(statement)
        now = iso(NOW)
        for asset_id, status in (
            ("file", "active"),
            ("photo-original", "active"),
            ("photo-thumbnail", "active"),
            ("knowledge", "active"),
            ("mail", "active"),
            ("conflict", "active"),
            ("known-deleted", "deleted"),
            ("unknown-deleted", "deleted"),
            ("missing-deleted-at", "deleted"),
        ):
            conn.execute(
                "INSERT INTO asset (id, original_name, mime_type, size, object_key, sha256, status, created_at, deleted_at) "
                "VALUES (?, 'fixture.bin', 'application/octet-stream', 1, ?, ?, ?, ?, ?)",
                (asset_id, f"{asset_id}.bin", "a" * 64, status, now, now if status == "deleted" else None),
            )
        conn.execute("UPDATE asset SET deleted_at=NULL WHERE id='missing-deleted-at'")
        conn.execute(
            "INSERT INTO photo (id, asset_id, thumbnail_asset_id, created_at) VALUES ('photo', 'photo-original', 'photo-thumbnail', ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO photo (id, asset_id, thumbnail_asset_id, created_at) VALUES ('conflict-photo', 'conflict', 'photo-thumbnail', ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO mail_message (id, message_id, from_addr, received_at, read_flag) VALUES ('mail-message', 'fixture@example.test', '', ?, 0)",
            (now,),
        )
        conn.execute(
            "INSERT INTO mail_attachment (id, message_id, asset_id, name) VALUES ('mail-attachment', 'mail-message', 'mail', '')"
        )
        conn.execute(
            "INSERT INTO knowledge_document (id, asset_id, title, document_type, hash, created_at) VALUES ('knowledge-doc', 'knowledge', 'x', 'txt', ?, ?)",
            ("a" * 64, now),
        )
        conn.execute(
            "INSERT INTO knowledge_document (id, asset_id, title, document_type, hash, created_at) VALUES ('conflict-doc', 'conflict', 'x', 'txt', ?, ?)",
            ("a" * 64, now),
        )
        conn.execute(
            "INSERT INTO knowledge_document (id, asset_id, title, document_type, hash, created_at, deleted_at) VALUES ('deleted-doc', 'known-deleted', 'x', 'txt', ?, ?, ?)",
            ("a" * 64, now, now),
        )
        conn.execute(
            "CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO schema_version VALUES (1, 4, ?)", (now,))
        conn.commit()
    finally:
        conn.close()

    init_db()
    with get_conn() as conn:
        rows = {
            row["id"]: row
            for row in conn.execute(
                "SELECT id, storage_class, gc_next_run_at FROM asset ORDER BY id"
            )
        }
    assert rows["file"]["storage_class"] == "file"
    assert rows["photo-original"]["storage_class"] == "photo-original"
    assert rows["photo-thumbnail"]["storage_class"] == "photo-thumbnail"
    assert rows["knowledge"]["storage_class"] == "knowledge"
    assert rows["mail"]["storage_class"] == "mail-attachment"
    assert rows["conflict"]["storage_class"] == "unknown"
    assert rows["known-deleted"]["storage_class"] == "knowledge"
    assert rows["known-deleted"]["gc_next_run_at"] == iso(NOW)
    assert rows["unknown-deleted"]["storage_class"] == "unknown"
    assert rows["unknown-deleted"]["gc_next_run_at"] is None
    assert rows["missing-deleted-at"]["storage_class"] == "unknown"
    assert rows["missing-deleted-at"]["gc_next_run_at"] is None


def test_v5_layout_rejects_weakened_gc_index(gc_env):
    del gc_env
    with get_conn() as conn:
        conn.execute("DROP INDEX idx_asset_gc_due")
        conn.execute("CREATE INDEX idx_asset_gc_due ON asset(gc_next_run_at, id)")
    with pytest.raises(SchemaVersionError, match="asset GC 索引"):
        init_db()


def test_gc_purges_normal_missing_and_integrity_mismatch(gc_env):
    _, adapter = gc_env
    with get_conn() as conn:
        normal_key = _insert_asset(conn, adapter, "normal", "file", b"normal", status="deleted", pending=True)
        missing_key = _insert_asset(conn, adapter, "missing", "knowledge", b"missing", status="deleted", pending=True)
        mismatch_key = _insert_asset(
            conn,
            adapter,
            "mismatch",
            "photo-original",
            b"mismatch",
            status="deleted",
            pending=True,
            digest="f" * 64,
        )
    adapter.delete("originals", missing_key)

    result = asset_gc.run_asset_gc(now=NOW)
    assert result.processed == 3
    assert result.purged_assets == 1
    assert result.missing_objects == 1
    assert result.deferred == 1
    assert not adapter.exists("originals", normal_key)
    assert adapter.exists("originals", mismatch_key)
    assert _asset("normal")["object_purged_at"] == iso(NOW)
    assert _asset("missing")["object_purged_at"] == iso(NOW)
    deferred = _asset("mismatch")
    assert deferred["object_purged_at"] is None
    assert deferred["gc_error_code"] == "OBJECT_INTEGRITY_MISMATCH"
    assert deferred["gc_next_run_at"] > iso(NOW)


def test_gc_failure_is_deferred_behind_later_due_asset(gc_env, monkeypatch: pytest.MonkeyPatch):
    _, adapter = gc_env
    with get_conn() as conn:
        failed_key = _insert_asset(conn, adapter, "asset-a", "file", b"first", status="deleted", pending=True)
        later_key = _insert_asset(conn, adapter, "asset-b", "file", b"later", status="deleted", pending=True)

    original_delete = adapter.delete

    def selective_delete(subdir: str, object_key: str) -> None:
        if subdir == "originals" and object_key == failed_key:
            raise OSError("private storage failure")
        original_delete(subdir, object_key)

    monkeypatch.setattr(adapter, "delete", selective_delete)
    first = asset_gc.run_asset_gc(limit=1, now=NOW)
    assert first.processed == first.deferred == 1
    assert _asset("asset-a")["gc_next_run_at"] > iso(NOW)
    assert _asset("asset-b")["object_purged_at"] is None

    second = asset_gc.run_asset_gc(limit=1, now=NOW + timedelta(seconds=1))
    assert second.purged_assets == 1
    assert _asset("asset-b")["object_purged_at"] == iso(NOW + timedelta(seconds=1))
    assert not adapter.exists("originals", later_key)
    repeated = asset_gc.run_asset_gc(limit=1, now=NOW + timedelta(hours=2))
    assert repeated.deferred == 1
    assert _asset("asset-a")["object_purged_at"] is None


def test_gc_stale_candidate_cannot_bypass_retry_backoff(gc_env, monkeypatch: pytest.MonkeyPatch):
    """显式模拟两个 worker 已枚举同一候选，后者不得穿透前者的未来退避。"""
    _, adapter = gc_env
    with get_conn() as conn:
        key = _insert_asset(conn, adapter, "stale-candidate", "file", b"retry", status="deleted", pending=True)
        stale_candidates = [
            str(row["id"])
            for row in conn.execute(
                "SELECT id FROM asset WHERE status='deleted' AND object_purged_at IS NULL "
                "AND gc_next_run_at IS NOT NULL AND gc_next_run_at <= ?",
                (iso(NOW),),
            ).fetchall()
        ]
    assert stale_candidates == ["stale-candidate"]

    open_calls: list[tuple[str, str]] = []
    delete_calls: list[tuple[str, str]] = []
    original_open = adapter.open

    def observed_open(subdir: str, object_key: str) -> bytes:
        open_calls.append((subdir, object_key))
        return original_open(subdir, object_key)

    def fail_delete(subdir: str, object_key: str) -> None:
        delete_calls.append((subdir, object_key))
        raise OSError("private storage failure")

    monkeypatch.setattr(adapter, "open", observed_open)
    monkeypatch.setattr(adapter, "delete", fail_delete)
    assert asset_gc._process_asset(stale_candidates[0], NOW) == "deferred"
    # 第二个调用复用上述已经枚举出的 id，等价于陈旧 worker 在首个 worker 提交后获得写锁。
    assert asset_gc._process_asset(stale_candidates[0], NOW) == "skipped"

    row = _asset("stale-candidate")
    assert open_calls == [("originals", key)]
    assert delete_calls == [("originals", key)]
    assert row["gc_attempts"] == 1
    assert row["gc_error_code"] == "OBJECT_DELETE_FAILED"
    assert row["gc_next_run_at"] > iso(NOW)
    assert adapter.exists("originals", key)


def test_gc_unexpected_recovery_keeps_future_candidate_unchanged(
    gc_env, monkeypatch: pytest.MonkeyPatch
):
    """首次连接瞬时失败后，恢复路径也不能覆盖已后移到未来的陈旧候选。"""
    _, adapter = gc_env
    with get_conn() as conn:
        _insert_asset(conn, adapter, "recovery-stale", "file", b"future", status="deleted", pending=True)
        conn.execute(
            "UPDATE asset SET gc_next_run_at=?, gc_attempts=?, gc_error_code=? WHERE id='recovery-stale'",
            (iso(NOW + timedelta(hours=1)), 7, "EXISTING_BACKOFF"),
        )
    before = _asset_gc_state("recovery-stale")

    storage_calls: list[tuple[object, ...]] = []

    def forbid_storage(*args, **kwargs):
        storage_calls.append((*args, *kwargs.values()))
        raise AssertionError("stale recovery must not access storage")

    original_get_conn = asset_gc.get_conn
    get_conn_calls = 0

    def fail_first_get_conn():
        nonlocal get_conn_calls
        get_conn_calls += 1
        if get_conn_calls == 1:
            raise sqlite3.OperationalError("transient connection failure")
        return original_get_conn()

    monkeypatch.setattr(adapter, "open", forbid_storage)
    monkeypatch.setattr(adapter, "delete", forbid_storage)
    monkeypatch.setattr(asset_gc, "get_conn", fail_first_get_conn)
    assert asset_gc._process_asset("recovery-stale", NOW) == "skipped"

    assert get_conn_calls == 2
    assert _asset_gc_state("recovery-stale") == before
    assert storage_calls == []


def test_gc_converges_when_object_delete_precedes_db_failure(gc_env, monkeypatch: pytest.MonkeyPatch):
    _, adapter = gc_env
    with get_conn() as conn:
        key = _insert_asset(conn, adapter, "crash", "file", b"crash", status="deleted", pending=True)

    original_mark = asset_gc._mark_purged_locked

    def fail_after_physical_delete(*_args, **_kwargs) -> None:
        raise RuntimeError("private database failure")

    monkeypatch.setattr(asset_gc, "_mark_purged_locked", fail_after_physical_delete)
    first = asset_gc.run_asset_gc(now=NOW)
    assert first.deferred == 1
    assert not adapter.exists("originals", key)
    assert _asset("crash")["object_purged_at"] is None

    monkeypatch.setattr(asset_gc, "_mark_purged_locked", original_mark)
    second = asset_gc.run_asset_gc(now=NOW + timedelta(hours=2))
    assert second.missing_objects == 1
    assert _asset("crash")["object_purged_at"] == iso(NOW + timedelta(hours=2))


def test_gc_keeps_shared_assets_and_never_handles_mail_or_unknown(gc_env, monkeypatch: pytest.MonkeyPatch):
    _, adapter = gc_env
    with get_conn() as conn:
        photo_key = _insert_asset(conn, adapter, "shared-photo", "file", b"photo")
        mail_key = _insert_asset(conn, adapter, "shared-mail", "file", b"mail")
        knowledge_key = _insert_asset(conn, adapter, "shared-knowledge", "file", b"knowledge")
        isolated_mail_key = _insert_asset(
            conn,
            adapter,
            "mail-lifecycle",
            "mail-attachment",
            b"mail-only",
            status="deleted",
            pending=True,
        )
        unknown_key = _insert_asset(
            conn,
            adapter,
            "unknown-lifecycle",
            "unknown",
            b"unknown-only",
            status="deleted",
            pending=True,
        )
        conn.execute(
            "INSERT INTO photo (id, asset_id, thumbnail_asset_id, created_at) VALUES ('photo-ref', 'shared-photo', 'shared-photo', ?)",
            (iso(NOW),),
        )
        conn.execute(
            "INSERT INTO mail_message (id, message_id, from_addr, received_at, read_flag) VALUES ('mail-ref', 'ref@example.test', '', ?, 0)",
            (iso(NOW),),
        )
        conn.execute(
            "INSERT INTO mail_attachment (id, message_id, asset_id, name) VALUES ('attachment-ref', 'mail-ref', 'shared-mail', '')"
        )
        conn.execute(
            "INSERT INTO knowledge_document (id, asset_id, title, document_type, hash, created_at) VALUES ('knowledge-ref', 'shared-knowledge', 'x', 'txt', ?, ?)",
            ("a" * 64, iso(NOW)),
        )
        assert not asset_gc.queue_deleted_asset(conn, "shared-photo", now=NOW)
        assert not asset_gc.queue_deleted_asset(conn, "shared-mail", now=NOW)
        assert not asset_gc.queue_deleted_asset(conn, "shared-knowledge", now=NOW)

    calls: list[tuple[str, str]] = []
    original_delete = adapter.delete

    def observed_delete(subdir: str, object_key: str) -> None:
        calls.append((subdir, object_key))
        original_delete(subdir, object_key)

    monkeypatch.setattr(adapter, "delete", observed_delete)
    result = asset_gc.run_asset_gc(now=NOW)
    assert result.processed == 0
    assert calls == []
    for asset_id, key in (
        ("shared-photo", photo_key),
        ("shared-mail", mail_key),
        ("shared-knowledge", knowledge_key),
    ):
        assert _asset(asset_id)["status"] == "active"
        assert adapter.exists("originals", key)
    assert adapter.exists("mail-attachments", isolated_mail_key)
    assert adapter.exists("originals", unknown_key)


def test_queue_deleted_asset_keeps_active_mail_and_unknown_unchanged(gc_env):
    _, adapter = gc_env
    with get_conn() as conn:
        mail_key = _insert_asset(conn, adapter, "mail-queue-isolated", "mail-attachment", b"mail")
        unknown_key = _insert_asset(conn, adapter, "unknown-queue-isolated", "unknown", b"unknown")
        before = {
            asset_id: _asset_gc_state(asset_id, conn=conn)
            for asset_id in ("mail-queue-isolated", "unknown-queue-isolated")
        }
        assert not asset_gc.queue_deleted_asset(conn, "mail-queue-isolated", now=NOW)
        assert not asset_gc.queue_deleted_asset(conn, "unknown-queue-isolated", now=NOW)
        after = {
            asset_id: tuple(
                conn.execute(
                    "SELECT status, deleted_at, object_purged_at, gc_next_run_at, gc_attempts, gc_error_code "
                    "FROM asset WHERE id=?",
                    (asset_id,),
                ).fetchone()
            )
            for asset_id in before
        }

    assert after == before
    assert adapter.exists("mail-attachments", mail_key)
    assert adapter.exists("originals", unknown_key)


def test_gc_rejects_path_failures_without_deleting_object(gc_env, monkeypatch: pytest.MonkeyPatch):
    _, adapter = gc_env
    with get_conn() as conn:
        key = _insert_asset(conn, adapter, "path-failure", "file", b"path", status="deleted", pending=True)

    def reject_open(*_args, **_kwargs):
        raise ValueError("unsafe object path")

    monkeypatch.setattr(adapter, "open", reject_open)
    result = asset_gc.run_asset_gc(now=NOW)
    assert result.deferred == 1
    assert adapter.exists("originals", key)
    row = _asset("path-failure")
    assert row["gc_error_code"] == "OBJECT_PATH_INVALID"
    assert row["object_purged_at"] is None


def test_gc_rejects_illegal_key_and_nonlowercase_digest_before_storage_access(
    gc_env, monkeypatch: pytest.MonkeyPatch
):
    _, adapter = gc_env
    with get_conn() as conn:
        key = _insert_asset(conn, adapter, "illegal-key", "file", b"key", status="deleted", pending=True)
        upper_key = _insert_asset(
            conn,
            adapter,
            "upper-digest",
            "file",
            b"upper",
            status="deleted",
            pending=True,
            digest="A" * 64,
        )
        conn.execute("UPDATE asset SET object_key='../unsafe' WHERE id='illegal-key'")

    calls: list[tuple[str, str]] = []
    original_open = adapter.open

    def observed_open(subdir: str, object_key: str) -> bytes:
        calls.append((subdir, object_key))
        return original_open(subdir, object_key)

    monkeypatch.setattr(adapter, "open", observed_open)
    result = asset_gc.run_asset_gc(now=NOW)
    assert result.deferred == 2
    assert _asset("illegal-key")["gc_error_code"] == "OBJECT_METADATA_INVALID"
    assert _asset("upper-digest")["gc_error_code"] == "OBJECT_METADATA_INVALID"
    assert calls == []
    assert adapter.exists("originals", key)
    assert adapter.exists("originals", upper_key)


def test_gc_defers_unexpected_storage_read_and_delete_failures_with_stable_codes(
    gc_env, monkeypatch: pytest.MonkeyPatch
):
    _, adapter = gc_env
    with get_conn() as conn:
        read_key = _insert_asset(conn, adapter, "a-read-failure", "file", b"read", status="deleted", pending=True)
        delete_key = _insert_asset(conn, adapter, "z-delete-failure", "file", b"delete", status="deleted", pending=True)

    original_open = adapter.open

    def fail_read(subdir: str, object_key: str) -> bytes:
        if object_key == read_key:
            raise RuntimeError("backend private error")
        return original_open(subdir, object_key)

    monkeypatch.setattr(adapter, "open", fail_read)
    first = asset_gc.run_asset_gc(limit=1, now=NOW)
    assert first.deferred == 1
    assert _asset("a-read-failure")["gc_error_code"] == "OBJECT_READ_FAILED"
    assert adapter.exists("originals", read_key)

    monkeypatch.setattr(adapter, "open", original_open)
    original_delete = adapter.delete

    def fail_delete(subdir: str, object_key: str) -> None:
        if object_key == delete_key:
            raise RuntimeError("backend private error")
        original_delete(subdir, object_key)

    monkeypatch.setattr(adapter, "delete", fail_delete)
    second = asset_gc.run_asset_gc(limit=1, now=NOW)
    assert second.deferred == 1
    assert _asset("z-delete-failure")["gc_error_code"] == "OBJECT_DELETE_FAILED"
    assert adapter.exists("originals", delete_key)


def test_gc_rejects_symlink_target_without_following_it(gc_env):
    root, adapter = gc_env
    with get_conn() as conn:
        key = _insert_asset(conn, adapter, "symlink", "file", b"safe", status="deleted", pending=True)
    target = root / "objects" / "originals" / key
    outside = root / "outside.bin"
    outside.write_bytes(b"outside")
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError as exc:  # Windows hosts without symlink privilege cannot exercise this OS primitive.
        pytest.skip(f"symlink fixture unavailable: {type(exc).__name__}")

    result = asset_gc.run_asset_gc(now=NOW)
    row = _asset("symlink")
    assert result.deferred == 1
    assert row["gc_error_code"] == "OBJECT_PATH_INVALID"
    assert target.is_symlink()
    assert outside.read_bytes() == b"outside"


def test_photo_relationship_removal_queues_both_objects_and_preserves_shared_side(
    gc_env, monkeypatch: pytest.MonkeyPatch
):
    _, adapter = gc_env
    with get_conn() as conn:
        original_key = _insert_asset(conn, adapter, "photo-original", "photo-original", b"original")
        first_thumb_key = _insert_asset(conn, adapter, "photo-thumb-one", "photo-thumbnail", b"thumb-one")
        second_thumb_key = _insert_asset(conn, adapter, "photo-thumb-two", "photo-thumbnail", b"thumb-two")
        conn.execute(
            "INSERT INTO photo (id, asset_id, thumbnail_asset_id, created_at) VALUES ('photo-one', 'photo-original', 'photo-thumb-one', ?)",
            (iso(NOW),),
        )
        conn.execute(
            "INSERT INTO photo (id, asset_id, thumbnail_asset_id, created_at) VALUES ('photo-two', 'photo-original', 'photo-thumb-two', ?)",
            (iso(NOW),),
        )
    _delete_photo_for_test(monkeypatch, "photo-one")

    assert _asset("photo-original")["status"] == "active"
    queued_thumbnail = _asset("photo-thumb-one")
    assert queued_thumbnail["status"] == "deleted"
    assert queued_thumbnail["gc_next_run_at"] == iso(NOW)
    result = asset_gc.run_asset_gc(now=NOW)
    assert result.purged_assets == 1
    assert adapter.exists("originals", original_key)
    assert not adapter.exists("thumbnails", first_thumb_key)
    assert adapter.exists("thumbnails", second_thumb_key)


def test_photo_relationship_removal_queues_each_mapped_subdir(gc_env, monkeypatch: pytest.MonkeyPatch):
    _, adapter = gc_env
    with get_conn() as conn:
        original_key = _insert_asset(conn, adapter, "original", "photo-original", b"original")
        thumbnail_key = _insert_asset(conn, adapter, "thumbnail", "photo-thumbnail", b"thumbnail")
        conn.execute(
            "INSERT INTO photo (id, asset_id, thumbnail_asset_id, created_at) VALUES ('photo', 'original', 'thumbnail', ?)",
            (iso(NOW),),
        )
    _delete_photo_for_test(monkeypatch, "photo")

    calls: list[tuple[str, str]] = []
    original_open = adapter.open
    original_delete = adapter.delete

    def observed_open(subdir: str, object_key: str) -> bytes:
        calls.append(("open:" + subdir, object_key))
        return original_open(subdir, object_key)

    def observed_delete(subdir: str, object_key: str) -> None:
        calls.append(("delete:" + subdir, object_key))
        original_delete(subdir, object_key)

    monkeypatch.setattr(adapter, "open", observed_open)
    monkeypatch.setattr(adapter, "delete", observed_delete)
    result = asset_gc.run_asset_gc(now=NOW)
    assert result.purged_assets == 2
    assert not adapter.exists("originals", original_key)
    assert not adapter.exists("thumbnails", thumbnail_key)
    assert {call[0] for call in calls} == {
        "open:originals",
        "delete:originals",
        "open:thumbnails",
        "delete:thumbnails",
    }
    assert all("mail-attachments" not in call[0] and "mail-raw" not in call[0] for call in calls)


def test_photo_delete_isolates_cross_class_and_dual_role_assets(
    gc_env, monkeypatch: pytest.MonkeyPatch
):
    """损坏的照片关系只删元数据，不能把非照片或双角色 asset 交给通用 GC。"""
    _, adapter = gc_env
    with get_conn() as conn:
        keys = {
            "mail-role": _insert_asset(conn, adapter, "mail-role", "mail-attachment", b"mail"),
            "unknown-role": _insert_asset(conn, adapter, "unknown-role", "unknown", b"unknown"),
            "file-role": _insert_asset(conn, adapter, "file-role", "file", b"file"),
            "knowledge-role": _insert_asset(conn, adapter, "knowledge-role", "knowledge", b"knowledge"),
            "dual-role": _insert_asset(conn, adapter, "dual-role", "photo-original", b"dual"),
        }
        conn.executemany(
            "INSERT INTO photo (id, asset_id, thumbnail_asset_id, created_at) VALUES (?, ?, ?, ?)",
            (
                ("photo-mail-unknown", "mail-role", "unknown-role", iso(NOW)),
                ("photo-file-knowledge", "file-role", "knowledge-role", iso(NOW)),
                ("photo-dual-role", "dual-role", "dual-role", iso(NOW)),
            ),
        )
        before = {
            asset_id: _asset_gc_state(asset_id, conn=conn)
            for asset_id in keys
        }

    for photo_id in ("photo-mail-unknown", "photo-file-knowledge", "photo-dual-role"):
        _delete_photo_for_test(monkeypatch, photo_id)

    assert {asset_id: _asset_gc_state(asset_id) for asset_id in keys} == before
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM photo").fetchone()[0] == 0

    calls: list[tuple[str, str]] = []
    original_open = adapter.open
    original_delete = adapter.delete

    def observed_open(subdir: str, object_key: str) -> bytes:
        calls.append(("open:" + subdir, object_key))
        return original_open(subdir, object_key)

    def observed_delete(subdir: str, object_key: str) -> None:
        calls.append(("delete:" + subdir, object_key))
        original_delete(subdir, object_key)

    monkeypatch.setattr(adapter, "open", observed_open)
    monkeypatch.setattr(adapter, "delete", observed_delete)
    assert asset_gc.run_asset_gc(now=NOW).processed == 0
    assert calls == []
    assert adapter.exists("mail-attachments", keys["mail-role"])
    assert all(adapter.exists("originals", keys[asset_id]) for asset_id in ("unknown-role", "file-role", "knowledge-role", "dual-role"))


def test_knowledge_purge_only_queues_exclusive_asset_and_ingest_checks_class(gc_env):
    _, adapter = gc_env
    with get_conn() as conn:
        _insert_asset(conn, adapter, "knowledge-asset", "knowledge", b"knowledge")
        _insert_asset(conn, adapter, "wrong-asset", "file", b"wrong")
        for document_id in ("first", "second"):
            conn.execute(
                "INSERT INTO knowledge_document (id, asset_id, title, document_type, hash, created_at) VALUES (?, 'knowledge-asset', 'x', 'txt', ?, ?)",
                (document_id, "a" * 64, iso(NOW)),
            )
        conn.execute(
            "INSERT INTO knowledge_document (id, asset_id, title, document_type, hash, created_at) VALUES ('wrong-doc', 'wrong-asset', 'x', 'txt', ?, ?)",
            ("a" * 64, iso(NOW)),
        )
        conn.execute(
            "INSERT INTO chunk (id, document_id, seq, text, token_count, hash) VALUES ('first-chunk', 'first', 0, 'x', 1, 'h')"
        )
        conn.execute(
            "INSERT INTO vector_ref (chunk_id, store, vector_id, model_id, dim) "
            "VALUES ('first-chunk', 'sqlite-vec', 'first-chunk', 'fixture', 1)"
        )
        conn.execute("INSERT INTO chunk_embedding (chunk_id, embedding) VALUES ('first-chunk', ?)", (b"\x00\x00\x00\x00",))
        first_job = enqueue_job(conn, "purge", {"documentId": "first", "assetId": "knowledge-asset"})
        second_job = enqueue_job(conn, "purge", {"documentId": "second", "assetId": "knowledge-asset"})
        wrong_job = enqueue_job(conn, "purge", {"documentId": "wrong-doc", "assetId": "wrong-asset"})
        conn.execute("UPDATE ingestion_job SET status='running' WHERE id IN (?, ?, ?)", (first_job, second_job, wrong_job))
        worker_runner.process_purge(conn, first_job, {"documentId": "first", "assetId": "knowledge-asset"})
        assert conn.execute("SELECT 1 FROM chunk WHERE document_id='first'").fetchone() is None
        assert conn.execute("SELECT 1 FROM vector_ref WHERE chunk_id='first-chunk'").fetchone() is None
        assert conn.execute("SELECT 1 FROM chunk_embedding WHERE chunk_id='first-chunk'").fetchone() is None
        assert conn.execute("SELECT status FROM asset WHERE id='knowledge-asset'").fetchone()[0] == "active"
        worker_runner.process_purge(conn, second_job, {"documentId": "second", "assetId": "knowledge-asset"})
        queued = conn.execute(
            "SELECT status, deleted_at, gc_next_run_at FROM asset WHERE id='knowledge-asset'"
        ).fetchone()
        assert queued["status"] == "deleted"
        assert queued["deleted_at"] is not None and queued["gc_next_run_at"] is not None
        with pytest.raises(worker_runner.JobProcessingError, match="ASSET_MISSING"):
            worker_runner.process_ingest(
                conn,
                "fixture",
                {"documentId": "wrong-doc", "assetId": "wrong-asset"},
            )
        worker_runner.process_purge(conn, wrong_job, {"documentId": "wrong-doc", "assetId": "wrong-asset"})
        wrong = conn.execute(
            "SELECT status, deleted_at, gc_next_run_at FROM asset WHERE id='wrong-asset'"
        ).fetchone()
        assert tuple(wrong) == ("active", None, None)
        assert conn.execute("SELECT deleted_at FROM knowledge_document WHERE id='wrong-doc'").fetchone()[0] is not None


def test_gc_rereads_live_references_after_waiting_for_concurrent_writer(
    gc_env, monkeypatch: pytest.MonkeyPatch
):
    """候选枚举后新增的关系仍会在写锁内重读，不能误删对象。"""
    _, adapter = gc_env
    with get_conn() as conn:
        key = _insert_asset(conn, adapter, "race-asset", "file", b"race", status="deleted", pending=True)

    selected = Event()
    release_processor = Event()
    original_process = asset_gc._process_asset

    def gated_process(asset_id: str, now: datetime) -> str:
        selected.set()
        assert release_processor.wait(timeout=10)
        return original_process(asset_id, now)

    monkeypatch.setattr(asset_gc, "_process_asset", gated_process)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(asset_gc.run_asset_gc, now=NOW)
        assert selected.wait(timeout=10)
        connection = sqlite3.connect(settings.db_path, timeout=10)
        try:
            connection.execute(
                "INSERT INTO photo (id, asset_id, thumbnail_asset_id, created_at) VALUES ('race-photo', 'race-asset', 'race-asset', ?)",
                (iso(NOW),),
            )
            connection.commit()
        finally:
            connection.close()
        release_processor.set()
        result = pending.result(timeout=15)
    assert result.restored_shared == 1
    row = _asset("race-asset")
    assert row["status"] == "active"
    assert row["gc_next_run_at"] is None
    assert adapter.exists("originals", key)


def test_two_gc_workers_are_idempotent_for_the_same_asset(gc_env):
    _, adapter = gc_env
    with get_conn() as conn:
        key = _insert_asset(conn, adapter, "two-workers", "file", b"same", status="deleted", pending=True)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: asset_gc.run_asset_gc(now=NOW), range(2)))
    assert sum(result.purged_assets for result in results) == 1
    assert _asset("two-workers")["object_purged_at"] == iso(NOW)
    assert not adapter.exists("originals", key)


def test_worker_maintenance_isolates_mail_and_asset_gc(monkeypatch: pytest.MonkeyPatch):
    called: list[str] = []

    def fail_mail() -> None:
        called.append("mail")
        raise RuntimeError("private mail error")

    monkeypatch.setattr(worker_runner, "run_mail_retention", fail_mail)
    monkeypatch.setattr(worker_runner, "run_asset_gc", lambda: called.append("asset"))
    assert worker_runner.run_maintenance() is False
    assert called == ["mail", "asset"]

    called.clear()
    monkeypatch.setattr(worker_runner, "run_mail_retention", lambda: called.append("mail"))

    def fail_asset() -> None:
        called.append("asset")
        raise RuntimeError("private asset error")

    monkeypatch.setattr(worker_runner, "run_asset_gc", fail_asset)
    assert worker_runner.run_maintenance() is False
    assert called == ["mail", "asset"]


def test_worker_maintenance_survives_pre_schema_start_and_recovers(
    gc_env, monkeypatch: pytest.MonkeyPatch
):
    """worker 先于 API migration 启动时，两个维护失败都不能终止其后续轮询能力。"""
    root, _ = gc_env
    early_root = root.parent / "pre-schema"
    _set_data_root(monkeypatch, early_root)

    assert worker_runner.run_maintenance() is False
    init_db()
    assert worker_runner.run_maintenance() is True
