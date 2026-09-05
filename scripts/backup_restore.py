#!/usr/bin/env python3
"""StarIsle 本地备份 / 恢复 / 校验。

只使用 Python 标准库。备份仅包含 SQLite 一致性快照和 objects，绝不复制 .env、
模型、日志或既有备份。校验只读检查 SQLite，不加载 sqlite-vec 扩展。
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import sqlite3
import stat
import sys
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as distribution_version
from pathlib import Path, PurePosixPath
from typing import Any


FORMAT_VERSION = 3
LEGACY_FORMAT_VERSION = 2
DB_RELATIVE = Path("db") / "star-isle.db"
OBJECTS_RELATIVE = Path("objects")
SECRET_ENVIRONMENT_VARIABLES = [
    "AUTH_JWT_SIGNING_KEY",
    "ADMIN_BOOTSTRAP_PASSWORD_HASH",
    "MAIL_HMAC_KEY",
]
LEGACY_ROW_COUNT_TABLES = [
    "asset",
    "photo",
    "knowledge_document",
    "chunk",
    "vector_ref",
    "chunk_embedding",
]
ROW_COUNT_TABLES = LEGACY_ROW_COUNT_TABLES + [
    "mail_message",
    "mail_attachment",
    "mail_raw",
]
REQUIRED_VECTOR_TABLES = ["knowledge_document", "chunk", "vector_ref", "chunk_embedding"]
VECTOR_FORMAT = "sqlite-vec-float32-blob-v1"
ASSET_STORAGE_CLASS_TO_SUBDIR = {
    "file": "originals",
    "photo-original": "originals",
    "photo-thumbnail": "thumbnails",
    "knowledge": "originals",
    "mail-attachment": "mail-attachments",
}
GC_STORAGE_CLASSES = frozenset({"file", "photo-original", "photo-thumbnail", "knowledge"})
OBJECT_SUBDIRECTORIES = ("originals", "thumbnails", "mail-attachments", "mail-raw")
# format v3 是当前公开格式：只能包含恢复运行时会使用的四类对象。
# format v2 的历史包可包含已废弃的 health 文件，兼容规则由
# _object_subdirectories_for_format 集中管理。
MANIFEST_OBJECT_SUBDIRECTORIES = frozenset(OBJECT_SUBDIRECTORIES)
LEGACY_MANIFEST_OBJECT_SUBDIRECTORIES = frozenset((*OBJECT_SUBDIRECTORIES, "health"))
SUPPORTED_SCHEMA_VERSIONS = frozenset(range(1, 6))
INCOMPLETE_MARKER_NAME = ".star-isle-backup-incomplete"
MANIFEST_TEMP_NAME = ".star-isle-manifest.tmp"
DEFAULT_LOCK_TIMEOUT_SECONDS = 15.0
MIN_LOCK_TIMEOUT_SECONDS = 0.1
MAX_LOCK_TIMEOUT_SECONDS = 60.0


class BackupError(RuntimeError):
    """可预期的安全 / 一致性失败；调用方只输出脱敏摘要。"""


@dataclass(frozen=True)
class _LogicalObject:
    """由 destination SQLite 快照引用的、唯一的物理对象。"""

    source: Path
    relative: Path
    size: int
    sha256: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _absolute_lexical(value: str) -> Path:
    """保留命令行中的链接语义，供 restore 在 resolve 前拒绝不安全路径。"""
    path = Path(value).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _is_link_or_reparse(path: Path) -> bool:
    """同时识别 POSIX 链接与 Windows 可见的 reparse point / junction。"""
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise BackupError("路径无法安全检查") from exc
    if stat.S_ISLNK(metadata.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)


def _lstat_mode(path: Path, message: str) -> int | None:
    """以 lstat 判断类型，绝不让 Path 的跟随链接语义参与安全判断。"""
    try:
        return os.lstat(path).st_mode
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise BackupError(message) from exc


def _require_safe_directory(path: Path, message: str) -> None:
    if _is_link_or_reparse(path):
        raise BackupError(message)
    mode = _lstat_mode(path, message)
    if mode is None or not stat.S_ISDIR(mode):
        raise BackupError(message)


def _require_safe_regular_file(path: Path, message: str) -> None:
    if _is_link_or_reparse(path):
        raise BackupError(message)
    mode = _lstat_mode(path, message)
    if mode is None or not stat.S_ISREG(mode):
        raise BackupError(message)


def _assert_safe_path_chain(path: Path) -> None:
    lexical = path if path.is_absolute() else Path.cwd() / path
    for candidate in reversed((lexical, *lexical.parents)):
        if _is_link_or_reparse(candidate):
            raise BackupError("路径包含符号链接或重解析点")


def _safe_resolved_path(path: Path) -> Path:
    _assert_safe_path_chain(path)
    try:
        return path.resolve(strict=False)
    except OSError as exc:
        raise BackupError("路径无法安全解析") from exc


def _require_new_or_empty(directory: Path, label: str) -> None:
    _assert_safe_path_chain(directory)
    mode = _lstat_mode(directory, f"{label} 无法安全检查")
    if mode is not None:
        if not stat.S_ISDIR(mode):
            raise BackupError(f"{label} 不是目录")
        try:
            if any(directory.iterdir()):
                raise BackupError(f"{label} 必须不存在或为空，拒绝覆盖")
        except BackupError:
            raise
        except OSError as exc:
            raise BackupError(f"{label} 无法安全检查") from exc
    else:
        directory.mkdir(parents=True, exist_ok=False)
        _assert_safe_path_chain(directory)
    _require_safe_directory(directory, f"{label} 不是安全目录")


def _marker_path(backup_root: Path) -> Path:
    return backup_root / INCOMPLETE_MARKER_NAME


def _marker_exists(backup_root: Path) -> bool:
    marker = _marker_path(backup_root)
    return _lstat_mode(marker, "备份构建状态无法安全检查") is not None


def _fsync_directory(directory: Path) -> None:
    """尽力持久化目录项；Windows 不支持目录 fd 时保持兼容。"""
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _create_incomplete_marker(backup_root: Path) -> None:
    marker = _marker_path(backup_root)
    try:
        descriptor = os.open(
            marker,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
    except FileExistsError as exc:
        raise BackupError("备份目录正在构建或不可重用") from exc
    except OSError as exc:
        raise BackupError("无法声明备份构建状态") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(b"incomplete\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise BackupError("无法声明备份构建状态") from exc
    _fsync_directory(backup_root)


def _prepare_backup_output(output: Path) -> None:
    """只接受新建或真正空的 output，并以独占 marker 锁定它。"""
    _assert_safe_path_chain(output)
    if _is_link_or_reparse(output):
        raise BackupError("备份目录不安全")
    mode = _lstat_mode(output, "无法创建备份目录")
    try:
        if mode is not None:
            if not stat.S_ISDIR(mode):
                raise BackupError("备份目录不是目录")
            if any(output.iterdir()):
                raise BackupError("备份目录必须不存在或为空，拒绝覆盖")
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
            _require_safe_directory(output.parent, "备份目录父路径不安全")
            output.mkdir(parents=False, exist_ok=False)
    except BackupError:
        raise
    except FileExistsError as exc:
        raise BackupError("备份目录创建冲突") from exc
    except OSError as exc:
        raise BackupError("无法创建备份目录") from exc
    _require_safe_directory(output, "备份目录不安全")
    _create_incomplete_marker(output)


def _release_incomplete_marker(backup_root: Path) -> None:
    marker = _marker_path(backup_root)
    try:
        _require_safe_regular_file(marker, "备份构建状态异常")
        marker.unlink()
    except BackupError:
        raise
    except OSError as exc:
        raise BackupError("无法发布完成的备份") from exc
    _fsync_directory(backup_root)


def _validate_lock_timeout(lock_timeout_seconds: float) -> float:
    if not isinstance(lock_timeout_seconds, (int, float)):
        raise BackupError("SQLite 协调锁超时参数无效")
    timeout = float(lock_timeout_seconds)
    if not (MIN_LOCK_TIMEOUT_SECONDS <= timeout <= MAX_LOCK_TIMEOUT_SECONDS):
        raise BackupError("SQLite 协调锁超时参数无效")
    return timeout


def _acquire_source_lock(source: Path, lock_timeout_seconds: float) -> sqlite3.Connection:
    """连接 A 只保留 SQLite RESERVED 锁，绝不用于生成 snapshot。"""
    timeout = _validate_lock_timeout(lock_timeout_seconds)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"file:{source.as_posix()}?mode=rw",
            uri=True,
            timeout=timeout,
        )
        connection.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
        connection.execute("BEGIN IMMEDIATE")
        return connection
    except sqlite3.Error as exc:
        if connection is not None:
            connection.close()
        raise BackupError("无法获取 SQLite 备份协调锁") from exc


def _sha256(path: Path) -> str:
    _require_safe_regular_file(path, "文件无法安全读取")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_relative(path_text: str) -> Path:
    pure = PurePosixPath(path_text)
    if (
        "\\" in path_text
        or pure.is_absolute()
        or ".." in pure.parts
        or not pure.parts
        or path_text != pure.as_posix()
    ):
        raise BackupError("manifest 包含不安全路径")
    return Path(*pure.parts)


def _sqlite_snapshot(source: Path, destination: Path) -> None:
    _require_safe_regular_file(source, "SQLite 源数据库不安全")
    _assert_safe_path_chain(destination.parent)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _require_safe_directory(destination.parent, "SQLite 快照目录不安全")
    if _is_link_or_reparse(destination) or _lstat_mode(destination, "SQLite 快照路径无法安全检查") is not None:
        raise BackupError("SQLite 快照路径冲突")
    # 连接 B 必须独立于持 BEGIN IMMEDIATE 的连接 A；在线 backup API 只在 B 上运行。
    source_conn: sqlite3.Connection | None = None
    destination_conn: sqlite3.Connection | None = None
    try:
        source_conn = sqlite3.connect(
            f"file:{source.as_posix()}?mode=ro",
            uri=True,
            timeout=DEFAULT_LOCK_TIMEOUT_SECONDS,
        )
        destination_conn = sqlite3.connect(destination, timeout=DEFAULT_LOCK_TIMEOUT_SECONDS)
        source_conn.backup(destination_conn)
        # backup API 会复制源库的 WAL journal_mode 标记；将 destination 规范为单文件快照，
        # 不能把 source/destination 的 -wal、-shm 或 journal 作为备份产物留下。
        destination_conn.execute("PRAGMA journal_mode=DELETE")
    except sqlite3.Error as exc:
        raise BackupError("SQLite 在线快照失败") from exc
    finally:
        if destination_conn is not None:
            destination_conn.close()
        if source_conn is not None:
            source_conn.close()


def _require_safe_source_db(data_root: Path) -> Path:
    source = data_root / DB_RELATIVE
    _assert_safe_path_chain(data_root)
    _require_safe_directory(data_root, "找不到可备份的 SQLite 数据库")
    _require_safe_directory(source.parent, "找不到可备份的 SQLite 数据库")
    _require_safe_regular_file(source, "找不到可备份的 SQLite 数据库")
    return source


def _assert_no_snapshot_sidecars(backup_root: Path) -> None:
    database = backup_root / DB_RELATIVE
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = database.with_name(database.name + suffix)
        if _lstat_mode(sidecar, "SQLite 快照 sidecar 无法安全检查") is not None:
            raise BackupError("SQLite 快照包含不允许的 sidecar")


def _safe_object_key(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise BackupError("对象元数据无效")
    posix = PurePosixPath(value)
    if posix.is_absolute() or posix.name != value or ".." in posix.parts:
        raise BackupError("对象元数据无效")
    # PurePosixPath 不识别 Windows 驱动器与反斜杠；在全部平台上一并拒绝。
    if "\\" in value or ":" in value:
        raise BackupError("对象元数据无效")
    return value


def _valid_digest(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        return None
    return value


def _safe_source_object_candidate(data_root: Path, subdir: str, object_key: object) -> Path | None:
    key = _safe_object_key(object_key)
    if subdir not in OBJECT_SUBDIRECTORIES:
        raise BackupError("对象目录无效")
    _require_safe_directory(data_root, "对象存储路径不安全")
    objects_root = data_root / OBJECTS_RELATIVE
    subdirectory = objects_root / subdir
    for directory in (objects_root, subdirectory):
        if _is_link_or_reparse(directory):
            raise BackupError("对象存储路径不安全")
        mode = _lstat_mode(directory, "对象存储路径无法安全检查")
        if mode is not None and not stat.S_ISDIR(mode):
            raise BackupError("对象存储路径不安全")
    candidate = subdirectory / key
    if _is_link_or_reparse(candidate):
        raise BackupError("拒绝复制符号链接")
    mode = _lstat_mode(candidate, "无法读取对象存储")
    if mode is None:
        return None
    if not stat.S_ISREG(mode):
        raise BackupError("对象存储包含不支持的文件类型")
    return candidate


def _verify_object_metadata(path: Path, object_key: object, size: object, digest: object, label: str) -> tuple[int, str]:
    _safe_object_key(object_key)
    valid_digest = _valid_digest(digest)
    if not isinstance(size, int) or size < 0 or valid_digest is None:
        raise BackupError(f"{label} 元数据无效")
    _require_safe_regular_file(path, f"{label} 对象缺失")
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise BackupError(f"{label} 对象无法读取") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != size or _sha256(path) != valid_digest:
        raise BackupError(f"{label} 物理状态不一致")
    return size, valid_digest


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _sqlite_vec_version() -> str:
    """仅读取包元数据；这里绝不 import 或 load SQLite 扩展。"""
    try:
        return distribution_version("sqlite-vec")
    except PackageNotFoundError:
        return "unavailable"


def _readonly_conn(db_path: Path) -> sqlite3.Connection:
    _require_safe_directory(db_path.parent, "恢复目标缺少 SQLite 快照")
    _require_safe_regular_file(db_path, "恢复目标缺少 SQLite 快照")
    return sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)


def _row_count_tables_for_manifest(manifest: dict[str, Any]) -> list[str]:
    format_version = manifest.get("formatVersion")
    schema_version = manifest.get("schemaVersion")
    if type(schema_version) is not int or schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise BackupError("manifest 缺少有效 schema 版本")
    if format_version == LEGACY_FORMAT_VERSION:
        if schema_version >= 4:
            raise BackupError("旧格式不得声明 schema v4 或更高版本")
        return LEGACY_ROW_COUNT_TABLES
    if format_version == FORMAT_VERSION:
        return ROW_COUNT_TABLES
    raise BackupError("manifest 版本或结构不受支持")


def _sqlite_metadata(
    db_path: Path,
    *,
    row_count_tables: list[str] = ROW_COUNT_TABLES,
) -> dict[str, Any]:
    conn = _readonly_conn(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row_counts = {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            if _table_exists(conn, table)
            else 0
            for table in row_count_tables
        }
        schema_version = None
        if _table_exists(conn, "schema_version"):
            row = conn.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
            schema_version = int(row["version"]) if row else None

        profiles: list[dict[str, Any]] = []
        indexed_model_ids: list[str] = []
        if _table_exists(conn, "vector_ref"):
            indexed_model_ids = [
                str(row["model_id"])
                for row in conn.execute(
                    "SELECT DISTINCT model_id FROM vector_ref WHERE model_id IS NOT NULL ORDER BY model_id"
                ).fetchall()
            ]
            if _table_exists(conn, "chunk_embedding"):
                rows = conn.execute(
                    "SELECT v.store, v.model_id, v.dim, COUNT(*) AS vector_ref_count, "
                    "COUNT(e.chunk_id) AS embedding_count "
                    "FROM vector_ref v LEFT JOIN chunk_embedding e ON e.chunk_id=v.chunk_id "
                    "GROUP BY v.store, v.model_id, v.dim "
                    "ORDER BY v.store, v.model_id, v.dim"
                ).fetchall()
                profiles = [
                    {
                        "store": row["store"],
                        "modelId": row["model_id"],
                        "dim": row["dim"],
                        "vectorRefCount": int(row["vector_ref_count"]),
                        "embeddingCount": int(row["embedding_count"]),
                    }
                    for row in rows
                ]

        coverage = {"readyDocuments": 0, "readyChunks": 0, "readyVectorRefs": 0, "readyEmbeddings": 0}
        if _table_exists(conn, "knowledge_document"):
            coverage["readyDocuments"] = int(
                conn.execute(
                    "SELECT COUNT(*) FROM knowledge_document WHERE index_state='ready' AND deleted_at IS NULL"
                ).fetchone()[0]
            )
        if _table_exists(conn, "chunk") and _table_exists(conn, "knowledge_document"):
            coverage["readyChunks"] = int(
                conn.execute(
                    "SELECT COUNT(*) FROM chunk c JOIN knowledge_document d ON d.id=c.document_id "
                    "WHERE d.index_state='ready' AND d.deleted_at IS NULL"
                ).fetchone()[0]
            )
        if _table_exists(conn, "vector_ref") and _table_exists(conn, "chunk") and _table_exists(conn, "knowledge_document"):
            coverage["readyVectorRefs"] = int(
                conn.execute(
                    "SELECT COUNT(*) FROM vector_ref v JOIN chunk c ON c.id=v.chunk_id "
                    "JOIN knowledge_document d ON d.id=c.document_id "
                    "WHERE d.index_state='ready' AND d.deleted_at IS NULL"
                ).fetchone()[0]
            )
        if _table_exists(conn, "chunk_embedding") and _table_exists(conn, "chunk") and _table_exists(conn, "knowledge_document"):
            coverage["readyEmbeddings"] = int(
                conn.execute(
                    "SELECT COUNT(*) FROM chunk_embedding e JOIN chunk c ON c.id=e.chunk_id "
                    "JOIN knowledge_document d ON d.id=c.document_id "
                    "WHERE d.index_state='ready' AND d.deleted_at IS NULL"
                ).fetchone()[0]
            )

        return {
            "schemaVersion": schema_version,
            "rowCounts": row_counts,
            "sqliteVecVersion": _sqlite_vec_version(),
            "index": {
                "format": VECTOR_FORMAT,
                "chunkCount": row_counts["chunk"],
                "vectorRefCount": row_counts["vector_ref"],
                "embeddingCount": row_counts["chunk_embedding"],
                "indexedModelIds": indexed_model_ids,
                "profiles": profiles,
                "coverage": coverage,
            },
            # 仅记录模型标识，不读取 .env，也不记录任何 secret 值。
            "model": {
                "chatModelId": os.environ.get("CHAT_MODEL_ID") or None,
                "embeddingModelId": os.environ.get("EMBEDDING_MODEL_ID") or None,
            },
        }
    finally:
        conn.close()


def _manifest_files(root: Path) -> list[dict[str, Any]]:
    database = root / DB_RELATIVE
    _require_safe_directory(root, "备份根目录不安全")
    _require_safe_directory(database.parent, "SQLite 快照缺失")
    _require_safe_regular_file(database, "SQLite 快照缺失")
    _assert_no_snapshot_sidecars(root)
    files: list[dict[str, Any]] = [
        {
            "path": DB_RELATIVE.as_posix(),
            "size": database.stat().st_size,
            "sha256": _sha256(database),
        }
    ]
    base = root / OBJECTS_RELATIVE
    _require_safe_directory(base, "对象存储快照缺失")
    try:
        subdirectories = sorted(base.iterdir())
    except OSError as exc:
        raise BackupError("对象存储快照无法检查") from exc
    for subdirectory in subdirectories:
        _require_safe_directory(subdirectory, "对象存储包含不安全目录")
        if subdirectory.name not in MANIFEST_OBJECT_SUBDIRECTORIES:
            raise BackupError("对象存储包含不允许的目录")
        try:
            items = sorted(subdirectory.iterdir())
        except OSError as exc:
            raise BackupError("对象存储快照无法检查") from exc
        for item in items:
            _require_safe_regular_file(item, "对象存储包含不支持的文件类型")
            files.append(
                {
                    "path": item.relative_to(root).as_posix(),
                    "size": os.lstat(item).st_size,
                    "sha256": _sha256(item),
                }
            )
    return files


def _write_manifest(backup_root: Path, *, object_count: int) -> dict[str, Any]:
    files = _manifest_files(backup_root)
    if sum(1 for entry in files if entry["path"].startswith("objects/")) != object_count:
        raise BackupError("对象清单与复制结果不一致")
    metadata = _sqlite_metadata(backup_root / DB_RELATIVE)
    manifest = {
        "formatVersion": FORMAT_VERSION,
        "createdAt": _utc_now(),
        "files": files,
        # objectCount 是 destination SQLite 逻辑清单中的唯一物理对象数，不含 orphan / health。
        "objectCount": object_count,
        "secretEnvironmentVariables": SECRET_ENVIRONMENT_VARIABLES,
        **metadata,
    }
    temporary = backup_root / MANIFEST_TEMP_NAME
    manifest_path = backup_root / "manifest.json"
    try:
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
    except FileExistsError as exc:
        raise BackupError("manifest 临时发布状态冲突") from exc
    except OSError as exc:
        raise BackupError("无法写入 manifest") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, manifest_path)
        _fsync_directory(backup_root)
    except OSError as exc:
        raise BackupError("无法原子发布 manifest") from exc
    return manifest


def _load_manifest(backup_root: Path, *, allow_incomplete: bool = False) -> dict[str, Any]:
    _assert_safe_path_chain(backup_root)
    _require_safe_directory(backup_root, "备份根目录不安全")
    if not allow_incomplete and _marker_exists(backup_root):
        raise BackupError("备份尚未完成")
    manifest_path = backup_root / "manifest.json"
    _require_safe_regular_file(manifest_path, "找不到安全的 manifest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError("manifest 无法解析") from exc
    if (
        not isinstance(manifest, dict)
        or type(manifest.get("formatVersion")) is not int
        or manifest.get("formatVersion") not in {LEGACY_FORMAT_VERSION, FORMAT_VERSION}
        or not isinstance(manifest.get("files"), list)
    ):
        raise BackupError("manifest 版本或结构不受支持")
    _row_count_tables_for_manifest(manifest)
    if manifest.get("secretEnvironmentVariables") != SECRET_ENVIRONMENT_VARIABLES:
        raise BackupError("manifest 的 secret 引用清单不符合预期")
    _validated_manifest_files(manifest)
    return manifest


def _object_subdirectories_for_format(format_version: object) -> frozenset[str]:
    if format_version == LEGACY_FORMAT_VERSION:
        return LEGACY_MANIFEST_OBJECT_SUBDIRECTORIES
    if format_version == FORMAT_VERSION:
        return MANIFEST_OBJECT_SUBDIRECTORIES
    raise BackupError("manifest 版本或结构不受支持")


def _validated_manifest_files(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """返回严格、唯一且只含 SQLite / objects 的 manifest 文件项。"""
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise BackupError("manifest 文件项非法")
    validated: dict[str, dict[str, Any]] = {}
    database_count = 0
    object_count = 0
    allowed_object_subdirectories = _object_subdirectories_for_format(manifest.get("formatVersion"))
    for entry in entries:
        if not isinstance(entry, dict):
            raise BackupError("manifest 文件项非法")
        path_text = entry.get("path")
        size = entry.get("size")
        digest = entry.get("sha256")
        if (
            not isinstance(path_text, str)
            or type(size) is not int
            or size < 0
            or _valid_digest(digest) is None
        ):
            raise BackupError("manifest 文件项非法")
        relative = _safe_relative(path_text)
        normalized = relative.as_posix()
        if normalized in validated:
            raise BackupError("manifest 文件路径重复")
        if normalized == DB_RELATIVE.as_posix():
            database_count += 1
        else:
            parts = relative.parts
            if (
                len(parts) != 3
                or parts[0] != OBJECTS_RELATIVE.as_posix()
                or parts[1] not in allowed_object_subdirectories
            ):
                raise BackupError("manifest 包含不允许的文件路径")
            _safe_object_key(parts[2])
            object_count += 1
        validated[normalized] = entry
    if database_count != 1:
        raise BackupError("manifest 必须恰有一个 SQLite 文件")
    declared_object_count = manifest.get("objectCount")
    if type(declared_object_count) is not int or declared_object_count < 0:
        raise BackupError("manifest objectCount 非法")
    if declared_object_count != object_count:
        raise BackupError("manifest objectCount 与文件项不一致")
    return validated


def _actual_snapshot_file_paths(
    data_root: Path,
    *,
    format_version: int = FORMAT_VERSION,
    allow_manifest: bool = False,
    allow_incomplete_marker: bool = False,
    allow_missing_objects_root: bool = False,
) -> set[str]:
    """严格重算恢复目标可见的 SQLite / objects 文件集合。"""
    _assert_safe_path_chain(data_root)
    _require_safe_directory(data_root, "恢复目标根目录无法检查")
    allowed_object_subdirectories = _object_subdirectories_for_format(format_version)
    allowed_root_entries = {DB_RELATIVE.parts[0], OBJECTS_RELATIVE.parts[0]}
    if allow_manifest:
        allowed_root_entries.add("manifest.json")
    if allow_incomplete_marker:
        allowed_root_entries.add(INCOMPLETE_MARKER_NAME)
    try:
        root_entries = list(data_root.iterdir())
    except OSError as exc:
        raise BackupError("恢复目标根目录无法检查") from exc
    for item in root_entries:
        if _is_link_or_reparse(item) or item.name not in allowed_root_entries:
            raise BackupError("恢复目标根目录包含额外文件或目录")
        try:
            mode = os.lstat(item).st_mode
        except OSError as exc:
            raise BackupError("恢复目标根目录无法检查") from exc
        if item.name in {DB_RELATIVE.parts[0], OBJECTS_RELATIVE.parts[0]}:
            if not stat.S_ISDIR(mode):
                raise BackupError("恢复目标根目录类型不安全")
        elif not stat.S_ISREG(mode):
            raise BackupError("恢复目标根目录类型不安全")
    database_directory = data_root / DB_RELATIVE.parent
    database = data_root / DB_RELATIVE
    _require_safe_directory(database_directory, "恢复目标缺少安全 SQLite 快照")
    _require_safe_regular_file(database, "恢复目标缺少安全 SQLite 快照")
    try:
        database_mode = os.lstat(database).st_mode
    except OSError as exc:
        raise BackupError("恢复目标 SQLite 无法检查") from exc
    if not stat.S_ISREG(database_mode):
        raise BackupError("恢复目标 SQLite 类型不安全")
    for item in database_directory.iterdir():
        if _is_link_or_reparse(item) or item != database:
            raise BackupError("恢复目标 SQLite 目录包含额外文件")
        try:
            if not stat.S_ISREG(os.lstat(item).st_mode):
                raise BackupError("恢复目标 SQLite 类型不安全")
        except OSError as exc:
            raise BackupError("恢复目标 SQLite 无法检查") from exc
    _assert_no_snapshot_sidecars(data_root)

    objects_root = data_root / OBJECTS_RELATIVE
    paths = {DB_RELATIVE.as_posix()}
    if _is_link_or_reparse(objects_root):
        raise BackupError("恢复目标对象目录不安全")
    objects_mode = _lstat_mode(objects_root, "恢复目标对象目录无法检查")
    if objects_mode is None:
        if allow_missing_objects_root:
            return paths
        raise BackupError("恢复目标对象目录不安全")
    if not stat.S_ISDIR(objects_mode):
        raise BackupError("恢复目标对象目录不安全")
    try:
        subdirectories = list(objects_root.iterdir())
    except OSError as exc:
        raise BackupError("恢复目标对象目录无法检查") from exc
    for subdirectory in subdirectories:
        _require_safe_directory(subdirectory, "恢复目标对象目录不安全")
        if subdirectory.name not in allowed_object_subdirectories:
            raise BackupError("恢复目标对象目录包含额外目录")
        try:
            items = list(subdirectory.iterdir())
        except OSError as exc:
            raise BackupError("恢复目标对象无法检查") from exc
        for item in items:
            _require_safe_regular_file(item, "恢复目标对象目录不允许嵌套或非常规文件")
            key = _safe_object_key(item.name)
            paths.add((OBJECTS_RELATIVE / subdirectory.name / key).as_posix())
    return paths


def _verify_file_hashes(manifest: dict[str, Any], data_root: Path) -> None:
    for path_text, entry in _validated_manifest_files(manifest).items():
        relative = _safe_relative(path_text)
        path = data_root / relative
        _require_safe_regular_file(path, "备份文件缺失或为符号链接")
        if os.lstat(path).st_size != entry["size"] or _sha256(path) != entry["sha256"]:
            raise BackupError("备份文件校验和不匹配")


def _verify_manifest_metadata(manifest: dict[str, Any], db_path: Path) -> None:
    actual = _sqlite_metadata(
        db_path,
        row_count_tables=_row_count_tables_for_manifest(manifest),
    )
    if not isinstance(manifest.get("sqliteVecVersion"), str) or not manifest["sqliteVecVersion"]:
        raise BackupError("manifest 缺少 sqlite-vec 版本元数据")
    # sqliteVecVersion 是创建备份时的事实，不能因验证主机升级/未安装包而失效。
    for key in ("schemaVersion", "rowCounts", "index"):
        if manifest.get(key) != actual.get(key):
            raise BackupError("SQLite metadata 与 manifest 不一致")


def _require_tables(conn: sqlite3.Connection) -> None:
    for table in REQUIRED_VECTOR_TABLES:
        if not _table_exists(conn, table):
            raise BackupError(f"SQLite 缺少必需向量表：{table}")


def _verify_vector_references(conn: sqlite3.Connection) -> None:
    # 三个显式孤儿方向：ref→chunk、embedding→chunk、embedding→ref。
    checks = [
        (
            "vector_ref 存在孤儿 chunk 引用",
            "SELECT 1 FROM vector_ref v LEFT JOIN chunk c ON c.id=v.chunk_id WHERE c.id IS NULL LIMIT 1",
        ),
        (
            "chunk_embedding 存在孤儿 chunk 引用",
            "SELECT 1 FROM chunk_embedding e LEFT JOIN chunk c ON c.id=e.chunk_id WHERE c.id IS NULL LIMIT 1",
        ),
        (
            "chunk_embedding 缺少对应 vector_ref",
            "SELECT 1 FROM chunk_embedding e LEFT JOIN vector_ref v ON v.chunk_id=e.chunk_id WHERE v.chunk_id IS NULL LIMIT 1",
        ),
    ]
    for message, sql in checks:
        if conn.execute(sql).fetchone() is not None:
            raise BackupError(message)

    unknown_store = conn.execute(
        "SELECT 1 FROM vector_ref WHERE store NOT IN ('sqlite-vec', 'keyword') OR store IS NULL LIMIT 1"
    ).fetchone()
    if unknown_store is not None:
        raise BackupError("vector_ref 使用未知 store")

    bad_sqlite_vec = conn.execute(
        "SELECT 1 FROM vector_ref v LEFT JOIN chunk_embedding e ON e.chunk_id=v.chunk_id "
        "WHERE v.store='sqlite-vec' AND ("
        "e.chunk_id IS NULL OR v.vector_id IS NULL OR length(trim(v.vector_id))=0 "
        "OR v.model_id IS NULL OR length(trim(v.model_id))=0 "
        "OR v.dim IS NULL OR v.dim <= 0 OR typeof(e.embedding) <> 'blob' "
        "OR length(e.embedding) = 0 OR (length(e.embedding) % 4) <> 0 "
        "OR length(e.embedding) <> v.dim * 4) LIMIT 1"
    ).fetchone()
    if bad_sqlite_vec is not None:
        raise BackupError("sqlite-vec ref 缺少完整 embedding/profile")

    bad_blob_pair = conn.execute(
        "SELECT 1 FROM vector_ref v JOIN chunk_embedding e ON e.chunk_id=v.chunk_id "
        "WHERE v.store <> 'sqlite-vec' OR v.store IS NULL LIMIT 1"
    ).fetchone()
    if bad_blob_pair is not None:
        raise BackupError("embedding 必须关联 sqlite-vec ref")

    # 非 ready 文档可保留 v1 legacy keyword ref（无 BLOB）；ready 则必须完全可检索。
    missing_ready = conn.execute(
        "SELECT 1 FROM knowledge_document d "
        "WHERE d.index_state='ready' AND d.deleted_at IS NULL AND ("
        "NOT EXISTS (SELECT 1 FROM chunk c WHERE c.document_id=d.id) OR "
        "EXISTS (SELECT 1 FROM chunk c LEFT JOIN vector_ref v ON v.chunk_id=c.id "
        "LEFT JOIN chunk_embedding e ON e.chunk_id=c.id "
        "WHERE c.document_id=d.id AND ("
        "v.chunk_id IS NULL OR v.store <> 'sqlite-vec' "
        "OR v.vector_id IS NULL OR length(trim(v.vector_id))=0 "
        "OR v.model_id IS NULL OR length(trim(v.model_id))=0 "
        "OR v.dim IS NULL OR v.dim <= 0 OR e.chunk_id IS NULL "
        "OR typeof(e.embedding) <> 'blob' OR length(e.embedding)=0 "
        "OR (length(e.embedding) % 4) <> 0 OR length(e.embedding) <> v.dim * 4"
        "))"
        ") LIMIT 1"
    ).fetchone()
    if missing_ready is not None:
        raise BackupError("ready 文档缺少 chunk、vector_ref 或 embedding")


def _verify_mail_raw_layout_and_references(
    conn: sqlite3.Connection,
    data_root: Path,
    *,
    allow_unreferenced_files: bool = False,
) -> None:
    """v4 私有原文只能以一对一记录和受限目录中的完整对象存在。"""
    if not _table_exists(conn, "mail_raw"):
        raise BackupError("邮件原文归档布局不完整")
    message_column_rows = {
        str(row["name"]): row
        for row in conn.execute("PRAGMA table_info(mail_message)")
    }
    message_columns = {
        name: str(row["type"]).upper()
        for name, row in message_column_rows.items()
    }
    raw_column_rows = {
        str(row["name"]): row
        for row in conn.execute("PRAGMA table_info(mail_raw)")
    }
    raw_columns = {
        name: str(row["type"]).upper()
        for name, row in raw_column_rows.items()
    }
    raw_state_column = message_column_rows.get("raw_state")
    required_not_null = ("object_key", "sha256", "size", "content_type", "created_at")
    message_id = raw_column_rows.get("message_id")
    if (
        message_columns.get("raw_state") != "TEXT"
        or raw_state_column is None
        or int(raw_state_column["notnull"]) != 1
        or str(raw_state_column["dflt_value"] or "").strip("'") != "legacy"
        or raw_columns != {
            "message_id": "TEXT",
            "object_key": "TEXT",
            "sha256": "TEXT",
            "size": "INTEGER",
            "content_type": "TEXT",
            "created_at": "TEXT",
        }
        or message_id is None
        # The approved SQLite TEXT PRIMARY KEY has notnull=0; PK metadata is
        # the stable constraint to require here.
        or int(message_id["pk"]) != 1
        or any(
            int(row["pk"]) != 0
            for name, row in raw_column_rows.items()
            if name != "message_id"
        )
        or any(int(raw_column_rows[name]["notnull"]) != 1 for name in required_not_null)
    ):
        raise BackupError("邮件原文归档布局不完整")

    mail_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='mail_message'"
    ).fetchone()
    raw_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='mail_raw'"
    ).fetchone()
    mail_sql = "".join(str(mail_sql_row["sql"] or "").lower().split()) if mail_sql_row else ""
    raw_sql = "".join(str(raw_sql_row["sql"] or "").lower().split()) if raw_sql_row else ""
    if (
        "check(raw_statein('legacy','stored','removed'))" not in mail_sql
        or "check(length(sha256)=64)" not in raw_sql
        or "check(size>0)" not in raw_sql
        or "check(content_type='message/rfc822')" not in raw_sql
    ):
        raise BackupError("邮件原文归档布局不完整")

    foreign_keys = conn.execute("PRAGMA foreign_key_list(mail_raw)").fetchall()
    if not any(
        str(row["from"]) == "message_id"
        and str(row["table"]) == "mail_message"
        and str(row["to"]) == "id"
        and str(row["on_delete"]).upper() == "RESTRICT"
        for row in foreign_keys
    ):
        raise BackupError("邮件原文归档布局不完整")
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
        raise BackupError("邮件原文归档布局不完整")

    invalid_state = conn.execute(
        "SELECT 1 FROM mail_message WHERE raw_state NOT IN ('legacy', 'stored', 'removed') "
        "OR raw_state IS NULL LIMIT 1"
    ).fetchone()
    stored_without_record = conn.execute(
        "SELECT 1 FROM mail_message m LEFT JOIN mail_raw r ON r.message_id=m.id "
        "WHERE m.raw_state='stored' AND r.message_id IS NULL LIMIT 1"
    ).fetchone()
    nonstored_with_record = conn.execute(
        "SELECT 1 FROM mail_message m JOIN mail_raw r ON r.message_id=m.id "
        "WHERE m.raw_state IN ('legacy', 'removed') LIMIT 1"
    ).fetchone()
    orphan_record = conn.execute(
        "SELECT 1 FROM mail_raw r LEFT JOIN mail_message m ON m.id=r.message_id "
        "WHERE m.id IS NULL LIMIT 1"
    ).fetchone()
    if any((invalid_state, stored_without_record, nonstored_with_record, orphan_record)):
        raise BackupError("邮件原文归档引用不一致")

    raw_dir = data_root / "objects" / "mail-raw"
    expected_keys: set[str] = set()
    for row in conn.execute(
        "SELECT object_key, sha256, size, content_type FROM mail_raw"
    ).fetchall():
        object_key = _safe_object_key(row["object_key"])
        digest = _valid_digest(row["sha256"])
        size = row["size"]
        if (
            row["content_type"] != "message/rfc822"
            or not isinstance(size, int)
            or size <= 0
            or digest is None
        ):
            raise BackupError("邮件原文归档元数据无效")
        path = _safe_source_object_candidate(data_root, "mail-raw", object_key)
        if path is None:
            raise BackupError("邮件原文归档对象缺失")
        if path.stat().st_size != size or _sha256(path) != digest:
            raise BackupError("邮件原文归档对象校验失败")
        expected_keys.add(object_key)

    actual_keys: set[str] = set()
    if _is_link_or_reparse(raw_dir):
        raise BackupError("邮件原文归档目录不安全")
    raw_mode = _lstat_mode(raw_dir, "邮件原文归档目录无法检查")
    if raw_mode is not None:
        if not stat.S_ISDIR(raw_mode):
            raise BackupError("邮件原文归档目录不安全")
        try:
            items = list(raw_dir.iterdir())
        except OSError as exc:
            raise BackupError("邮件原文归档目录无法检查") from exc
        for item in items:
            _require_safe_regular_file(item, "邮件原文归档目录不安全")
            actual_keys.add(item.name)
    # 构建新备份时，live source 可能遗留不受 SQLite 快照引用的普通文件。
    # 它们不应阻止从快照清单复制；destination/verify/restore 仍必须严格一对一。
    if not allow_unreferenced_files and actual_keys != expected_keys:
        raise BackupError("邮件原文归档对象集合不一致")


def _verify_asset_object(row: sqlite3.Row, path: Path) -> None:
    _verify_object_metadata(path, row["object_key"], row["size"], row["sha256"], "asset")


def _mapped_asset_path(data_root: Path, row: sqlite3.Row, subdir: str) -> Path:
    key = _safe_object_key(row["object_key"])
    return data_root / "objects" / subdir / key


def _verify_unknown_asset_object(data_root: Path, row: sqlite3.Row) -> None:
    candidates: list[Path] = []
    for subdir in ("originals", "thumbnails", "mail-attachments"):
        candidate = _safe_source_object_candidate(data_root, subdir, row["object_key"])
        if candidate is not None:
            candidates.append(candidate)
    if len(candidates) != 1:
        raise BackupError("未知 asset 的对象归属不明确")
    _verify_asset_object(row, candidates[0])


def _verify_v5_asset_layout_and_references(conn: sqlite3.Connection, data_root: Path) -> None:
    """v5 以 storage_class 为唯一目录归属，未知历史资产只允许隔离验证。"""
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
        raise BackupError("asset GC 布局不完整")

    table_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='asset'"
    ).fetchone()
    table_sql = "".join(str(table_row["sql"] or "").lower().split()) if table_row else ""
    if (
        "check(storage_classin('unknown','file','photo-original','photo-thumbnail','knowledge','mail-attachment'))"
        not in table_sql
        or "check(gc_attempts>=0)" not in table_sql
    ):
        raise BackupError("asset GC 约束不完整")

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
    expected = ("status", "object_purged_at", "gc_next_run_at", "id")
    if (
        gc_index is None
        or int(gc_index["unique"]) != 0
        or ("partial" in gc_index.keys() and int(gc_index["partial"]) != 0)
        or len(index_info) != len(expected)
        or len(key_columns) != len(expected)
        or any(
            int(row["seqno"]) != position
            or int(row["cid"]) < 0
            or str(row["name"]) != column
            for position, (row, column) in enumerate(zip(index_info, expected, strict=True))
        )
        or any(
            int(row["seqno"]) != position
            or int(row["cid"]) < 0
            or str(row["name"]) != column
            for position, (row, column) in enumerate(zip(key_columns, expected, strict=True))
        )
    ):
        raise BackupError("asset GC 索引不安全")

    for row in conn.execute("SELECT * FROM asset").fetchall():
        storage_class = row["storage_class"]
        status = row["status"]
        purged_at = row["object_purged_at"]
        next_run_at = row["gc_next_run_at"]
        if (
            storage_class not in {"unknown", *ASSET_STORAGE_CLASS_TO_SUBDIR}
            or status not in {"active", "deleted"}
            or not isinstance(row["gc_attempts"], int)
            or row["gc_attempts"] < 0
            or (row["gc_error_code"] is not None and not isinstance(row["gc_error_code"], str))
        ):
            raise BackupError("asset GC 状态不一致")

        referenced_classes = {
            name
            for name, present in (
                (
                    "mail-attachment",
                    conn.execute(
                        "SELECT 1 FROM mail_attachment WHERE asset_id=? LIMIT 1", (row["id"],)
                    ).fetchone()
                    is not None,
                ),
                (
                    "photo-original",
                    conn.execute("SELECT 1 FROM photo WHERE asset_id=? LIMIT 1", (row["id"],)).fetchone()
                    is not None,
                ),
                (
                    "photo-thumbnail",
                    conn.execute(
                        "SELECT 1 FROM photo WHERE thumbnail_asset_id=? LIMIT 1", (row["id"],)
                    ).fetchone()
                    is not None,
                ),
                (
                    "knowledge",
                    conn.execute(
                        "SELECT 1 FROM knowledge_document WHERE asset_id=? LIMIT 1", (row["id"],)
                    ).fetchone()
                    is not None,
                ),
            )
            if present
        }
        if storage_class != "unknown" and referenced_classes != set() and referenced_classes != {storage_class}:
            raise BackupError("asset 引用归属不一致")

        if purged_at is not None:
            if (
                status != "deleted"
                or next_run_at is not None
                or storage_class not in GC_STORAGE_CLASSES
                or referenced_classes & {"mail-attachment", "photo-original", "photo-thumbnail"}
                or conn.execute(
                    "SELECT 1 FROM knowledge_document WHERE asset_id=? AND deleted_at IS NULL LIMIT 1",
                    (row["id"],),
                ).fetchone()
                is not None
            ):
                raise BackupError("asset GC 状态不一致")
            path = _safe_source_object_candidate(
                data_root,
                ASSET_STORAGE_CLASS_TO_SUBDIR[storage_class],
                row["object_key"],
            )
            if path is not None:
                raise BackupError("已清理 asset 仍存在对象")
            continue

        if status == "active":
            if row["deleted_at"] is not None or next_run_at is not None:
                raise BackupError("asset GC 状态不一致")
        else:
            if storage_class in GC_STORAGE_CLASSES:
                if row["deleted_at"] is None or next_run_at is None:
                    raise BackupError("asset GC 状态不一致")
            elif next_run_at is not None:
                raise BackupError("asset GC 状态不一致")

        if storage_class == "unknown":
            _verify_unknown_asset_object(data_root, row)
        else:
            path = _safe_source_object_candidate(
                data_root,
                ASSET_STORAGE_CLASS_TO_SUBDIR[storage_class],
                row["object_key"],
            )
            if path is None:
                raise BackupError("asset 对象缺失")
            _verify_asset_object(row, path)


def _snapshot_schema_version(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "schema_version"):
        raise BackupError("SQLite 快照缺少 schema 版本")
    row = conn.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
    if (
        row is None
        or type(row["version"]) is not int
        or int(row["version"]) not in SUPPORTED_SCHEMA_VERSIONS
    ):
        raise BackupError("SQLite 快照 schema 版本无效")
    return int(row["version"])


def _record_logical_object(
    records: dict[str, _LogicalObject],
    *,
    source: Path,
    subdir: str,
    object_key: object,
    size: object,
    digest: object,
    label: str,
) -> None:
    key = _safe_object_key(object_key)
    validated_size, validated_digest = _verify_object_metadata(source, key, size, digest, label)
    relative = OBJECTS_RELATIVE / subdir / key
    identity = relative.as_posix()
    existing = records.get(identity)
    if existing is not None:
        if existing.size != validated_size or existing.sha256 != validated_digest:
            raise BackupError("同一对象路径的引用元数据不一致")
        return
    records[identity] = _LogicalObject(
        source=source,
        relative=relative,
        size=validated_size,
        sha256=validated_digest,
    )


def _logical_objects_from_snapshot(
    snapshot_root: Path,
    source_data_root: Path,
) -> tuple[int, list[_LogicalObject]]:
    """只从 destination SQLite 快照生成可复制对象，不扫描 live orphan。"""
    connection = _readonly_conn(snapshot_root / DB_RELATIVE)
    connection.row_factory = sqlite3.Row
    records: dict[str, _LogicalObject] = {}
    try:
        schema_version = _snapshot_schema_version(connection)
        if schema_version >= 5:
            rows = connection.execute(
                "SELECT object_key, size, sha256, storage_class FROM asset "
                "WHERE object_purged_at IS NULL"
            ).fetchall()
            for row in rows:
                storage_class = row["storage_class"]
                if storage_class == "unknown":
                    candidates: list[tuple[str, Path]] = []
                    for subdir in ("originals", "thumbnails", "mail-attachments"):
                        candidate = _safe_source_object_candidate(
                            source_data_root,
                            subdir,
                            row["object_key"],
                        )
                        if candidate is not None:
                            candidates.append((subdir, candidate))
                    if len(candidates) != 1:
                        raise BackupError("未知 asset 的对象归属不明确")
                    subdir, source = candidates[0]
                else:
                    subdir = ASSET_STORAGE_CLASS_TO_SUBDIR.get(storage_class)
                    if subdir is None:
                        raise BackupError("asset storage_class 无效")
                    source = _safe_source_object_candidate(source_data_root, subdir, row["object_key"])
                    if source is None:
                        raise BackupError("asset 对象缺失")
                _record_logical_object(
                    records,
                    source=source,
                    subdir=subdir,
                    object_key=row["object_key"],
                    size=row["size"],
                    digest=row["sha256"],
                    label="asset",
                )
        else:
            rows = connection.execute("SELECT object_key, size, sha256 FROM asset").fetchall()
            for row in rows:
                candidates: list[tuple[str, Path]] = []
                for subdir in ("originals", "thumbnails", "mail-attachments"):
                    candidate = _safe_source_object_candidate(source_data_root, subdir, row["object_key"])
                    if candidate is not None:
                        candidates.append((subdir, candidate))
                if len(candidates) != 1:
                    raise BackupError("历史 asset 的对象归属不明确")
                subdir, source = candidates[0]
                _record_logical_object(
                    records,
                    source=source,
                    subdir=subdir,
                    object_key=row["object_key"],
                    size=row["size"],
                    digest=row["sha256"],
                    label="asset",
                )

        if schema_version >= 4:
            rows = connection.execute("SELECT object_key, size, sha256 FROM mail_raw").fetchall()
            for row in rows:
                source = _safe_source_object_candidate(source_data_root, "mail-raw", row["object_key"])
                if source is None:
                    raise BackupError("邮件原文归档对象缺失")
                if not isinstance(row["size"], int) or row["size"] <= 0:
                    raise BackupError("邮件原文归档元数据无效")
                _record_logical_object(
                    records,
                    source=source,
                    subdir="mail-raw",
                    object_key=row["object_key"],
                    size=row["size"],
                    digest=row["sha256"],
                    label="邮件原文归档",
                )
        return schema_version, list(records.values())
    except sqlite3.Error as exc:
        raise BackupError("SQLite 快照对象清单无法生成") from exc
    finally:
        connection.close()


def _copy_logical_objects(
    source_data_root: Path,
    backup_root: Path,
    records: list[_LogicalObject],
) -> None:
    _require_safe_directory(source_data_root, "对象存储路径不安全")
    _require_safe_directory(backup_root, "备份目录不安全")
    objects_root = backup_root / OBJECTS_RELATIVE
    for subdir in OBJECT_SUBDIRECTORIES:
        directory = objects_root / subdir
        _assert_safe_path_chain(directory.parent)
        directory.mkdir(parents=True, exist_ok=True)
        _require_safe_directory(directory, "备份对象目录不安全")
    for record in records:
        subdir = record.relative.parts[1]
        source = _safe_source_object_candidate(source_data_root, subdir, record.relative.name)
        if source is None:
            raise BackupError("对象在复制前缺失")
        _verify_object_metadata(source, record.relative.name, record.size, record.sha256, "对象")
        destination = backup_root / record.relative
        _require_safe_directory(destination.parent, "备份对象目录不安全")
        if _is_link_or_reparse(destination) or _lstat_mode(destination, "备份对象路径无法安全检查") is not None:
            raise BackupError("备份对象路径冲突")
        try:
            shutil.copy2(source, destination)
        except OSError as exc:
            raise BackupError("对象复制失败") from exc
        _verify_object_metadata(destination, record.relative.name, record.size, record.sha256, "对象")


def _validate_snapshot_references(
    snapshot_root: Path,
    object_data_root: Path,
    schema_version: int,
    *,
    allow_unreferenced_mail_raw_files: bool = False,
) -> None:
    """在 manifest 发布前检查 snapshot 所引用的 object / raw / GC 关联。"""
    connection = _readonly_conn(snapshot_root / DB_RELATIVE)
    connection.row_factory = sqlite3.Row
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise BackupError("SQLite integrity_check 失败")
        if schema_version >= 5:
            _verify_v5_asset_layout_and_references(connection, object_data_root)
        if schema_version >= 4:
            _verify_mail_raw_layout_and_references(
                connection,
                object_data_root,
                allow_unreferenced_files=allow_unreferenced_mail_raw_files,
            )
    except sqlite3.Error as exc:
        raise BackupError("SQLite 快照校验失败") from exc
    finally:
        connection.close()


def _verify_sqlite_and_references(manifest: dict[str, Any], data_root: Path) -> None:
    db_path = data_root / DB_RELATIVE
    conn = _readonly_conn(db_path)
    conn.row_factory = sqlite3.Row
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise BackupError("SQLite integrity_check 失败")
        _require_tables(conn)
        _verify_manifest_metadata(manifest, db_path)

        expected_counts = manifest.get("rowCounts", {})
        for table in _row_count_tables_for_manifest(manifest):
            actual = (
                int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                if _table_exists(conn, table)
                else 0
            )
            if actual != expected_counts.get(table):
                raise BackupError("SQLite 行计数与 manifest 不一致")

        if int(manifest["schemaVersion"]) >= 5:
            _verify_v5_asset_layout_and_references(conn, data_root)
        else:
            # v4 及之前没有明确目录归属，只能确认对象位于旧版允许目录之一。
            for row in conn.execute("SELECT object_key FROM asset").fetchall():
                candidates = [
                    _safe_source_object_candidate(data_root, subdir, row["object_key"])
                    for subdir in ("originals", "thumbnails", "mail-attachments")
                ]
                if not any(candidate is not None for candidate in candidates):
                    raise BackupError("asset 引用缺少对象文件")

        photo_count = int(conn.execute("SELECT COUNT(*) FROM photo").fetchone()[0])
        if photo_count:
            photo = conn.execute(
                "SELECT a.object_key AS original_key, t.object_key AS thumbnail_key "
                "FROM photo p JOIN asset a ON a.id=p.asset_id "
                "JOIN asset t ON t.id=p.thumbnail_asset_id LIMIT 1"
            ).fetchone()
            if photo is None:
                raise BackupError("照片原件关联无法验证")
            if _safe_source_object_candidate(data_root, "originals", photo["original_key"]) is None:
                raise BackupError("照片原件关联无法验证")
            if _safe_source_object_candidate(data_root, "thumbnails", photo["thumbnail_key"]) is None:
                raise BackupError("照片缩略图关联无法验证")

        _verify_vector_references(conn)
        if manifest["schemaVersion"] >= 4:
            _verify_mail_raw_layout_and_references(conn, data_root)
    finally:
        conn.close()


def _verify_backup(
    backup_root: Path,
    data_root: Path | None = None,
    *,
    allow_incomplete: bool = False,
) -> None:
    manifest = _load_manifest(backup_root, allow_incomplete=allow_incomplete)
    target = data_root or backup_root
    expected_paths = set(_validated_manifest_files(manifest))
    actual_paths = _actual_snapshot_file_paths(
        target,
        format_version=manifest["formatVersion"],
        allow_manifest=data_root is None,
        allow_incomplete_marker=allow_incomplete and data_root is None,
        allow_missing_objects_root=not any(path.startswith("objects/") for path in expected_paths),
    )
    if expected_paths != actual_paths:
        raise BackupError("恢复目标文件集合与 manifest 不一致")
    actual_object_count = sum(path.startswith("objects/") for path in actual_paths)
    if manifest["objectCount"] != actual_object_count:
        raise BackupError("恢复目标 objectCount 不一致")
    _verify_file_hashes(manifest, target)
    _verify_sqlite_and_references(manifest, target)


def verify(backup_root: Path, data_root: Path | None = None) -> None:
    """校验时仅以 sqlite3 read-only URI 查询；不加载 sqlite-vec 扩展。"""
    _verify_backup(backup_root, data_root)
    print("验证通过：SQLite、向量 BLOB、manifest、对象数量与引用均一致")


def backup(
    data_root: Path,
    output: Path,
    verify_after: bool,
    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> None:
    """创建协调一致的快照；连接 A 锁写者，连接 B 生成只读 online backup。"""
    # 先检查调用方给出的词法链，再解析：若先 resolve，Windows junction / link
    # 的风险会被吞掉，后续无法再确认 source/output 的真实入口。
    data_root = _safe_resolved_path(data_root)
    output = _safe_resolved_path(output)
    source = _require_safe_source_db(data_root)
    if _is_within(output, data_root):
        raise BackupError("备份目录不能位于 DATA_ROOT 内")
    lock_connection = _acquire_source_lock(source, lock_timeout_seconds)
    try:
        _prepare_backup_output(output)
        _sqlite_snapshot(source, output / DB_RELATIVE)
        _assert_no_snapshot_sidecars(output)
        schema_version, records = _logical_objects_from_snapshot(output, data_root)
        # 在锁 A 持有期间先以 snapshot DB 检验 live source，再复制且复验 destination。
        _validate_snapshot_references(
            output,
            data_root,
            schema_version,
            allow_unreferenced_mail_raw_files=True,
        )
        _copy_logical_objects(data_root, output, records)
        _validate_snapshot_references(output, output, schema_version)
        manifest = _write_manifest(output, object_count=len(records))
    finally:
        try:
            lock_connection.rollback()
        except sqlite3.Error:
            pass
        lock_connection.close()

    # 连接 A 已释放；若请求 verify，内部检查允许尚存 marker，外部接口仍拒绝它。
    if verify_after:
        _verify_backup(output, allow_incomplete=True)
    _release_incomplete_marker(output)
    print(f"备份完成：{len(manifest['files'])} 个文件，{manifest['objectCount']} 个对象")


def restore(backup_root: Path, target: Path, *, preserve_partial: bool = False) -> None:
    backup_root = _safe_resolved_path(backup_root)
    target = _safe_resolved_path(target)
    if _is_within(target, backup_root) or _is_within(backup_root, target):
        raise BackupError("恢复目标不能与备份目录重叠")
    manifest = _load_manifest(backup_root)
    verify(backup_root)
    _require_new_or_empty(target, "恢复目标")
    try:
        for entry in manifest["files"]:
            relative = _safe_relative(entry["path"])
            source = backup_root / relative
            _assert_safe_path_chain(source)
            _require_safe_directory(source.parent, "恢复源目录不安全")
            _require_safe_regular_file(source, "恢复源文件不安全")
            destination = target / relative
            _assert_safe_path_chain(destination.parent)
            destination.parent.mkdir(parents=True, exist_ok=True)
            _require_safe_directory(destination.parent, "恢复目标目录不安全")
            if _is_link_or_reparse(destination) or _lstat_mode(destination, "恢复目标路径无法安全检查") is not None:
                raise BackupError("恢复目标文件路径冲突")
            shutil.copy2(source, destination)
            _require_safe_regular_file(destination, "恢复目标文件不安全")
        # 旧 format2 可合法地没有对象文件；恢复后仍建立受限的空目录布局，
        # 让 public verify 能以同一实际文件集合规则检查它。
        for subdirectory in OBJECT_SUBDIRECTORIES:
            directory = target / OBJECTS_RELATIVE / subdirectory
            _assert_safe_path_chain(directory.parent)
            directory.mkdir(parents=True, exist_ok=True)
            _require_safe_directory(directory, "恢复目标对象目录不安全")
        _verify_file_hashes(manifest, target)
        _verify_sqlite_and_references(manifest, target)
    except Exception:
        if not preserve_partial:
            # 目标在进入前为空，仅清理由本次 restore 创建的文件，不覆盖任何既有目录。
            for item in sorted(target.rglob("*"), reverse=True):
                if _is_link_or_reparse(item):
                    item.unlink()
                    continue
                mode = _lstat_mode(item, "恢复目标清理失败")
                if mode is None:
                    continue
                if stat.S_ISREG(mode):
                    item.unlink()
                elif stat.S_ISDIR(mode):
                    item.rmdir()
        raise
    print("恢复完成：已写入隔离目标并完成校验")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="StarIsle 本地备份、恢复与验证")
    commands = parser.add_subparsers(dest="command", required=True)

    backup_parser = commands.add_parser("backup", help="创建一致性 SQLite + objects 备份")
    backup_parser.add_argument("--data-root", required=True, help="运行时 DATA_ROOT")
    backup_parser.add_argument("--output", required=True, help="新建或空的备份目录")
    backup_parser.add_argument("--verify", action="store_true", help="备份后立即校验")
    backup_parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="只输出一行脱敏机器可读结果",
    )
    backup_parser.add_argument(
        "--lock-timeout-seconds",
        type=float,
        default=DEFAULT_LOCK_TIMEOUT_SECONDS,
        help="获取 SQLite 协调锁的超时秒数（0.1 至 60，默认 15）",
    )

    restore_parser = commands.add_parser("restore", help="仅恢复到新建或空的隔离目录")
    restore_parser.add_argument("--backup", required=True, help="备份目录")
    restore_parser.add_argument("--target", required=True, help="新建或空的恢复目标")
    restore_parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="只输出一行脱敏机器可读结果",
    )

    verify_parser = commands.add_parser("verify", help="校验备份或已恢复的数据目录")
    verify_parser.add_argument("--backup", required=True, help="备份目录（含 manifest）")
    verify_parser.add_argument("--data-root", help="可选：要与该备份比对的恢复目标")
    verify_parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="只输出一行脱敏机器可读结果",
    )
    return parser


def _json_summary(command: str, backup_root: Path) -> dict[str, int | str]:
    """只从已通过严格 manifest 规则的元数据提取安全计数。"""
    manifest = _load_manifest(backup_root)
    files = _validated_manifest_files(manifest)
    format_version = manifest.get("formatVersion")
    schema_version = manifest.get("schemaVersion")
    object_count = manifest.get("objectCount")
    if (
        type(format_version) is not int
        or type(schema_version) is not int
        or type(object_count) is not int
        or object_count < 0
    ):
        raise BackupError("manifest 缺少安全 JSON 汇总字段")
    return {
        "tool": "starisle-backup-restore",
        "command": command,
        "status": "ok",
        "formatVersion": format_version,
        "schemaVersion": schema_version,
        "fileCount": len(files),
        "objectCount": object_count,
    }


def _json_failure(command: str, error_code: str) -> dict[str, str]:
    return {
        "tool": "starisle-backup-restore",
        "command": command,
        "status": "error",
        "errorCode": error_code,
    }


def _json_error_code(command: str, error: BaseException) -> str:
    prefix = command.upper()
    return f"{prefix}_CHECK_FAILED" if isinstance(error, BackupError) else f"{prefix}_IO_FAILED"


def _print_json_result(payload: dict[str, int | str]) -> None:
    """JSON CLI 模式唯一的 stdout 写点，固定为一行且不含路径或失败原文。"""
    print(json.dumps(payload, ensure_ascii=True, separators=(",", ":"), allow_nan=False))


def _main_json(args: argparse.Namespace) -> int:
    """隔离既有函数的人类输出，保留其全部备份/恢复安全语义。"""
    command = args.command
    try:
        with redirect_stdout(io.StringIO()):
            if command == "backup":
                backup_root = _absolute_lexical(args.output)
                backup(
                    _absolute_lexical(args.data_root),
                    backup_root,
                    args.verify,
                    args.lock_timeout_seconds,
                )
            elif command == "restore":
                backup_root = _absolute_lexical(args.backup)
                restore(backup_root, _absolute_lexical(args.target))
            elif command == "verify":
                backup_root = _absolute_lexical(args.backup)
                verify(
                    backup_root,
                    _absolute_lexical(args.data_root) if args.data_root else None,
                )
            else:  # pragma: no cover - argparse 已限制 command
                raise BackupError("未知命令")
        _print_json_result(_json_summary(command, backup_root))
        return 0
    except (BackupError, OSError, sqlite3.Error) as exc:
        _print_json_result(_json_failure(command, _json_error_code(command, exc)))
        return 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.json_output:
        return _main_json(args)
    try:
        if args.command == "backup":
            backup(
                _absolute_lexical(args.data_root),
                _absolute_lexical(args.output),
                args.verify,
                args.lock_timeout_seconds,
            )
        elif args.command == "restore":
            restore(_absolute_lexical(args.backup), _absolute_lexical(args.target))
        elif args.command == "verify":
            verify(_absolute_lexical(args.backup), _absolute_lexical(args.data_root) if args.data_root else None)
        return 0
    except BackupError:
        print("备份/恢复失败：一致性或安全检查失败", file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error):
        print("备份/恢复失败：本地文件或 SQLite 操作失败", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
