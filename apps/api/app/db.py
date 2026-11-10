"""SQLite: WAL、明确版本迁移与单用户元数据。"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from .config import settings


SCHEMA_VERSION = 5

# 旧版基础 schema 保持为单独常量，便于真实执行 v1 -> v2 迁移，而不是在
# 启动时把新表静默塞进一个标记为 v1 的数据库。
SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS admin (
  id TEXT PRIMARY KEY,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'admin',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session (
  id TEXT PRIMARY KEY,
  admin_id TEXT NOT NULL,
  refresh_hash TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT,
  user_agent TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_admin ON session(admin_id, expires_at);

CREATE TABLE IF NOT EXISTS csrf (
  id TEXT PRIMARY KEY,
  session_id TEXT,
  token_hash TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_csrf_session ON csrf(session_id);

CREATE TABLE IF NOT EXISTS asset (
  id TEXT PRIMARY KEY,
  original_name TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  size INTEGER NOT NULL,
  object_key TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_asset_status ON asset(status, created_at);

CREATE TABLE IF NOT EXISTS photo (
  id TEXT PRIMARY KEY,
  asset_id TEXT NOT NULL,
  capture_at TEXT,
  thumbnail_asset_id TEXT,
  width INTEGER,
  height INTEGER,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_photo_capture ON photo(capture_at DESC);

CREATE TABLE IF NOT EXISTS mail_message (
  id TEXT PRIMARY KEY,
  message_id TEXT NOT NULL,
  from_addr TEXT NOT NULL,
  to_addr TEXT,
  subject TEXT,
  received_at TEXT NOT NULL,
  text_body TEXT,
  html_body TEXT,
  read_flag INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mail_message_id ON mail_message(message_id);

CREATE TABLE IF NOT EXISTS mail_attachment (
  id TEXT PRIMARY KEY,
  message_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  name TEXT NOT NULL,
  mime_type TEXT,
  size INTEGER
);

CREATE TABLE IF NOT EXISTS mail_nonce (
  nonce TEXT PRIMARY KEY,
  received_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_document (
  id TEXT PRIMARY KEY,
  asset_id TEXT NOT NULL,
  title TEXT NOT NULL,
  document_type TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  hash TEXT NOT NULL,
  index_state TEXT NOT NULL DEFAULT 'queued',
  index_error_code TEXT,
  created_at TEXT NOT NULL,
  deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS chunk (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  text TEXT NOT NULL,
  token_count INTEGER,
  hash TEXT NOT NULL,
  UNIQUE(document_id, seq)
);

CREATE TABLE IF NOT EXISTS vector_ref (
  chunk_id TEXT PRIMARY KEY,
  store TEXT NOT NULL,
  vector_id TEXT NOT NULL,
  model_id TEXT,
  dim INTEGER
);

CREATE TABLE IF NOT EXISTS ingestion_job (
  id TEXT PRIMARY KEY,
  job_type TEXT NOT NULL,
  payload TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  attempts INTEGER NOT NULL DEFAULT 0,
  next_run_at TEXT NOT NULL,
  error_code TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_job_claim ON ingestion_job(status, next_run_at);

CREATE TABLE IF NOT EXISTS conversation (
  id TEXT PRIMARY KEY,
  title TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS message (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  citations TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_event (
  id TEXT PRIMARY KEY,
  actor TEXT,
  action TEXT NOT NULL,
  resource_type TEXT,
  resource_id TEXT,
  request_id TEXT,
  result TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS idempotency (
  key_hash TEXT PRIMARY KEY,
  resource_id TEXT NOT NULL,
  resource_type TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""

MIGRATION_V2 = (
    "CREATE INDEX IF NOT EXISTS idx_chunk_document_id ON chunk(document_id)",
    """
    CREATE TABLE IF NOT EXISTS chunk_embedding (
      chunk_id TEXT PRIMARY KEY REFERENCES chunk(id) ON DELETE CASCADE,
      embedding BLOB NOT NULL CHECK (
        typeof(embedding) = 'blob'
        AND length(embedding) > 0
        AND (length(embedding) % 4) = 0
      )
    )
    """,
)

MIGRATION_V3 = (
    "ALTER TABLE mail_message ADD COLUMN retention_queued_at TEXT",
    "CREATE INDEX IF NOT EXISTS idx_mail_retention ON mail_message(retention_queued_at, received_at)",
    "CREATE INDEX IF NOT EXISTS idx_mail_attachment_message ON mail_attachment(message_id)",
    "CREATE INDEX IF NOT EXISTS idx_mail_attachment_asset ON mail_attachment(asset_id)",
    "CREATE INDEX IF NOT EXISTS idx_mail_nonce_expires ON mail_nonce(expires_at)",
)

MIGRATION_V4 = (
    "ALTER TABLE mail_message ADD COLUMN raw_state TEXT NOT NULL DEFAULT 'legacy' "
    "CHECK(raw_state IN ('legacy', 'stored', 'removed'))",
    """
    CREATE TABLE IF NOT EXISTS mail_raw (
      message_id TEXT PRIMARY KEY REFERENCES mail_message(id) ON DELETE RESTRICT,
      object_key TEXT NOT NULL,
      sha256 TEXT NOT NULL CHECK(length(sha256)=64),
      size INTEGER NOT NULL CHECK(size>0),
      content_type TEXT NOT NULL CHECK(content_type='message/rfc822'),
      created_at TEXT NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_mail_raw_object_key ON mail_raw(object_key)",
)

MIGRATION_V5 = (
    "ALTER TABLE asset ADD COLUMN storage_class TEXT NOT NULL DEFAULT 'unknown' "
    "CHECK(storage_class IN ('unknown', 'file', 'photo-original', 'photo-thumbnail', 'knowledge', 'mail-attachment'))",
    "ALTER TABLE asset ADD COLUMN object_purged_at TEXT",
    "ALTER TABLE asset ADD COLUMN gc_next_run_at TEXT",
    "ALTER TABLE asset ADD COLUMN gc_attempts INTEGER NOT NULL DEFAULT 0 CHECK(gc_attempts>=0)",
    "ALTER TABLE asset ADD COLUMN gc_error_code TEXT",
    "CREATE INDEX IF NOT EXISTS idx_asset_gc_due "
    "ON asset(status, object_purged_at, gc_next_run_at, id)",
)

# 完整 schema 供测试/离线 fixture 重复建库使用。它直接创建带 v3 字段的表，避免
# 对同一数据库重复执行 ALTER TABLE 而触发 duplicate column。
SCHEMA_V3 = SCHEMA_V1.replace(
    "  read_flag INTEGER NOT NULL DEFAULT 0\n);",
    "  read_flag INTEGER NOT NULL DEFAULT 0,\n  retention_queued_at TEXT\n);",
    1,
)

# 保留 v3 完整 schema 供旧备份/迁移 fixture 使用；fresh schema 直接到达 v4，
# 不会在同一建库路径重复执行 raw_state 的 ALTER TABLE。
SCHEMA_V4 = SCHEMA_V3.replace(
    "  retention_queued_at TEXT\n);",
    "  retention_queued_at TEXT,\n"
    "  raw_state TEXT NOT NULL DEFAULT 'legacy' CHECK(raw_state IN ('legacy', 'stored', 'removed'))\n);",
    1,
)

# v5 为普通资产增加唯一的目录归属与可重试物理清理状态；v4 常量仍保留给
# 历史备份和前向迁移 fixture。
SCHEMA_V5 = SCHEMA_V4.replace(
    "  deleted_at TEXT\n);",
    "  deleted_at TEXT,\n"
    "  storage_class TEXT NOT NULL DEFAULT 'unknown' "
    "CHECK(storage_class IN ('unknown', 'file', 'photo-original', 'photo-thumbnail', 'knowledge', 'mail-attachment')),\n"
    "  object_purged_at TEXT,\n"
    "  gc_next_run_at TEXT,\n"
    "  gc_attempts INTEGER NOT NULL DEFAULT 0 CHECK(gc_attempts>=0),\n"
    "  gc_error_code TEXT\n);",
    1,
)

# 测试、离线 fixture 和备份工具可以用完整 v5 schema 建库；运行时仍通过上面的
# 显式迁移路径到达它。
SCHEMA = SCHEMA_V5 + "\n" + ";\n".join(
    MIGRATION_V2 + MIGRATION_V3[1:] + MIGRATION_V4[1:] + MIGRATION_V5[5:]
) + ";\n"


class SchemaVersionError(RuntimeError):
    """数据库版本不受当前程序支持，拒绝在未知 schema 上写入。"""


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_conn(*, immediate: bool = False) -> Iterator[sqlite3.Connection]:
    """打开短连接。``immediate`` 在第一条业务语句前取得写锁，避免延迟事务的快照竞争。"""

    settings.ensure_dirs()
    conn = sqlite3.connect(settings.db_path, check_same_thread=False, timeout=15)
    try:
        from .services.photo_time import normalize_photo_capture_at

        conn.create_function(
            "photo_capture_norm",
            1,
            normalize_photo_capture_at,
            deterministic=True,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA foreign_keys=ON")
        if immediate:
            conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _index_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _column_exists(conn: sqlite3.Connection, table: str, name: str) -> bool:
    return any(row[1] == name for row in conn.execute(f"PRAGMA table_info({table})"))


def _queue_incomplete_ready_documents(conn: sqlite3.Connection) -> None:
    """保留历史索引行，但不让无法按 sqlite-vec 契约检索的文档继续 ready。"""
    conn.execute(
        "UPDATE knowledge_document AS d SET index_state='queued', index_error_code='INDEX_REBUILD_REQUIRED' "
        "WHERE d.index_state='ready' AND d.deleted_at IS NULL AND ("
        "NOT EXISTS (SELECT 1 FROM chunk c WHERE c.document_id=d.id) OR "
        "EXISTS (SELECT 1 FROM chunk c "
        "LEFT JOIN vector_ref v ON v.chunk_id=c.id "
        "LEFT JOIN chunk_embedding e ON e.chunk_id=c.id "
        "WHERE c.document_id=d.id AND ("
        "v.chunk_id IS NULL OR v.store <> 'sqlite-vec' "
        "OR v.vector_id IS NULL OR length(trim(v.vector_id))=0 "
        "OR v.model_id IS NULL OR length(trim(v.model_id))=0 "
        "OR v.dim IS NULL OR v.dim <= 0 "
        "OR e.chunk_id IS NULL OR typeof(e.embedding) <> 'blob' "
        "OR length(e.embedding)=0 OR (length(e.embedding) % 4) <> 0 "
        "OR length(e.embedding) <> v.dim * 4"
        ")"
        ")"
        ")"
    )


def _assert_v2_layout(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "chunk_embedding"):
        raise SchemaVersionError("schema v2 缺少 chunk_embedding，已拒绝启动")
    if not _index_exists(conn, "idx_chunk_document_id"):
        raise SchemaVersionError("schema v2 缺少 idx_chunk_document_id，已拒绝启动")


def _assert_v3_layout(conn: sqlite3.Connection) -> None:
    _assert_v2_layout(conn)
    column_types = {
        str(row[1]): str(row[2]).upper()
        for row in conn.execute("PRAGMA table_info(mail_message)")
    }
    if column_types.get("retention_queued_at") != "TEXT":
        raise SchemaVersionError("schema v3 缺少 mail_message.retention_queued_at，已拒绝启动")
    for index in (
        "idx_mail_retention",
        "idx_mail_attachment_message",
        "idx_mail_attachment_asset",
        "idx_mail_nonce_expires",
    ):
        if not _index_exists(conn, index):
            raise SchemaVersionError(f"schema v3 缺少 {index}，已拒绝启动")


def _assert_v4_layout(conn: sqlite3.Connection) -> None:
    """拒绝 raw_state/mail_raw 不完整或删除策略被削弱的 v4 数据库。"""
    _assert_v3_layout(conn)
    mail_columns = {
        str(row["name"]): row
        for row in conn.execute("PRAGMA table_info(mail_message)")
    }
    raw_state = mail_columns.get("raw_state")
    if (
        raw_state is None
        or str(raw_state["type"]).upper() != "TEXT"
        or int(raw_state["notnull"]) != 1
        or str(raw_state["dflt_value"] or "").strip("'") != "legacy"
    ):
        raise SchemaVersionError("schema v4 缺少受约束的 mail_message.raw_state，已拒绝启动")

    if not _table_exists(conn, "mail_raw"):
        raise SchemaVersionError("schema v4 缺少 mail_raw，已拒绝启动")
    raw_column_rows = {
        str(row["name"]): row
        for row in conn.execute("PRAGMA table_info(mail_raw)")
    }
    raw_columns = {
        name: str(row["type"]).upper()
        for name, row in raw_column_rows.items()
    }
    expected_columns = {
        "message_id": "TEXT",
        "object_key": "TEXT",
        "sha256": "TEXT",
        "size": "INTEGER",
        "content_type": "TEXT",
        "created_at": "TEXT",
    }
    required_not_null = ("object_key", "sha256", "size", "content_type", "created_at")
    message_id = raw_column_rows.get("message_id")
    if (
        raw_columns != expected_columns
        or message_id is None
        # SQLite reports TEXT PRIMARY KEY as notnull=0 for the approved DDL;
        # PK metadata, rather than notnull, pins the one-to-one relationship.
        or int(message_id["pk"]) != 1
        or any(
            int(row["pk"]) != 0
            for name, row in raw_column_rows.items()
            if name != "message_id"
        )
        or any(int(raw_column_rows[name]["notnull"]) != 1 for name in required_not_null)
    ):
        raise SchemaVersionError("schema v4 的 mail_raw 字段不完整，已拒绝启动")

    raw_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='mail_raw'"
    ).fetchone()
    mail_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='mail_message'"
    ).fetchone()
    raw_sql = "".join(str(raw_sql_row["sql"] or "").lower().split()) if raw_sql_row else ""
    mail_sql = "".join(str(mail_sql_row["sql"] or "").lower().split()) if mail_sql_row else ""
    if (
        "check(raw_statein('legacy','stored','removed'))" not in mail_sql
        or "check(length(sha256)=64)" not in raw_sql
        or "check(size>0)" not in raw_sql
        or "check(content_type='message/rfc822')" not in raw_sql
    ):
        raise SchemaVersionError("schema v4 的原文完整性约束缺失，已拒绝启动")

    foreign_keys = conn.execute("PRAGMA foreign_key_list(mail_raw)").fetchall()
    if not any(
        str(row["from"]) == "message_id"
        and str(row["table"]) == "mail_message"
        and str(row["to"]) == "id"
        and str(row["on_delete"]).upper() == "RESTRICT"
        for row in foreign_keys
    ):
        raise SchemaVersionError("schema v4 的 mail_raw 外键删除策略不安全，已拒绝启动")
    raw_index = next(
        (
            row
            for row in conn.execute("PRAGMA index_list(mail_raw)")
            if str(row["name"]) == "idx_mail_raw_object_key"
        ),
        None,
    )
    index_info = conn.execute("PRAGMA index_info(idx_mail_raw_object_key)").fetchall()
    index_key_columns = [
        row
        for row in conn.execute("PRAGMA index_xinfo(idx_mail_raw_object_key)")
        if int(row["key"]) == 1
    ]
    if (
        raw_index is None
        or int(raw_index["unique"]) != 1
        or ("partial" in raw_index.keys() and int(raw_index["partial"]) != 0)
        or len(index_info) != 1
        or int(index_info[0]["seqno"]) != 0
        or int(index_info[0]["cid"]) < 0
        or str(index_info[0]["name"]) != "object_key"
        or len(index_key_columns) != 1
        or int(index_key_columns[0]["seqno"]) != 0
        or int(index_key_columns[0]["cid"]) < 0
        or str(index_key_columns[0]["name"]) != "object_key"
    ):
        raise SchemaVersionError("schema v4 缺少唯一 raw 对象索引，已拒绝启动")


def _assert_v5_layout(conn: sqlite3.Connection) -> None:
    """拒绝削弱资产目录归属、GC 状态或待处理索引的 v5 数据库。"""
    _assert_v4_layout(conn)
    columns = {
        str(row["name"]): row
        for row in conn.execute("PRAGMA table_info(asset)")
    }
    storage_class = columns.get("storage_class")
    object_purged_at = columns.get("object_purged_at")
    gc_next_run_at = columns.get("gc_next_run_at")
    gc_attempts = columns.get("gc_attempts")
    gc_error_code = columns.get("gc_error_code")
    if (
        storage_class is None
        or str(storage_class["type"]).upper() != "TEXT"
        or int(storage_class["notnull"]) != 1
        or str(storage_class["dflt_value"] or "").strip("'") != "unknown"
        or object_purged_at is None
        or str(object_purged_at["type"]).upper() != "TEXT"
        or int(object_purged_at["notnull"]) != 0
        or object_purged_at["dflt_value"] is not None
        or gc_next_run_at is None
        or str(gc_next_run_at["type"]).upper() != "TEXT"
        or int(gc_next_run_at["notnull"]) != 0
        or gc_next_run_at["dflt_value"] is not None
        or gc_attempts is None
        or str(gc_attempts["type"]).upper() != "INTEGER"
        or int(gc_attempts["notnull"]) != 1
        or str(gc_attempts["dflt_value"] or "") != "0"
        or gc_error_code is None
        or str(gc_error_code["type"]).upper() != "TEXT"
        or int(gc_error_code["notnull"]) != 0
        or gc_error_code["dflt_value"] is not None
    ):
        raise SchemaVersionError("schema v5 的 asset GC 字段不完整，已拒绝启动")

    asset_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='asset'"
    ).fetchone()
    asset_sql = "".join(str(asset_sql_row["sql"] or "").lower().split()) if asset_sql_row else ""
    if (
        "check(storage_classin('unknown','file','photo-original','photo-thumbnail','knowledge','mail-attachment'))"
        not in asset_sql
        or "check(gc_attempts>=0)" not in asset_sql
    ):
        raise SchemaVersionError("schema v5 的 asset GC 约束缺失，已拒绝启动")

    gc_index = next(
        (
            row
            for row in conn.execute("PRAGMA index_list(asset)")
            if str(row["name"]) == "idx_asset_gc_due"
        ),
        None,
    )
    index_info = conn.execute("PRAGMA index_info(idx_asset_gc_due)").fetchall()
    key_columns = [
        row
        for row in conn.execute("PRAGMA index_xinfo(idx_asset_gc_due)")
        if int(row["key"]) == 1
    ]
    expected_index_columns = ("status", "object_purged_at", "gc_next_run_at", "id")
    if (
        gc_index is None
        or int(gc_index["unique"]) != 0
        or ("partial" in gc_index.keys() and int(gc_index["partial"]) != 0)
        or len(index_info) != len(expected_index_columns)
        or len(key_columns) != len(expected_index_columns)
        or any(
            int(row["seqno"]) != position
            or int(row["cid"]) < 0
            or str(row["name"]) != column
            for position, (row, column) in enumerate(zip(index_info, expected_index_columns, strict=True))
        )
        or any(
            int(row["seqno"]) != position
            or int(row["cid"]) < 0
            or str(row["name"]) != column
            for position, (row, column) in enumerate(zip(key_columns, expected_index_columns, strict=True))
        )
    ):
        raise SchemaVersionError("schema v5 缺少安全的 asset GC 索引，已拒绝启动")


def _apply_v2_migration(conn: sqlite3.Connection) -> None:
    """将已明确标注为 v1 的库原子推进到 v2。"""
    conn.execute("BEGIN IMMEDIATE")
    try:
        for statement in MIGRATION_V2:
            conn.execute(statement)
        _queue_incomplete_ready_documents(conn)
        conn.execute(
            "UPDATE schema_version SET version=?, applied_at=? WHERE id=1",
            (2, _now()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _apply_v3_migration(conn: sqlite3.Connection) -> None:
    """将已明确标注为 v2 的库原子推进到 v3。"""
    _assert_v2_layout(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not _column_exists(conn, "mail_message", "retention_queued_at"):
            conn.execute(MIGRATION_V3[0])
        for statement in MIGRATION_V3[1:]:
            conn.execute(statement)
        conn.execute(
            "UPDATE schema_version SET version=?, applied_at=? WHERE id=1",
            (3, _now()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _apply_v4_migration(conn: sqlite3.Connection) -> None:
    """将 v3 邮件显式标记为 legacy，并建立一对一原文归档表。"""
    _assert_v3_layout(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not _column_exists(conn, "mail_message", "raw_state"):
            conn.execute(MIGRATION_V4[0])
        for statement in MIGRATION_V4[1:]:
            conn.execute(statement)
        conn.execute(
            "UPDATE schema_version SET version=?, applied_at=? WHERE id=1",
            (4, _now()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _classify_v4_assets(conn: sqlite3.Connection) -> None:
    """只按现有关系保守归类，绝不依赖文件名、MIME 或对象目录猜测。"""
    conn.execute(
        """
        UPDATE asset AS a
        SET storage_class = CASE
          WHEN (
            (EXISTS (SELECT 1 FROM mail_attachment ma WHERE ma.asset_id=a.id)) +
            (EXISTS (SELECT 1 FROM photo p WHERE p.asset_id=a.id)) +
            (EXISTS (SELECT 1 FROM photo p WHERE p.thumbnail_asset_id=a.id)) +
            (EXISTS (SELECT 1 FROM knowledge_document kd WHERE kd.asset_id=a.id))
          ) > 1 THEN 'unknown'
          WHEN EXISTS (SELECT 1 FROM mail_attachment ma WHERE ma.asset_id=a.id) THEN 'mail-attachment'
          WHEN EXISTS (SELECT 1 FROM photo p WHERE p.asset_id=a.id) THEN 'photo-original'
          WHEN EXISTS (SELECT 1 FROM photo p WHERE p.thumbnail_asset_id=a.id) THEN 'photo-thumbnail'
          WHEN EXISTS (SELECT 1 FROM knowledge_document kd WHERE kd.asset_id=a.id) THEN 'knowledge'
          WHEN a.status='active' THEN 'file'
          ELSE 'unknown'
        END
        """
    )
    conn.execute(
        """
        UPDATE asset AS a
        SET gc_next_run_at=deleted_at, gc_attempts=0, gc_error_code=NULL
        WHERE a.status='deleted'
          AND a.storage_class IN ('file', 'photo-original', 'photo-thumbnail', 'knowledge')
          AND a.deleted_at IS NOT NULL
          AND a.object_purged_at IS NULL
          AND a.gc_next_run_at IS NULL
          AND NOT EXISTS (SELECT 1 FROM photo p WHERE p.asset_id=a.id OR p.thumbnail_asset_id=a.id)
          AND NOT EXISTS (SELECT 1 FROM mail_attachment ma WHERE ma.asset_id=a.id)
          AND NOT EXISTS (
            SELECT 1 FROM knowledge_document kd
            WHERE kd.asset_id=a.id AND kd.deleted_at IS NULL
          )
        """
    )


def _apply_v5_migration(conn: sqlite3.Connection) -> None:
    """在 v4 资产上补齐归属与 GC 状态，并只排队可证明的历史 tombstone。"""
    _assert_v4_layout(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        for statement, column in zip(
            MIGRATION_V5[:5],
            ("storage_class", "object_purged_at", "gc_next_run_at", "gc_attempts", "gc_error_code"),
            strict=True,
        ):
            if not _column_exists(conn, "asset", column):
                conn.execute(statement)
        conn.execute(MIGRATION_V5[5])
        _classify_v4_assets(conn)
        conn.execute(
            "UPDATE schema_version SET version=?, applied_at=? WHERE id=1",
            (5, _now()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db() -> None:
    """初始化基础表并只允许已知的前向 schema 迁移。"""
    settings.ensure_dirs()
    with get_conn() as conn:
        has_version_table = _table_exists(conn, "schema_version")
        if has_version_table:
            row = conn.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
            if row is None:
                raise SchemaVersionError("schema_version 缺少当前版本记录")
            current = int(row["version"])
            if current > SCHEMA_VERSION:
                raise SchemaVersionError("数据库 schema 版本高于当前程序，已拒绝启动")
            # 只补齐 v1 的历史基础对象；绝不借此跳过 v2 migration 记录。
            conn.executescript(SCHEMA_V1)
            conn.commit()
        else:
            # 新库不留下 v1/v2/v3/v4 的中间版本记录；所有对象在本次初始化中直接到达 v5。
            conn.executescript(SCHEMA_V1)
            for statement in MIGRATION_V2:
                conn.execute(statement)
            if not _column_exists(conn, "mail_message", "retention_queued_at"):
                conn.execute(MIGRATION_V3[0])
            for statement in MIGRATION_V3[1:]:
                conn.execute(statement)
            if not _column_exists(conn, "mail_message", "raw_state"):
                conn.execute(MIGRATION_V4[0])
            for statement in MIGRATION_V4[1:]:
                conn.execute(statement)
            for statement in MIGRATION_V5:
                conn.execute(statement)
            conn.execute(
                "CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO schema_version (id, version, applied_at) VALUES (1, ?, ?)",
                (SCHEMA_VERSION, _now()),
            )
            conn.commit()
            current = SCHEMA_VERSION

        while current < SCHEMA_VERSION:
            if current == 1:
                _apply_v2_migration(conn)
                current = 2
            elif current == 2:
                _apply_v3_migration(conn)
                current = 3
            elif current == 3:
                _apply_v4_migration(conn)
                current = 4
            elif current == 4:
                _apply_v5_migration(conn)
                current = 5
            else:  # pragma: no cover - SCHEMA_VERSION 变化时的保护栏
                raise SchemaVersionError("缺少已知的 schema 前向迁移")
        _ensure_visitor_letter_table(conn)
        _ensure_desk_note_tables(conn)
        _ensure_desk_index_tables(conn)
        conn.commit()
        _assert_v5_layout(conn)


def _ensure_visitor_letter_table(conn: sqlite3.Connection) -> None:
    """来信附在同一份库文件里，但不提升 schema 版本。

    备份契约仍是 schema v5。整库在线快照会带上这张表；版本号保持不变，
    避免把访客留言卷进资产、邮件和恢复演练的版本门。
    """

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS visitor_letter (
          id TEXT PRIMARY KEY,
          received_at TEXT NOT NULL,
          imported_at TEXT NOT NULL,
          name TEXT NOT NULL,
          contact TEXT NOT NULL,
          body TEXT NOT NULL,
          read_at TEXT,
          archived_at TEXT,
          deleted_at TEXT,
          cloud_ack_at TEXT
        )
        """
    )


def _ensure_desk_note_tables(conn: sqlite3.Connection) -> None:
    """手记和日记附在同一份库文件里，但不提升 schema 版本。

    整库在线备份会带上这些表。``updated_at`` 和 ``content_hash`` 留给下一阶段按篇增量索引。
    全文检索不用 FTS5 trigram：那个分词器至少要三个字，两个字的中文会落空。这里把正文切成
    连续的二字和三字，再交给 FTS5 的 unicode61 索引。
    """

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS desk_note (
          id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          body_md TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          diary_on TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          deleted_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_desk_note_diary
        ON desk_note(diary_on)
        WHERE diary_on IS NOT NULL AND deleted_at IS NULL
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_desk_note_updated ON desk_note(updated_at, id)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS desk_note_tag (
          note_id TEXT NOT NULL,
          tag TEXT NOT NULL,
          PRIMARY KEY (note_id, tag)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS desk_note_photo (
          note_id TEXT NOT NULL,
          photo_id TEXT NOT NULL,
          PRIMARY KEY (note_id, photo_id)
        )
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS desk_note_fts USING fts5(
          note_id UNINDEXED,
          grams,
          tokenize = "unicode61 remove_diacritics 0"
        )
        """
    )


def _ensure_desk_index_tables(conn: sqlite3.Connection) -> None:
    """问屿的索引附在同一份库里，不提升 schema 版本。

    整库在线备份会带上这些表。向量也可以按原文重新算，所以嵌入服务恢复后不必整库重建。
    """

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS desk_index_item (
          source_kind TEXT NOT NULL,
          source_id TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          source_updated_at TEXT NOT NULL,
          indexed_at TEXT,
          status TEXT NOT NULL,
          title TEXT NOT NULL,
          PRIMARY KEY (source_kind, source_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS desk_index_chunk (
          id TEXT PRIMARY KEY,
          source_kind TEXT NOT NULL,
          source_id TEXT NOT NULL,
          seq INTEGER NOT NULL,
          text TEXT NOT NULL,
          embedding BLOB NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_desk_index_chunk_source
        ON desk_index_chunk(source_kind, source_id, seq)
        """
    )


def audit(action: str, *, actor: str | None = None, resource_type: str | None = None,
          resource_id: str | None = None, request_id: str | None = None,
          result: str | None = None, conn: sqlite3.Connection | None = None) -> None:
    """写入审计事件。事务内调用时须复用 conn，避免开新连接造成写锁死锁。"""
    import secrets

    if conn is not None:
        conn.execute(
            "INSERT INTO audit_event (id, actor, action, resource_type, resource_id, request_id, result, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (secrets.token_hex(8), actor, action, resource_type, resource_id, request_id, result, _now()),
        )
        return
    with get_conn() as c:
        c.execute(
            "INSERT INTO audit_event (id, actor, action, resource_type, resource_id, request_id, result, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (secrets.token_hex(8), actor, action, resource_type, resource_id, request_id, result, _now()),
        )
