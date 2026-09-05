#!/usr/bin/env python3
"""隔离恢复演练 runner：只接受已完成 backup，永不读取 source DATA_ROOT。"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import sqlite3
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKUP_SCRIPT = REPO_ROOT / "scripts" / "backup_restore.py"
API_ROOT = REPO_ROOT / "apps" / "api"
REPORT_VERSION = 1
TOOL_NAME = "star-isle-recovery-drill"
SCOPE = "isolated-local-candidate"
PROVENANCE = "restored-target"
MAX_QUERY_BYTES = 4096
REPORT_MARKER_SUFFIX = ".star-isle-recovery-drill-incomplete"
REPORT_TEMP_SUFFIX = ".star-isle-recovery-drill.tmp"


SPEC = importlib.util.spec_from_file_location("starisle_recovery_backup_restore", BACKUP_SCRIPT)
assert SPEC and SPEC.loader
backup_restore = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = backup_restore
SPEC.loader.exec_module(backup_restore)


class DrillFailure(RuntimeError):
    """只携带固定 failure code，调用方绝不输出异常细节。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _QuietParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise DrillFailure("ARGUMENT_INVALID")

    def exit(self, _status: int = 0, _message: str | None = None) -> None:
        raise DrillFailure("ARGUMENT_INVALID")


@dataclass(frozen=True)
class DrillPaths:
    backup: Path
    target: Path
    report: Path
    marker: Path
    temporary: Path


@dataclass(frozen=True)
class DrillClaim:
    report: Path
    marker: Path
    temporary: Path


def _one_line_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _base_report(
    status: str,
    failure_code: str | None = None,
    *,
    backup_metadata: dict[str, int] | None = None,
    target_state: str = "not-created",
    counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    metadata = backup_metadata or {}
    statistics = counts or {}
    return {
        "reportVersion": REPORT_VERSION,
        "tool": TOOL_NAME,
        "scope": SCOPE,
        "productionAccepted": False,
        "provenance": PROVENANCE,
        "status": status,
        "failureCode": failure_code,
        "backup": {
            "formatVersion": metadata.get("formatVersion"),
            "schemaVersion": metadata.get("schemaVersion"),
            "objectCount": metadata.get("objectCount"),
        },
        "target": {
            "createdFresh": target_state != "not-created",
            "state": target_state,
        },
        "checks": {
            "database": {"status": "passed" if status == "passed" else "not-run"},
            "objects": {
                "status": "passed" if status == "passed" else "not-run",
                "count": statistics.get("objectCount"),
            },
            "photo": {
                "status": "passed" if status == "passed" else "not-run",
                "count": statistics.get("photoCount"),
            },
            "knowledge": {
                "status": "passed" if status == "passed" else "not-run",
                "readyDocuments": statistics.get("readyDocuments"),
                "searchHits": statistics.get("searchHits"),
            },
            "records": {
                "activeFiles": statistics.get("activeFiles"),
                "mailRecords": statistics.get("mailRecords"),
            },
        },
    }


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise DrillFailure("PATH_UNSAFE") from exc
    if stat.S_ISLNK(metadata.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)


def _path_exists_or_link(path: Path) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise DrillFailure("PATH_UNSAFE") from exc
    return True


def _absolute_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise DrillFailure("PATH_NOT_ABSOLUTE")
    return path


def _safe_path_chain(path: Path, code: str) -> Path:
    lexical = _absolute_path(str(path))
    try:
        chain = (lexical, *lexical.parents)
        for candidate in reversed(chain):
            if _is_link_or_reparse(candidate):
                raise DrillFailure(code)
        return lexical.resolve(strict=False)
    except DrillFailure:
        raise
    except OSError as exc:
        raise DrillFailure(code) from exc


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _data_roots() -> tuple[Path, ...]:
    """返回去重后的 live 数据根；显式根绝不静默跳过。"""

    roots = [REPO_ROOT / "data"]
    for variable in ("DATA_ROOT", "DATA_HOST_ROOT"):
        configured = os.environ.get(variable)
        if configured:
            try:
                candidate = Path(configured).expanduser()
            except (OSError, RuntimeError) as exc:
                raise DrillFailure("DATA_ROOT_UNSAFE") from exc
            roots.append(candidate if candidate.is_absolute() else Path.cwd() / candidate)
    resolved: list[Path] = []
    for root in roots:
        try:
            safe_root = _safe_path_chain(root, "DATA_ROOT_UNSAFE")
        except DrillFailure:
            raise
        if not any(os.path.normcase(str(existing)) == os.path.normcase(str(safe_root)) for existing in resolved):
            resolved.append(safe_root)
    return tuple(resolved)


def _validate_paths(backup_value: str, target_value: str, report_value: str) -> DrillPaths:
    backup = _safe_path_chain(_absolute_path(backup_value), "BACKUP_PATH_UNSAFE")
    for data_root in _data_roots():
        # 备份既不能位于 live DATA_ROOT 中，也不能成为其祖先；否则后续 manifest /
        # SQLite 读取可能重新落到运行时数据。此门禁必须发生在任何 claim 之前。
        if _is_within(backup, data_root) or _is_within(data_root, backup):
            raise DrillFailure("BACKUP_IN_DATA_ROOT")
    target = _safe_path_chain(_absolute_path(target_value), "TARGET_PATH_UNSAFE")
    report = _safe_path_chain(_absolute_path(report_value), "REPORT_PATH_UNSAFE")
    marker = report.with_name(report.name + REPORT_MARKER_SUFFIX)
    temporary = report.with_name(report.name + REPORT_TEMP_SUFFIX)

    if not backup.is_dir() or _is_link_or_reparse(backup):
        raise DrillFailure("BACKUP_PATH_UNSAFE")
    if _path_exists_or_link(target):
        raise DrillFailure("TARGET_NOT_NEW")
    if not target.parent.is_dir() or _is_link_or_reparse(target.parent):
        raise DrillFailure("TARGET_PATH_UNSAFE")
    if not report.parent.is_dir() or _is_link_or_reparse(report.parent):
        raise DrillFailure("REPORT_PATH_UNSAFE")
    if any(_path_exists_or_link(path) for path in (report, marker, temporary)):
        raise DrillFailure("REPORT_CONFLICT")

    for first, second in ((backup, target), (backup, report), (target, report)):
        if _is_within(first, second) or _is_within(second, first):
            raise DrillFailure("PATH_OVERLAP")
    for data_root in _data_roots():
        if _is_within(report, data_root) or _is_within(data_root, report):
            raise DrillFailure("REPORT_IN_DATA_ROOT")
        if _is_within(target, data_root) or _is_within(data_root, target):
            raise DrillFailure("TARGET_IN_DATA_ROOT")
    return DrillPaths(backup=backup, target=target, report=report, marker=marker, temporary=temporary)


def _fsync_directory(directory: Path) -> None:
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


def _write_exclusive(path: Path, payload: bytes, code: str) -> None:
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise DrillFailure("REPORT_CONFLICT") from exc
    except OSError as exc:
        raise DrillFailure(code) from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise DrillFailure(code) from exc


def _claim_report(paths: DrillPaths) -> DrillClaim:
    _write_exclusive(paths.marker, b"incomplete\n", "REPORT_PUBLISH_FAILED")
    _fsync_directory(paths.report.parent)
    return DrillClaim(report=paths.report, marker=paths.marker, temporary=paths.temporary)


def _publish_initial_report(claim: DrillClaim, payload: dict[str, Any]) -> None:
    _write_exclusive(claim.temporary, (_one_line_json(payload) + "\n").encode("ascii"), "REPORT_PUBLISH_FAILED")
    try:
        os.link(claim.temporary, claim.report)
        claim.temporary.unlink()
        _fsync_directory(claim.report.parent)
    except FileExistsError as exc:
        raise DrillFailure("REPORT_CONFLICT") from exc
    except OSError as exc:
        raise DrillFailure("REPORT_PUBLISH_FAILED") from exc


def _publish_report_update(claim: DrillClaim, payload: dict[str, Any]) -> None:
    _write_exclusive(claim.temporary, (_one_line_json(payload) + "\n").encode("ascii"), "REPORT_PUBLISH_FAILED")
    try:
        if _is_link_or_reparse(claim.report) or not claim.report.is_file():
            raise DrillFailure("REPORT_PUBLISH_FAILED")
        os.replace(claim.temporary, claim.report)
        _fsync_directory(claim.report.parent)
    except DrillFailure:
        raise
    except OSError as exc:
        raise DrillFailure("REPORT_PUBLISH_FAILED") from exc


def _release_marker(claim: DrillClaim) -> None:
    try:
        if _is_link_or_reparse(claim.marker) or not claim.marker.is_file():
            raise DrillFailure("MARKER_RELEASE_FAILED")
        claim.marker.unlink()
        _fsync_directory(claim.marker.parent)
    except DrillFailure:
        raise
    except OSError as exc:
        raise DrillFailure("MARKER_RELEASE_FAILED") from exc


def _quiet_call(function: Any, *args: Any, **kwargs: Any) -> Any:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return function(*args, **kwargs)


def _backup_metadata(manifest: dict[str, Any]) -> dict[str, int]:
    return {
        "formatVersion": int(manifest["formatVersion"]),
        "schemaVersion": int(manifest["schemaVersion"]),
        "objectCount": int(manifest["objectCount"]),
    }


def _candidate_manifest(backup: Path) -> dict[str, Any]:
    marker = backup / backup_restore.INCOMPLETE_MARKER_NAME
    temporary = backup / backup_restore.MANIFEST_TEMP_NAME
    database = backup / backup_restore.DB_RELATIVE
    if _path_exists_or_link(marker) or _path_exists_or_link(temporary):
        raise DrillFailure("BACKUP_INCOMPLETE")
    if any(_path_exists_or_link(database.with_name(database.name + suffix)) for suffix in ("-wal", "-shm", "-journal")):
        raise DrillFailure("BACKUP_INVALID")
    health = backup / backup_restore.OBJECTS_RELATIVE / "health"
    if _path_exists_or_link(health):
        raise DrillFailure("BACKUP_CANDIDATE_INVALID")
    try:
        manifest = backup_restore._load_manifest(backup)
    except (backup_restore.BackupError, OSError, ValueError, TypeError) as exc:
        raise DrillFailure("BACKUP_INVALID") from exc
    if (
        manifest.get("formatVersion") != backup_restore.FORMAT_VERSION
        or manifest.get("schemaVersion") != max(backup_restore.SUPPORTED_SCHEMA_VERSIONS)
    ):
        raise DrillFailure("BACKUP_CANDIDATE_INVALID")
    if any(path.startswith("objects/health/") for path in backup_restore._validated_manifest_files(manifest)):
        raise DrillFailure("BACKUP_CANDIDATE_INVALID")
    return manifest


def _open_readonly_database(data_root: Path) -> sqlite3.Connection:
    database = data_root / backup_restore.DB_RELATIVE
    try:
        for directory in (data_root, database.parent):
            if _is_link_or_reparse(directory):
                raise DrillFailure("DATABASE_UNREADABLE")
            if not stat.S_ISDIR(os.lstat(directory).st_mode):
                raise DrillFailure("DATABASE_UNREADABLE")
        if _is_link_or_reparse(database) or not stat.S_ISREG(os.lstat(database).st_mode):
            raise DrillFailure("DATABASE_UNREADABLE")
        connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection
    except DrillFailure:
        raise
    except OSError as exc:
        raise DrillFailure("DATABASE_UNREADABLE") from exc
    except sqlite3.Error as exc:
        raise DrillFailure("DATABASE_UNREADABLE") from exc


def _photo_preflight(data_root: Path) -> int:
    connection = _open_readonly_database(data_root)
    try:
        count = int(connection.execute("SELECT COUNT(*) FROM photo").fetchone()[0])
        if count <= 0:
            raise DrillFailure("PHOTO_REQUIRED")
        broken = connection.execute(
            "SELECT 1 FROM photo p "
            "LEFT JOIN asset a ON a.id=p.asset_id "
            "LEFT JOIN asset t ON t.id=p.thumbnail_asset_id "
            "WHERE a.id IS NULL OR t.id IS NULL "
            "OR a.storage_class <> 'photo-original' "
            "OR t.storage_class <> 'photo-thumbnail' LIMIT 1"
        ).fetchone()
        if broken is not None:
            raise DrillFailure("PHOTO_ASSOCIATION_FAILED")
        return count
    except DrillFailure:
        raise
    except sqlite3.Error as exc:
        raise DrillFailure("PHOTO_ASSOCIATION_FAILED") from exc
    finally:
        connection.close()


def _knowledge_preflight(data_root: Path) -> tuple[int, int]:
    connection = _open_readonly_database(data_root)
    try:
        ready_documents = int(
            connection.execute(
                "SELECT COUNT(*) FROM knowledge_document d "
                "JOIN asset a ON a.id=d.asset_id "
                "WHERE d.index_state='ready' AND d.deleted_at IS NULL AND a.status <> 'deleted'"
            ).fetchone()[0]
        )
        if ready_documents <= 0:
            raise DrillFailure("KNOWLEDGE_REQUIRED")
        broken = connection.execute(
            "SELECT 1 FROM knowledge_document d "
            "LEFT JOIN asset a ON a.id=d.asset_id "
            "WHERE d.index_state='ready' AND d.deleted_at IS NULL AND ("
            "a.id IS NULL OR a.status='deleted' OR "
            "NOT EXISTS (SELECT 1 FROM chunk c WHERE c.document_id=d.id) OR "
            "EXISTS (SELECT 1 FROM chunk c "
            "LEFT JOIN vector_ref v ON v.chunk_id=c.id "
            "LEFT JOIN chunk_embedding e ON e.chunk_id=c.id "
            "WHERE c.document_id=d.id AND ("
            "v.chunk_id IS NULL OR v.store <> 'sqlite-vec' OR v.store IS NULL "
            "OR v.vector_id IS NULL OR length(trim(v.vector_id))=0 "
            "OR v.model_id IS NULL OR length(trim(v.model_id))=0 "
            "OR v.dim IS NULL OR v.dim <= 0 OR e.chunk_id IS NULL "
            "OR typeof(e.embedding) <> 'blob' OR length(e.embedding)=0 "
            "OR (length(e.embedding) % 4) <> 0 OR length(e.embedding) <> v.dim * 4"
            "))"
            ") LIMIT 1"
        ).fetchone()
        if broken is not None:
            raise DrillFailure("KNOWLEDGE_NOT_SEARCHABLE")
        profiles = connection.execute(
            "SELECT DISTINCT v.store, v.model_id, v.dim FROM vector_ref v "
            "JOIN chunk c ON c.id=v.chunk_id "
            "JOIN knowledge_document d ON d.id=c.document_id "
            "JOIN asset a ON a.id=d.asset_id "
            "WHERE d.index_state='ready' AND d.deleted_at IS NULL AND a.status <> 'deleted'"
        ).fetchall()
        if len(profiles) != 1:
            raise DrillFailure("KNOWLEDGE_PROFILE_UNAVAILABLE")
        profile = profiles[0]
        dimension = profile["dim"]
        if (
            profile["store"] != "sqlite-vec"
            or not isinstance(profile["model_id"], str)
            or not profile["model_id"].strip()
            or type(dimension) is not int
            or dimension <= 0
        ):
            raise DrillFailure("KNOWLEDGE_PROFILE_UNAVAILABLE")
        return ready_documents, dimension
    except DrillFailure:
        raise
    except sqlite3.Error as exc:
        raise DrillFailure("KNOWLEDGE_NOT_SEARCHABLE") from exc
    finally:
        connection.close()


_SEARCH_CHILD = r"""
import json
import os
import sqlite3
import sys
from pathlib import Path

def emit(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")

try:
    payload = json.loads(sys.stdin.buffer.read())
    if set(payload) != {"target", "query"} or not isinstance(payload["target"], str) or not isinstance(payload["query"], str):
        raise ValueError
    os.environ["DATA_ROOT"] = payload["target"]
    os.environ["DATA_HOST_ROOT"] = payload["target"]
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    from app.services import rag_engine, vector_store
    database = Path(payload["target"]) / "db" / "star-isle.db"
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        profiles = connection.execute(
            "SELECT DISTINCT v.store, v.model_id, v.dim FROM vector_ref v "
            "JOIN chunk c ON c.id=v.chunk_id "
            "JOIN knowledge_document d ON d.id=c.document_id "
            "JOIN asset a ON a.id=d.asset_id "
            "WHERE d.index_state='ready' AND d.deleted_at IS NULL AND a.status <> 'deleted'"
        ).fetchall()
        if len(profiles) != 1:
            emit({"ok": False, "code": "PROFILE_UNAVAILABLE"})
            raise SystemExit(0)
        stored = profiles[0]
        profile = rag_engine.embedding_profile()
        if (
            stored["store"] != profile.store
            or stored["model_id"] != profile.model_id
            or stored["dim"] != profile.dim
            or not vector_store.VectorStore(connection).has_compatible_vectors(profile)
        ):
            emit({"ok": False, "code": "PROFILE_UNAVAILABLE"})
            raise SystemExit(0)
        result = rag_engine.search(connection, payload["query"])
        if not isinstance(result, list):
            raise ValueError
        emit({"ok": True, "hits": len(result)})
    finally:
        connection.close()
except Exception:
    emit({"ok": False, "code": "SEARCH_FAILED"})
"""


def _run_target_search(target: Path, query: str, dimension: int) -> int:
    environment = os.environ.copy()
    environment.update(
        {
            "DATA_ROOT": str(target),
            "DATA_HOST_ROOT": str(target),
            "PYTHON_DOTENV_DISABLED": "1",
            "EMBEDDING_DIM": str(dimension),
        }
    )
    payload = json.dumps({"target": str(target), "query": query}, ensure_ascii=False).encode("utf-8")
    try:
        completed = subprocess.run(
            [sys.executable, "-B", "-c", _SEARCH_CHILD],
            cwd=API_ROOT,
            env=environment,
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DrillFailure("KNOWLEDGE_CHILD_FAILED") from exc
    if completed.returncode != 0:
        raise DrillFailure("KNOWLEDGE_CHILD_FAILED")
    try:
        lines = completed.stdout.decode("utf-8").splitlines()
        if len(lines) != 1:
            raise ValueError
        result = json.loads(lines[0])
        if not isinstance(result, dict):
            raise ValueError
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise DrillFailure("KNOWLEDGE_CHILD_FAILED") from exc
    if result.get("ok") is True and set(result) == {"ok", "hits"}:
        hits = result.get("hits")
        if type(hits) is not int or hits < 0:
            raise DrillFailure("KNOWLEDGE_CHILD_FAILED")
        if hits == 0:
            raise DrillFailure("KNOWLEDGE_NO_HITS")
        return hits
    if result == {"ok": False, "code": "SEARCH_FAILED"}:
        raise DrillFailure("KNOWLEDGE_SEARCH_FAILED")
    if result == {"ok": False, "code": "PROFILE_UNAVAILABLE"}:
        raise DrillFailure("KNOWLEDGE_PROFILE_UNAVAILABLE")
    raise DrillFailure("KNOWLEDGE_CHILD_FAILED")


def _target_counts(target: Path, manifest: dict[str, Any]) -> dict[str, int]:
    try:
        actual_paths = backup_restore._actual_snapshot_file_paths(
            target,
            format_version=manifest["formatVersion"],
        )
        expected_paths = set(backup_restore._validated_manifest_files(manifest))
        actual_objects = {path for path in actual_paths if path.startswith("objects/")}
        expected_objects = {path for path in expected_paths if path.startswith("objects/")}
        if actual_paths != expected_paths or actual_objects != expected_objects:
            raise DrillFailure("OBJECT_CLOSURE_FAILED")
        if any(path.startswith("objects/health/") for path in actual_objects):
            raise DrillFailure("OBJECT_CLOSURE_FAILED")
        schema_version, logical_objects = backup_restore._logical_objects_from_snapshot(target, target)
        logical_paths = {record.relative.as_posix() for record in logical_objects}
        if schema_version != max(backup_restore.SUPPORTED_SCHEMA_VERSIONS) or logical_paths != actual_objects:
            raise DrillFailure("OBJECT_CLOSURE_FAILED")
        if manifest["objectCount"] != len(actual_objects):
            raise DrillFailure("OBJECT_CLOSURE_FAILED")
    except DrillFailure:
        raise
    except (backup_restore.BackupError, OSError, sqlite3.Error, ValueError, TypeError) as exc:
        raise DrillFailure("OBJECT_CLOSURE_FAILED") from exc

    connection = _open_readonly_database(target)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise DrillFailure("DATABASE_INTEGRITY_FAILED")
        active_files = int(
            connection.execute(
                "SELECT COUNT(*) FROM asset WHERE storage_class='file' AND status='active' "
                "AND object_purged_at IS NULL"
            ).fetchone()[0]
        )
        mail_records = int(connection.execute("SELECT COUNT(*) FROM mail_message").fetchone()[0])
    except DrillFailure:
        raise
    except sqlite3.Error as exc:
        raise DrillFailure("DATABASE_INTEGRITY_FAILED") from exc
    finally:
        connection.close()

    return {
        "objectCount": len(actual_objects),
        "photoCount": _photo_preflight(target),
        "readyDocuments": _knowledge_preflight(target)[0],
        "activeFiles": active_files,
        "mailRecords": mail_records,
    }


def _validate_query(value: object) -> str:
    if not isinstance(value, str):
        raise DrillFailure("QUERY_INVALID")
    try:
        raw = value.encode("utf-8")
    except UnicodeError as exc:
        raise DrillFailure("QUERY_INVALID") from exc
    if not 1 <= len(raw) <= MAX_QUERY_BYTES or not value.strip():
        raise DrillFailure("QUERY_INVALID")
    return value


def _read_query_from_stdin() -> str:
    raw = sys.stdin.buffer.read(MAX_QUERY_BYTES + 1)
    if not 1 <= len(raw) <= MAX_QUERY_BYTES:
        raise DrillFailure("QUERY_INVALID")
    try:
        query = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DrillFailure("QUERY_INVALID") from exc
    return _validate_query(query)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = _QuietParser(add_help=False)
    parser.add_argument("--backup")
    parser.add_argument("--target")
    parser.add_argument("--report")
    parser.add_argument("--knowledge-query-stdin", action="store_true")
    args = parser.parse_args(argv)
    if not args.backup or not args.target or not args.report or not args.knowledge_query_stdin:
        raise DrillFailure("ARGUMENT_INVALID")
    return args


def _finish_failure(
    claim: DrillClaim | None,
    code: str,
    *,
    backup_metadata: dict[str, int] | None,
    target_created: bool,
) -> dict[str, Any]:
    target_state = "partial-or-untrusted" if target_created else "not-created"
    report = _base_report(
        "failed",
        code,
        backup_metadata=backup_metadata,
        target_state=target_state,
    )
    if claim is None:
        return report
    try:
        _publish_report_update(claim, report)
        return report
    except DrillFailure:
        return _base_report(
            "failed",
            "REPORT_PUBLISH_FAILED",
            backup_metadata=backup_metadata,
            target_state=target_state,
        )


def run(backup_value: str, target_value: str, report_value: str, query: str) -> dict[str, Any]:
    claim: DrillClaim | None = None
    backup_metadata: dict[str, int] | None = None
    target_created = False
    try:
        # 公开 run() 与 CLI stdin 必须共享同一 query 门禁，且在任何路径 claim /
        # 文件操作之前拒绝不合法输入。
        query = _validate_query(query)
        paths = _validate_paths(backup_value, target_value, report_value)
        claim = _claim_report(paths)
        _publish_initial_report(claim, _base_report("running"))

        manifest = _candidate_manifest(paths.backup)
        try:
            _quiet_call(backup_restore.verify, paths.backup)
        except (backup_restore.BackupError, OSError, sqlite3.Error, ValueError, TypeError) as exc:
            raise DrillFailure("BACKUP_INVALID") from exc
        backup_metadata = _backup_metadata(manifest)
        _photo_preflight(paths.backup)
        _knowledge_preflight(paths.backup)

        try:
            paths.target.mkdir(exist_ok=False)
        except FileExistsError as exc:
            raise DrillFailure("TARGET_NOT_NEW") from exc
        except OSError as exc:
            raise DrillFailure("TARGET_PATH_UNSAFE") from exc
        target_created = True
        try:
            _quiet_call(backup_restore.restore, paths.backup, paths.target, preserve_partial=True)
        except (backup_restore.BackupError, OSError, sqlite3.Error, ValueError, TypeError) as exc:
            raise DrillFailure("RESTORE_FAILED") from exc
        try:
            _quiet_call(backup_restore.verify, paths.backup, paths.target)
        except (backup_restore.BackupError, OSError, sqlite3.Error, ValueError, TypeError) as exc:
            raise DrillFailure("VERIFY_FAILED") from exc

        counts = _target_counts(paths.target, manifest)
        _, dimension = _knowledge_preflight(paths.target)
        counts["searchHits"] = _run_target_search(paths.target, query, dimension)
        passed = _base_report(
            "passed",
            backup_metadata=backup_metadata,
            target_state="trusted",
            counts=counts,
        )
        _publish_report_update(claim, passed)
        try:
            _release_marker(claim)
        except DrillFailure:
            vetoed = _base_report(
                "failed",
                "MARKER_RELEASE_FAILED",
                backup_metadata=backup_metadata,
                target_state="partial-or-untrusted",
                counts=counts,
            )
            try:
                _publish_report_update(claim, vetoed)
            except DrillFailure:
                pass
            return vetoed
        return passed
    except DrillFailure as exc:
        return _finish_failure(
            claim,
            exc.code,
            backup_metadata=backup_metadata,
            target_created=target_created,
        )
    except Exception:
        return _finish_failure(
            claim,
            "INTERNAL_FAILURE",
            backup_metadata=backup_metadata,
            target_created=target_created,
        )


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parse_args(argv)
        query = _read_query_from_stdin()
        report = run(args.backup, args.target, args.report, query)
    except DrillFailure as exc:
        report = _base_report("failed", exc.code)
    except Exception:
        report = _base_report("failed", "INTERNAL_FAILURE")
    sys.stdout.write(_one_line_json(report) + "\n")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
