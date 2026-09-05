"""作业 / 会话 / 健康端点。"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..db import audit, get_conn
from ..deps import get_request_id, require_auth
from ..observability import emit_ready_check
from ..security import iso, now_utc, random_id
from ..services.jobs import cancel_job, job_status, list_jobs
from ..storage import storage

router = APIRouter(tags=["jobs"])

_CURSOR_MAX_LENGTH = 512
_CURSOR_ID_MAX_LENGTH = 128
_CURSOR_VERSION = 1
_CONVERSATION_LIST_SCOPE = "conversation-list"
_CONVERSATION_MESSAGE_SCOPE = "conversation-messages"
_INVALID_CURSOR_DETAIL = {"code": "UNPROCESSABLE", "message": "游标无效。"}


def _invalid_cursor() -> None:
    """Keep every cursor parse failure on one intentionally opaque response."""

    raise HTTPException(422, detail=_INVALID_CURSOR_DETAIL)


def _cursor_object_pairs(pairs: list[tuple[object, object]]) -> dict[str, object]:
    """Reject duplicate/non-string JSON object keys before field validation."""

    result: dict[str, object] = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise ValueError("invalid cursor object")
        result[key] = value
    return result


def _normalise_cursor_time(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("cursor createdAt is invalid")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("cursor createdAt has no timezone")
    return iso(parsed.astimezone(timezone.utc))


def _decode_cursor(
    cursor: str | None,
    *,
    scope: str,
    conversation_id: str | None = None,
) -> dict[str, str] | None:
    """Decode the small opaque keyset anchor used only by this router."""

    if cursor is None:
        return None
    if not cursor or len(cursor) > _CURSOR_MAX_LENGTH:
        _invalid_cursor()
    try:
        encoded = cursor.encode("ascii")
        if any(
            char not in b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
            for char in encoded
        ):
            raise ValueError("not base64url")
        padding = b"=" * (-len(encoded) % 4)
        decoded = base64.b64decode(encoded + padding, altchars=b"-_", validate=True)
        parsed = json.loads(decoded.decode("utf-8"), object_pairs_hook=_cursor_object_pairs)
        if not isinstance(parsed, dict) or type(parsed.get("v")) is not int:
            raise ValueError("cursor version is invalid")
        if parsed["v"] != _CURSOR_VERSION or parsed.get("scope") != scope:
            raise ValueError("cursor scope is invalid")

        expected_fields = {"v", "scope", "createdAt", "id"}
        if conversation_id is not None:
            expected_fields.add("conversationId")
        if set(parsed) != expected_fields:
            raise ValueError("cursor fields are invalid")

        item_id = parsed.get("id")
        if (
            not isinstance(item_id, str)
            or not item_id.strip()
            or len(item_id) > _CURSOR_ID_MAX_LENGTH
        ):
            raise ValueError("cursor id is invalid")

        result = {"createdAt": _normalise_cursor_time(parsed.get("createdAt")), "id": item_id}
        if conversation_id is not None:
            cursor_conversation_id = parsed.get("conversationId")
            if (
                not isinstance(cursor_conversation_id, str)
                or not cursor_conversation_id.strip()
                or len(cursor_conversation_id) > _CURSOR_ID_MAX_LENGTH
                or cursor_conversation_id != conversation_id
            ):
                raise ValueError("cursor conversation is invalid")
        return result
    except (
        UnicodeEncodeError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
        OverflowError,
        TypeError,
        ValueError,
    ):
        _invalid_cursor()


def _encode_cursor(
    *,
    scope: str,
    created_at: str,
    item_id: str,
    conversation_id: str | None = None,
) -> str:
    payload: dict[str, object] = {
        "v": _CURSOR_VERSION,
        "scope": scope,
        "createdAt": created_at,
        "id": item_id,
    }
    if conversation_id is not None:
        payload["conversationId"] = conversation_id
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


@router.get("/jobs")
def list_job_summaries(
    limit: int = 20,
    activeOnly: bool = False,
    payload: dict = Depends(require_auth),
):
    with get_conn() as conn:
        return list_jobs(conn, limit=max(1, min(limit, 100)), active_only=activeOnly)


@router.get("/stats/storage")
def get_storage_stats(payload: dict = Depends(require_auth)):
    with get_conn() as conn:
        used_bytes = conn.execute(
            "SELECT COALESCE(SUM(size), 0) FROM asset "
            "WHERE status='active' AND object_purged_at IS NULL "
            "AND storage_class IN ('file', 'photo-original', 'photo-thumbnail')"
        ).fetchone()[0]
        file_count = conn.execute(
            "SELECT COUNT(*) FROM asset WHERE status='active' AND object_purged_at IS NULL "
            "AND storage_class='file'"
        ).fetchone()[0]
        photo_count = conn.execute(
            "SELECT COUNT(*) FROM photo p JOIN asset a ON a.id=p.asset_id "
            "WHERE a.status='active' AND a.object_purged_at IS NULL AND a.storage_class='photo-original'"
        ).fetchone()[0]
    return {"usedBytes": used_bytes, "fileCount": file_count, "photoCount": photo_count}


@router.post("/jobs/{job_id}/cancel")
def cancel_job_route(
    job_id: str,
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    payload: dict = Depends(require_auth),
):
    from ..deps import require_csrf

    require_csrf(request, x_csrf_token)
    with get_conn() as conn:
        try:
            status = cancel_job(conn, job_id)
        except ValueError:
            raise HTTPException(409, detail={"code": "CONFLICT", "message": "该任务当前不可取消。"})
        if status is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "任务不存在"})
        audit("job_cancel", actor=payload["sub"], resource_type="job", resource_id=job_id,
              request_id=get_request_id(request), result="ok", conn=conn)
    return status


@router.get("/jobs/{job_id}")
def get_job(job_id: str, payload: dict = Depends(require_auth)):
    with get_conn() as conn:
        status = job_status(conn, job_id)
    if status is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "任务不存在"})
    return status


# ---------- 会话 ----------

class ConversationCreate(BaseModel):
    title: str | None = None


@router.get("/conversations")
def list_conversations(
    cursor: str | None = None,
    limit: int = 20,
    payload: dict = Depends(require_auth),
):
    limit = max(1, min(limit, 100))
    anchor = _decode_cursor(cursor, scope=_CONVERSATION_LIST_SCOPE)
    where = ""
    params: list[object] = []
    if anchor is not None:
        where = "WHERE (created_at < ? OR (created_at = ? AND id < ?))"
        params.extend((anchor["createdAt"], anchor["createdAt"], anchor["id"]))
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT id, title, created_at FROM conversation {where} "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (*params, limit + 1),
        ).fetchall()
    page_rows = rows[:limit]
    return {
        "items": [
            {"id": r["id"], "title": r["title"] or "新会话", "createdAt": r["created_at"]}
            for r in page_rows
        ],
        "nextCursor": (
            _encode_cursor(
                scope=_CONVERSATION_LIST_SCOPE,
                created_at=page_rows[-1]["created_at"],
                item_id=page_rows[-1]["id"],
            )
            if len(rows) > limit
            else None
        ),
    }


@router.post("/conversations", status_code=201)
def create_conversation(
    body: ConversationCreate,
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    payload: dict = Depends(require_auth),
):
    from ..deps import require_csrf

    require_csrf(request, x_csrf_token)
    cid = random_id("conv_")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO conversation (id, title, created_at) VALUES (?, ?, ?)",
            (cid, (body.title or "新会话")[:120], iso(now_utc())),
        )
    audit("conversation_create", actor=payload["sub"], resource_type="conversation", resource_id=cid,
          request_id=get_request_id(request), result="ok")
    return {"id": cid, "title": body.title or "新会话", "createdAt": iso(now_utc())}


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: str,
    cursor: str | None = None,
    limit: int = 20,
    payload: dict = Depends(require_auth),
):
    limit = max(1, min(limit, 100))
    with get_conn() as conn:
        conv = conn.execute(
            "SELECT * FROM conversation WHERE id=?", (conversation_id,)
        ).fetchone()
        if conv is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "会话不存在"})
        anchor = _decode_cursor(
            cursor,
            scope=_CONVERSATION_MESSAGE_SCOPE,
            conversation_id=conversation_id,
        )
        where = "WHERE conversation_id=?"
        params: list[object] = [conversation_id]
        if anchor is not None:
            where += " AND (created_at < ? OR (created_at = ? AND id < ?))"
            params.extend((anchor["createdAt"], anchor["createdAt"], anchor["id"]))
        rows = conn.execute(
            f"SELECT * FROM message {where} ORDER BY created_at DESC, id DESC LIMIT ?",
            (*params, limit + 1),
        ).fetchall()
    page_rows = rows[:limit]
    messages = [
        {
            "id": r["id"],
            "role": r["role"],
            "content": r["content"],
            "citations": json.loads(r["citations"]) if r["citations"] else [],
            "createdAt": r["created_at"],
        }
        for r in reversed(page_rows)
    ]
    return {
        "id": conv["id"],
        "title": conv["title"] or "新会话",
        "createdAt": conv["created_at"],
        "messages": messages,
        "nextCursor": (
            _encode_cursor(
                scope=_CONVERSATION_MESSAGE_SCOPE,
                created_at=page_rows[-1]["created_at"],
                item_id=page_rows[-1]["id"],
                conversation_id=conversation_id,
            )
            if len(rows) > limit
            else None
        ),
    }


# ---------- 健康 ----------

@router.get("/health/live")
def health_live():
    return {"status": "ok", "time": iso(now_utc())}


@router.get("/health/ready")
def health_ready():
    checks = {"db": False, "storage": False}
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1")
        checks["db"] = True
    except Exception:
        pass
    try:
        checks["storage"] = bool(storage.healthcheck())
    except Exception:
        pass
    ready = all(checks.values())
    try:
        emit_ready_check(db=checks["db"], storage=checks["storage"], ready=ready)
    except Exception:
        # 探针和响应语义不能因日志输出问题而改变。
        pass
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )
