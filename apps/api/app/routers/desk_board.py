"""总览和待办。读取要登录，改动还要 CSRF。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

from ..db import audit
from ..deps import get_request_id, require_auth, require_csrf
from ..services.desk_board import (
    DeskBoardError,
    create_todo,
    delete_todo,
    home_summary,
    list_todos,
    parse_due,
    parse_text,
    update_todo,
)

router = APIRouter(tags=["desk-board"])


def _raise(error: DeskBoardError) -> None:
    raise HTTPException(error.status, detail={"code": error.code, "message": error.message})


async def _json(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(422, detail={"code": "UNPROCESSABLE", "message": "请求参数无效。"}) from exc
    if not isinstance(payload, dict):
        raise HTTPException(422, detail={"code": "UNPROCESSABLE", "message": "请求参数无效。"})
    return payload


@router.get("/desk-home")
def desk_home(payload: dict = Depends(require_auth)):
    del payload
    return home_summary()


@router.get("/desk-todos")
def todos_list(payload: dict = Depends(require_auth)):
    del payload
    return {"items": list_todos()}


@router.post("/desk-todos", status_code=201)
async def todos_create(
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    payload: dict = Depends(require_auth),
):
    require_csrf(request, x_csrf_token)
    incoming = await _json(request)
    try:
        text = parse_text(incoming.get("text"))
        due_on = parse_due(incoming.get("dueOn"))
        todo = create_todo(text=text, due_on=due_on)
    except DeskBoardError as error:
        _raise(error)
    audit(
        "todo_create",
        actor=payload["sub"],
        resource_type="desk_todo",
        resource_id=todo["id"],
        request_id=get_request_id(request),
        result="ok",
    )
    return todo


@router.patch("/desk-todos/{todo_id}")
async def todos_update(
    todo_id: str,
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    payload: dict = Depends(require_auth),
):
    require_csrf(request, x_csrf_token)
    incoming = await _json(request)
    done = incoming.get("done") if "done" in incoming else None
    if done is not None and not isinstance(done, bool):
        raise HTTPException(422, detail={"code": "UNPROCESSABLE", "message": "完成状态无效。"})
    try:
        text = parse_text(incoming.get("text")) if "text" in incoming else None
        due_on = parse_due(incoming.get("dueOn")) if "dueOn" in incoming else None
        todo = update_todo(todo_id, text=text, due_on=due_on, due_set="dueOn" in incoming, done=done)
    except DeskBoardError as error:
        _raise(error)
    audit(
        "todo_update",
        actor=payload["sub"],
        resource_type="desk_todo",
        resource_id=todo_id,
        request_id=get_request_id(request),
        result="ok",
    )
    return todo


@router.delete("/desk-todos/{todo_id}", status_code=204)
def todos_delete(
    todo_id: str,
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    payload: dict = Depends(require_auth),
):
    require_csrf(request, x_csrf_token)
    try:
        delete_todo(todo_id)
    except DeskBoardError as error:
        _raise(error)
    audit(
        "todo_delete",
        actor=payload["sub"],
        resource_type="desk_todo",
        resource_id=todo_id,
        request_id=get_request_id(request),
        result="ok",
    )
    return Response(status_code=204)
