"""邮件保留期：先隔离脱敏，再以可重试方式清理邮件附件。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..config import settings
from ..db import audit, get_conn
from ..observability import emit_mail_retention_result
from ..security import iso, now_utc
from ..storage import storage


MAIL_RETENTION_BATCH_SIZE = 500
MAX_MESSAGE_ID_LENGTH = 998


@dataclass
class MailRetentionResult:
    """仅包含可安全审计的聚合维护计数。"""

    queued_messages: int = 0
    deleted_messages: int = 0
    deleted_attachments: int = 0
    deleted_assets: int = 0
    deleted_objects: int = 0
    deleted_raw_records: int = 0
    deleted_raw_objects: int = 0
    shared_asset_references: int = 0
    missing_assets: int = 0
    failed_attachments: int = 0
    failed_raw: int = 0
    inconsistent_raw: int = 0
    deferred_messages: int = 0
    deleted_nonces: int = 0

    def audit_result(self) -> str:
        return (
            f"queued={self.queued_messages};messages={self.deleted_messages};"
            f"attachments={self.deleted_attachments};assets={self.deleted_assets};"
            f"objects={self.deleted_objects};raw_records={self.deleted_raw_records};"
            f"raw_objects={self.deleted_raw_objects};shared={self.shared_asset_references};"
            f"missing_assets={self.missing_assets};failed={self.failed_attachments};"
            f"raw_failed={self.failed_raw};raw_inconsistent={self.inconsistent_raw};"
            f"deferred_messages={self.deferred_messages};"
            f"nonces={self.deleted_nonces}"
        )


@dataclass(frozen=True)
class _AttachmentCleanup:
    deleted_attachment: int = 0
    deleted_asset: int = 0
    deleted_object: int = 0
    shared_asset: int = 0
    missing_asset: int = 0
    failed: int = 0


@dataclass(frozen=True)
class _RawCleanup:
    deleted_record: int = 0
    deleted_object: int = 0
    failed: int = 0
    inconsistent: int = 0


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _retention_days() -> int:
    days = settings.mail_retention_days
    if not isinstance(days, int) or days < 1:
        raise ValueError("MAIL_RETENTION_DAYS 必须大于或等于 1")
    return days


def _tombstone_message_id(mail_id: str) -> str:
    value = f"retained-{mail_id}"
    if len(value) <= MAX_MESSAGE_ID_LENGTH:
        return value
    # 罕见的手工超长主键仍要释放原 Message-ID；固定长度摘要保持确定性且适合该字段。
    return "retained-" + hashlib.sha256(mail_id.encode("utf-8")).hexdigest()


def _queue_expired_messages(cutoff: str, queued_at: str, now: str) -> tuple[int, int]:
    """在一个数据库事务中隐藏、脱敏过期邮件并清理已过期 nonce。"""
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT id FROM mail_message WHERE retention_queued_at IS NULL AND received_at < ?",
            (cutoff,),
        ).fetchall()
        # 先更新附件展示名，随后标记消息；整个 context 退出时才会提交，因此读取端
        # 不会观察到半完成状态。
        conn.execute(
            "UPDATE mail_attachment SET name='' WHERE message_id IN ("
            "SELECT id FROM mail_message "
            "WHERE retention_queued_at IS NULL AND received_at < ?"
            ")",
            (cutoff,),
        )
        for row in rows:
            conn.execute(
                "UPDATE mail_message SET retention_queued_at=?, message_id=?, "
                "from_addr='', to_addr=NULL, subject=NULL, text_body=NULL, html_body=NULL, read_flag=1 "
                "WHERE id=? AND retention_queued_at IS NULL",
                (queued_at, _tombstone_message_id(str(row["id"])), row["id"]),
            )
        expired_nonces = conn.execute(
            "DELETE FROM mail_nonce WHERE expires_at < ?",
            (now,),
        ).rowcount
    return len(rows), int(expired_nonces)


def _asset_is_shared(conn, asset_id: str, attachment_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 WHERE "
        "EXISTS (SELECT 1 FROM mail_attachment WHERE asset_id=? AND id<>?) "
        "OR EXISTS (SELECT 1 FROM photo WHERE asset_id=? OR thumbnail_asset_id=?) "
        "OR EXISTS (SELECT 1 FROM knowledge_document WHERE asset_id=?)",
        (asset_id, attachment_id, asset_id, asset_id, asset_id),
    ).fetchone()
    return row is not None


def _cleanup_attachment(attachment_id: str) -> _AttachmentCleanup:
    """清理一条附件关系；物理删除成功但 DB 未提交时可由下次调用收敛。"""
    try:
        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            attachment = conn.execute(
                "SELECT id, asset_id FROM mail_attachment WHERE id=?",
                (attachment_id,),
            ).fetchone()
            if attachment is None:
                return _AttachmentCleanup()

            asset = conn.execute(
                "SELECT id, object_key FROM asset WHERE id=?",
                (attachment["asset_id"],),
            ).fetchone()
            if asset is None:
                conn.execute("DELETE FROM mail_attachment WHERE id=?", (attachment_id,))
                return _AttachmentCleanup(deleted_attachment=1, missing_asset=1)

            if _asset_is_shared(conn, asset["id"], attachment_id):
                conn.execute("DELETE FROM mail_attachment WHERE id=?", (attachment_id,))
                return _AttachmentCleanup(deleted_attachment=1, shared_asset=1)

            try:
                # 只允许删除邮件附件目录；StorageAdapter 本身将不存在的对象视为已删除。
                storage.delete("mail-attachments", asset["object_key"])
            except FileNotFoundError:
                pass
            except Exception:
                return _AttachmentCleanup(failed=1)

            conn.execute("DELETE FROM mail_attachment WHERE id=?", (attachment_id,))
            conn.execute("DELETE FROM asset WHERE id=?", (asset["id"],))
            return _AttachmentCleanup(
                deleted_attachment=1,
                deleted_asset=1,
                deleted_object=1,
            )
    except Exception:
        # 不记录 attachment id、对象键或异常文本；下一次维护会重新尝试。
        return _AttachmentCleanup(failed=1)


def _cleanup_raw(message_id: str) -> _RawCleanup:
    """只处理 stored 原文；状态不一致时保留所有数据并等待人工修复。"""
    try:
        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            message = conn.execute(
                "SELECT raw_state FROM mail_message WHERE id=?",
                (message_id,),
            ).fetchone()
            if message is None:
                return _RawCleanup()
            raw = conn.execute(
                "SELECT object_key FROM mail_raw WHERE message_id=?",
                (message_id,),
            ).fetchone()
            raw_state = str(message["raw_state"])
            if raw_state in {"legacy", "removed"}:
                if raw is not None:
                    return _RawCleanup(inconsistent=1)
                return _RawCleanup()
            if raw_state != "stored" or raw is None:
                return _RawCleanup(inconsistent=1)

            try:
                # StorageAdapter 对不存在对象收敛为成功；绝不触碰邮件原文目录以外的对象。
                storage.delete("mail-raw", raw["object_key"])
            except FileNotFoundError:
                pass
            except Exception:
                return _RawCleanup(failed=1)

            deleted_record = conn.execute(
                "DELETE FROM mail_raw WHERE message_id=?",
                (message_id,),
            ).rowcount
            updated_state = conn.execute(
                "UPDATE mail_message SET raw_state='removed' "
                "WHERE id=? AND raw_state='stored'",
                (message_id,),
            ).rowcount
            if deleted_record != 1 or updated_state != 1:
                # 若 DB 在物理删除后未能收敛，回滚会留下 raw 行；下一轮会把缺失对象视为已删。
                raise RuntimeError("raw cleanup state changed")
            return _RawCleanup(deleted_record=1, deleted_object=1)
    except Exception:
        return _RawCleanup(failed=1)


def _pending_message_ids(batch_size: int) -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM mail_message WHERE retention_queued_at IS NOT NULL "
            "ORDER BY retention_queued_at, id LIMIT ?",
            (batch_size,),
        ).fetchall()
    return [str(row["id"]) for row in rows]


def _attachment_ids(message_id: str) -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM mail_attachment WHERE message_id=? ORDER BY id",
            (message_id,),
        ).fetchall()
    return [str(row["id"]) for row in rows]


def _delete_completed_message(message_id: str) -> int:
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        return int(
            conn.execute(
                "DELETE FROM mail_message WHERE id=? AND retention_queued_at IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM mail_attachment WHERE message_id=mail_message.id) "
                "AND NOT EXISTS (SELECT 1 FROM mail_raw WHERE message_id=mail_message.id) "
                "AND raw_state IN ('legacy', 'removed')",
                (message_id,),
            ).rowcount
        )


def _defer_pending_message(message_id: str, retry_sort_at: str) -> None:
    """只后移仍 pending 的消息，避免失败项长期占满有界批次。"""
    try:
        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE mail_message SET retention_queued_at=? "
                "WHERE id=? AND retention_queued_at IS NOT NULL",
                (retry_sort_at, message_id),
            )
    except Exception:
        # 排序后移自身失败不能泄露标识或影响既有 pending/脱敏状态。
        pass


def _apply_attachment_result(result: MailRetentionResult, cleanup: _AttachmentCleanup) -> None:
    result.deleted_attachments += cleanup.deleted_attachment
    result.deleted_assets += cleanup.deleted_asset
    result.deleted_objects += cleanup.deleted_object
    result.shared_asset_references += cleanup.shared_asset
    result.missing_assets += cleanup.missing_asset
    result.failed_attachments += cleanup.failed


def _apply_raw_result(result: MailRetentionResult, cleanup: _RawCleanup) -> None:
    result.deleted_raw_records += cleanup.deleted_record
    result.deleted_raw_objects += cleanup.deleted_object
    result.failed_raw += cleanup.failed
    result.inconsistent_raw += cleanup.inconsistent


def _defer_message(result: MailRetentionResult, message_id: str, retry_sort_at: str) -> None:
    """记录一次待重试消息，再保持原有后移行为。"""
    result.deferred_messages += 1
    _defer_pending_message(message_id, retry_sort_at)


def _process_pending_messages(
    result: MailRetentionResult,
    batch_size: int,
    retry_sort_at: str,
) -> None:
    for message_id in _pending_message_ids(batch_size):
        cleanup_failed = False
        try:
            attachment_ids = _attachment_ids(message_id)
        except Exception:
            attachment_ids = []
            cleanup_failed = True
        for attachment_id in attachment_ids:
            cleanup = _cleanup_attachment(attachment_id)
            _apply_attachment_result(result, cleanup)
            cleanup_failed = cleanup_failed or bool(cleanup.failed)
        raw_cleanup = _cleanup_raw(message_id)
        _apply_raw_result(result, raw_cleanup)
        cleanup_failed = cleanup_failed or bool(raw_cleanup.failed or raw_cleanup.inconsistent)
        if cleanup_failed:
            _defer_message(result, message_id, retry_sort_at)
            continue
        try:
            deleted = _delete_completed_message(message_id)
        except Exception:
            _defer_message(result, message_id, retry_sort_at)
            continue
        result.deleted_messages += deleted
        if deleted == 0:
            # UPDATE 自带 pending 条件；消息若已由其它维护者清除则不会产生副作用。
            _defer_message(result, message_id, retry_sort_at)


def _safe_audit(result: MailRetentionResult) -> str:
    """审计是旁路，绝不因其失败回滚已完成的过期清理。"""
    try:
        audit(
            "mail_retention_maintenance",
            resource_type="mail",
            result=result.audit_result(),
        )
    except Exception:
        return "unavailable"
    return "written"


def _emit_result(result: MailRetentionResult, audit_status: str) -> None:
    """维护事件只写固定聚合计数；JSONL 输出失败同样是旁路。"""
    event_result = "partial" if (
        audit_status == "unavailable"
        or result.failed_attachments
        or result.failed_raw
        or result.inconsistent_raw
        or result.deferred_messages
    ) else "ok"
    try:
        emit_mail_retention_result(
            result=event_result,
            audit_status=audit_status,
            queued_messages=result.queued_messages,
            deleted_messages=result.deleted_messages,
            deleted_attachments=result.deleted_attachments,
            deleted_assets=result.deleted_assets,
            deleted_objects=result.deleted_objects,
            deleted_raw_records=result.deleted_raw_records,
            deleted_raw_objects=result.deleted_raw_objects,
            shared_asset_references=result.shared_asset_references,
            missing_assets=result.missing_assets,
            failed_attachments=result.failed_attachments,
            failed_raw=result.failed_raw,
            inconsistent_raw=result.inconsistent_raw,
            deferred_messages=result.deferred_messages,
            deleted_nonces=result.deleted_nonces,
        )
    except Exception:
        pass


def run_mail_retention(
    *,
    now: datetime | None = None,
    batch_size: int = MAIL_RETENTION_BATCH_SIZE,
) -> MailRetentionResult:
    """执行一次保留期维护，返回幂等、仅含计数的结果。"""
    if not isinstance(batch_size, int):
        raise ValueError("邮件保留期批次大小非法")
    bounded_batch_size = max(1, min(batch_size, MAIL_RETENTION_BATCH_SIZE))
    current = _as_utc(now or now_utc())
    cutoff = current - timedelta(days=_retention_days())

    result = MailRetentionResult()
    result.queued_messages, result.deleted_nonces = _queue_expired_messages(
        iso(cutoff),
        iso(current),
        iso(current),
    )
    _process_pending_messages(
        result,
        bounded_batch_size,
        iso(current + timedelta(microseconds=1)),
    )
    audit_status = _safe_audit(result)
    _emit_result(result, audit_status)
    return result
