"""手记和日记。读和写都要登录，改动还要 CSRF。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

from ..db import audit
from ..deps import get_request_id, require_auth, require_csrf
from ..services.desk_notes import (
    DeskNoteError,
    create_note,
    delete_note,
    diary_month,
    list_notes,
    open_diary,
    parse_body,
    parse_tags,
    parse_title,
    read_note,
    render_markdown,
    update_note,
)

router = APIRouter(tags=["desk-notes"])


def _raise(error: DeskNoteError) -> None:
    detail: dict[str, str] = {"code": error.code, "message": error.message}
    if error.note_id:
        detail["noteId"] = error.note_id
    raise HTTPException(error.status, detail=detail)


async def _json(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(422, detail={"code": "UNPROCESSABLE", "message": "请求参数无效。"}) from exc
    if not isinstance(payload, dict):
        raise HTTPException(422, detail={"code": "UNPROCESSABLE", "message": "请求参数无效。"})
    return payload


def _fields(payload: dict[str, Any], *, title_default: str = "") -> tuple[str, str, list[str]]:
    try:
        title = parse_title(payload.get("title"), default=title_default)
        body = parse_body(payload.get("body"))
        tags = parse_tags(payload.get("tags"))
    except DeskNoteError as error:
        _raise(error)
    return title, body, tags


@router.get("/notes")
def notes_list(
    q: str = "",
    tag: str = "",
    kind: str = "",
    payload: dict = Depends(require_auth),
):
    del payload
    try:
        items = list_notes(query=q, tag=tag, kind=kind)
    except DeskNoteError as error:
        _raise(error)
    return {"items": items}


@router.get("/notes/diary")
def notes_diary(month: str, payload: dict = Depends(require_auth)):
    del payload
    try:
        return diary_month(month)
    except DeskNoteError as error:
        _raise(error)


@router.put("/notes/diary/{day}")
def notes_open_diary(
    day: str,
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    payload: dict = Depends(require_auth),
):
    require_csrf(request, x_csrf_token)
    try:
        note = open_diary(day)
    except DeskNoteError as error:
        _raise(error)
    audit(
        "note_open_diary",
        actor=payload["sub"],
        resource_type="desk_note",
        resource_id=note["id"],
        request_id=get_request_id(request),
        result="ok",
    )
    return note


@router.post("/notes/preview")
async def notes_preview(
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    payload: dict = Depends(require_auth),
):
    del payload
    require_csrf(request, x_csrf_token)
    body = await _json(request)
    try:
        source = parse_body(body.get("body"))
    except DeskNoteError as error:
        _raise(error)
    return {"html": render_markdown(source)}


@router.post("/notes", status_code=201)
async def notes_create(
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    payload: dict = Depends(require_auth),
):
    require_csrf(request, x_csrf_token)
    incoming = await _json(request)
    diary_on = incoming.get("diaryOn")
    if diary_on is not None and not isinstance(diary_on, str):
        raise HTTPException(422, detail={"code": "UNPROCESSABLE", "message": "日期无效。"})
    title, body, tags = _fields(incoming, title_default=diary_on or "")
    try:
        if isinstance(diary_on, str):
            from ..services.desk_notes import parse_day

            diary_on = parse_day(diary_on)
        note = create_note(title=title, body=body, tags=tags, diary_on=diary_on)
    except DeskNoteError as error:
        _raise(error)
    audit(
        "note_create",
        actor=payload["sub"],
        resource_type="desk_note",
        resource_id=note["id"],
        request_id=get_request_id(request),
        result="ok",
    )
    return note


@router.get("/notes/{note_id}")
def notes_read(note_id: str, payload: dict = Depends(require_auth)):
    del payload
    try:
        return read_note(note_id)
    except DeskNoteError as error:
        _raise(error)


@router.patch("/notes/{note_id}")
async def notes_update(
    note_id: str,
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    payload: dict = Depends(require_auth),
):
    require_csrf(request, x_csrf_token)
    incoming = await _json(request)
    current = None
    try:
        current = read_note(note_id)
        title = parse_title(incoming.get("title"), default=current["title"]) if "title" in incoming else current["title"]
        body = parse_body(incoming.get("body")) if "body" in incoming else current["body"]
        tags = parse_tags(incoming.get("tags")) if "tags" in incoming else current["tags"]
        note = update_note(note_id, title=title, body=body, tags=tags)
    except DeskNoteError as error:
        _raise(error)
    audit(
        "note_update",
        actor=payload["sub"],
        resource_type="desk_note",
        resource_id=note_id,
        request_id=get_request_id(request),
        result="ok",
    )
    return note


@router.delete("/notes/{note_id}", status_code=204)
def notes_delete(
    note_id: str,
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    payload: dict = Depends(require_auth),
):
    require_csrf(request, x_csrf_token)
    try:
        delete_note(note_id)
    except DeskNoteError as error:
        _raise(error)
    audit(
        "note_delete",
        actor=payload["sub"],
        resource_type="desk_note",
        resource_id=note_id,
        request_id=get_request_id(request),
        result="ok",
    )
    return Response(status_code=204)
