"""私有原始 MIME 归档：逐字节保存、回滚、幂等与公开面隔离。"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import settings  # noqa: E402
from app.db import SchemaVersionError, get_conn, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.routers import mail  # noqa: E402
from app.storage import StorageAdapter  # noqa: E402


@pytest.fixture
def raw_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = tmp_path / "data"
    monkeypatch.setattr(settings, "data_root", root)
    monkeypatch.setattr(settings, "db_path", root / "db" / "star-isle.db")
    monkeypatch.setattr(settings, "objects_root", root / "objects")
    monkeypatch.setattr(settings, "originals_dir", root / "objects" / "originals")
    monkeypatch.setattr(settings, "thumbnails_dir", root / "objects" / "thumbnails")
    monkeypatch.setattr(settings, "mail_attachments_dir", root / "objects" / "mail-attachments")
    monkeypatch.setattr(settings, "mail_raw_dir", root / "objects" / "mail-raw")
    monkeypatch.setattr(settings, "storage_quota_bytes", 10 * 1024 * 1024)
    adapter = StorageAdapter(root / "objects")
    monkeypatch.setattr(mail, "storage", adapter)
    init_db()
    return root, adapter


@pytest.fixture
def client(raw_env):
    del raw_env
    with TestClient(app) as value:
        yield value


def _nonce() -> str:
    return uuid4().hex


def _message_id() -> str:
    return f"<mail-raw-{uuid4().hex}@example.test>"


def _headers(body: bytes, *, nonce: str, timestamp: str | None = None) -> dict[str, str]:
    timestamp = timestamp or str(int(time.time()))
    digest = hashlib.sha256(body).hexdigest()
    signature = hmac.new(
        settings.mail_hmac_key.encode(),
        f"{timestamp}.{nonce}.{digest}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "message/rfc822",
        "x-mail-timestamp": timestamp,
        "x-mail-nonce": nonce,
        "x-mail-signature": signature,
    }


def _simple_mail(message_id: str) -> bytes:
    return (
        f"From: sender@example.test\r\n"
        f"To: inbox@example.test\r\n"
        f"Subject: private MIME subject\r\n"
        f"Message-ID: {message_id}\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\n"
        "private MIME body\r\n"
    ).encode()


def _binary_multipart_mail(message_id: str) -> bytes:
    boundary = b"mail-raw-boundary"
    return (
        b"From: sender@example.test\r\n"
        b"To: inbox@example.test\r\n"
        b"Subject: private MIME subject\r\n"
        + f"Message-ID: {message_id}\r\n".encode()
        + b"MIME-Version: 1.0\r\n"
        + b"Content-Type: multipart/mixed; boundary=mail-raw-boundary\r\n\r\n"
        + b"--" + boundary + b"\r\n"
        + b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        + b"plain summary\r\n"
        + b"--" + boundary + b"\r\n"
        + b"Content-Type: application/octet-stream\r\n"
        + b"Content-Disposition: attachment; filename=bytes.bin\r\n"
        + b"Content-Transfer-Encoding: binary\r\n\r\n"
        + b"\x00\xff\x80binary-payload\r\n"
        + b"--" + boundary + b"--\r\n"
    )


def _raw_rows() -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT m.id, m.raw_state, r.object_key, r.sha256, r.size, r.content_type "
            "FROM mail_message m LEFT JOIN mail_raw r ON r.message_id=m.id ORDER BY m.id"
        ).fetchall()


def _has_nonce(nonce: str) -> bool:
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM mail_nonce WHERE nonce=?", (nonce,)).fetchone() is not None


def _replace_mail_raw_layout(
    conn: sqlite3.Connection,
    *,
    message_id_definition: str,
    object_key_definition: str,
    index_definition: str,
) -> None:
    conn.execute("DROP INDEX idx_mail_raw_object_key")
    conn.execute("DROP TABLE mail_raw")
    conn.execute(
        f"""
        CREATE TABLE mail_raw (
          {message_id_definition},
          {object_key_definition},
          sha256 TEXT NOT NULL CHECK(length(sha256)=64),
          size INTEGER NOT NULL CHECK(size>0),
          content_type TEXT NOT NULL CHECK(content_type='message/rfc822'),
          created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(index_definition)


def test_binary_mime_is_stored_exactly_and_never_enters_public_payload(
    client: TestClient, raw_env, monkeypatch: pytest.MonkeyPatch
):
    _, adapter = raw_env
    body = _binary_multipart_mail(_message_id())
    audit_calls: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(mail, "audit", lambda *args, **kwargs: audit_calls.append((args, kwargs)))

    response = client.post("/api/v1/mail/receive", content=body, headers=_headers(body, nonce=_nonce()))
    assert response.status_code == 200, "mail raw receipt must succeed"
    assert set(response.json()) == {"id", "duplicate"}
    mail_id = response.json()["id"]

    rows = _raw_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == mail_id
    assert row["raw_state"] == "stored"
    assert row["size"] == len(body)
    assert row["sha256"] == hashlib.sha256(body).hexdigest()
    assert row["content_type"] == "message/rfc822"
    assert adapter.open("mail-raw", row["object_key"]) == body
    with get_conn() as conn:
        attachment_class = conn.execute(
            "SELECT a.storage_class FROM mail_attachment ma JOIN asset a ON a.id=ma.asset_id "
            "WHERE ma.message_id=?",
            (mail_id,),
        ).fetchone()
    assert attachment_class and attachment_class["storage_class"] == "mail-attachment"

    public_payload = json.dumps(
        {"list": mail.list_mail(payload={}), "detail": mail.mail_detail(mail_id, payload={})},
        ensure_ascii=False,
    )
    for forbidden in ("raw_state", "mail_raw", "object_key", "sha256", "mail-raw", row["object_key"]):
        assert forbidden not in public_payload
    audit_payload = json.dumps(audit_calls, ensure_ascii=False, default=str)
    for forbidden in (row["object_key"], row["sha256"], "binary-payload"):
        assert forbidden not in audit_payload


def test_rejected_replayed_and_duplicate_messages_never_allocate_extra_raw(client: TestClient, raw_env):
    _, adapter = raw_env
    body = _simple_mail(_message_id())
    nonce = _nonce()
    bad_headers = _headers(body, nonce=nonce)
    bad_headers["x-mail-signature"] = "0" * 64
    assert client.post("/api/v1/mail/receive", content=body, headers=bad_headers).status_code == 401
    assert not _has_nonce(nonce)
    assert not _raw_rows()

    expired_nonce = _nonce()
    assert client.post(
        "/api/v1/mail/receive",
        content=body,
        headers=_headers(body, nonce=expired_nonce, timestamp="1"),
    ).status_code == 401
    assert not _has_nonce(expired_nonce)
    assert not _raw_rows()

    accepted_nonce = _nonce()
    accepted_headers = _headers(body, nonce=accepted_nonce)
    first = client.post("/api/v1/mail/receive", content=body, headers=accepted_headers)
    assert first.status_code == 200
    assert client.post("/api/v1/mail/receive", content=body, headers=accepted_headers).status_code == 401
    duplicate = client.post(
        "/api/v1/mail/receive",
        content=body,
        headers=_headers(body, nonce=_nonce()),
    )
    assert duplicate.status_code == 200
    assert duplicate.json() == {"id": first.json()["id"], "duplicate": True}
    rows = _raw_rows()
    assert len(rows) == 1 and rows[0]["raw_state"] == "stored"
    assert len(list((adapter.root / "mail-raw").glob("*"))) == 1


def test_raw_quota_and_attachment_failure_clean_objects_and_allow_retry(client: TestClient, raw_env, monkeypatch):
    _, adapter = raw_env
    quota_body = _simple_mail(_message_id())
    quota_nonce = _nonce()
    quota_headers = _headers(quota_body, nonce=quota_nonce)
    monkeypatch.setattr(settings, "storage_quota_bytes", 0)
    assert client.post("/api/v1/mail/receive", content=quota_body, headers=quota_headers).status_code == 503
    assert not _has_nonce(quota_nonce)
    assert not _raw_rows()
    assert not list((adapter.root / "mail-raw").glob("*"))

    monkeypatch.setattr(settings, "storage_quota_bytes", 10 * 1024 * 1024)
    assert client.post("/api/v1/mail/receive", content=quota_body, headers=quota_headers).status_code == 200

    original_save = adapter.save
    raw_io_body = _simple_mail(_message_id())
    raw_io_nonce = _nonce()
    raw_io_headers = _headers(raw_io_body, nonce=raw_io_nonce)

    def fail_raw(data: bytes, subdir: str, suffix: str = "") -> str:
        if subdir == "mail-raw":
            raise OSError("raw storage unavailable")
        return original_save(data, subdir, suffix)

    monkeypatch.setattr(adapter, "save", fail_raw)
    assert client.post("/api/v1/mail/receive", content=raw_io_body, headers=raw_io_headers).status_code == 503
    assert not _has_nonce(raw_io_nonce)
    assert len(_raw_rows()) == 1
    assert len(list((adapter.root / "mail-raw").glob("*"))) == 1
    monkeypatch.setattr(adapter, "save", original_save)
    assert client.post("/api/v1/mail/receive", content=raw_io_body, headers=raw_io_headers).status_code == 200

    attachment_body = _binary_multipart_mail(_message_id())
    attachment_nonce = _nonce()
    attachment_headers = _headers(attachment_body, nonce=attachment_nonce)

    def fail_attachment(data: bytes, subdir: str, suffix: str = "") -> str:
        if subdir == "mail-attachments":
            raise OSError("attachment storage unavailable")
        return original_save(data, subdir, suffix)

    monkeypatch.setattr(adapter, "save", fail_attachment)
    assert client.post(
        "/api/v1/mail/receive", content=attachment_body, headers=attachment_headers
    ).status_code == 503
    assert not _has_nonce(attachment_nonce)
    assert len(_raw_rows()) == 2
    assert len(list((adapter.root / "mail-raw").glob("*"))) == 2
    assert not list((adapter.root / "mail-attachments").glob("*"))

    monkeypatch.setattr(adapter, "save", original_save)
    assert client.post(
        "/api/v1/mail/receive", content=attachment_body, headers=attachment_headers
    ).status_code == 200
    assert len(_raw_rows()) == 3


def test_mail_raw_insert_failure_cleans_object_and_releases_nonce_for_retry(
    client: TestClient, raw_env, monkeypatch: pytest.MonkeyPatch
):
    _, adapter = raw_env
    body = _simple_mail(_message_id())
    nonce = _nonce()
    headers = _headers(body, nonce=nonce)
    real_get_conn = mail.get_conn

    class RawInsertFailureConnection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql: str, parameters=()):
            if sql.lstrip().startswith("INSERT INTO mail_raw"):
                raise sqlite3.IntegrityError("raw insert unavailable")
            return self.connection.execute(sql, parameters)

        def __getattr__(self, name: str):
            return getattr(self.connection, name)

    @contextmanager
    def fail_raw_insert():
        with real_get_conn() as connection:
            yield RawInsertFailureConnection(connection)

    monkeypatch.setattr(mail, "get_conn", fail_raw_insert)
    assert client.post("/api/v1/mail/receive", content=body, headers=headers).status_code == 503
    assert not _has_nonce(nonce)
    assert not _raw_rows()
    assert not list((adapter.root / "mail-raw").glob("*"))

    monkeypatch.setattr(mail, "get_conn", real_get_conn)
    assert client.post("/api/v1/mail/receive", content=body, headers=headers).status_code == 200
    assert _has_nonce(nonce)
    assert len(_raw_rows()) == 1


def test_commit_failure_cleans_raw_and_releases_nonce_for_same_request(client: TestClient, raw_env, monkeypatch):
    _, adapter = raw_env
    body = _simple_mail(_message_id())
    nonce = _nonce()
    headers = _headers(body, nonce=nonce)
    real_get_conn = mail.get_conn
    calls = 0

    @contextmanager
    def fail_main_transaction():
        nonlocal calls
        calls += 1
        with real_get_conn() as conn:
            yield conn
            if calls == 2:
                raise sqlite3.OperationalError("commit unavailable")

    monkeypatch.setattr(mail, "get_conn", fail_main_transaction)
    assert client.post("/api/v1/mail/receive", content=body, headers=headers).status_code == 503
    assert not _has_nonce(nonce)
    assert not _raw_rows()
    assert not list((adapter.root / "mail-raw").glob("*"))

    monkeypatch.setattr(mail, "get_conn", real_get_conn)
    assert client.post("/api/v1/mail/receive", content=body, headers=headers).status_code == 200
    assert _has_nonce(nonce)
    assert len(_raw_rows()) == 1


@pytest.mark.parametrize(
    ("message_id_definition", "object_key_definition", "index_definition"),
    [
        (
            "message_id TEXT NOT NULL REFERENCES mail_message(id) ON DELETE RESTRICT",
            "object_key TEXT NOT NULL",
            "CREATE UNIQUE INDEX idx_mail_raw_object_key ON mail_raw(object_key)",
        ),
        (
            "message_id TEXT PRIMARY KEY REFERENCES mail_message(id) ON DELETE RESTRICT",
            "object_key TEXT",
            "CREATE UNIQUE INDEX idx_mail_raw_object_key ON mail_raw(object_key)",
        ),
        (
            "message_id TEXT PRIMARY KEY REFERENCES mail_message(id) ON DELETE RESTRICT",
            "object_key TEXT NOT NULL",
            "CREATE UNIQUE INDEX idx_mail_raw_object_key ON mail_raw(sha256)",
        ),
    ],
    ids=("message-id-not-primary-key", "nullable-object-key", "wrong-index-column"),
)
def test_init_db_rejects_weakened_mail_raw_layout(
    raw_env,
    message_id_definition: str,
    object_key_definition: str,
    index_definition: str,
):
    del raw_env
    with get_conn() as conn:
        _replace_mail_raw_layout(
            conn,
            message_id_definition=message_id_definition,
            object_key_definition=object_key_definition,
            index_definition=index_definition,
        )

    with pytest.raises(SchemaVersionError):
        init_db()
