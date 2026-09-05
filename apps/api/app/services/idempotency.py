"""幂等键助手:可重试写操作接受 Idempotency-Key,重复请求返回首次结果。

幂等记录只承担短期重放去重职责,由 worker 周期任务按保留期清理,
避免表无界增长;清理按批次执行且可安全重试。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Callable, TypeVar

from ..config import settings
from ..db import audit, get_conn
from ..observability import emit_idempotency_cleanup_result
from ..security import iso, now_utc, sha256_hex

T = TypeVar("T")

IDEMPOTENCY_CLEANUP_BATCH_SIZE = 500


def with_idempotency(key: str | None, resource_type: str, producer: Callable[[], T]) -> T:
    """producer() 返回任意结果(可序列化);重复键直接返回首次结果。"""
    if not key:
        return producer()
    key_hash = sha256_hex(key.encode())
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT resource_id FROM idempotency WHERE key_hash=? AND resource_type=?",
            (key_hash, resource_type),
        ).fetchone()
        if existing:
            return json.loads(existing["resource_id"])
        result = producer()
        conn.execute(
            "INSERT INTO idempotency (key_hash, resource_id, resource_type, created_at) VALUES (?, ?, ?, ?)",
            (key_hash, json.dumps(result, ensure_ascii=False), resource_type, iso(now_utc())),
        )
        return result


def _retention_days() -> int:
    days = settings.idempotency_retention_days
    if not isinstance(days, int) or days < 1:
        raise ValueError("IDEMPOTENCY_RETENTION_DAYS 必须大于或等于 1")
    return days


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _delete_expired(cutoff: str, batch_size: int) -> int:
    """单事务有界删除;行数超过批次时留给下一轮,绝不一次锁表过久。"""
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        return int(
            conn.execute(
                "DELETE FROM idempotency WHERE key_hash IN ("
                "SELECT key_hash FROM idempotency WHERE created_at < ? "
                "ORDER BY created_at, key_hash LIMIT ?"
                ")",
                (cutoff, batch_size),
            ).rowcount
        )


def _safe_audit(deleted_rows: int) -> str:
    """审计是旁路,绝不因其失败回滚已完成的过期清理。"""
    try:
        audit(
            "idempotency_cleanup",
            resource_type="idempotency",
            result=f"deleted_rows={deleted_rows}",
        )
    except Exception:
        return "unavailable"
    return "written"


def _emit_result(deleted_rows: int, audit_status: str) -> None:
    """维护事件只写固定聚合计数;JSONL 输出失败同样是旁路。"""
    try:
        emit_idempotency_cleanup_result(
            result="partial" if audit_status == "unavailable" else "ok",
            audit_status=audit_status,
            deleted_rows=deleted_rows,
        )
    except Exception:
        pass


def run_idempotency_cleanup(
    *,
    now: datetime | None = None,
    batch_size: int = IDEMPOTENCY_CLEANUP_BATCH_SIZE,
) -> int:
    """执行一次幂等记录过期清理,返回删除行数;重复执行安全。"""
    if not isinstance(batch_size, int):
        raise ValueError("幂等清理批次大小非法")
    bounded_batch_size = max(1, min(batch_size, IDEMPOTENCY_CLEANUP_BATCH_SIZE))
    current = _as_utc(now or now_utc())
    cutoff = iso(current - timedelta(days=_retention_days()))

    deleted_rows = _delete_expired(cutoff, bounded_batch_size)
    audit_status = _safe_audit(deleted_rows)
    _emit_result(deleted_rows, audit_status)
    return deleted_rows