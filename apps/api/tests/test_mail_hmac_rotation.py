"""Mail HMAC kid rotation keeps legacy verification on the active key only."""

from __future__ import annotations

import hashlib
import hmac
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import get_conn, init_db
from app.main import app
from app.routers import mail


ACTIVE_KID = "current"
PREVIOUS_KID = "previous"
ACTIVE_SECRET = "active-mail-secret-for-rotation-tests-0123456789abcdef"
PREVIOUS_SECRET = "previous-mail-secret-for-rotation-tests-0123456789abcdef"


@pytest.fixture(autouse=True)
def _mail_rotation_settings():
    init_db()
    original = (
        settings.mail_hmac_key,
        settings.mail_hmac_active_kid,
        settings.mail_hmac_previous_keys,
    )
    settings.mail_hmac_key = ACTIVE_SECRET
    settings.mail_hmac_active_kid = ACTIVE_KID
    settings.mail_hmac_previous_keys = {PREVIOUS_KID: PREVIOUS_SECRET}
    yield
    (
        settings.mail_hmac_key,
        settings.mail_hmac_active_kid,
        settings.mail_hmac_previous_keys,
    ) = original


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _message() -> bytes:
    return (
        "From: sender@example.test\r\n"
        "To: inbox@example.test\r\n"
        "Subject: mail rotation\r\n"
        f"Message-ID: <rotation-{uuid4().hex}@example.test>\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\n"
        "rotation body"
    ).encode()


def _nonce() -> str:
    return uuid4().hex


def _headers(body: bytes, *, secret: str, kid: str | None, nonce: str | None = None) -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = nonce or _nonce()
    digest = hashlib.sha256(body).hexdigest()
    payload = f"{timestamp}.{nonce}.{digest}" if kid is None else f"v1.{kid}.{timestamp}.{nonce}.{digest}"
    headers = {
        "Content-Type": "message/rfc822",
        "x-mail-timestamp": timestamp,
        "x-mail-nonce": nonce,
        "x-mail-signature": hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest(),
    }
    if kid is not None:
        headers["x-mail-key-id"] = kid
    return headers


def _persistence_state() -> tuple[int, int, int]:
    with get_conn() as conn:
        return tuple(
            conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("mail_nonce", "mail_message", "audit_event")
        )


def _has_nonce(nonce: str) -> bool:
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM mail_nonce WHERE nonce=?", (nonce,)).fetchone() is not None


def test_kid_bound_active_and_previous_keys_verify_by_exact_lookup(client: TestClient) -> None:
    active_body = _message()
    previous_body = _message()

    active = client.post(
        "/api/v1/mail/receive",
        content=active_body,
        headers=_headers(active_body, secret=ACTIVE_SECRET, kid=ACTIVE_KID),
    )
    previous = client.post(
        "/api/v1/mail/receive",
        content=previous_body,
        headers=_headers(previous_body, secret=PREVIOUS_SECRET, kid=PREVIOUS_KID),
    )

    assert active.status_code == previous.status_code == 200


def test_unknown_invalid_or_tampered_kid_rejects_before_body_or_persistence(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = _message()
    before = _persistence_state()
    read_limited_body = mail._read_limited_body

    async def body_must_not_be_read(_request):
        raise AssertionError("unknown kid must be rejected before reading the MIME body")

    monkeypatch.setattr(mail, "_read_limited_body", body_must_not_be_read)
    for headers in (
        _headers(body, secret=ACTIVE_SECRET, kid="unknown"),
        _headers(body, secret=ACTIVE_SECRET, kid="not safe"),
    ):
        nonce = headers["x-mail-nonce"]
        response = client.post("/api/v1/mail/receive", content=body, headers=headers)

        assert response.status_code == 401
        assert not _has_nonce(nonce)
        assert _persistence_state() == before

    monkeypatch.setattr(mail, "_read_limited_body", read_limited_body)
    tampered_headers = _headers(body, secret=PREVIOUS_SECRET, kid=ACTIVE_KID)
    tampered = client.post("/api/v1/mail/receive", content=body, headers=tampered_headers)

    assert tampered.status_code == 401
    assert not _has_nonce(tampered_headers["x-mail-nonce"])
    assert _persistence_state() == before


def test_legacy_mail_verifies_only_with_active_secret(client: TestClient) -> None:
    active_body = _message()
    active_headers = _headers(active_body, secret=ACTIVE_SECRET, kid=None)
    assert client.post("/api/v1/mail/receive", content=active_body, headers=active_headers).status_code == 200

    previous_body = _message()
    previous_headers = _headers(previous_body, secret=PREVIOUS_SECRET, kid=None)
    before = _persistence_state()
    rejected = client.post("/api/v1/mail/receive", content=previous_body, headers=previous_headers)

    assert rejected.status_code == 401
    assert not _has_nonce(previous_headers["x-mail-nonce"])
    assert _persistence_state() == before
