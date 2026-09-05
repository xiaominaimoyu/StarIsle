"""审计事件保留期：最小保留期 + 软容量目标的有界批量清理。

两阶段语义：
1. 过期清理：删除早于 ``now - AUDIT_RETENTION_DAYS`` 的行。
2. 软容量：行数超过 ``AUDIT_SOFT_CAP_ROWS`` 时从最旧删起，但绝不触碰
   登录限速窗口（与 ``routers/auth.py`` 的 5 分钟 login_fail 统计耦合）内的行；
   窗口内超量只能留待后续轮次，以 deferred 计数如实上报。

不新增 schema 索引（保持在 v5）：软容量上限内的全表扫描成本可忽略，
避免备份/恢复/演练工具链的版本涟漪。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..config import settings
from ..db import audit, get_conn
from ..observability import emit_audit_retention_result
from ..security import iso, now_utc

AUDIT_RETENTION_BATCH_SIZE = 500
# 与 routers/auth.py 登录限速器的统计窗口保持完全一致；
# 清理绝不删除该窗口内的任何行，避免破坏 login_fail 计数语义。
LOGIN_LIMITER_WINDOW = timedelta(minutes=5)


@dataclass
class AuditRetentionResult:
    """仅包含可安全审计的聚合维护计数。"""

    deleted_expired: int = 0
    deleted_capped: int = 0
    deferred_rows: int = 0

    def audit_result(self) -> str:
        return (
            f"expired={self.deleted_expired};capped={self.deleted_capped};"
            f"deferred={self.deferred_rows}"
        )


def _retention_days() -> int:
    days = settings.audit_retention_days
    if not isinstance(days, int) or days < 1:
        raise ValueError("AUDIT_RETENTION_DAYS 必须大于或等于 1")
    return days


def _soft_cap_rows() -> int:
    cap = settings.audit_soft_cap_rows
    if not isinstance(cap, int) or cap < 1:
        raise ValueError("AUDIT_SOFT_CAP_ROWS 必须大于或等于 1")
    return cap


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _delete_expired(cutoff: str, batch_size: int) -> int:
    """单事务有界删除过期行；行数超批次时留给下一轮。"""
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        return int(
            conn.execute(
                "DELETE FROM audit_event WHERE id IN ("
                "SELECT id FROM audit_event WHERE created_at < ? "
                "ORDER BY created_at, id LIMIT ?"
                ")",
                (cutoff, batch_size),
            ).rowcount
        )


def _enforce_soft_cap(soft_cap: int, protect_cutoff: str, batch_size: int) -> tuple[int, int]:
    """超出软容量时从最旧删起；返回 (已删, 因窗口保护而推迟的行数)。

    删除目标 = min(超出量, 窗口外可删量, 批次)：删除后总行数不会低于软容量，
    窗口内的行（含攻击驱动的短时写放大）一律保留并记为 deferred。
    """
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        total = int(conn.execute("SELECT COUNT(*) AS c FROM audit_event").fetchone()["c"])
        excess = total - soft_cap
        if excess <= 0:
            return 0, 0
        deletable = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM audit_event WHERE created_at <= ?",
                (protect_cutoff,),
            ).fetchone()["c"]
        )
        target = min(excess, deletable, batch_size)
        if target == 0:
            return 0, excess
        deleted = int(
            conn.execute(
                "DELETE FROM audit_event WHERE id IN ("
                "SELECT id FROM audit_event WHERE created_at <= ? "
                "ORDER BY created_at, id LIMIT ?"
                ")",
                (protect_cutoff, target),
            ).rowcount
        )
    return deleted, excess - deleted


def _safe_audit(result: AuditRetentionResult) -> str:
    """审计是旁路，绝不因其失败回滚已完成的清理。"""
    try:
        audit(
            "audit_retention_maintenance",
            resource_type="audit",
            result=result.audit_result(),
        )
    except Exception:
        return "unavailable"
    return "written"


def _emit_result(result: AuditRetentionResult, audit_status: str) -> None:
    """维护事件只写固定聚合计数；JSONL 输出失败同样是旁路。"""
    event_result = "partial" if (audit_status == "unavailable" or result.deferred_rows) else "ok"
    try:
        emit_audit_retention_result(
            result=event_result,
            audit_status=audit_status,
            deleted_rows=result.deleted_expired + result.deleted_capped,
            deferred=result.deferred_rows,
        )
    except Exception:
        pass


def run_audit_retention(
    *,
    now: datetime | None = None,
    batch_size: int = AUDIT_RETENTION_BATCH_SIZE,
) -> AuditRetentionResult:
    """执行一次审计保留期维护，返回幂等、仅含计数的结果。"""
    if not isinstance(batch_size, int):
        raise ValueError("审计保留期批次大小非法")
    bounded_batch_size = max(1, min(batch_size, AUDIT_RETENTION_BATCH_SIZE))
    current = _as_utc(now or now_utc())
    cutoff = iso(current - timedelta(days=_retention_days()))
    protect_cutoff = iso(current - LOGIN_LIMITER_WINDOW)

    result = AuditRetentionResult()
    result.deleted_expired = _delete_expired(cutoff, bounded_batch_size)
    result.deleted_capped, result.deferred_rows = _enforce_soft_cap(
        _soft_cap_rows(),
        protect_cutoff,
        bounded_batch_size,
    )
    audit_status = _safe_audit(result)
    _emit_result(result, audit_status)
    return result
