"""手记：登录、增删改、中文检索、消毒和相册引用。"""

from __future__ import annotations

import io
import sqlite3
from contextlib import closing

from app.config import settings
from app.daily_backup import snapshot_database
from app.db import SCHEMA_VERSION, get_conn, init_db
from app.main import app
from app.services.desk_notes import content_hash, render_markdown
from fastapi.testclient import TestClient
from PIL import Image

from tests._isolation import TEST_PASSWORD


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (180, 70, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


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


def test_note_routes_require_login_and_csrf():
    init_db()
    client = TestClient(app)
    assert client.get("/api/v1/notes").status_code == 401
    assert client.post("/api/v1/notes", json={"title": "夜"}).status_code == 401
    assert client.get("/private/notes", follow_redirects=False).status_code == 302
    token = _login(client)
    page = client.get("/private/notes")
    assert page.status_code == 200
    assert "手记" in page.text
    assert "/private/assets/notes.js" in page.text
    missing = client.post(
        "/api/v1/notes",
        json={"title": "夜", "body": "夜里", "tags": []},
        headers={"Origin": "http://testserver"},
    )
    assert missing.status_code == 403
    created = client.post(
        "/api/v1/notes",
        json={"title": "夜", "body": "夜里", "tags": ["随笔"]},
        headers=_headers(token),
    )
    assert created.status_code == 201
    assert created.json()["contentHash"] == content_hash("夜", "夜里", ["随笔"])


def test_markdown_preview_strips_active_html():
    html = render_markdown(
        "\n".join(
            [
                "# 标题",
                "",
                "<script>alert(1)</script>",
                '<img src="x" onerror="alert(1)">',
                "![岸](javascript:alert(1))",
                "![岸](https://evil.test/x.png)",
                "今天**星屿**",
            ]
        )
    )
    assert "<h1>" in html
    assert "<script" not in html
    assert "<img" not in html
    assert 'href="javascript:' not in html
    assert 'href="https://evil.test' not in html
    assert 'src="https://evil.test' not in html
    assert "<strong>星屿</strong>" in html
    assert "alert(1)" in html


def test_note_crud_search_diary_and_photo_reference(tmp_path):
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
    body = f"今天在星屿写手记\n\n![岸](photo:{photo_id})"
    created = client.post(
        "/api/v1/notes",
        json={"title": "岸边", "body": body, "tags": ["星屿", "手记"]},
        headers=_headers(token),
    )
    assert created.status_code == 201
    note = created.json()
    assert f"/api/v1/photos/{photo_id}/original" in note["html"]
    assert note["photoIds"] == [photo_id]
    assert "<img" in note["html"]
    anon = TestClient(app)
    assert anon.get(f"/api/v1/photos/{photo_id}/original").status_code == 401
    assert client.get(f"/api/v1/photos/{photo_id}/original").status_code == 200

    missing = client.post(
        "/api/v1/notes",
        json={"title": "缺图", "body": "![岸](photo:photo_missing_id)", "tags": []},
        headers=_headers(token),
    )
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "PHOTO_NOT_FOUND"

    other = client.post(
        "/api/v1/notes",
        json={"title": "另一篇", "body": "只写了别的事情", "tags": []},
        headers=_headers(token),
    )
    assert other.status_code == 201
    found = client.get("/api/v1/notes", params={"q": "星屿"})
    assert found.status_code == 200
    found_ids = [item["id"] for item in found.json()["items"]]
    assert note["id"] in found_ids
    assert other.json()["id"] not in found_ids
    hand_ids = [item["id"] for item in client.get("/api/v1/notes", params={"q": "手记"}).json()["items"]]
    assert note["id"] in hand_ids
    assert other.json()["id"] not in hand_ids
    assert client.get("/api/v1/notes", params={"q": "不存在"}).json()["items"] == []

    changed = client.patch(
        f"/api/v1/notes/{note['id']}",
        json={"body": body + "\n又写了一句", "baseHash": note["contentHash"]},
        headers=_headers(token),
    )
    assert changed.status_code == 200
    assert changed.json()["contentHash"] != note["contentHash"]
    assert changed.json()["updatedAt"] >= note["updatedAt"]
    stale = client.patch(
        f"/api/v1/notes/{note['id']}",
        json={"body": "旧稿覆盖", "baseHash": note["contentHash"]},
        headers=_headers(token),
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "STALE_NOTE"
    assert client.get(f"/api/v1/notes/{note['id']}").json()["body"] == body + "\n又写了一句"
    missing_version = client.patch(
        f"/api/v1/notes/{note['id']}",
        json={"body": "没有版本"},
        headers=_headers(token),
    )
    assert missing_version.status_code == 422

    first = client.put("/api/v1/notes/diary/2026-11-10", headers=_headers(token))
    again = client.put("/api/v1/notes/diary/2026-11-10", headers=_headers(token))
    second = client.put("/api/v1/notes/diary/2026-11-11", headers=_headers(token))
    assert first.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert second.json()["id"] != first.json()["id"]
    month = client.get("/api/v1/notes/diary", params={"month": "2026-11"})
    assert {day["date"] for day in month.json()["days"]} == {"2026-11-10", "2026-11-11"}
    assert client.get("/api/v1/notes/diary", params={"month": "2026-12"}).json()["days"] == []

    removed = client.delete(f"/api/v1/notes/{note['id']}", headers=_headers(token))
    assert removed.status_code == 204
    assert client.get(f"/api/v1/notes/{note['id']}").status_code == 404
    assert note["id"] not in [item["id"] for item in client.get("/api/v1/notes", params={"q": "星屿"}).json()["items"]]
    reopened = client.put("/api/v1/notes/diary/2026-11-10", headers=_headers(token))
    assert reopened.json()["id"] == first.json()["id"]
    assert client.delete(f"/api/v1/notes/{first.json()['id']}", headers=_headers(token)).status_code == 204
    replacement = client.put("/api/v1/notes/diary/2026-11-10", headers=_headers(token))
    assert replacement.json()["id"] != first.json()["id"]

    with get_conn() as conn:
        version = conn.execute("SELECT version FROM schema_version WHERE id=1").fetchone()[0]
        stored = conn.execute("SELECT content_hash, updated_at, deleted_at FROM desk_note").fetchall()
    assert version == SCHEMA_VERSION == 5
    assert stored

    snapshot = snapshot_database(settings.db_path, tmp_path / "backup")
    with closing(sqlite3.connect(snapshot)) as copied:
        copied.row_factory = sqlite3.Row
        assert copied.execute("SELECT COUNT(*) FROM desk_note").fetchone()[0] >= 1
        assert copied.execute("SELECT name FROM sqlite_master WHERE name='desk_note_fts'").fetchone()
