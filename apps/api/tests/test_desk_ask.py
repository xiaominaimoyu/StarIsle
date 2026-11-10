"""问屿：假的本机嵌入和生成服务，增量索引，以及模型不在线时的提示。"""

from __future__ import annotations

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from app.config import settings
from app.db import SCHEMA_VERSION, get_conn, init_db
from app.main import app
from fastapi.testclient import TestClient
from PIL import Image

from tests._isolation import TEST_PASSWORD


class _ModelState:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.fail_embed = False
        self.chat_calls = 0
        self.chat_body: dict | None = None


def _vector(text: str, dim: int = 384) -> list[float]:
    values = [0.0] * dim
    if "星屿" in text:
        values[0] = 1.0
    elif "来信" in text:
        values[1] = 1.0
    else:
        values[2] = 1.0
    return values


def _handler(state: _ModelState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args) -> None:
            return

        def _send(self, payload: bytes, status: int = 200, content_type: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            if self.path.startswith("/v1/models"):
                self._send(b'{"data":[]}')
                return
            self._send(b"{}", status=404)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode() or "{}")
            if self.path.startswith("/v1/embeddings"):
                if state.fail_embed:
                    self._send(b"{}", status=503)
                    return
                texts = body.get("input") if isinstance(body.get("input"), list) else []
                state.texts.extend(str(text) for text in texts)
                data = [
                    {"index": index, "embedding": _vector(str(text))}
                    for index, text in enumerate(texts)
                ]
                self._send(json.dumps({"model": body.get("model"), "data": data}).encode())
                return
            if self.path.startswith("/v1/chat/completions"):
                state.chat_calls += 1
                state.chat_body = body if isinstance(body, dict) else None
                raw = (
                    'data: {"choices":[{"delta":{"reasoning_content":"秘密推理"}}]}\n\n'
                    'data: {"choices":[{"delta":{"content":"<think>内部</think>在手记里。"}}]}\n\n'
                    "data: [DONE]\n\n"
                ).encode()
                self._send(raw, content_type="text/event-stream")
                return
            self._send(b"{}", status=404)

    return Handler


@pytest.fixture
def models(monkeypatch: pytest.MonkeyPatch):
    state = _ModelState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setattr(settings, "llama_embed_base_url", base)
    monkeypatch.setattr(settings, "llama_chat_base_url", base)
    monkeypatch.setattr(settings, "embedding_model_id", "")
    monkeypatch.setattr(settings, "chat_model_id", "")
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


def _login(client: TestClient) -> str:
    token = client.get("/api/v1/auth/csrf").json()["csrfToken"]
    response = client.post(
        "/api/v1/auth/login",
        json={"password": TEST_PASSWORD},
        headers={"X-CSRF-Token": token, "Origin": "http://testserver"},
    )
    assert response.status_code == 200
    return client.get("/api/v1/auth/csrf").json()["csrfToken"]


def _headers(token: str) -> dict[str, str]:
    return {"X-CSRF-Token": token, "Origin": "http://testserver"}


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (20, 80, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


def _events(client: TestClient, token: str, query: str) -> str:
    response = client.post(
        "/api/v1/desk-ask/query",
        json={"query": query},
        headers=_headers(token),
    )
    assert response.status_code == 200
    return response.text


def test_desk_ask_requires_login_and_refuses_outside_urls(monkeypatch: pytest.MonkeyPatch):
    init_db()
    client = TestClient(app)
    assert client.get("/api/v1/desk-ask/status").status_code == 401
    assert client.post("/api/v1/desk-ask/index", json={"mode": "continue"}).status_code == 401
    token = _login(client)
    opened: list[str] = []
    real_client = httpx.Client

    class _Spy(real_client):
        def request(self, method, url, **kwargs):
            opened.append(str(url))
            return super().request(method, url, **kwargs)

    monkeypatch.setattr("app.services.desk_ask.httpx.Client", _Spy)
    monkeypatch.setattr(settings, "llama_embed_base_url", "https://outside.invalid")
    refused = client.post("/api/v1/desk-ask/index", json={"mode": "continue"}, headers=_headers(token))
    assert refused.status_code == 200
    assert refused.json()["code"] == "URL_REFUSED"
    assert all("outside.invalid" not in url for url in opened)


def test_incremental_index_citations_and_offline_models(models: _ModelState):
    init_db()
    client = TestClient(app)
    token = _login(client)
    uploaded = client.post(
        "/api/v1/photos",
        files={"file": ("shore.png", io.BytesIO(_png()), "image/png")},
        headers=_headers(token),
    )
    assert uploaded.status_code == 201
    photo_id = uploaded.json()["id"]
    created = client.post(
        "/api/v1/notes",
        json={"title": "岸边", "body": f"今天在星屿写手记\n\n![](photo:{photo_id})", "tags": ["星屿"]},
        headers=_headers(token),
    )
    assert created.status_code == 201
    note_id = created.json()["id"]
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO visitor_letter
              (id, received_at, imported_at, name, contact, body, cloud_ack_at)
            VALUES ('letter-ask', '2026-11-10T00:00:00+00:00', '2026-11-10T00:00:00+00:00',
                    '远客', 'guest@example.test', '来信里没有那个地名', '2026-11-10T00:00:00+00:00')
            """
        )
    first = client.post("/api/v1/desk-ask/index", json={"mode": "continue"}, headers=_headers(token))
    assert first.status_code == 200
    assert first.json()["code"] == "INDEX_OK"
    assert first.json()["pending"] == 0
    assert first.json()["ready"] >= 3
    answer = _events(client, token, "星屿在哪里")
    assert "在手记里" in answer
    assert "秘密推理" not in answer
    assert "内部" not in answer
    assert "<think>" not in answer
    assert note_id in answer
    assert "/private/notes?note=" in answer
    assert models.chat_calls == 1
    assert models.chat_body is not None
    assert models.chat_body.get("chat_template_kwargs") == {"enable_thinking": False}

    changed = client.patch(
        f"/api/v1/notes/{note_id}",
        json={"body": "今天在星屿又写了一句", "baseHash": created.json()["contentHash"]},
        headers=_headers(token),
    )
    assert changed.status_code == 200
    before = len(models.texts)
    second = client.post("/api/v1/desk-ask/index", json={"mode": "continue"}, headers=_headers(token))
    assert second.json()["code"] == "INDEX_OK"
    again = models.texts[before:]
    assert again
    assert all("来信里没有那个地名" not in text for text in again)

    models.fail_embed = True
    extra = client.post(
        "/api/v1/notes",
        json={"title": "稍后", "body": "星屿还没索引", "tags": []},
        headers=_headers(token),
    )
    paused = client.post("/api/v1/desk-ask/index", json={"mode": "continue"}, headers=_headers(token))
    assert paused.json()["code"] == "EMBED_OFFLINE"
    assert paused.json()["pending"] >= 1
    with get_conn() as conn:
        kept = conn.execute(
            "SELECT status FROM desk_index_item WHERE source_kind='note' AND source_id=?",
            (note_id,),
        ).fetchone()
    assert kept["status"] == "ready"
    models.fail_embed = False
    resumed = client.post("/api/v1/desk-ask/index", json={"mode": "continue"}, headers=_headers(token))
    assert resumed.json()["code"] == "INDEX_OK"
    assert resumed.json()["pending"] == 0

    removed = client.delete(f"/api/v1/notes/{note_id}", headers=_headers(token))
    assert removed.status_code == 204
    extra_id = extra.json()["id"]
    assert client.delete(f"/api/v1/notes/{extra_id}", headers=_headers(token)).status_code == 204
    with get_conn() as conn:
        conn.execute("UPDATE visitor_letter SET deleted_at=imported_at WHERE id='letter-ask'")
    assert client.delete(f"/api/v1/photos/{photo_id}", headers=_headers(token)).status_code == 204
    cleaned = client.post("/api/v1/desk-ask/index", json={"mode": "continue"}, headers=_headers(token))
    assert cleaned.json()["code"] == "INDEX_OK"
    forgotten = _events(client, token, "星屿在哪里")
    assert "没有找到可以依据的内容" in forgotten
    with get_conn() as conn:
        version = conn.execute("SELECT version FROM schema_version WHERE id=1").fetchone()[0]
        leftover = conn.execute(
            "SELECT COUNT(*) FROM desk_index_item WHERE source_id IN (?, ?, ?, 'letter-ask')",
            (note_id, extra_id, photo_id),
        ).fetchone()[0]
    assert version == SCHEMA_VERSION == 6
    assert leftover == 0


def test_chat_offline_still_returns_sources(models: _ModelState, monkeypatch: pytest.MonkeyPatch):
    init_db()
    client = TestClient(app)
    token = _login(client)
    created = client.post(
        "/api/v1/notes",
        json={"title": "岸边", "body": "星屿的灯还亮着", "tags": []},
        headers=_headers(token),
    )
    assert created.status_code == 201
    indexed = client.post("/api/v1/desk-ask/index", json={"mode": "continue"}, headers=_headers(token))
    assert indexed.json()["code"] == "INDEX_OK"
    monkeypatch.setattr(settings, "llama_chat_base_url", "http://127.0.0.1:9")
    answer = _events(client, token, "星屿的灯")
    assert "生成服务没开" in answer
    assert "Stop-Qwen" in answer
    assert created.json()["id"] in answer
    assert models.chat_calls == 0
    page = client.get("/private/ask")
    assert page.status_code == 200
    assert "问屿" in page.text
    assert client.delete(f"/api/v1/notes/{created.json()['id']}", headers=_headers(token)).status_code == 204
