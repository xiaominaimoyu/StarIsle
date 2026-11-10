"""会话与消息游标分页的隔离 API 回归。"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import SCHEMA_VERSION, get_conn, init_db
from app.main import app


TEST_PASSWORD = "test-password-12345"
LIST_SCOPE = "conversation-list"
MESSAGE_SCOPE = "conversation-messages"


def _set_data_root(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    objects_root = root / "objects"
    monkeypatch.setattr(settings, "data_root", root)
    monkeypatch.setattr(settings, "db_path", root / "db" / "star-isle.db")
    monkeypatch.setattr(settings, "objects_root", objects_root)
    monkeypatch.setattr(settings, "originals_dir", objects_root / "originals")
    monkeypatch.setattr(settings, "thumbnails_dir", objects_root / "thumbnails")
    monkeypatch.setattr(settings, "mail_attachments_dir", objects_root / "mail-attachments")
    monkeypatch.setattr(settings, "mail_raw_dir", objects_root / "mail-raw")


def _login(client: TestClient) -> None:
    csrf = client.get("/api/v1/auth/csrf")
    assert csrf.status_code == 200
    response = client.post(
        "/api/v1/auth/login",
        json={"password": TEST_PASSWORD},
        headers={"X-CSRF-Token": csrf.json()["csrfToken"], "Origin": "http://testserver"},
    )
    assert response.status_code == 200


@pytest.fixture
def conversation_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Each test owns a tmp_path data root and never reads the repository data root."""

    _set_data_root(monkeypatch, tmp_path / "conversation-pagination-data")
    init_db()
    with TestClient(app) as client:
        _login(client)
        yield client


def _cursor(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _cursor_url(cursor: str) -> str:
    return quote(cursor, safe="")


def _insert_conversations(records: list[tuple[str, str, str]]) -> None:
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO conversation (id, title, created_at) VALUES (?, ?, ?)",
            records,
        )


def _list_all(client: TestClient, *, limit: int) -> list[str]:
    cursor: str | None = None
    ids: list[str] = []
    pages = 0
    while True:
        path = f"/api/v1/conversations?limit={limit}"
        if cursor is not None:
            path += f"&cursor={_cursor_url(cursor)}"
        response = client.get(path)
        assert response.status_code == 200
        page = response.json()
        page_ids = [item["id"] for item in page["items"]]
        assert len(page_ids) <= limit
        ids.extend(page_ids)
        pages += 1
        assert pages <= 200
        cursor = page["nextCursor"]
        if cursor is None:
            return ids


def _assert_invalid_cursor(response) -> None:
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "UNPROCESSABLE"
    assert body["error"]["message"] == "游标无效。"


def test_conversation_list_keyset_pages_default_and_clamp(conversation_client: TestClient):
    timestamp = "2026-09-04T12:00:00+00:00"
    records = [(f"conv-{index:03d}", f"会话 {index}", timestamp) for index in range(105)]
    _insert_conversations(records)
    expected = sorted((record[0] for record in records), reverse=True)

    default_page = conversation_client.get("/api/v1/conversations")
    assert default_page.status_code == 200
    assert len(default_page.json()["items"]) == 20
    assert default_page.json()["nextCursor"] is not None

    lower_clamped = conversation_client.get("/api/v1/conversations?limit=0")
    assert lower_clamped.status_code == 200
    assert len(lower_clamped.json()["items"]) == 1

    upper_clamped = conversation_client.get("/api/v1/conversations?limit=999")
    assert upper_clamped.status_code == 200
    assert len(upper_clamped.json()["items"]) == 100
    assert upper_clamped.json()["nextCursor"] is not None

    seen = _list_all(conversation_client, limit=7)
    assert seen == expected
    assert len(seen) == len(set(seen)) == len(records)

    with get_conn() as conn:
        assert conn.execute("SELECT version FROM schema_version WHERE id=1").fetchone()[0] == SCHEMA_VERSION
    assert SCHEMA_VERSION == 6


def test_message_pages_start_latest_and_prepend_to_full_history(conversation_client: TestClient):
    conversation_id = "conv-history"
    _insert_conversations([
        (conversation_id, "历史会话", "2026-09-04T10:00:00+00:00"),
        ("conv-other", "其他会话", "2026-09-04T10:00:01+00:00"),
    ])
    citation = [{
        "documentId": "doc-1",
        "documentName": "来源",
        "version": 1,
        "chunkId": "chunk-1",
        "snippet": "引用片段",
    }]
    messages = [
        ("msg-001", "user", "第一条用户消息", [], "2026-09-04T10:00:01+00:00"),
        ("msg-002", "assistant", "第一条回答", [], "2026-09-04T10:00:02+00:00"),
        ("msg-003", "user", "孤立用户消息", [], "2026-09-04T10:00:03+00:00"),
        ("msg-004", "user", "连续用户消息", [], "2026-09-04T10:00:03+00:00"),
        ("msg-005", "assistant", "带引用的回答", citation, "2026-09-04T10:00:04+00:00"),
        ("msg-006", "user", "最新孤立用户消息", [], "2026-09-04T10:00:04+00:00"),
    ]
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO message (id, conversation_id, role, content, citations, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (message_id, conversation_id, role, content, json.dumps(citations), created_at)
                for message_id, role, content, citations, created_at in messages
            ],
        )

    expected = [message[0] for message in sorted(messages, key=lambda message: (message[4], message[0]))]
    first = conversation_client.get(f"/api/v1/conversations/{conversation_id}?limit=2")
    assert first.status_code == 200
    first_body = first.json()
    assert [message["id"] for message in first_body["messages"]] == expected[-2:]
    assert first_body["messages"][-2]["citations"] == citation
    assert first_body["messages"][-1]["role"] == "user"
    assert first_body["nextCursor"] is not None

    cursor: str | None = None
    rebuilt: list[dict[str, object]] = []
    pages = 0
    while True:
        path = f"/api/v1/conversations/{conversation_id}?limit=2"
        if cursor is not None:
            path += f"&cursor={_cursor_url(cursor)}"
        response = conversation_client.get(path)
        assert response.status_code == 200
        page = response.json()
        page_messages = page["messages"]
        assert [message["id"] for message in page_messages] == sorted(
            (message["id"] for message in page_messages),
            key=lambda message_id: next(
                (created_at, candidate_id)
                for candidate_id, _, _, _, created_at in messages
                if candidate_id == message_id
            ),
        )
        rebuilt = page_messages + rebuilt
        pages += 1
        assert pages <= len(messages) + 1
        cursor = page["nextCursor"]
        if cursor is None:
            break

    assert [message["id"] for message in rebuilt] == expected
    assert len({message["id"] for message in rebuilt}) == len(messages)
    by_id = {message["id"]: message for message in rebuilt}
    assert by_id["msg-003"]["role"] == "user"
    assert by_id["msg-005"]["citations"] == citation


def test_message_detail_default_and_clamp(conversation_client: TestClient):
    conversation_id = "conv-message-clamp"
    timestamp = "2026-09-04T11:00:00+00:00"
    _insert_conversations([(conversation_id, "消息 clamp 会话", timestamp)])
    messages = [
        (f"clamp-msg-{index:03d}", conversation_id, "user", f"消息 {index}", "[]", timestamp)
        for index in range(105)
    ]
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO message (id, conversation_id, role, content, citations, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            messages,
        )

    expected = sorted(message[0] for message in messages)
    default_page = conversation_client.get(f"/api/v1/conversations/{conversation_id}")
    assert default_page.status_code == 200
    assert [message["id"] for message in default_page.json()["messages"]] == expected[-20:]
    assert default_page.json()["nextCursor"] is not None

    lower_clamped = conversation_client.get(f"/api/v1/conversations/{conversation_id}?limit=0")
    assert lower_clamped.status_code == 200
    assert [message["id"] for message in lower_clamped.json()["messages"]] == expected[-1:]
    assert lower_clamped.json()["nextCursor"] is not None

    upper_clamped = conversation_client.get(f"/api/v1/conversations/{conversation_id}?limit=999")
    assert upper_clamped.status_code == 200
    assert [message["id"] for message in upper_clamped.json()["messages"]] == expected[-100:]
    assert upper_clamped.json()["nextCursor"] is not None

    with get_conn() as conn:
        assert conn.execute("SELECT version FROM schema_version WHERE id=1").fetchone()[0] == SCHEMA_VERSION
    assert SCHEMA_VERSION == 6


def test_conversation_cursor_rejections_auth_and_not_found(conversation_client: TestClient):
    _insert_conversations([
        ("conv-one", "会话一", "2026-09-04T10:00:00+00:00"),
        ("conv-two", "会话二", "2026-09-04T10:00:01+00:00"),
    ])
    list_cursor = _cursor({
        "v": 1,
        "scope": LIST_SCOPE,
        "createdAt": "2026-09-04T10:00:00+00:00",
        "id": "conv-one",
    })
    message_cursor = _cursor({
        "v": 1,
        "scope": MESSAGE_SCOPE,
        "conversationId": "conv-one",
        "createdAt": "2026-09-04T10:00:00+00:00",
        "id": "msg-one",
    })
    invalid_list_cursors = [
        "%%%",
        base64.urlsafe_b64encode(b"not-json").decode("ascii").rstrip("="),
        base64.urlsafe_b64encode(b"\xff").decode("ascii").rstrip("="),
        _cursor({"v": 1}),
        "a" * 513,
        _cursor({
            "v": 2,
            "scope": LIST_SCOPE,
            "createdAt": "2026-09-04T10:00:00+00:00",
            "id": "conv-one",
        }),
        _cursor({
            "v": 1,
            "scope": "other-scope",
            "createdAt": "2026-09-04T10:00:00+00:00",
            "id": "conv-one",
        }),
        message_cursor,
    ]
    for cursor in invalid_list_cursors:
        _assert_invalid_cursor(
            conversation_client.get(f"/api/v1/conversations?cursor={_cursor_url(cursor)}")
        )

    _assert_invalid_cursor(
        conversation_client.get(f"/api/v1/conversations/conv-one?cursor={_cursor_url(list_cursor)}")
    )
    cross_conversation_cursor = _cursor({
        "v": 1,
        "scope": MESSAGE_SCOPE,
        "conversationId": "conv-two",
        "createdAt": "2026-09-04T10:00:00+00:00",
        "id": "msg-one",
    })
    _assert_invalid_cursor(
        conversation_client.get(
            f"/api/v1/conversations/conv-one?cursor={_cursor_url(cross_conversation_cursor)}"
        )
    )
    malformed_field_cursor = _cursor({
        "v": 1,
        "scope": LIST_SCOPE,
        "createdAt": "2026-09-04T10:00:00",
        "id": " ",
    })
    _assert_invalid_cursor(
        conversation_client.get(f"/api/v1/conversations?cursor={_cursor_url(malformed_field_cursor)}")
    )

    invalid_limit = conversation_client.get("/api/v1/conversations?limit=not-an-integer")
    assert invalid_limit.status_code == 422
    assert invalid_limit.json()["error"]["code"] == "UNPROCESSABLE"
    assert conversation_client.get("/api/v1/conversations/missing").status_code == 404

    conversation_client.cookies.clear()
    assert conversation_client.get("/api/v1/conversations").status_code == 401
