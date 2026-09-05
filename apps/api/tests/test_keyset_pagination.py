"""Tie-safe keyset integration coverage for the four list endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import get_conn, init_db
from app.main import app
from app.services.keyset_cursor import encode_keyset_cursor


TEST_PASSWORD = "test-password-12345"
_TIMESTAMP = "2026-09-05T10:00:00+00:00"


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
def keyset_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Every case owns a temporary database and never reads repository data."""

    _set_data_root(monkeypatch, tmp_path / "keyset-pagination-data")
    init_db()
    with TestClient(app) as client:
        _login(client)
        yield client


def _asset(
    asset_id: str, *, mime_type: str = "text/plain", storage_class: str = "file"
) -> tuple[object, ...]:
    return (
        asset_id,
        f"{asset_id}.bin",
        mime_type,
        1,
        f"{asset_id}.bin",
        "a" * 64,
        "active",
        _TIMESTAMP,
        storage_class,
    )


def _insert_tied_rows() -> dict[str, list[str]]:
    file_ids = ["file-001", "file-002", "file-003"]
    photo_ids = ["photo-001", "photo-002", "photo-003"]
    mail_ids = ["mail-001", "mail-002", "mail-003"]
    document_ids = ["document-001", "document-002", "document-003"]
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO asset (id, original_name, mime_type, size, object_key, sha256, status, created_at, storage_class) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [_asset(item_id) for item_id in file_ids]
            + [
                _asset(
                    f"thumb-{item_id}", mime_type="image/webp", storage_class="photo-thumbnail"
                )
                for item_id in photo_ids
            ],
        )
        conn.executemany(
            "INSERT INTO photo (id, asset_id, capture_at, thumbnail_asset_id, created_at) VALUES (?, ?, ?, ?, ?)",
            [
                ("photo-001", "file-001", "2026:09:05 10:00:00", "thumb-photo-001", _TIMESTAMP),
                ("photo-002", "file-002", "2026:09:05 10:00:00", "thumb-photo-002", _TIMESTAMP),
                ("photo-003", "file-003", None, "thumb-photo-003", "2026:09:05 10:00:00"),
            ],
        )
        conn.executemany(
            "INSERT INTO mail_message (id, message_id, from_addr, subject, received_at, read_flag) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(item_id, f"<{item_id}@example.com>", "sender@example.com", item_id, _TIMESTAMP, 0) for item_id in mail_ids],
        )
        conn.executemany(
            "INSERT INTO knowledge_document (id, asset_id, title, document_type, hash, created_at) "
            "VALUES (?, ?, ?, 'txt', ?, ?)",
            [(item_id, "file-001", item_id, "b" * 64, _TIMESTAMP) for item_id in document_ids],
        )
    return {"files": file_ids, "photos": photo_ids, "mail": mail_ids, "documents": document_ids}


def _all_ids(client: TestClient, path: str, *, item_key: str = "id", **filters: object) -> list[str]:
    cursor: str | None = None
    result: list[str] = []
    for _ in range(10):
        params: dict[str, object] = {"limit": 2, **filters}
        if cursor is not None:
            params["cursor"] = cursor
        response = client.get(path, params=params)
        assert response.status_code == 200
        page = response.json()
        result.extend(item[item_key] for item in page["items"])
        cursor = page["nextCursor"]
        if cursor is None:
            return result
    raise AssertionError("keyset pagination did not terminate")


def test_tied_keysets_are_complete_stable_and_photo_fallback_is_visible(keyset_client: TestClient):
    inserted = _insert_tied_rows()

    assert _all_ids(keyset_client, "/api/v1/files") == sorted(inserted["files"], reverse=True)
    photos = _all_ids(keyset_client, "/api/v1/photos")
    assert photos == ["photo-002", "photo-001", "photo-003"]
    assert _all_ids(keyset_client, "/api/v1/mail") == sorted(inserted["mail"], reverse=True)
    assert _all_ids(keyset_client, "/api/v1/knowledge/documents") == sorted(inserted["documents"], reverse=True)

    photo_page = keyset_client.get("/api/v1/photos", params={"limit": 3})
    assert photo_page.status_code == 200
    capture_at = {item["id"]: item["captureAt"] for item in photo_page.json()["items"]}
    assert capture_at["photo-001"] == "2026-09-05T10:00:00"
    assert capture_at["photo-003"] is None


def test_photo_timeline_normalizes_mixed_history_and_paginates_by_semantic_group(
    keyset_client: TestClient,
):
    rows = [
        ("photo-canonical-dec", "2026-12-01T08:00:00", "2026-01-01T00:00:00+00:00"),
        ("photo-tie-z", "2026:01:15 12:00:00", "2026-01-01T00:00:00+00:00"),
        ("photo-tie-a", "2026-01-15T12:00:00", "2026-01-01T00:00:00+00:00"),
        ("photo-legacy-jan", "2026:01:01 08:00:00", "2026-01-01T00:00:00+00:00"),
        ("photo-invalid", "2026:02:31 10:00:00", "2027-12-31T00:00:00+00:00"),
        ("photo-null", None, "2026-11-30T00:00:00+00:00"),
    ]
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO asset (id, original_name, mime_type, size, object_key, sha256, status, created_at, storage_class) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                _asset(
                    f"thumb-{photo_id}",
                    mime_type="image/webp",
                    storage_class="photo-thumbnail",
                )
                for photo_id, _, _ in rows
            ],
        )
        conn.executemany(
            "INSERT INTO photo (id, asset_id, capture_at, thumbnail_asset_id, created_at) "
            "VALUES (?, 'unused-original', ?, ?, ?)",
            [
                (photo_id, capture_at, f"thumb-{photo_id}", created_at)
                for photo_id, capture_at, created_at in rows
            ],
        )

    expected = [
        "photo-canonical-dec",
        "photo-tie-z",
        "photo-tie-a",
        "photo-legacy-jan",
        "photo-invalid",
        "photo-null",
    ]
    assert _all_ids(keyset_client, "/api/v1/photos") == expected

    response = keyset_client.get("/api/v1/photos", params={"limit": 20})
    assert response.status_code == 200
    capture_at = {item["id"]: item["captureAt"] for item in response.json()["items"]}
    assert capture_at["photo-tie-z"] == "2026-01-15T12:00:00"
    assert capture_at["photo-tie-a"] == "2026-01-15T12:00:00"
    assert capture_at["photo-invalid"] is None
    assert capture_at["photo-null"] is None


def test_keyset_http_rejects_wrong_scope_filter_and_malformed_cursor(keyset_client: TestClient):
    _insert_tied_rows()
    files = keyset_client.get("/api/v1/files", params={"limit": 2, "mime_type": "text/plain"})
    assert files.status_code == 200
    cursor = files.json()["nextCursor"]
    assert cursor
    unread_mail = keyset_client.get(
        "/api/v1/mail", params={"limit": 2, "unread_only": True}
    )
    assert unread_mail.status_code == 200
    unread_cursor = unread_mail.json()["nextCursor"]
    assert unread_cursor
    legacy_photo_cursor = encode_keyset_cursor(
        scope="photos",
        sort_value="2026:09:05 10:00:00",
        item_id="photo-001",
    )

    invalid_responses = [
        keyset_client.get("/api/v1/mail", params={"cursor": cursor}),
        keyset_client.get("/api/v1/files", params={"cursor": cursor}),
        keyset_client.get("/api/v1/mail", params={"cursor": unread_cursor}),
        keyset_client.get("/api/v1/photos", params={"cursor": "2026-09-05T10:00:00+00:00"}),
        keyset_client.get("/api/v1/photos", params={"cursor": legacy_photo_cursor}),
    ]
    for response in invalid_responses:
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "UNPROCESSABLE"
        assert response.json()["error"]["message"] == "游标无效。"
