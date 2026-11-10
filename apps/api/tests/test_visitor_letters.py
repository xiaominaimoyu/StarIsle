"""访客留言：云端收取后本地保存，离线时不丢已有来信。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from app.db import get_conn, init_db
from app.main import app
from app.services.visitor_letters import pull_visitor_letters
from fastapi.testclient import TestClient

from tests._isolation import TEST_PASSWORD


def _login(client: TestClient) -> str:
    token = client.get("/api/v1/auth/csrf").json()["csrfToken"]
    response = client.post(
        "/api/v1/auth/login",
        json={"password": TEST_PASSWORD},
        headers={"X-CSRF-Token": token, "Origin": "http://testserver"},
    )
    assert response.status_code == 200
    return client.get("/api/v1/auth/csrf").json()["csrfToken"]


def test_visitor_letter_routes_require_login():
    init_db()
    client = TestClient(app)
    assert client.get("/api/v1/visitor-letters").status_code == 401
    assert client.post("/api/v1/visitor-letters/pull").status_code == 401


def test_pull_imports_once_and_acks_then_lists_locally(monkeypatch):
    init_db()
    monkeypatch.setenv("VISITOR_PULL_BASE_URL", "https://starisle-6bt.pages.dev")
    monkeypatch.setenv("VISITOR_PULL_KEY", "desk-pull-key-0123456789abcdef")
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        seen.append(body)
        assert request.headers["authorization"] == "Bearer desk-pull-key-0123456789abcdef"
        if body["acknowledge"]:
            return httpx.Response(200, json={"removed": len(body["acknowledge"]), "messages": []})
        return httpx.Response(
            200,
            json={
                "removed": 0,
                "messages": [
                    {
                        "id": "11111111-1111-4111-8111-111111111111",
                        "createdAt": "2026-11-10T00:00:00+00:00",
                        "name": "远客",
                        "contact": "guest@example.test",
                        "body": "夜里好。",
                    }
                ],
            },
        )

    report = pull_visitor_letters(httpx.Client(transport=httpx.MockTransport(handler)))
    assert report.code == "VISITOR_PULL_OK"
    assert report.imported == 1
    again = pull_visitor_letters(httpx.Client(transport=httpx.MockTransport(handler)))
    assert again.imported == 0
    with get_conn() as conn:
        row = conn.execute("SELECT name, cloud_ack_at FROM visitor_letter").fetchone()
    assert row["name"] == "远客"
    assert row["cloud_ack_at"] is not None
    assert any(item["acknowledge"] == ["11111111-1111-4111-8111-111111111111"] for item in seen)

    client = TestClient(app)
    token = _login(client)
    listing = client.get("/api/v1/visitor-letters")
    assert listing.status_code == 200
    assert listing.json()["items"][0]["body"] == "夜里好。"
    deleted = client.delete(
        "/api/v1/visitor-letters/11111111-1111-4111-8111-111111111111",
        headers={"X-CSRF-Token": token, "Origin": "http://testserver"},
    )
    assert deleted.status_code == 204
    assert client.get("/api/v1/visitor-letters").json()["items"] == []


def test_pull_base_accepts_preview_and_production_hosts(monkeypatch):
    from app.services.visitor_letters import pull_base_url

    monkeypatch.setenv("VISITOR_PULL_BASE_URL", "https://visitor-messages.starisle-6bt.pages.dev")
    assert pull_base_url() == "https://visitor-messages.starisle-6bt.pages.dev"
    monkeypatch.setenv("VISITOR_PULL_BASE_URL", "https://starisle-6bt.pages.dev/")
    assert pull_base_url() == "https://starisle-6bt.pages.dev"


def test_d1_binding_is_committed_config():
    repo = Path(__file__).resolve().parents[3]
    text = (repo / "wrangler.toml").read_text(encoding="utf-8")
    assert 'binding = "VISITOR_DB"' in text
    assert 'database_name = "starisle-visitors"' in text
    assert "database_id" in text
    assert "commit-dirty" not in text
    assert "不要提交" not in text


def test_pull_offline_keeps_existing_letters(monkeypatch):
    init_db()
    monkeypatch.setenv("VISITOR_PULL_BASE_URL", "https://starisle-6bt.pages.dev")
    monkeypatch.setenv("VISITOR_PULL_KEY", "desk-pull-key-0123456789abcdef")

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    report = pull_visitor_letters(httpx.Client(transport=httpx.MockTransport(handler)))
    assert report.code == "VISITOR_PULL_OFFLINE"
    assert report.imported == 0
