"""隐私优先的本地 JSONL 运维事件。

此模块只允许少量、预先声明的字段进入 stdout。它不是通用日志封装：不接受
``message``、任意 ``**kwargs`` 或容器值，避免调用方意外把请求、异常或私有数据
写入运行日志。
"""

from __future__ import annotations

import json
import re
import secrets
import sys
from datetime import datetime, timezone
from typing import Any


_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_ROUTE_RE = re.compile(r"^/[A-Za-z0-9_./{}:-]{0,159}$")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,79}$")
_MAX_COUNT = (2**63) - 1
_UNMATCHED_ROUTE = "/__unmatched__"
_ROUTE_TEMPLATE_SCOPE_KEY = "starisle.canonical_route_template"

_EVENTS: dict[str, frozenset[str]] = {
    "api": frozenset({"request_completed"}),
    "health": frozenset({"ready_checked"}),
    "mail": frozenset({"audit_unavailable", "object_cleanup_unavailable"}),
    "worker": frozenset(
        {
            "started",
            "stopped",
            "loop_error",
            "job_succeeded",
            "job_failed",
            "job_cancelled",
            "mail_retention_completed",
            "mail_retention_failed",
            "asset_gc_completed",
            "asset_gc_failed",
            "idempotency_cleanup_completed",
            "idempotency_cleanup_failed",
            "audit_retention_completed",
            "audit_retention_failed",
        }
    ),
    "mail_retention": frozenset({"maintenance_completed"}),
    "asset_gc": frozenset({"maintenance_completed"}),
    "idempotency_cleanup": frozenset({"maintenance_completed"}),
    "audit_retention": frozenset({"maintenance_completed"}),
}
_RESULTS = frozenset(
    {
        "ok",
        "client_error",
        "server_error",
        "ready",
        "not_ready",
        "started",
        "stopped",
        "succeeded",
        "failed",
        "cancelled",
        "partial",
    }
)
_LEVELS = frozenset({"INFO", "WARNING", "ERROR"})
_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"})
_JOB_TYPES = frozenset({"ingest", "rebuild", "purge", "unknown"})
_AUDIT_STATUSES = frozenset({"written", "unavailable"})
_WORKER_ERROR_CODES = frozenset(
    {
        "ASSET_MISSING",
        "ASSET_READ_FAILED",
        "DOCUMENT_MISSING",
        "EMBED_CLIENT_FAILURE",
        "EMBED_DIM_INVALID",
        "EMBED_HASH_INVALID",
        "EMBED_HTTP_RETRYABLE",
        "EMBED_HTTP_STATUS",
        "EMBED_MODEL_ID_REQUIRED",
        "EMBED_NETWORK",
        "EMBED_PROFILE_MISMATCH",
        "EMBED_RESPONSE_COUNT",
        "EMBED_RESPONSE_DIM",
        "EMBED_RESPONSE_INDEX",
        "EMBED_RESPONSE_JSON",
        "EMBED_RESPONSE_MODEL",
        "EMBED_RESPONSE_SCHEMA",
        "EMBED_RESPONSE_VALUE",
        "EMBED_RESPONSE_ZERO",
        "EMBED_TEXT_INVALID",
        "EMBED_TIMEOUT",
        "EMPTY_TEXT",
        "INVALID_JOB_PAYLOAD",
        "PROCESSING_FAILED",
        "UNKNOWN_JOB_TYPE",
        "VECTOR_ATOMIC_COUNT_MISMATCH",
        "VECTOR_BATCH_COUNT_MISMATCH",
        "VECTOR_CHUNK_INVALID",
        "VECTOR_DIM_INVALID",
        "VECTOR_EXTENSION_UNAVAILABLE",
        "VECTOR_PROFILE_INVALID",
        "VECTOR_SERIALIZE_FAILED",
        "VECTOR_VALUE_INVALID",
        "VECTOR_ZERO",
    }
)
_WORKER_EVENT_ERROR_CODES = _WORKER_ERROR_CODES | {"MAINTENANCE_FAILED", "LOOP_FAILED"}

def new_request_id() -> str:
    """生成固定 12 位小写十六进制请求标识。"""
    return secrets.token_hex(6)


def safe_request_id(value: object) -> str:
    """只保留完整匹配的 ASCII 请求标识；其它输入全部本地替换。"""
    if isinstance(value, str) and value.isascii() and _REQUEST_ID_RE.fullmatch(value):
        return value
    return new_request_id()


def _require_choice(value: object, allowed: frozenset[str], label: str) -> str:
    if type(value) is not str or value not in allowed:
        raise ValueError(f"无效的安全事件 {label}")
    return value


def _require_request_id(value: object) -> str:
    if not (isinstance(value, str) and value.isascii() and _REQUEST_ID_RE.fullmatch(value)):
        raise ValueError("无效的安全事件 requestId")
    return value


def _require_route(value: object) -> str:
    if not (isinstance(value, str) and value.isascii() and _ROUTE_RE.fullmatch(value)):
        raise ValueError("无效的安全事件 route")
    return value


def _require_token(value: object, label: str) -> str:
    if not (isinstance(value, str) and value.isascii() and _SAFE_TOKEN_RE.fullmatch(value)):
        raise ValueError(f"无效的安全事件 {label}")
    return value


def _require_status(value: object) -> int:
    if type(value) is not int or not 100 <= value <= 599:
        raise ValueError("无效的安全事件 status")
    return value


def _require_bool(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"无效的安全事件 {label}")
    return value


def _require_count(value: object, label: str) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_COUNT:
        raise ValueError(f"无效的安全事件 {label}")
    return value


def emit_event(
    *,
    component: str,
    event: str,
    result: str,
    level: str = "INFO",
    request_id: str | None = None,
    method: str | None = None,
    route: str | None = None,
    status: int | None = None,
    db: bool | None = None,
    storage: bool | None = None,
    ready: bool | None = None,
    job_type: str | None = None,
    error_code: str | None = None,
    retryable: bool | None = None,
    audit_status: str | None = None,
    queued_messages: int | None = None,
    deleted_messages: int | None = None,
    deleted_attachments: int | None = None,
    deleted_assets: int | None = None,
    deleted_objects: int | None = None,
    deleted_raw_records: int | None = None,
    deleted_raw_objects: int | None = None,
    shared_asset_references: int | None = None,
    missing_assets: int | None = None,
    failed_attachments: int | None = None,
    failed_raw: int | None = None,
    inconsistent_raw: int | None = None,
    deferred_messages: int | None = None,
    deleted_nonces: int | None = None,
    processed: int | None = None,
    purged_assets: int | None = None,
    missing_objects: int | None = None,
    deferred: int | None = None,
    restored_shared: int | None = None,
    skipped: int | None = None,
    deleted_rows: int | None = None,
) -> dict[str, Any]:
    """写入一行白名单 JSON；非法字段在输出前拒绝，绝不静默降级。"""
    safe_component = _require_choice(component, frozenset(_EVENTS), "component")
    safe_event = _require_choice(event, _EVENTS[safe_component], "event")
    safe_result = _require_choice(result, _RESULTS, "result")
    safe_level = _require_choice(level, _LEVELS, "level")
    record: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "level": safe_level,
        "component": safe_component,
        "event": safe_event,
        "result": safe_result,
    }
    if request_id is not None:
        record["requestId"] = _require_request_id(request_id)
    if method is not None:
        record["method"] = _require_choice(method, _METHODS | {"OTHER"}, "method")
    if route is not None:
        record["route"] = _require_route(route)
    if status is not None:
        record["status"] = _require_status(status)
    if db is not None:
        record["db"] = _require_bool(db, "db")
    if storage is not None:
        record["storage"] = _require_bool(storage, "storage")
    if ready is not None:
        record["ready"] = _require_bool(ready, "ready")
    if job_type is not None:
        record["jobType"] = _require_choice(job_type, _JOB_TYPES, "jobType")
    if error_code is not None:
        record["errorCode"] = _require_choice(error_code, _WORKER_EVENT_ERROR_CODES, "errorCode")
    if retryable is not None:
        record["retryable"] = _require_bool(retryable, "retryable")
    if audit_status is not None:
        record["auditStatus"] = _require_choice(audit_status, _AUDIT_STATUSES, "auditStatus")

    count_fields = (
        ("queuedMessages", queued_messages),
        ("deletedMessages", deleted_messages),
        ("deletedAttachments", deleted_attachments),
        ("deletedAssets", deleted_assets),
        ("deletedObjects", deleted_objects),
        ("deletedRawRecords", deleted_raw_records),
        ("deletedRawObjects", deleted_raw_objects),
        ("sharedAssetReferences", shared_asset_references),
        ("missingAssets", missing_assets),
        ("failedAttachments", failed_attachments),
        ("failedRaw", failed_raw),
        ("inconsistentRaw", inconsistent_raw),
        ("deferredMessages", deferred_messages),
        ("deletedNonces", deleted_nonces),
        ("processed", processed),
        ("purgedAssets", purged_assets),
        ("missingObjects", missing_objects),
        ("deferred", deferred),
        ("restoredShared", restored_shared),
        ("skipped", skipped),
        ("deletedRows", deleted_rows),
    )
    for name, value in count_fields:
        if value is not None:
            record[name] = _require_count(value, name)

    encoded = json.dumps(record, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    # 运行日志是旁路，输出设备故障不得改变 API、worker 或维护事务语义。
    try:
        sys.stdout.write(encoded + "\n")
        sys.stdout.flush()
    except (OSError, ValueError):
        pass
    return record


def route_template(request: object) -> str:
    """从路由对象取得模板；未匹配或不安全的 scope 一律归为固定常量。"""
    try:
        scope = getattr(request, "scope")
        if not isinstance(scope, dict):
            return _UNMATCHED_ROUTE
        if _ROUTE_TEMPLATE_SCOPE_KEY in scope:
            candidate = scope[_ROUTE_TEMPLATE_SCOPE_KEY]
        else:
            route = scope.get("route")
            candidate = getattr(route, "path", None)
    except Exception:
        return _UNMATCHED_ROUTE
    if isinstance(candidate, str) and candidate.isascii() and _ROUTE_RE.fullmatch(candidate):
        return candidate
    return _UNMATCHED_ROUTE


def request_result(status: int) -> str:
    if 200 <= status < 400:
        return "ok"
    if 400 <= status < 500:
        return "client_error"
    return "server_error"


def emit_api_request(*, request_id: object, method: object, route: object, status: object) -> dict[str, Any]:
    safe_status = _require_status(status)
    safe_method = method if type(method) is str and method in _METHODS else "OTHER"
    safe_route = route if isinstance(route, str) and route.isascii() and _ROUTE_RE.fullmatch(route) else _UNMATCHED_ROUTE
    return emit_event(
        component="api",
        event="request_completed",
        result=request_result(safe_status),
        level="INFO" if safe_status < 500 else "ERROR",
        request_id=safe_request_id(request_id),
        method=safe_method,
        route=safe_route,
        status=safe_status,
    )


def emit_ready_check(*, db: object, storage: object, ready: object) -> dict[str, Any]:
    safe_db = _require_bool(db, "db")
    safe_storage = _require_bool(storage, "storage")
    safe_ready = _require_bool(ready, "ready")
    return emit_event(
        component="health",
        event="ready_checked",
        result="ready" if safe_ready else "not_ready",
        level="INFO" if safe_ready else "WARNING",
        db=safe_db,
        storage=safe_storage,
        ready=safe_ready,
    )


def emit_mail_audit_unavailable(*, request_id: object | None = None) -> dict[str, Any]:
    """邮件审计旁路失败；只允许可选的安全请求标识。"""
    return emit_event(
        component="mail",
        event="audit_unavailable",
        result="failed",
        level="ERROR",
        request_id=safe_request_id(request_id) if request_id is not None else None,
    )


def emit_mail_object_cleanup_unavailable() -> dict[str, Any]:
    """邮件私有对象清理旁路失败；不接受对象、路径或异常数据。"""
    return emit_event(
        component="mail",
        event="object_cleanup_unavailable",
        result="failed",
        level="ERROR",
    )


def _worker_error_code(value: object) -> str:
    return value if type(value) is str and value in _WORKER_EVENT_ERROR_CODES else "PROCESSING_FAILED"


def emit_worker_event(
    *,
    event: str,
    result: str,
    job_type: object | None = None,
    error_code: object | None = None,
    retryable: object | None = None,
) -> dict[str, Any]:
    if job_type is None:
        safe_job_type = None
    elif type(job_type) is str and job_type in _JOB_TYPES:
        safe_job_type = job_type
    else:
        safe_job_type = "unknown"
    safe_error_code = _worker_error_code(error_code) if error_code is not None else None
    safe_retryable = _require_bool(retryable, "retryable") if retryable is not None else None
    return emit_event(
        component="worker",
        event=event,
        result=result,
        level="ERROR" if result == "failed" else "INFO",
        job_type=safe_job_type,
        error_code=safe_error_code,
        retryable=safe_retryable,
    )


def emit_mail_retention_result(
    *,
    result: str,
    audit_status: str,
    queued_messages: int,
    deleted_messages: int,
    deleted_attachments: int,
    deleted_assets: int,
    deleted_objects: int,
    deleted_raw_records: int,
    deleted_raw_objects: int,
    shared_asset_references: int,
    missing_assets: int,
    failed_attachments: int,
    failed_raw: int,
    inconsistent_raw: int,
    deferred_messages: int,
    deleted_nonces: int,
) -> dict[str, Any]:
    return emit_event(
        component="mail_retention",
        event="maintenance_completed",
        result=result,
        level="WARNING" if result == "partial" else "INFO",
        audit_status=audit_status,
        queued_messages=queued_messages,
        deleted_messages=deleted_messages,
        deleted_attachments=deleted_attachments,
        deleted_assets=deleted_assets,
        deleted_objects=deleted_objects,
        deleted_raw_records=deleted_raw_records,
        deleted_raw_objects=deleted_raw_objects,
        shared_asset_references=shared_asset_references,
        missing_assets=missing_assets,
        failed_attachments=failed_attachments,
        failed_raw=failed_raw,
        inconsistent_raw=inconsistent_raw,
        deferred_messages=deferred_messages,
        deleted_nonces=deleted_nonces,
    )


def emit_asset_gc_result(
    *,
    result: str,
    audit_status: str,
    processed: int,
    purged_assets: int,
    missing_objects: int,
    deferred: int,
    restored_shared: int,
    skipped: int,
) -> dict[str, Any]:
    return emit_event(
        component="asset_gc",
        event="maintenance_completed",
        result=result,
        level="WARNING" if result == "partial" else "INFO",
        audit_status=audit_status,
        processed=processed,
        purged_assets=purged_assets,
        missing_objects=missing_objects,
        deferred=deferred,
        restored_shared=restored_shared,
        skipped=skipped,
    )


def emit_idempotency_cleanup_result(
    *,
    result: str,
    audit_status: str,
    deleted_rows: int,
) -> dict[str, Any]:
    return emit_event(
        component="idempotency_cleanup",
        event="maintenance_completed",
        result=result,
        level="WARNING" if result == "partial" else "INFO",
        audit_status=audit_status,
        deleted_rows=deleted_rows,
    )


def emit_audit_retention_result(
    *,
    result: str,
    audit_status: str,
    deleted_rows: int,
    deferred: int,
) -> dict[str, Any]:
    return emit_event(
        component="audit_retention",
        event="maintenance_completed",
        result=result,
        level="WARNING" if result == "partial" else "INFO",
        audit_status=audit_status,
        deleted_rows=deleted_rows,
        deferred=deferred,
    )
