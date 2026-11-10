"""标准库备份 → 隔离恢复 → 校验的临时演练，不接触现有 data。"""

from __future__ import annotations

import json
import hashlib
import sqlite3
import struct
import sys
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest


from app.db import MIGRATION_V2, MIGRATION_V3, SCHEMA, SCHEMA_V3  # noqa: E402
from app.config import settings  # noqa: E402
from app.services import mail_retention  # noqa: E402
from app.storage import StorageAdapter  # noqa: E402


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "backup_restore.py"
SPEC = importlib.util.spec_from_file_location("starisle_backup_restore", SCRIPT)
assert SPEC and SPEC.loader
backup_restore = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = backup_restore
SPEC.loader.exec_module(backup_restore)


def _fixture_data_root(root: Path) -> None:
    db_path = root / "db" / "star-isle.db"
    db_path.parent.mkdir(parents=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.execute(
            "CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO schema_version VALUES (1, 6, '2026-08-27T00:00:00+00:00')")
        assets = [
            (
                "asset-photo",
                "photo.png",
                "image/png",
                b"photo",
                "photo.bin",
                "photo-original",
            ),
            (
                "asset-thumb",
                "thumb.webp",
                "image/webp",
                b"thumb",
                "thumb.bin",
                "photo-thumbnail",
            ),
            (
                "asset-doc",
                "manual.md",
                "text/markdown",
                b"manual!!",
                "manual.bin",
                "knowledge",
            ),
            (
                "asset-mail",
                "invoice.html",
                "text/html",
                b"mail attachment",
                "mail-attachment.bin",
                "mail-attachment",
            ),
        ]
        for asset_id, name, mime, content, key, storage_class in assets:
            conn.execute(
                "INSERT INTO asset (id, original_name, mime_type, size, object_key, sha256, storage_class, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', '2026-08-27T00:00:00+00:00')",
                (asset_id, name, mime, len(content), key, hashlib.sha256(content).hexdigest(), storage_class),
            )
        conn.execute(
            "INSERT INTO photo (id, asset_id, capture_at, thumbnail_asset_id, width, height, created_at) "
            "VALUES ('photo-1', 'asset-photo', NULL, 'asset-thumb', 1, 1, '2026-08-27T00:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO knowledge_document (id, asset_id, title, document_type, version, hash, index_state, created_at) "
            "VALUES ('doc-1', 'asset-doc', 'manual', 'markdown', 1, 'h3', 'ready', '2026-08-27T00:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO chunk (id, document_id, seq, text, token_count, hash) "
            "VALUES ('chunk-1', 'doc-1', 0, '可检索的非空知识库文本', 1, 'chunk-h')"
        )
        conn.execute(
            "INSERT INTO vector_ref (chunk_id, store, vector_id, model_id, dim) "
            "VALUES ('chunk-1', 'sqlite-vec', 'chunk-1', 'hash-v1', 3)"
        )
        conn.execute(
            "INSERT INTO chunk_embedding (chunk_id, embedding) VALUES ('chunk-1', ?)",
            (struct.pack('<3f', 1.0, 0.0, 0.0),),
        )
        conn.execute(
            "INSERT INTO mail_message ("
            "id, message_id, from_addr, to_addr, subject, received_at, text_body, html_body, read_flag, raw_state"
            ") VALUES ('mail-1', 'backup-mail@example.test', 'sender@example.test', 'me@example.test', "
            "'backup mail', '2026-08-27T00:00:00+00:00', 'plain', '<p>safe</p>', 0, 'stored')"
        )
        raw_payload = b"From: backup@example.test\r\nSubject: backup raw\r\n\r\nexact raw\r\n"
        conn.execute(
            "INSERT INTO mail_raw (message_id, object_key, sha256, size, content_type, created_at) "
            "VALUES ('mail-1', 'mail-raw.eml', ?, ?, 'message/rfc822', '2026-08-27T00:00:00+00:00')",
            (hashlib.sha256(raw_payload).hexdigest(), len(raw_payload)),
        )
        conn.execute(
            "INSERT INTO mail_attachment (id, message_id, asset_id, name, mime_type, size) "
            "VALUES ('mail-att-1', 'mail-1', 'asset-mail', 'invoice.html', 'text/html', ?)",
            (len(b"mail attachment"),),
        )
        conn.commit()
    finally:
        conn.close()
    objects = root / "objects"
    for subdir, name, content in [
        ("originals", "photo.bin", b"photo"),
        ("thumbnails", "thumb.bin", b"thumb"),
        ("originals", "manual.bin", b"manual!!"),
        ("mail-attachments", "mail-attachment.bin", b"mail attachment"),
        ("mail-raw", "mail-raw.eml", raw_payload),
    ]:
        path = objects / subdir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _fixture_legacy_v3_data_root(root: Path) -> None:
    """构造历史 v3 数据库，用于 formatVersion=2 兼容性验证。"""
    db_path = root / "db" / "star-isle.db"
    db_path.parent.mkdir(parents=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_V3)
        for statement in MIGRATION_V2 + MIGRATION_V3[1:]:
            conn.execute(statement)
        conn.execute(
            "CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO schema_version VALUES (1, 3, '2026-08-27T00:00:00+00:00')")
        conn.execute(
            "INSERT INTO mail_message (id, message_id, from_addr, received_at, read_flag) "
            "VALUES ('legacy-mail', 'legacy@example.test', 'sender@example.test', '2026-08-27T00:00:00+00:00', 0)"
        )
        conn.commit()
    finally:
        conn.close()


def _fixture_legacy_v4_data_root(root: Path) -> None:
    """formatVersion=3 必须继续支持 v4 的 raw 归档布局。"""
    db_path = root / "db" / "star-isle.db"
    db_path.parent.mkdir(parents=True)
    conn = sqlite3.connect(db_path)
    try:
        from app.db import MIGRATION_V4, SCHEMA_V4

        conn.executescript(SCHEMA_V4)
        for statement in MIGRATION_V2 + MIGRATION_V3[1:] + MIGRATION_V4[1:]:
            conn.execute(statement)
        conn.execute(
            "CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO schema_version VALUES (1, 4, '2026-08-27T00:00:00+00:00')")
        conn.commit()
    finally:
        conn.close()


def _add_v5_lifecycle_asset(
    root: Path,
    *,
    asset_id: str,
    storage_class: str,
    content: bytes,
    object_key: str,
    status: str = "active",
    purged: bool = False,
    subdir: str | None = None,
) -> None:
    """构造 v5 生命周期状态；对象写入由调用方明确控制，避免目录猜测。"""
    now = "2026-08-30T00:00:00+00:00"
    conn = sqlite3.connect(root / "db" / "star-isle.db")
    try:
        deleted_at = now if status == "deleted" else None
        object_purged_at = now if purged else None
        next_run_at = now if status == "deleted" and not purged and storage_class in backup_restore.GC_STORAGE_CLASSES else None
        conn.execute(
            "INSERT INTO asset (id, original_name, mime_type, size, object_key, sha256, storage_class, status, "
            "created_at, deleted_at, object_purged_at, gc_next_run_at, gc_attempts, gc_error_code) "
            "VALUES (?, 'fixture.bin', 'application/octet-stream', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)",
            (
                asset_id,
                len(content),
                object_key,
                hashlib.sha256(content).hexdigest(),
                storage_class,
                status,
                now,
                deleted_at,
                object_purged_at,
                next_run_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    if not purged:
        target_subdir = subdir or backup_restore.ASSET_STORAGE_CLASS_TO_SUBDIR.get(storage_class, "originals")
        path = root / "objects" / target_subdir / object_key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _weaken_v5_nullable_asset_field(root: Path, column: str) -> None:
    """用实际 SQLite 表结构构造 NOT NULL DEFAULT '' 的弱化字段，供 PRAGMA 校验。"""
    conn = sqlite3.connect(root / "db" / "star-isle.db")
    try:
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='asset'").fetchone()
        assert row is not None
        expected = f"{column} TEXT"
        weakened = str(row[0]).replace(expected, f"{expected} NOT NULL DEFAULT ''", 1)
        assert weakened != row[0]
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute("UPDATE sqlite_master SET sql=? WHERE type='table' AND name='asset'", (weakened,))
        conn.execute("PRAGMA writable_schema=OFF")
        conn.commit()
    finally:
        conn.close()


def _write_legacy_format_manifest(backup: Path) -> None:
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["formatVersion"] = backup_restore.LEGACY_FORMAT_VERSION
    manifest["rowCounts"] = {
        table: manifest["rowCounts"][table]
        for table in backup_restore.LEGACY_ROW_COUNT_TABLES
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def _replace_mail_raw_layout(
    root: Path,
    *,
    message_id_definition: str,
    object_key_definition: str,
    index_definition: str,
) -> None:
    conn = sqlite3.connect(root / "db" / "star-isle.db")
    try:
        row = conn.execute(
            "SELECT message_id, object_key, sha256, size, content_type, created_at "
            "FROM mail_raw WHERE message_id='mail-1'"
        ).fetchone()
        assert row is not None
        conn.execute("DROP INDEX idx_mail_raw_object_key")
        conn.execute("DROP TABLE mail_raw")
        conn.execute(
            f"""
            CREATE TABLE mail_raw (
              {message_id_definition},
              {object_key_definition},
              sha256 TEXT NOT NULL CHECK(length(sha256)=64),
              size INTEGER NOT NULL CHECK(size>0),
              content_type TEXT NOT NULL CHECK(content_type='message/rfc822'),
              created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(index_definition)
        conn.execute(
            "INSERT INTO mail_raw (message_id, object_key, sha256, size, content_type, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            row,
        )
        conn.commit()
    finally:
        conn.close()


def test_backup_restore_verify_round_trip(tmp_path: Path):
    source = tmp_path / "source"
    backup = tmp_path / "backup"
    restored = tmp_path / "restored"
    _fixture_data_root(source)

    backup_restore.backup(source, backup, verify_after=True)
    assert (backup / "manifest.json").is_file()
    assert not (backup / ".env").exists()
    assert not (backup / "models").exists()

    backup_restore.restore(backup, restored)
    backup_restore.verify(backup, restored)
    conn = sqlite3.connect(restored / "db" / "star-isle.db")
    try:
        row = conn.execute(
            "SELECT m.message_id, a.object_key, a.sha256, ma.name, ma.mime_type, ma.size "
            "FROM mail_message m JOIN mail_attachment ma ON ma.message_id=m.id "
            "JOIN asset a ON a.id=ma.asset_id WHERE m.id='mail-1'"
        ).fetchone()
    finally:
        conn.close()
    assert row == (
        "backup-mail@example.test",
        "mail-attachment.bin",
        hashlib.sha256(b"mail attachment").hexdigest(),
        "invoice.html",
        "text/html",
        len(b"mail attachment"),
    )
    restored_object = restored / "objects" / "mail-attachments" / row[1]
    assert restored_object.read_bytes() == b"mail attachment"
    assert hashlib.sha256(restored_object.read_bytes()).hexdigest() == row[2]
    connection = sqlite3.connect(restored / "db" / "star-isle.db")
    try:
        raw_row = connection.execute(
            "SELECT m.raw_state, r.object_key, r.sha256, r.size, r.content_type "
            "FROM mail_message m JOIN mail_raw r ON r.message_id=m.id WHERE m.id='mail-1'"
        ).fetchone()
    finally:
        connection.close()
    raw_object = restored / "objects" / "mail-raw" / raw_row[1]
    raw_bytes = b"From: backup@example.test\r\nSubject: backup raw\r\n\r\nexact raw\r\n"
    assert raw_row == (
        "stored",
        "mail-raw.eml",
        hashlib.sha256(raw_bytes).hexdigest(),
        len(raw_bytes),
        "message/rfc822",
    )
    assert raw_object.read_bytes() == raw_bytes


def test_retention_cleanup_round_trips_without_expired_mail_or_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "source"
    backup = tmp_path / "backup"
    restored = tmp_path / "restored"
    _fixture_data_root(source)

    monkeypatch.setattr(settings, "data_root", source)
    monkeypatch.setattr(settings, "db_path", source / "db" / "star-isle.db")
    monkeypatch.setattr(settings, "objects_root", source / "objects")
    monkeypatch.setattr(settings, "originals_dir", source / "objects" / "originals")
    monkeypatch.setattr(settings, "thumbnails_dir", source / "objects" / "thumbnails")
    monkeypatch.setattr(settings, "mail_attachments_dir", source / "objects" / "mail-attachments")
    monkeypatch.setattr(settings, "mail_raw_dir", source / "objects" / "mail-raw")
    monkeypatch.setattr(settings, "mail_retention_days", 30)
    monkeypatch.setattr(mail_retention, "storage", StorageAdapter(source / "objects"))

    conn = sqlite3.connect(source / "db" / "star-isle.db")
    try:
        conn.execute("UPDATE mail_message SET received_at='2026-06-01T00:00:00+00:00' WHERE id='mail-1'")
        conn.commit()
    finally:
        conn.close()

    result = mail_retention.run_mail_retention(
        now=datetime(2026, 8, 29, tzinfo=timezone.utc)
    )
    assert result.deleted_messages == 1
    assert result.deleted_assets == 1
    assert not (source / "objects" / "mail-attachments" / "mail-attachment.bin").exists()
    assert not (source / "objects" / "mail-raw" / "mail-raw.eml").exists()

    backup_restore.backup(source, backup, verify_after=True)
    backup_restore.restore(backup, restored)
    backup_restore.verify(backup, restored)
    conn = sqlite3.connect(restored / "db" / "star-isle.db")
    try:
        assert conn.execute("SELECT 1 FROM mail_message WHERE id='mail-1'").fetchone() is None
        assert conn.execute("SELECT 1 FROM mail_attachment WHERE id='mail-att-1'").fetchone() is None
        assert conn.execute("SELECT 1 FROM asset WHERE id='asset-mail'").fetchone() is None
        assert conn.execute("SELECT 1 FROM mail_raw WHERE message_id='mail-1'").fetchone() is None
    finally:
        conn.close()
    assert not (restored / "objects" / "mail-attachments" / "mail-attachment.bin").exists()
    assert not (restored / "objects" / "mail-raw" / "mail-raw.eml").exists()


@pytest.mark.parametrize("mutation", ["missing", "tampered", "orphan", "state-mismatch"])
def test_backup_handles_raw_orphans_but_rejects_raw_missing_tampered_or_inconsistent_state(
    tmp_path: Path, mutation: str
):
    source = tmp_path / f"source-{mutation}"
    backup = tmp_path / f"backup-{mutation}"
    _fixture_data_root(source)

    if mutation == "missing":
        (source / "objects" / "mail-raw" / "mail-raw.eml").unlink()
    elif mutation == "tampered":
        conn = sqlite3.connect(source / "db" / "star-isle.db")
        try:
            conn.execute("UPDATE mail_raw SET size=size+1 WHERE message_id='mail-1'")
            conn.commit()
        finally:
            conn.close()
    elif mutation == "orphan":
        (source / "objects" / "mail-raw" / "orphan.eml").write_bytes(b"orphan")
    else:
        conn = sqlite3.connect(source / "db" / "star-isle.db")
        try:
            conn.execute("UPDATE mail_message SET raw_state='legacy' WHERE id='mail-1'")
            conn.commit()
        finally:
            conn.close()

    if mutation == "orphan":
        backup_restore.backup(source, backup, verify_after=True)
        assert "objects/mail-raw/orphan.eml" not in {
            item["path"]
            for item in json.loads((backup / "manifest.json").read_text(encoding="utf-8"))["files"]
        }
        restored = tmp_path / "restored-orphan"
        backup_restore.restore(backup, restored)
        assert not (restored / "objects" / "mail-raw" / "orphan.eml").exists()
        # Source orphan 不进入 snapshot；若后来出现在 destination，则 public verify/restore
        # 仍须以 schema >=4 的严格 raw 集合规则拒绝该备份。
        (backup / "objects" / "mail-raw" / "orphan.eml").write_bytes(b"orphan")
        with pytest.raises(backup_restore.BackupError, match="文件集合|邮件原文"):
            backup_restore.verify(backup)
        with pytest.raises(backup_restore.BackupError, match="文件集合|邮件原文"):
            backup_restore.restore(backup, tmp_path / "restore-rejects-destination-orphan")
    else:
        with pytest.raises(backup_restore.BackupError, match="邮件原文"):
            backup_restore.backup(source, backup, verify_after=False)
        assert (backup / backup_restore.INCOMPLETE_MARKER_NAME).is_file()
        with pytest.raises(backup_restore.BackupError, match="尚未完成"):
            backup_restore.verify(backup)


@pytest.mark.parametrize(
    ("message_id_definition", "object_key_definition", "index_definition"),
    [
        (
            "message_id TEXT NOT NULL REFERENCES mail_message(id) ON DELETE RESTRICT",
            "object_key TEXT NOT NULL",
            "CREATE UNIQUE INDEX idx_mail_raw_object_key ON mail_raw(object_key)",
        ),
        (
            "message_id TEXT PRIMARY KEY REFERENCES mail_message(id) ON DELETE RESTRICT",
            "object_key TEXT",
            "CREATE UNIQUE INDEX idx_mail_raw_object_key ON mail_raw(object_key)",
        ),
        (
            "message_id TEXT PRIMARY KEY REFERENCES mail_message(id) ON DELETE RESTRICT",
            "object_key TEXT NOT NULL",
            "CREATE UNIQUE INDEX idx_mail_raw_object_key ON mail_raw(sha256)",
        ),
    ],
    ids=("message-id-not-primary-key", "nullable-object-key", "wrong-index-column"),
)
def test_backup_and_restore_reject_weakened_mail_raw_layout(
    tmp_path: Path,
    message_id_definition: str,
    object_key_definition: str,
    index_definition: str,
):
    source = tmp_path / "source"
    backup = tmp_path / "backup"
    target = tmp_path / "restored"
    _fixture_data_root(source)
    _replace_mail_raw_layout(
        source,
        message_id_definition=message_id_definition,
        object_key_definition=object_key_definition,
        index_definition=index_definition,
    )

    with pytest.raises(backup_restore.BackupError):
        backup_restore.backup(source, backup, verify_after=False)
    assert (backup / backup_restore.INCOMPLETE_MARKER_NAME).is_file()
    with pytest.raises(backup_restore.BackupError, match="尚未完成"):
        backup_restore.verify(backup)
    with pytest.raises(backup_restore.BackupError):
        backup_restore.restore(backup, target)
    assert not target.exists()


def test_backup_restores_legacy_format2_schema3_without_raw_requirements(tmp_path: Path):
    source = tmp_path / "legacy-source"
    backup = tmp_path / "legacy-backup"
    restored = tmp_path / "legacy-restored"
    _fixture_legacy_v3_data_root(source)

    backup_restore.backup(source, backup, verify_after=False)
    _write_legacy_format_manifest(backup)
    backup_restore.verify(backup)
    backup_restore.restore(backup, restored)
    backup_restore.verify(backup, restored)
    conn = sqlite3.connect(restored / "db" / "star-isle.db")
    try:
        assert conn.execute("SELECT version FROM schema_version WHERE id=1").fetchone()[0] == 3
        assert conn.execute("SELECT 1 FROM mail_message WHERE id='legacy-mail'").fetchone()
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mail_raw'"
        ).fetchone() is None
    finally:
        conn.close()


def test_backup_restores_format3_schema4_with_raw_compatibility(tmp_path: Path):
    source = tmp_path / "v4-source"
    backup = tmp_path / "v4-backup"
    restored = tmp_path / "v4-restored"
    _fixture_legacy_v4_data_root(source)

    backup_restore.backup(source, backup, verify_after=True)
    assert json.loads((backup / "manifest.json").read_text(encoding="utf-8"))["formatVersion"] == 3
    backup_restore.restore(backup, restored)
    conn = sqlite3.connect(restored / "db" / "star-isle.db")
    try:
        assert conn.execute("SELECT version FROM schema_version WHERE id=1").fetchone()[0] == 4
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mail_raw'").fetchone()
    finally:
        conn.close()


def test_backup_restore_accepts_v5_active_pending_purged_and_unknown_assets(tmp_path: Path):
    source = tmp_path / "v5-source"
    backup = tmp_path / "v5-backup"
    restored = tmp_path / "v5-restored"
    _fixture_data_root(source)
    _add_v5_lifecycle_asset(
        source,
        asset_id="asset-v5-active",
        storage_class="file",
        content=b"active",
        object_key="active.bin",
    )
    _add_v5_lifecycle_asset(
        source,
        asset_id="asset-v5-pending",
        storage_class="file",
        content=b"pending",
        object_key="pending.bin",
        status="deleted",
    )
    _add_v5_lifecycle_asset(
        source,
        asset_id="asset-v5-purged",
        storage_class="file",
        content=b"purged",
        object_key="purged.bin",
        status="deleted",
        purged=True,
    )
    _add_v5_lifecycle_asset(
        source,
        asset_id="asset-v5-unknown",
        storage_class="unknown",
        content=b"unknown",
        object_key="unknown.bin",
        subdir="originals",
    )

    backup_restore.backup(source, backup, verify_after=True)
    backup_restore.restore(backup, restored)
    backup_restore.verify(backup, restored)
    conn = sqlite3.connect(restored / "db" / "star-isle.db")
    try:
        rows = {
            row[0]: row[1:]
            for row in conn.execute(
                "SELECT id, status, object_purged_at, gc_next_run_at, storage_class FROM asset "
                "WHERE id LIKE 'asset-v5-%' ORDER BY id"
            )
        }
    finally:
        conn.close()
    assert rows["asset-v5-active"] == ("active", None, None, "file")
    assert rows["asset-v5-pending"][0] == "deleted"
    assert rows["asset-v5-pending"][1] is None and rows["asset-v5-pending"][2] is not None
    assert rows["asset-v5-purged"][0] == "deleted"
    assert rows["asset-v5-purged"][1] is not None and rows["asset-v5-purged"][2] is None
    assert rows["asset-v5-unknown"] == ("active", None, None, "unknown")
    assert not (restored / "objects" / "originals" / "purged.bin").exists()


def test_backup_rejects_v5_nonnullable_default_gc_field(tmp_path: Path):
    source = tmp_path / "source"
    backup = tmp_path / "backup"
    _fixture_data_root(source)
    _weaken_v5_nullable_asset_field(source, "object_purged_at")

    conn = sqlite3.connect(source / "db" / "star-isle.db")
    try:
        column = next(row for row in conn.execute("PRAGMA table_info(asset)") if row[1] == "object_purged_at")
        assert column[2].upper() == "TEXT"
        assert column[3] == 1
        assert column[4] == "''"
        conn.row_factory = sqlite3.Row
        with pytest.raises(backup_restore.BackupError, match="asset GC"):
            backup_restore._verify_v5_asset_layout_and_references(conn, source)
    finally:
        conn.close()

    with pytest.raises(backup_restore.BackupError):
        backup_restore.backup(source, backup, verify_after=False)
    assert (backup / backup_restore.INCOMPLETE_MARKER_NAME).is_file()


@pytest.mark.parametrize("mutation", ["tampered", "pending-without-next", "purged-object", "unknown-path-collision"])
def test_backup_rejects_invalid_v5_asset_gc_state_and_paths(tmp_path: Path, mutation: str):
    source = tmp_path / f"v5-invalid-{mutation}"
    backup = tmp_path / f"v5-invalid-{mutation}-backup"
    _fixture_data_root(source)
    if mutation == "tampered":
        _add_v5_lifecycle_asset(
            source,
            asset_id="asset-invalid",
            storage_class="file",
            content=b"tamper-source",
            object_key="tamper.bin",
        )
        conn = sqlite3.connect(source / "db" / "star-isle.db")
        try:
            conn.execute("UPDATE asset SET sha256=? WHERE id='asset-invalid'", ("f" * 64,))
            conn.commit()
        finally:
            conn.close()
    elif mutation == "pending-without-next":
        _add_v5_lifecycle_asset(
            source,
            asset_id="asset-invalid",
            storage_class="file",
            content=b"pending",
            object_key="pending-invalid.bin",
            status="deleted",
        )
        conn = sqlite3.connect(source / "db" / "star-isle.db")
        try:
            conn.execute("UPDATE asset SET gc_next_run_at=NULL WHERE id='asset-invalid'")
            conn.commit()
        finally:
            conn.close()
    elif mutation == "purged-object":
        _add_v5_lifecycle_asset(
            source,
            asset_id="asset-invalid",
            storage_class="file",
            content=b"purged",
            object_key="purged-invalid.bin",
            status="deleted",
            purged=True,
        )
        path = source / "objects" / "originals" / "purged-invalid.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"purged")
    else:
        _add_v5_lifecycle_asset(
            source,
            asset_id="asset-invalid",
            storage_class="unknown",
            content=b"collision",
            object_key="collision.bin",
            subdir="originals",
        )
        collision = source / "objects" / "thumbnails" / "collision.bin"
        collision.parent.mkdir(parents=True, exist_ok=True)
        collision.write_bytes(b"collision")

    with pytest.raises(backup_restore.BackupError):
        backup_restore.backup(source, backup, verify_after=False)
    assert (backup / backup_restore.INCOMPLETE_MARKER_NAME).is_file()


def test_format2_claiming_schema4_is_rejected_before_restore(tmp_path: Path):
    source = tmp_path / "source"
    backup = tmp_path / "backup"
    _fixture_data_root(source)
    backup_restore.backup(source, backup, verify_after=False)
    _write_legacy_format_manifest(backup)

    with pytest.raises(backup_restore.BackupError, match="旧格式"):
        backup_restore.verify(backup)


def test_restore_refuses_nonempty_target(tmp_path: Path):
    source = tmp_path / "source"
    backup = tmp_path / "backup"
    target = tmp_path / "not-empty"
    _fixture_data_root(source)
    backup_restore.backup(source, backup, verify_after=False)
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_text("do not overwrite", encoding="utf-8")

    with pytest.raises(backup_restore.BackupError, match="拒绝覆盖"):
        backup_restore.restore(backup, target)
    assert marker.read_text(encoding="utf-8") == "do not overwrite"


def test_verify_rejects_vector_metadata_tampering_and_missing_table(tmp_path: Path):
    source = tmp_path / "source"
    backup = tmp_path / "backup"
    _fixture_data_root(source)
    backup_restore.backup(source, backup, verify_after=True)

    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["index"]["format"] = "tampered"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(backup_restore.BackupError, match="metadata"):
        backup_restore.verify(backup)

    # 单独构造缺表快照；backup 本身可以保存，verify 必须拒绝它。
    incomplete = tmp_path / "incomplete"
    incomplete_backup = tmp_path / "incomplete-backup"
    _fixture_data_root(incomplete)
    conn = sqlite3.connect(incomplete / "db" / "star-isle.db")
    try:
        conn.execute("DROP TABLE chunk_embedding")
        conn.commit()
    finally:
        conn.close()
    backup_restore.backup(incomplete, incomplete_backup, verify_after=False)
    with pytest.raises(backup_restore.BackupError, match="chunk_embedding"):
        backup_restore.verify(incomplete_backup)

    malformed = tmp_path / "malformed"
    malformed_backup = tmp_path / "malformed-backup"
    _fixture_data_root(malformed)
    conn = sqlite3.connect(malformed / "db" / "star-isle.db")
    try:
        conn.execute("UPDATE vector_ref SET dim=4 WHERE chunk_id='chunk-1'")
        conn.commit()
    finally:
        conn.close()
    backup_restore.backup(malformed, malformed_backup, verify_after=False)
    with pytest.raises(backup_restore.BackupError, match="sqlite-vec"):
        backup_restore.verify(malformed_backup)


def test_backup_allows_migrated_nonready_keyword_legacy_ref_without_blob(tmp_path: Path):
    source = tmp_path / "source"
    backup = tmp_path / "backup"
    _fixture_data_root(source)
    conn = sqlite3.connect(source / "db" / "star-isle.db")
    try:
        conn.execute(
            "INSERT INTO asset (id, original_name, mime_type, size, object_key, sha256, storage_class, status, created_at) "
            "VALUES ('asset-legacy', 'legacy.txt', 'text/plain', 6, 'legacy.bin', ?, 'knowledge', 'active', '2026-08-27T00:00:00+00:00')",
            (hashlib.sha256(b"legacy").hexdigest(),),
        )
        conn.execute(
            "INSERT INTO knowledge_document (id, asset_id, title, document_type, hash, index_state, index_error_code, created_at) "
            "VALUES ('doc-legacy', 'asset-legacy', 'legacy', 'txt', 'h', 'queued', 'INDEX_REBUILD_REQUIRED', '2026-08-27T00:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO chunk (id, document_id, seq, text, token_count, hash) "
            "VALUES ('chunk-legacy', 'doc-legacy', 0, 'legacy', 1, 'h')"
        )
        conn.execute(
            "INSERT INTO vector_ref (chunk_id, store, vector_id, model_id, dim) "
            "VALUES ('chunk-legacy', 'keyword', 'legacy-key', NULL, NULL)"
        )
        conn.commit()
    finally:
        conn.close()
    legacy_object = source / "objects" / "originals" / "legacy.bin"
    legacy_object.write_bytes(b"legacy")

    backup_restore.backup(source, backup, verify_after=True)
    backup_restore.verify(backup)


def test_backup_rejects_ready_sqlite_vec_ref_without_blob_and_ignores_runtime_version_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "source"
    backup = tmp_path / "backup"
    _fixture_data_root(source)
    backup_restore.backup(source, backup, verify_after=False)
    monkeypatch.setattr(backup_restore, "_sqlite_vec_version", lambda: "different-or-unavailable")
    backup_restore.verify(backup)

    missing_blob = tmp_path / "missing-blob"
    missing_blob_backup = tmp_path / "missing-blob-backup"
    _fixture_data_root(missing_blob)
    conn = sqlite3.connect(missing_blob / "db" / "star-isle.db")
    try:
        conn.execute("DELETE FROM chunk_embedding WHERE chunk_id='chunk-1'")
        conn.commit()
    finally:
        conn.close()
    backup_restore.backup(missing_blob, missing_blob_backup, verify_after=False)
    with pytest.raises(backup_restore.BackupError, match="sqlite-vec"):
        backup_restore.verify(missing_blob_backup)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            "INSERT INTO vector_ref (chunk_id, store, vector_id, model_id, dim) VALUES ('orphan-ref', 'sqlite-vec', 'orphan-ref', 'hash-v1', 1)",
            "孤儿",
        ),
        ("UPDATE vector_ref SET store='unknown-store' WHERE chunk_id='chunk-1'", "未知 store"),
    ],
)
def test_backup_rejects_orphan_and_unknown_vector_metadata(tmp_path: Path, mutation: str, message: str):
    source = tmp_path / "source"
    backup = tmp_path / "backup"
    _fixture_data_root(source)
    conn = sqlite3.connect(source / "db" / "star-isle.db")
    try:
        conn.execute(mutation)
        conn.commit()
    finally:
        conn.close()
    backup_restore.backup(source, backup, verify_after=False)
    with pytest.raises(backup_restore.BackupError, match=message):
        backup_restore.verify(backup)


@pytest.mark.parametrize(
    "mutation",
    ["duplicate", "bool-size", "uppercase-digest", "backslash-path", "bad-object-count", "json-list"],
)
def test_manifest_files_are_strict_and_canonical(tmp_path: Path, mutation: str):
    source = tmp_path / "source"
    backup = tmp_path / "backup"
    _fixture_data_root(source)
    backup_restore.backup(source, backup, verify_after=True)
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if mutation == "duplicate":
        manifest["files"].append(dict(manifest["files"][0]))
    elif mutation == "bool-size":
        manifest["files"][0]["size"] = True
    elif mutation == "uppercase-digest":
        manifest["files"][0]["sha256"] = str(manifest["files"][0]["sha256"]).upper()
    elif mutation == "backslash-path":
        for entry in manifest["files"]:
            if entry["path"] == "objects/originals/photo.bin":
                entry["path"] = "objects\\originals\\photo.bin"
                break
    elif mutation == "bad-object-count":
        manifest["objectCount"] = True
    elif mutation == "json-list":
        manifest_path.write_text("[]", encoding="utf-8")
        with pytest.raises(backup_restore.BackupError):
            backup_restore.verify(backup)
        return
    else:
        raise AssertionError(mutation)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(backup_restore.BackupError):
        backup_restore.verify(backup)


@pytest.mark.parametrize("mutation", ["root-extra", "db-sidecar", "nested-object", "extra-object"])
def test_public_verify_recomputes_exact_actual_file_collection(tmp_path: Path, mutation: str):
    source = tmp_path / "source"
    backup = tmp_path / "backup"
    _fixture_data_root(source)
    backup_restore.backup(source, backup, verify_after=True)
    if mutation == "root-extra":
        (backup / "unexpected").mkdir()
    elif mutation == "db-sidecar":
        (backup / "db" / "star-isle.db-journal").write_bytes(b"sidecar")
    elif mutation == "nested-object":
        nested = backup / "objects" / "originals" / "nested"
        nested.mkdir()
        (nested / "object.bin").write_bytes(b"nested")
    elif mutation == "extra-object":
        (backup / "objects" / "originals" / "orphan.bin").write_bytes(b"orphan")
    else:
        raise AssertionError(mutation)
    with pytest.raises(backup_restore.BackupError):
        backup_restore.verify(backup)


@pytest.mark.parametrize("mutation", ["empty-health-directory", "declared-health-file"])
def test_format3_public_verify_rejects_health_directory_and_manifest_entries(tmp_path: Path, mutation: str):
    source = tmp_path / "source"
    backup = tmp_path / "backup"
    _fixture_data_root(source)
    backup_restore.backup(source, backup, verify_after=True)

    health = backup / "objects" / "health"
    health.mkdir()
    if mutation == "declared-health-file":
        health_file = health / "legacy-only.bin"
        health_file.write_bytes(b"legacy-only")
        manifest_path = backup / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"].append(
            {
                "path": "objects/health/legacy-only.bin",
                "size": len(b"legacy-only"),
                "sha256": hashlib.sha256(b"legacy-only").hexdigest(),
            }
        )
        manifest["objectCount"] += 1
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif mutation != "empty-health-directory":
        raise AssertionError(mutation)

    with pytest.raises(backup_restore.BackupError):
        backup_restore.verify(backup)


@pytest.mark.parametrize("reparse_path", ["db", "objects/originals"])
def test_backup_rejects_simulated_reparse_source_paths_before_read_or_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reparse_path: str,
):
    source = tmp_path / "source"
    backup = tmp_path / "backup"
    _fixture_data_root(source)
    flagged = source.joinpath(*reparse_path.split("/"))
    original_is_link_or_reparse = backup_restore._is_link_or_reparse

    def simulated_reparse(path: Path) -> bool:
        if Path(path) == flagged:
            return True
        return original_is_link_or_reparse(Path(path))

    monkeypatch.setattr(backup_restore, "_is_link_or_reparse", simulated_reparse)

    with pytest.raises(backup_restore.BackupError):
        backup_restore.backup(source, backup, verify_after=False)

    if reparse_path == "db":
        assert not backup.exists()
    else:
        assert not (backup / "objects" / "originals" / "photo.bin").exists()


def test_legacy_manifest_health_entry_remains_restorable(tmp_path: Path):
    source = tmp_path / "legacy-source"
    backup = tmp_path / "legacy-backup"
    restored = tmp_path / "legacy-restored"
    _fixture_legacy_v3_data_root(source)
    backup_restore.backup(source, backup, verify_after=False)
    _write_legacy_format_manifest(backup)
    health = backup / "objects" / "health" / "legacy-health.json"
    health.parent.mkdir()
    health.write_bytes(b"legacy health")
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"].append(
        {
            "path": "objects/health/legacy-health.json",
            "size": health.stat().st_size,
            "sha256": hashlib.sha256(health.read_bytes()).hexdigest(),
        }
    )
    manifest["objectCount"] += 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    backup_restore.verify(backup)
    backup_restore.restore(backup, restored)
    backup_restore.verify(backup, restored)
    assert (restored / "objects" / "health" / "legacy-health.json").read_bytes() == b"legacy health"


def test_legacy_empty_object_backup_without_objects_directory_remains_valid(tmp_path: Path):
    source = tmp_path / "legacy-source"
    backup = tmp_path / "legacy-backup"
    restored = tmp_path / "legacy-restored"
    _fixture_legacy_v3_data_root(source)
    backup_restore.backup(source, backup, verify_after=False)
    _write_legacy_format_manifest(backup)
    for directory in (backup / "objects").iterdir():
        directory.rmdir()
    (backup / "objects").rmdir()

    backup_restore.verify(backup)
    backup_restore.restore(backup, restored)
    backup_restore.verify(backup, restored)


def test_restore_accepts_existing_empty_ordinary_directory(tmp_path: Path):
    source = tmp_path / "source"
    backup = tmp_path / "backup"
    target = tmp_path / "target"
    _fixture_data_root(source)
    backup_restore.backup(source, backup, verify_after=True)
    target.mkdir()
    backup_restore.restore(backup, target)
    backup_restore.verify(backup, target)


def test_restore_and_cli_reject_target_or_parent_symlink(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    source = tmp_path / "source"
    backup = tmp_path / "backup"
    target_parent = tmp_path / "safe-parent"
    target_parent.mkdir()
    direct_link = tmp_path / "target-link"
    parent_link = tmp_path / "parent-link"
    _fixture_data_root(source)
    backup_restore.backup(source, backup, verify_after=True)
    try:
        direct_link.symlink_to(target_parent, target_is_directory=True)
        parent_link.symlink_to(target_parent, target_is_directory=True)
    except OSError:
        pytest.skip("当前 Windows 测试账户不允许创建符号链接")

    with pytest.raises(backup_restore.BackupError):
        backup_restore.restore(backup, direct_link)
    with pytest.raises(backup_restore.BackupError):
        backup_restore.restore(backup, parent_link / "child")
    assert not (target_parent / "child").exists()

    assert (
        backup_restore.main(
            ["restore", "--backup", str(backup), "--target", str(direct_link)]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert "备份/恢复失败" in captured.err
    assert not (target_parent / "db" / "star-isle.db").exists()


def test_cli_json_results_are_single_line_sanitized_and_keep_default_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    source = tmp_path / "source-private-canary"
    backup = tmp_path / "backup-private-canary"
    restored = tmp_path / "restore-private-canary"
    _fixture_data_root(source)

    assert backup_restore.main(
        [
            "backup",
            "--data-root",
            str(source),
            "--output",
            str(backup),
            "--verify",
            "--json",
        ]
    ) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    backup_lines = captured.out.splitlines()
    assert len(backup_lines) == 1
    backup_result = json.loads(backup_lines[0])
    assert backup_result == {
        "tool": "starisle-backup-restore",
        "command": "backup",
        "status": "ok",
        "formatVersion": backup_restore.FORMAT_VERSION,
        "schemaVersion": 6,
        "fileCount": 6,
        "objectCount": 5,
    }

    assert backup_restore.main(
        ["restore", "--backup", str(backup), "--target", str(restored), "--json"]
    ) == 0
    captured = capsys.readouterr()
    assert captured.err == "" and len(captured.out.splitlines()) == 1
    restore_result = json.loads(captured.out)
    assert restore_result["command"] == "restore" and restore_result["status"] == "ok"

    assert backup_restore.main(
        ["verify", "--backup", str(backup), "--data-root", str(restored), "--json"]
    ) == 0
    captured = capsys.readouterr()
    assert captured.err == "" and len(captured.out.splitlines()) == 1
    verify_result = json.loads(captured.out)
    assert verify_result["command"] == "verify" and verify_result["status"] == "ok"

    missing = tmp_path / "PRIVATE_CANARY_missing_backup"
    assert backup_restore.main(["verify", "--backup", str(missing), "--json"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "" and len(captured.out.splitlines()) == 1
    failed = json.loads(captured.out)
    assert failed == {
        "tool": "starisle-backup-restore",
        "command": "verify",
        "status": "error",
        "errorCode": "VERIFY_CHECK_FAILED",
    }
    assert str(missing) not in captured.out and "PRIVATE_CANARY" not in captured.out

    default_backup = tmp_path / "default-backup"
    assert backup_restore.main(
        ["backup", "--data-root", str(source), "--output", str(default_backup), "--verify"]
    ) == 0
    default_output = capsys.readouterr()
    assert "备份完成" in default_output.out
    assert default_output.err == ""
