"""普通资产的目录归属、软删排队与可重试物理清理。

邮件附件和原始 MIME 有独立生命周期；本服务永不接管它们，也不扫描未知孤儿。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from ..db import audit, get_conn
from ..observability import emit_asset_gc_result
from ..security import iso, now_utc
from ..storage import storage


ASSET_STORAGE_CLASS_TO_SUBDIR = {
    "file": "originals",
    "photo-original": "originals",
    "photo-thumbnail": "thumbnails",
    "knowledge": "originals",
    "mail-attachment": "mail-attachments",
}
ASSET_STORAGE_CLASSES = frozenset({"unknown", *ASSET_STORAGE_CLASS_TO_SUBDIR})
GC_STORAGE_CLASSES = frozenset({"file", "photo-original", "photo-thumbnail", "knowledge"})
MAX_BATCH_SIZE = 500


@dataclass
class AssetGcResult:
    """只暴露聚合计数，绝不携带对象键、文件名、摘要或异常详情。"""

    processed: int = 0
    purged_assets: int = 0
    missing_objects: int = 0
    deferred: int = 0
    restored_shared: int = 0
    skipped: int = 0

    def audit_result(self) -> str:
        """审计只携带固定聚合计数，不含 asset 或对象标识。"""
        return (
            f"processed={self.processed};purged={self.purged_assets};"
            f"missing={self.missing_objects};deferred={self.deferred};"
            f"shared={self.restored_shared};skipped={self.skipped}"
        )


def has_live_asset_reference(conn, asset_id: str) -> bool:
    """照片、邮件附件和未软删知识库文档都阻止通用对象清理。"""
    return conn.execute(
        """
        SELECT 1
        WHERE EXISTS (SELECT 1 FROM photo WHERE asset_id=? OR thumbnail_asset_id=?)
           OR EXISTS (SELECT 1 FROM mail_attachment WHERE asset_id=?)
           OR EXISTS (
               SELECT 1 FROM knowledge_document
               WHERE asset_id=? AND deleted_at IS NULL
           )
        """,
        (asset_id, asset_id, asset_id, asset_id),
    ).fetchone() is not None


def queue_deleted_asset(conn, asset_id: str, *, now: datetime | None = None) -> bool:
    """在调用方事务内，仅无 live 引用的通用 GC 类资产才能进入队列。"""
    current = now or now_utc()
    timestamp = iso(current)
    row = conn.execute(
        "SELECT id, status, storage_class, deleted_at, object_purged_at FROM asset WHERE id=?",
        (asset_id,),
    ).fetchone()
    if row is None:
        return False

    storage_class = str(row["storage_class"])
    # unknown 与 mail-attachment 有独立/未知生命周期；通用 GC 不能改变它们。
    if storage_class not in GC_STORAGE_CLASSES:
        return False
    if has_live_asset_reference(conn, asset_id):
        return False
    if row["object_purged_at"] is not None:
        return True
    conn.execute(
        """
        UPDATE asset
        SET status='deleted', deleted_at=COALESCE(deleted_at, ?),
            object_purged_at=NULL, gc_next_run_at=?, gc_attempts=0, gc_error_code=NULL
        WHERE id=?
        """,
        (timestamp, timestamp, asset_id),
    )
    return True


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _retry_at(now: datetime, attempts: int) -> str:
    seconds = min(60 * (2 ** min(max(attempts - 1, 0), 6)), 60 * 60)
    return iso(now + timedelta(seconds=seconds))


def _defer_locked(conn, row, now: datetime, error_code: str) -> None:
    attempts = max(int(row["gc_attempts"]), 0) + 1
    conn.execute(
        """
        UPDATE asset
        SET status='deleted', object_purged_at=NULL, gc_next_run_at=?,
            gc_attempts=?, gc_error_code=?
        WHERE id=? AND object_purged_at IS NULL
        """,
        (_retry_at(now, attempts), attempts, error_code, row["id"]),
    )


def _is_due_gc_candidate(row, due_at: str) -> bool:
    """同一写锁内确认候选仍属于通用 GC 且尚未被后移。"""
    return (
        row is not None
        and row["status"] == "deleted"
        and row["object_purged_at"] is None
        and str(row["storage_class"]) in GC_STORAGE_CLASSES
        and isinstance(row["gc_next_run_at"], str)
        and row["gc_next_run_at"] <= due_at
    )


def _restore_shared_locked(conn, asset_id: str) -> None:
    conn.execute(
        """
        UPDATE asset
        SET status='active', deleted_at=NULL, object_purged_at=NULL,
            gc_next_run_at=NULL, gc_error_code=NULL
        WHERE id=?
        """,
        (asset_id,),
    )


def _mark_purged_locked(conn, asset_id: str, now: datetime) -> None:
    conn.execute(
        """
        UPDATE asset
        SET status='deleted', object_purged_at=?, gc_next_run_at=NULL, gc_error_code=NULL
        WHERE id=?
        """,
        (iso(now), asset_id),
    )


def _defer_after_unexpected_failure(asset_id: str, now: datetime) -> str:
    """提交失败后保留 pending；若对象已删，下轮缺失对象分支会收敛。"""
    try:
        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM asset WHERE id=?", (asset_id,)).fetchone()
            if not _is_due_gc_candidate(row, iso(now)):
                return "skipped"
            if has_live_asset_reference(conn, asset_id):
                _restore_shared_locked(conn, asset_id)
                return "shared"
            _defer_locked(conn, row, now, "GC_DB_FAILURE")
            return "deferred"
    except Exception:
        # 无法安全记录重试时保留原状态；不输出异常内容或任何对象标识。
        return "deferred"


def _process_asset(asset_id: str, now: datetime) -> str:
    try:
        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM asset WHERE id=?", (asset_id,)).fetchone()
            due_at = iso(now)
            if not _is_due_gc_candidate(row, due_at):
                return "skipped"

            if has_live_asset_reference(conn, asset_id):
                _restore_shared_locked(conn, asset_id)
                return "shared"

            storage_class = str(row["storage_class"])
            subdir = ASSET_STORAGE_CLASS_TO_SUBDIR[storage_class]
            key = row["object_key"]
            size = row["size"]
            digest = row["sha256"]
            if (
                row["deleted_at"] is None
                or not isinstance(key, str)
                or not key
                or Path(key).name != key
                or not isinstance(size, int)
                or size < 0
                or not _valid_sha256(digest)
            ):
                _defer_locked(conn, row, now, "OBJECT_METADATA_INVALID")
                return "deferred"

            try:
                data = storage.open(subdir, key)
            except FileNotFoundError:
                _mark_purged_locked(conn, asset_id, now)
                return "missing"
            except ValueError:
                # StorageAdapter uses ValueError for an illegal key or any
                # symlinked path component.  It is not safe to retry a
                # physical delete until an operator resolves that condition.
                _defer_locked(conn, row, now, "OBJECT_PATH_INVALID")
                return "deferred"
            except Exception:
                # Backends may not consistently expose OS failures as
                # OSError.  Keep the outward state machine stable and never
                # leak a backend exception or object identifier to logs.
                _defer_locked(conn, row, now, "OBJECT_READ_FAILED")
                return "deferred"

            if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
                _defer_locked(conn, row, now, "OBJECT_INTEGRITY_MISMATCH")
                return "deferred"
            try:
                storage.delete(subdir, key)
            except ValueError:
                _defer_locked(conn, row, now, "OBJECT_PATH_INVALID")
                return "deferred"
            except Exception:
                _defer_locked(conn, row, now, "OBJECT_DELETE_FAILED")
                return "deferred"
            _mark_purged_locked(conn, asset_id, now)
            return "purged"
    except Exception:
        return _defer_after_unexpected_failure(asset_id, now)


def _safe_audit(result: AssetGcResult) -> str:
    """审计是物理清理完成后的旁路，失败绝不回滚或改写 GC 状态。"""
    try:
        audit(
            "asset_gc_maintenance",
            resource_type="asset",
            result=result.audit_result(),
        )
    except Exception:
        return "unavailable"
    return "written"


def _emit_result(result: AssetGcResult, audit_status: str) -> None:
    """GC 状态只输出固定计数和审计旁路状态。"""
    event_result = "partial" if audit_status == "unavailable" or result.deferred else "ok"
    try:
        emit_asset_gc_result(
            result=event_result,
            audit_status=audit_status,
            processed=result.processed,
            purged_assets=result.purged_assets,
            missing_objects=result.missing_objects,
            deferred=result.deferred,
            restored_shared=result.restored_shared,
            skipped=result.skipped,
        )
    except Exception:
        pass


def run_asset_gc(*, limit: int = MAX_BATCH_SIZE, now: datetime | None = None) -> AssetGcResult:
    """有界、逐资产、幂等的通用对象物理清理。"""
    current = now or now_utc()
    batch_size = max(1, min(int(limit), MAX_BATCH_SIZE))
    due_at = iso(current)
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id FROM asset
            WHERE status='deleted' AND object_purged_at IS NULL
              AND gc_next_run_at IS NOT NULL AND gc_next_run_at <= ?
              AND storage_class IN ('file', 'photo-original', 'photo-thumbnail', 'knowledge')
            ORDER BY gc_next_run_at ASC, id ASC
            LIMIT ?
            """,
            (due_at, batch_size),
        ).fetchall()

    result = AssetGcResult()
    for row in rows:
        result.processed += 1
        outcome = _process_asset(str(row["id"]), current)
        if outcome == "purged":
            result.purged_assets += 1
        elif outcome == "missing":
            result.missing_objects += 1
        elif outcome == "shared":
            result.restored_shared += 1
        elif outcome == "deferred":
            result.deferred += 1
        else:
            result.skipped += 1
    audit_status = _safe_audit(result)
    _emit_result(result, audit_status)
    return result
