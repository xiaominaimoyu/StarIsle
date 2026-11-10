"""问屿。读状态要登录，索引和提问还要 CSRF。"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse

from ..db import audit
from ..deps import get_request_id, require_auth, require_csrf
from ..services.desk_ask import answer, index_pending

router = APIRouter(tags=["desk-ask"])


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.get("/desk-ask/status")
def desk_ask_status(payload: dict = Depends(require_auth)):
    del payload
    from ..services.desk_ask import service_status

    return service_status()


@router.post("/desk-ask/index")
async def desk_ask_index(
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    payload: dict = Depends(require_auth),
):
    require_csrf(request, x_csrf_token)
    mode = "continue"
    try:
        incoming: Any = await request.json()
    except Exception:
        incoming = {}
    if isinstance(incoming, dict) and incoming.get("mode") == "rebuild":
        mode = "rebuild"
    report = index_pending(force=mode == "rebuild")
    audit(
        "desk_index",
        actor=payload["sub"],
        resource_type="desk_index",
        request_id=get_request_id(request),
        result=str(report.get("code", "")),
    )
    return report


@router.post("/desk-ask/query")
async def desk_ask_query(
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    payload: dict = Depends(require_auth),
):
    require_csrf(request, x_csrf_token)
    try:
        incoming = await request.json()
    except Exception:
        incoming = {}
    query = incoming.get("query") if isinstance(incoming, dict) else ""
    if not isinstance(query, str):
        query = ""

    async def events():
        async for event in answer(query.strip()):
            yield _sse(event)
        audit(
            "desk_ask",
            actor=payload["sub"],
            resource_type="desk_ask",
            request_id=get_request_id(request),
            result="ok",
        )

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
