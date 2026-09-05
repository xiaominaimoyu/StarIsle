"""M2 邮件入口的流式、重放、MIME 与持久化失败路径回归。"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.config import settings  # noqa: E402
from app.db import get_conn, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.routers import mail  # noqa: E402


@pytest.fixture(autouse=True)
def _mail_limits():
    init_db()
    original_limit = settings.mail_max_bytes
    yield
    settings.mail_max_bytes = original_limit


@pytest.fixture
def client():
    return TestClient(app)


def _nonce() -> str:
    return uuid4().hex


def _message_id() -> str:
    return f"<mail-hardening-{uuid4().hex}@example.test>"


def _simple_mail(
    *,
    message_id: str | None = None,
    body: str = "hello from hardened mail",
    extra_headers: list[str] | None = None,
) -> bytes:
    headers = [
        "From: sender@example.test",
        "To: inbox@example.test",
        "Subject: hardened mail",
    ]
    if message_id is not None:
        headers.append(f"Message-ID: {message_id}")
    headers.extend(extra_headers or ['Content-Type: text/plain; charset="utf-8"'])
    return ("\r\n".join(headers) + "\r\n\r\n" + body).encode("utf-8")


def _headers(body: bytes, *, nonce: str | None = None, timestamp: str | None = None) -> dict[str, str]:
    timestamp = timestamp or str(int(time.time()))
    nonce = nonce or _nonce()
    body_sha = hashlib.sha256(body).hexdigest()
    signature = hmac.new(
        settings.mail_hmac_key.encode(),
        f"{timestamp}.{nonce}.{body_sha}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "message/rfc822",
        "x-mail-timestamp": timestamp,
        "x-mail-nonce": nonce,
        "x-mail-signature": signature,
    }


def _post(client: TestClient, body: bytes, *, nonce: str | None = None, timestamp: str | None = None):
    return client.post("/api/v1/mail/receive", content=body, headers=_headers(body, nonce=nonce, timestamp=timestamp))


def _has_nonce(nonce: str) -> bool:
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM mail_nonce WHERE nonce=?", (nonce,)).fetchone() is not None


_MAIL_STATE_TABLES = ("audit_event", "mail_nonce", "mail_message", "mail_raw", "mail_attachment", "asset")


def _mail_persistence_state() -> tuple[dict[str, int], dict[str, frozenset[str]]]:
    """只观察 pytest 隔离 DATA_ROOT 内的邮件相关持久状态。"""
    with get_conn() as conn:
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in _MAIL_STATE_TABLES
        }
    objects = {
        "mail-raw": frozenset(path.name for path in settings.mail_raw_dir.iterdir() if path.is_file()),
        "mail-attachments": frozenset(
            path.name for path in settings.mail_attachments_dir.iterdir() if path.is_file()
        ),
    }
    return counts, objects


def _audit_action_count(action: str) -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM audit_event WHERE action=?", (action,)).fetchone()[0]


def _assert_audit_action_increment(action: str, result: str, before: int) -> None:
    assert _audit_action_count(action) == before + 1
    with get_conn() as conn:
        row = conn.execute(
            "SELECT result FROM audit_event WHERE action=? ORDER BY rowid DESC LIMIT 1", (action,)
        ).fetchone()
    assert row and row["result"] == result


def _audit_field_values() -> str:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT actor, action, resource_type, resource_id, request_id, result FROM audit_event"
        ).fetchall()
    return "\n".join(str(value) for row in rows for value in row if value is not None)


def _multipart_alternative(message_id: str, html: str = "<p>safe html</p>") -> bytes:
    boundary = "alt-" + uuid4().hex
    return (
        f"From: sender@example.test\r\n"
        f"To: inbox@example.test\r\n"
        f"Subject: multipart alternative\r\n"
        f"Message-ID: {message_id}\r\n"
        f"MIME-Version: 1.0\r\n"
        f'Content-Type: multipart/alternative; boundary="{boundary}"\r\n\r\n'
        f"--{boundary}\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nplain alternative\r\n"
        f"--{boundary}\r\nContent-Type: text/html; charset=utf-8\r\n\r\n{html}\r\n"
        f"--{boundary}--\r\n"
    ).encode()


def _multipart_attachment(message_id: str, *, attachment_count: int = 1) -> bytes:
    boundary = "mixed-" + uuid4().hex
    parts = [
        "From: sender@example.test",
        "To: inbox@example.test",
        "Subject: attachment mail",
        f"Message-ID: {message_id}",
        "MIME-Version: 1.0",
        f'Content-Type: multipart/mixed; boundary="{boundary}"',
        "",
        f"--{boundary}",
        "Content-Type: text/plain; charset=utf-8",
        "",
        "plain body",
    ]
    for index in range(attachment_count):
        parts.extend(
            [
                f"--{boundary}",
                "Content-Type: text/html; charset=utf-8",
                f'Content-Disposition: attachment; filename="file-{index}.html"',
                "",
                f"<p>attachment {index}</p>",
            ]
        )
    parts.extend([f"--{boundary}--", ""])
    return "\r\n".join(parts).encode()


class _ChunkedBody(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        return None


def _post_chunked(body: bytes, headers: dict[str, str], chunks: list[bytes]):
    async def run():
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as async_client:
            return await async_client.post(
                "/api/v1/mail/receive",
                content=_ChunkedBody(chunks),
                headers=headers,
            )

    return asyncio.run(run())


def test_streamed_limit_boundary_and_overflow_never_call_request_body(client, monkeypatch: pytest.MonkeyPatch):
    body = _simple_mail(message_id=_message_id())
    settings.mail_max_bytes = len(body)

    async def forbidden_body(_request):
        raise AssertionError("receive_mail must use request.stream(), never request.body()")

    monkeypatch.setattr(Request, "body", forbidden_body)
    accepted = _post_chunked(body, _headers(body), [body[:17], body[17:]])
    assert accepted.status_code == 200, "streamed boundary receipt must succeed"

    oversized = body + b"x"
    settings.mail_max_bytes = len(body)
    rejected = _post_chunked(oversized, _headers(oversized), [oversized[:9], oversized[9:]])
    assert rejected.status_code == 413


def test_content_length_limit_and_empty_or_invalid_mime_are_uniform_422_or_413(client):
    settings.mail_max_bytes = 20
    too_large_headers = _headers(b"x")
    too_large_headers["content-length"] = "21"
    too_large = client.post(
        "/api/v1/mail/receive",
        content=b"x",
        headers=too_large_headers,
    )
    assert too_large.status_code == 413

    settings.mail_max_bytes = 1024
    for body in (b"", b"not-an-rfc-message"):
        response = client.post(
            "/api/v1/mail/receive",
            content=body,
            headers=_headers(body),
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "UNPROCESSABLE"


def test_header_syntax_and_exact_timestamp_window(client, monkeypatch: pytest.MonkeyPatch):
    fixed_now = 1_700_000_000
    monkeypatch.setattr(mail.time, "time", lambda: fixed_now)
    body = _simple_mail(message_id=_message_id())

    for timestamp, nonce, signature in [
        ("x", _nonce(), "0" * 64),
        ("1" * 17, _nonce(), "0" * 64),
        (str(fixed_now), "short", "0" * 64),
        (str(fixed_now), "x" * 129, "0" * 64),
        (str(fixed_now), "nonce!unsafe", "0" * 64),
        (str(fixed_now), _nonce(), "A" * 64),
        (str(fixed_now), _nonce(), "0" * 63),
    ]:
        response = client.post(
            "/api/v1/mail/receive",
            content=body,
            headers={
                "Content-Type": "message/rfc822",
                "x-mail-timestamp": timestamp,
                "x-mail-nonce": nonce,
                "x-mail-signature": signature,
            },
        )
        assert response.status_code == 401
        assert not _has_nonce(nonce)

    for offset in (-300, 300):
        response = _post(client, body, nonce=_nonce(), timestamp=str(fixed_now + offset))
        assert response.status_code == 200, "timestamp boundary receipt must succeed"
    for offset in (-301, 301):
        nonce = _nonce()
        response = _post(client, body, nonce=nonce, timestamp=str(fixed_now + offset))
        assert response.status_code == 401
        assert not _has_nonce(nonce)


def test_configuration_header_and_timestamp_fail_before_request_stream(client, monkeypatch: pytest.MonkeyPatch):
    stream_started: list[bool] = []

    async def forbidden_stream(_request):
        stream_started.append(True)
        raise AssertionError("invalid headers must not start reading request.stream()")
        yield b""  # pragma: no cover - makes this an async iterator for the route contract.

    monkeypatch.setattr(Request, "stream", forbidden_stream)
    body = _simple_mail(message_id=_message_id())
    valid_headers = _headers(body)

    with monkeypatch.context() as scoped:
        scoped.setattr(settings, "mail_hmac_key", None)
        response = client.post("/api/v1/mail/receive", content=body, headers=valid_headers)
        assert response.status_code == 503

    with monkeypatch.context() as scoped:
        scoped.setattr(settings, "mail_max_bytes", 0)
        response = client.post("/api/v1/mail/receive", content=body, headers=valid_headers)
        assert response.status_code == 503

    response = client.post(
        "/api/v1/mail/receive",
        content=body,
        headers={
            "Content-Type": "message/rfc822",
            "x-mail-timestamp": "not-a-number",
            "x-mail-nonce": _nonce(),
            "x-mail-signature": "0" * 64,
        },
    )
    assert response.status_code == 401

    expired_timestamp = str(int(time.time()) + mail.HMAC_WINDOW_SECONDS + 1)
    response = client.post(
        "/api/v1/mail/receive",
        content=body,
        headers=_headers(body, timestamp=expired_timestamp),
    )
    assert response.status_code == 401
    assert stream_started == []


def test_all_preauth_failures_leave_audit_and_mail_state_unchanged(client, monkeypatch: pytest.MonkeyPatch):
    body = _simple_mail(message_id=_message_id())

    def assert_unchanged(response, status: int, code: str, before) -> None:
        assert response.status_code == status
        assert response.json()["error"]["code"] == code
        assert _mail_persistence_state() == before

    valid_headers = _headers(body)
    for invalid_key in ("", None):
        with monkeypatch.context() as scoped:
            scoped.setattr(settings, "mail_hmac_key", invalid_key)
            before = _mail_persistence_state()
            response = client.post("/api/v1/mail/receive", content=body, headers=valid_headers)
            assert_unchanged(response, 503, "INTERNAL", before)

    for invalid_limit in (0, -1):
        with monkeypatch.context() as scoped:
            scoped.setattr(settings, "mail_max_bytes", invalid_limit)
            before = _mail_persistence_state()
            response = client.post("/api/v1/mail/receive", content=body, headers=valid_headers)
            assert_unchanged(response, 503, "INTERNAL", before)

    invalid_headers = {
        "Content-Type": "message/rfc822",
        "x-mail-timestamp": "not-a-number",
        "x-mail-nonce": _nonce(),
        "x-mail-signature": "0" * 64,
    }
    before = _mail_persistence_state()
    response = client.post("/api/v1/mail/receive", content=body, headers=invalid_headers)
    assert_unchanged(response, 401, "AUTH_REQUIRED", before)

    expired_timestamp = str(int(time.time()) + mail.HMAC_WINDOW_SECONDS + 1)
    before = _mail_persistence_state()
    response = client.post(
        "/api/v1/mail/receive",
        content=body,
        headers=_headers(body, timestamp=expired_timestamp),
    )
    assert_unchanged(response, 401, "AUTH_REQUIRED", before)

    with monkeypatch.context() as scoped:
        scoped.setattr(settings, "mail_max_bytes", len(body) - 1)
        headers = _headers(body)
        headers["content-length"] = str(len(body))
        before = _mail_persistence_state()
        response = client.post("/api/v1/mail/receive", content=body, headers=headers)
        assert_unchanged(response, 413, "PAYLOAD_TOO_LARGE", before)

    with monkeypatch.context() as scoped:
        scoped.setattr(settings, "mail_max_bytes", len(body) - 1)
        before = _mail_persistence_state()
        response = _post_chunked(body, _headers(body), [body[:11], body[11:]])
        assert_unchanged(response, 413, "PAYLOAD_TOO_LARGE", before)

    empty_body = b""
    before = _mail_persistence_state()
    response = client.post("/api/v1/mail/receive", content=empty_body, headers=_headers(empty_body))
    assert_unchanged(response, 422, "UNPROCESSABLE", before)

    async def broken_stream(_request):
        raise RuntimeError("stream unavailable")
        yield b""  # pragma: no cover - makes this an async iterator for the route contract.

    with monkeypatch.context() as scoped:
        scoped.setattr(Request, "stream", broken_stream)
        before = _mail_persistence_state()
        response = client.post("/api/v1/mail/receive", content=body, headers=_headers(body))
        assert_unchanged(response, 503, "INTERNAL", before)

    def broken_hash(_body: bytes) -> str:
        raise RuntimeError("hash unavailable")

    with monkeypatch.context() as scoped:
        scoped.setattr(mail, "sha256_hex", broken_hash)
        before = _mail_persistence_state()
        response = client.post("/api/v1/mail/receive", content=body, headers=_headers(body))
        assert_unchanged(response, 503, "INTERNAL", before)

    def broken_hmac(*_args: str) -> bool:
        raise RuntimeError("hmac unavailable")

    with monkeypatch.context() as scoped:
        scoped.setattr(mail, "verify_hmac", broken_hmac)
        before = _mail_persistence_state()
        response = client.post("/api/v1/mail/receive", content=body, headers=_headers(body))
        assert_unchanged(response, 503, "INTERNAL", before)

    bad_nonce = _nonce()
    bad_headers = _headers(body, nonce=bad_nonce)
    bad_headers["x-mail-signature"] = "0" * 64
    before = _mail_persistence_state()
    response = client.post("/api/v1/mail/receive", content=body, headers=bad_headers)
    assert_unchanged(response, 401, "AUTH_REQUIRED", before)
    assert not _has_nonce(bad_nonce)


def test_verified_receive_paths_keep_existing_audit_actions(client, monkeypatch: pytest.MonkeyPatch):
    success_action = "mail_receive"
    before = _audit_action_count(success_action)
    response = _post(client, _simple_mail(message_id=_message_id()))
    assert response.status_code == 200
    _assert_audit_action_increment(success_action, "ok", before)

    message_id = _message_id()
    idempotent_body = _simple_mail(message_id=message_id)
    assert _post(client, idempotent_body, nonce=_nonce()).status_code == 200
    before = _audit_action_count("mail_idempotent")
    response = _post(client, idempotent_body, nonce=_nonce())
    assert response.status_code == 200 and response.json()["duplicate"] is True
    _assert_audit_action_increment("mail_idempotent", "dup", before)

    replay_body = _simple_mail(message_id=_message_id())
    replay_headers = _headers(replay_body, nonce=_nonce())
    assert client.post("/api/v1/mail/receive", content=replay_body, headers=replay_headers).status_code == 200
    before = _audit_action_count("mail_nonce_replay")
    response = client.post("/api/v1/mail/receive", content=replay_body, headers=replay_headers)
    assert response.status_code == 401
    _assert_audit_action_increment("mail_nonce_replay", "401", before)

    before = _audit_action_count("mail_mime_rejected")
    response = _post(client, b"not-a-mail")
    assert response.status_code == 422
    _assert_audit_action_increment("mail_mime_rejected", "422", before)

    def broken_mime(*_args):
        raise RuntimeError("mime parser unavailable")

    with monkeypatch.context() as scoped:
        scoped.setattr(mail, "_parse_mail_content", broken_mime)
        before = _audit_action_count("mail_mime_internal")
        response = _post(client, _simple_mail(message_id=_message_id()))
        assert response.status_code == 503
        _assert_audit_action_increment("mail_mime_internal", "503", before)

    def broken_nonce_check(_nonce: str) -> bool:
        raise RuntimeError("nonce check unavailable")

    with monkeypatch.context() as scoped:
        scoped.setattr(mail, "_nonce_already_used", broken_nonce_check)
        before = _audit_action_count("mail_nonce_check_failed")
        response = _post(client, _simple_mail(message_id=_message_id()))
        assert response.status_code == 503
        _assert_audit_action_increment("mail_nonce_check_failed", "503", before)

    def quota_unavailable(*_args, **_kwargs):
        raise mail.StorageQuotaExceeded()

    with monkeypatch.context() as scoped:
        scoped.setattr(mail.storage, "save", quota_unavailable)
        before = _audit_action_count("mail_storage_unavailable")
        response = _post(client, _simple_mail(message_id=_message_id()))
        assert response.status_code == 503
        _assert_audit_action_increment("mail_storage_unavailable", "503", before)

    def persist_unavailable(*_args, **_kwargs):
        raise OSError("persist unavailable")

    with monkeypatch.context() as scoped:
        scoped.setattr(mail.storage, "save", persist_unavailable)
        before = _audit_action_count("mail_persist_failed")
        response = _post(client, _simple_mail(message_id=_message_id()))
        assert response.status_code == 503
        _assert_audit_action_increment("mail_persist_failed", "503", before)


def test_preauth_canaries_stay_out_of_audit_fields_and_process_output(
    client, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    body_canary = f"preauth-body-{uuid4().hex}"
    nonce_canary = "preauth-nonce-" + uuid4().hex
    signature_canary = "a" * 64
    internal_error_canary = f"preauth-error-{uuid4().hex}"
    body = _simple_mail(message_id=_message_id(), body=body_canary)
    bad_headers = _headers(body, nonce=nonce_canary)
    bad_headers["x-mail-signature"] = signature_canary

    before = _mail_persistence_state()
    bad_hmac = client.post("/api/v1/mail/receive", content=body, headers=bad_headers)
    assert bad_hmac.status_code == 401
    assert _mail_persistence_state() == before

    async def broken_stream(_request):
        raise RuntimeError(internal_error_canary)
        yield b""  # pragma: no cover - makes this an async iterator for the route contract.

    with monkeypatch.context() as scoped:
        scoped.setattr(Request, "stream", broken_stream)
        stream_failure = client.post("/api/v1/mail/receive", content=body, headers=_headers(body))
        assert stream_failure.status_code == 503
        assert _mail_persistence_state() == before

    captured = capsys.readouterr()
    output = captured.out + captured.err + bad_hmac.text + stream_failure.text
    audit_values = _audit_field_values()
    assert all(
        marker not in output and marker not in audit_values
        for marker in (body_canary, nonce_canary, signature_canary, internal_error_canary)
    )


def test_bad_signature_and_mime_failure_do_not_consume_nonce(client):
    body = _simple_mail(message_id=_message_id())
    nonce = _nonce()
    headers = _headers(body, nonce=nonce)
    bad = dict(headers, **{"x-mail-signature": "0" * 64})
    assert client.post("/api/v1/mail/receive", content=body, headers=bad).status_code == 401
    assert not _has_nonce(nonce)
    assert client.post("/api/v1/mail/receive", content=body, headers=headers).status_code == 200

    bad_mime = b"not-a-mail"
    mime_nonce = _nonce()
    response = _post(client, bad_mime, nonce=mime_nonce)
    assert response.status_code == 422
    assert not _has_nonce(mime_nonce)


def test_temporary_storage_failure_rolls_back_nonce_then_same_signed_request_retries(client, monkeypatch: pytest.MonkeyPatch):
    body = _multipart_attachment(_message_id())
    nonce = _nonce()
    headers = _headers(body, nonce=nonce)
    original_save = mail.storage.save

    def broken_save(*_args, **_kwargs):
        raise OSError("temporary storage failure")

    monkeypatch.setattr(mail.storage, "save", broken_save)
    first = client.post("/api/v1/mail/receive", content=body, headers=headers)
    assert first.status_code == 503
    assert not _has_nonce(nonce)

    monkeypatch.setattr(mail.storage, "save", original_save)
    second = client.post("/api/v1/mail/receive", content=body, headers=headers)
    assert second.status_code == 200, "storage retry receipt must succeed"
    assert _has_nonce(nonce)


def test_unexpected_html_cleaner_failure_is_503_and_same_signed_request_retries(client, monkeypatch: pytest.MonkeyPatch):
    body = _multipart_alternative(_message_id(), "<p>cleaner retry</p>")
    nonce = _nonce()
    headers = _headers(body, nonce=nonce)
    original_clean = mail.sanitize_html

    def broken_cleaner(_raw: str) -> str:
        raise RuntimeError("cleaner temporarily unavailable")

    monkeypatch.setattr(mail, "sanitize_html", broken_cleaner)
    first = client.post("/api/v1/mail/receive", content=body, headers=headers)
    assert first.status_code == 503
    assert first.json()["error"]["code"] == "INTERNAL"
    assert not _has_nonce(nonce)

    monkeypatch.setattr(mail, "sanitize_html", original_clean)
    second = client.post("/api/v1/mail/receive", content=body, headers=headers)
    assert second.status_code == 200, "cleaner retry receipt must succeed"
    assert _has_nonce(nonce)


def test_replay_audit_survives_main_transaction_and_message_id_is_idempotent(client):
    message_id = _message_id()
    body = _simple_mail(message_id=message_id)
    first = _post(client, body, nonce=_nonce())
    assert first.status_code == 200
    mail_id = first.json()["id"]

    replay_nonce = _nonce()
    replay_headers = _headers(body, nonce=replay_nonce)
    assert client.post("/api/v1/mail/receive", content=body, headers=replay_headers).status_code == 200
    replay = client.post("/api/v1/mail/receive", content=body, headers=replay_headers)
    assert replay.status_code == 401
    with get_conn() as conn:
        audit_row = conn.execute(
            "SELECT action, result FROM audit_event WHERE action='mail_nonce_replay' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert audit_row and audit_row["result"] == "401"

    duplicate = _post(client, body, nonce=_nonce())
    assert duplicate.status_code == 200
    assert duplicate.json() == {"id": mail_id, "duplicate": True}


def test_missing_message_id_fallback_is_deterministic_and_invalid_ids_reject_without_nonce(client):
    body = _simple_mail(message_id=None)
    first = _post(client, body, nonce=_nonce())
    assert first.status_code == 200
    second = _post(client, body, nonce=_nonce())
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    with get_conn() as conn:
        row = conn.execute("SELECT message_id FROM mail_message WHERE id=?", (first.json()["id"],)).fetchone()
    assert row and row["message_id"] == "generated-" + hashlib.sha256(body).hexdigest()

    for headers in [
        ["Message-ID: <one@example.test>", "Message-ID: <two@example.test>"],
        ["Message-ID: invalid-no-at-sign"],
        ["Message-ID: <missing-end@example.test"],
    ]:
        invalid = _simple_mail(message_id=None, extra_headers=headers + ['Content-Type: text/plain; charset="utf-8"'])
        nonce = _nonce()
        response = _post(client, invalid, nonce=nonce)
        assert response.status_code == 422
        assert not _has_nonce(nonce)


def test_multipart_transfer_encodings_unknown_charset_and_html_attachment_classification(client):
    message_id = _message_id()
    boundary = "encoding-" + uuid4().hex
    body = (
        f"From: sender@example.test\r\nTo: inbox@example.test\r\nSubject: encodings\r\n"
        f"Message-ID: {message_id}\r\nMIME-Version: 1.0\r\n"
        f'Content-Type: multipart/alternative; boundary="{boundary}"\r\n\r\n'
        f"--{boundary}\r\nContent-Type: text/plain; charset=x-unknown\r\n"
        f"Content-Transfer-Encoding: base64\r\n\r\nbGluZSBmcm9tIGJhc2U2NA==\r\n"
        f"--{boundary}\r\nContent-Type: text/html; charset=utf-8\r\n"
        f"Content-Transfer-Encoding: quoted-printable\r\n\r\n<p>quoted=20html</p>\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    response = _post(client, body)
    assert response.status_code == 200, "multipart receipt must succeed"
    with get_conn() as conn:
        detail = conn.execute(
            "SELECT text_body, html_body FROM mail_message WHERE id=?", (response.json()["id"],)
        ).fetchone()
    assert detail and "line from base64" in detail["text_body"]
    assert detail and "quoted html" in detail["html_body"]

    attachment = _multipart_attachment(_message_id())
    received = _post(client, attachment)
    assert received.status_code == 200, "attachment receipt must succeed"
    with get_conn() as conn:
        attachment_detail = conn.execute(
            "SELECT text_body, html_body FROM mail_message WHERE id=?", (received.json()["id"],)
        ).fetchone()
        attachment_rows = conn.execute(
            "SELECT mime_type FROM mail_attachment WHERE message_id=?", (received.json()["id"],)
        ).fetchall()
        row = conn.execute(
            "SELECT a.object_key, a.sha256, ma.message_id FROM mail_attachment ma "
            "JOIN asset a ON a.id=ma.asset_id JOIN mail_message m ON m.id=ma.message_id "
            "WHERE ma.message_id=?",
            (received.json()["id"],),
        ).fetchone()
    assert attachment_detail and attachment_detail["text_body"] == "plain body"
    assert attachment_detail and attachment_detail["html_body"] == ""
    assert [item["mime_type"] for item in attachment_rows] == ["text/html"]
    assert row and (settings.mail_attachments_dir / row["object_key"]).is_file()
    assert row["sha256"] == hashlib.sha256(b"<p>attachment 0</p>").hexdigest()


def test_attachment_cap_does_not_silently_drop_parts_or_consume_nonce(client):
    body = _multipart_attachment(_message_id(), attachment_count=101)
    nonce = _nonce()
    response = _post(client, body, nonce=nonce)
    assert response.status_code == 422
    assert not _has_nonce(nonce)


def test_nh3_removes_dangerous_content_and_denies_relative_urls(client):
    dangerous = (
        '<p class="bad" id="bad" style="color:red" onclick="alert(1)">keep</p>'
        '<script>script-content</script><style>style-content</style><form>form-content</form>'
        '<a href="javascript:alert(1)">js</a><a href="/relative">relative</a>'
        '<a href="//protocol-relative.test">protocol relative</a><a href="mailto:safe@example.test">mail</a>'
        '<a href="https://safe.example.test/path">safe</a>'
        '<img src="/same-origin.png"><img src="//tracker.invalid/pixel"><img src="mailto:not-an-image"><img src="https://safe.example.test/image.png" onerror="alert(1)">'
    )
    body = _multipart_alternative(_message_id(), dangerous)
    response = _post(client, body)
    assert response.status_code == 200, "html sanitization receipt must succeed"
    with get_conn() as conn:
        row = conn.execute("SELECT html_body FROM mail_message WHERE id=?", (response.json()["id"],)).fetchone()
    assert row is not None
    html = row["html_body"]
    assert "keep" in html
    for forbidden in (
        "script-content", "style-content", "form-content", "<script", "<style", "<form",
        "javascript:", 'href="/relative"', "//protocol-relative.test", 'src="/same-origin.png"',
        "//tracker.invalid", "mailto:not-an-image", "onclick", "onerror", 'class=', 'id=', 'style=',
    ):
        assert forbidden not in html
    assert 'href="mailto:safe@example.test"' in html
    assert 'href="https://safe.example.test/path"' in html
    assert 'src="https://safe.example.test/image.png"' in html
    assert 'rel="noopener noreferrer nofollow"' in html


def test_private_inbox_rejects_anonymous_requests():
    anonymous = TestClient(app)
    assert anonymous.get("/api/v1/mail").status_code == 401
