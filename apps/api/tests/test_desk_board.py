"""总览：待办迁移、登录保护，以及近况不显示已删除的内容。"""

from __future__ import annotations

from app.db import SCHEMA_VERSION, MIGRATION_V6, get_conn, init_db
from app.main import app
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


def _headers(token: str) -> dict[str, str]:
    return {"X-CSRF-Token": token, "Origin": "http://testserver"}


def test_v5_database_gains_todos_without_losing_them_on_the_next_start():
    init_db()
    with get_conn() as conn:
        conn.execute("DROP TABLE desk_todo")
        conn.execute("UPDATE schema_version SET version=5 WHERE id=1")
        conn.execute(
            """
            INSERT INTO desk_note (id, title, body_md, content_hash, created_at, updated_at)
            VALUES ('note-keep', '留下', '原文', 'abc', '2026-11-01T00:00:00+00:00', '2026-11-01T00:00:00+00:00')
            """
        )
    init_db()
    with get_conn() as conn:
        assert conn.execute("SELECT version FROM schema_version WHERE id=1").fetchone()[0] == SCHEMA_VERSION == 6
        assert conn.execute("SELECT title FROM desk_note WHERE id='note-keep'").fetchone()["title"] == "留下"
        conn.execute(
            """
            INSERT INTO desk_todo (id, text, due_on, done_at, created_at, updated_at)
            VALUES ('todo-keep', '还在', NULL, NULL, '2026-11-02T00:00:00+00:00', '2026-11-02T00:00:00+00:00')
            """
        )
    init_db()
    with get_conn() as conn:
        assert conn.execute("SELECT text FROM desk_todo WHERE id='todo-keep'").fetchone()["text"] == "还在"
        for statement in MIGRATION_V6:
            conn.execute(statement)
        assert conn.execute("SELECT COUNT(*) FROM desk_todo").fetchone()[0] == 1


def test_todo_crud_requires_login_and_sorts_open_work_first():
    init_db()
    client = TestClient(app)
    assert client.get("/api/v1/desk-todos").status_code == 401
    assert client.get("/api/v1/desk-home").status_code == 401
    token = _login(client)
    missing = client.post(
        "/api/v1/desk-todos",
        json={"text": "没有令牌"},
        headers={"Origin": "http://testserver"},
    )
    assert missing.status_code == 403
    later = client.post(
        "/api/v1/desk-todos",
        json={"text": "后做", "dueOn": "2026-12-01"},
        headers=_headers(token),
    )
    sooner = client.post(
        "/api/v1/desk-todos",
        json={"text": "先做", "dueOn": "2026-11-12"},
        headers=_headers(token),
    )
    undated = client.post("/api/v1/desk-todos", json={"text": "无期"}, headers=_headers(token))
    assert later.status_code == sooner.status_code == undated.status_code == 201
    done = client.patch(
        f"/api/v1/desk-todos/{later.json()['id']}",
        json={"done": True, "text": "后做完了"},
        headers=_headers(token),
    )
    assert done.status_code == 200
    assert done.json()["done"] is True
    order = [
        item["text"]
        for item in client.get("/api/v1/desk-todos").json()["items"]
        if item["text"] in {"先做", "无期", "后做完了"}
    ]
    assert order == ["先做", "无期", "后做完了"]
    page = client.get("/private/")
    assert page.status_code == 200
    assert "总览" in page.text
    assert client.delete(f"/api/v1/desk-todos/{sooner.json()['id']}", headers=_headers(token)).status_code == 204
    assert sooner.json()["id"] not in [item["id"] for item in client.get("/api/v1/desk-todos").json()["items"]]


def test_activity_skips_deleted_notes_letters_and_counts_the_rest():
    init_db()
    client = TestClient(app)
    token = _login(client)
    live = client.post(
        "/api/v1/notes",
        json={"title": "还在", "body": "留下的手记", "tags": []},
        headers=_headers(token),
    )
    gone = client.post(
        "/api/v1/notes",
        json={"title": "删掉", "body": "不该出现", "tags": []},
        headers=_headers(token),
    )
    assert client.delete(f"/api/v1/notes/{gone.json()['id']}", headers=_headers(token)).status_code == 204
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO visitor_letter
              (id, received_at, imported_at, name, contact, body, cloud_ack_at)
            VALUES ('letter-live', '2099-01-01T00:00:00+00:00', '2099-01-01T00:00:00+00:00',
                    '远客', 'a@example.test', '还在的信', '2099-01-01T00:00:00+00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO visitor_letter
              (id, received_at, imported_at, name, contact, body, deleted_at, cloud_ack_at)
            VALUES ('letter-gone', '2099-01-02T00:00:00+00:00', '2099-01-02T00:00:00+00:00',
                    '旧客', 'b@example.test', '删掉的信', '2099-01-02T00:00:00+00:00',
                    '2099-01-02T00:00:00+00:00')
            """
        )
    home = client.get("/api/v1/desk-home")
    assert home.status_code == 200
    body = home.json()
    assert body["counts"]["notes"] >= 1
    assert body["counts"]["unreadMessages"] >= 1
    assert body["index"]["ready"] >= 0
    assert body["index"]["pending"] >= 0
    ids = [item["id"] for item in body["activity"]]
    assert live.json()["id"] in ids
    assert gone.json()["id"] not in ids
    assert "letter-live" in ids
    assert "letter-gone" not in ids
    assert all(item["href"].startswith("/private/") for item in body["activity"])
    with get_conn() as conn:
        conn.execute("DELETE FROM visitor_letter WHERE id IN ('letter-live', 'letter-gone')")
