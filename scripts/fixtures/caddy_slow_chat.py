"""Synthetic chat upstream for caddy_sse_e2e.py; never part of a production stack."""

from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

app = FastAPI(docs_url=None, openapi_url=None)
MODES = (
    "heartbeat-complete", "cancel-browser", "navigate-browser", "close-http",
    "cancel-before-token", "partial-error", "normal-after-cancel",
)
stats = {
    mode: {"opened": 0, "active": 0, "completed": 0, "interrupted": 0, "incomplete": 0}
    for mode in MODES
}


@app.get("/status")
async def status():
    # Fixed scenario counters only: no prompt, token, client or credential logging.
    return stats


def token(text: str) -> str:
    return "data: " + json.dumps({"choices": [{"delta": {"content": text}}]}) + "\n\n"


@app.post("/v1/chat/completions")
async def completions(body: dict):
    messages = body.get("messages", [])
    mode = next((m.get("content") for m in messages if m.get("role") == "user"), None)
    if mode not in stats or body.get("stream") is not True:
        raise HTTPException(422, "Unknown synthetic scenario")

    async def stream():
        state = stats[mode]
        state["opened"] += 1
        state["active"] += 1
        finished = False
        try:
            if mode != "cancel-before-token":
                yield token(f"fixture:{mode}:partial")
            if mode == "heartbeat-complete":
                # Preserve the application's real 10-second heartbeat interval.
                await asyncio.sleep(23)
            elif mode in ("cancel-browser", "navigate-browser", "close-http", "cancel-before-token"):
                await asyncio.sleep(60)
            elif mode == "partial-error":
                state["incomplete"] += 1
                finished = True
                return  # Deliberately omit [DONE] after a real partial token.
            yield token(":complete")
            state["completed"] += 1
            finished = True
            yield "data: [DONE]\n\n"
        finally:
            state["active"] -= 1
            if not finished:
                state["interrupted"] += 1

    return StreamingResponse(stream(), media_type="text/event-stream")
