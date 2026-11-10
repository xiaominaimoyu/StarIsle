"""书桌里的来信：收取、阅读、归档和本地删除。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

from ..db import audit, get_conn
from ..deps import get_request_id, require_auth
from ..security import iso, now_utc
from ..services.visitor_letters import pull_visitor_letters

router = APIRouter(tags=["visitor-letters"])


def _summary(row) -> dict:
    return {
        "id": row["id"],
        "receivedAt": row["received_at"],
        "name": row["name"],
        "contact": row["contact"],
        "body": row["body"],
        "read": row["read_at"] is not None,
        "archived": row["archived_at"] is not None,
    }


@router.get("/visitor-letters")
def list_letters(view: str = "inbox", payload: dict = Depends(require_auth)):
    del payload
    if view not in {"inbox", "archived"}:
        raise HTTPException(422, detail={"code": "UNPROCESSABLE", "message": "请求参数无效。"})
    where = (
        "deleted_at IS NULL AND archived_at IS NOT NULL"
        if view == "archived"
        else "deleted_at IS NULL AND archived_at IS NULL"
    )
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM visitor_letter WHERE {where} ORDER BY received_at DESC, id DESC"
        ).fetchall()
    return {"items": [_summary(row) for row in rows]}


@router.post("/visitor-letters/pull")
def pull_letters(
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    payload: dict = Depends(require_auth),
):
    from ..deps import require_csrf

    require_csrf(request, x_csrf_token)
    report = pull_visitor_letters()
    audit(
        "visitor_pull",
        actor=payload["sub"],
        resource_type="visitor_letter",
        request_id=get_request_id(request),
        result=report.code,
    )
    return {"code": report.code, "imported": report.imported}


_TOUCH_SQL = {
    "read_at": "UPDATE visitor_letter SET read_at=? WHERE id=? AND read_at IS NULL",
    "archived_at": "UPDATE visitor_letter SET archived_at=? WHERE id=? AND archived_at IS NULL",
    "deleted_at": "UPDATE visitor_letter SET deleted_at=? WHERE id=? AND deleted_at IS NULL",
}


def _touch(letter_id: str, column: str, payload: dict, request: Request, action: str) -> None:
    from ..deps import require_csrf

    require_csrf(request, request.headers.get("x-csrf-token"))
    moment = iso(now_utc())
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM visitor_letter WHERE id=? AND deleted_at IS NULL",
            (letter_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "来信不存在"})
        conn.execute(_TOUCH_SQL[column], (moment, letter_id))
    audit(
        action,
        actor=payload["sub"],
        resource_type="visitor_letter",
        resource_id=letter_id,
        request_id=get_request_id(request),
        result="ok",
    )


@router.post("/visitor-letters/{letter_id}/read", status_code=204)
def mark_read(
    letter_id: str,
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    payload: dict = Depends(require_auth),
):
    del x_csrf_token
    _touch(letter_id, "read_at", payload, request, "visitor_read")
    return Response(status_code=204)


@router.post("/visitor-letters/{letter_id}/archive", status_code=204)
def archive_letter(
    letter_id: str,
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    payload: dict = Depends(require_auth),
):
    del x_csrf_token
    _touch(letter_id, "archived_at", payload, request, "visitor_archive")
    return Response(status_code=204)


@router.delete("/visitor-letters/{letter_id}", status_code=204)
def delete_letter(
    letter_id: str,
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    payload: dict = Depends(require_auth),
):
    del x_csrf_token
    _touch(letter_id, "deleted_at", payload, request, "visitor_delete")
    return Response(status_code=204)
