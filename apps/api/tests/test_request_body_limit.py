"""Route-aware raw ASGI request-body gate regressions."""

from __future__ import annotations

import asyncio
import json
import re
import time
from contextlib import contextmanager
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette import formparsers
from starlette.middleware.exceptions import ExceptionMiddleware
from starlette.requests import Request
from starlette.routing import Route, Router

from app.config import settings
from app.db import get_conn, init_db
from app.main import app, http_exception_handler
from app.request_body_limit import (
    BAD_REQUEST_DETAIL,
    PAYLOAD_TOO_LARGE_DETAIL,
    RequestBodyLimitExceeded,
    RequestBodyLimitMiddleware,
    RequestBodyLimitPolicy,
    _resolve_route_decision,
    build_request_body_limit_policy,
)


_HEX_12 = re.compile(r"^[0-9a-f]{12}$")
_LIMITED_PATH = "/limited"


def _scope(
    path: str,
    *,
    method: str = "POST",
    headers: list[tuple[bytes, bytes]] | None = None,
    http_version: str = "1.1",
    root_path: str = "",
    scope_type: str = "http",
) -> dict[str, Any]:
    return {
        "type": scope_type,
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": http_version,
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "root_path": root_path,
        "headers": list(headers or []),
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }


def _request_messages(*chunks: bytes) -> list[dict[str, Any]]:
    return [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]


def _run_asgi(
    application: Any, scope: dict[str, Any], messages: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int]:
    pending = list(messages)
    sent: list[dict[str, Any]] = []
    receive_calls = 0

    async def receive() -> dict[str, Any]:
        nonlocal receive_calls
        receive_calls += 1
        if pending:
            return pending.pop(0)
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    asyncio.run(application(scope, receive, send))
    return sent, receive_calls


def _response(sent: list[dict[str, Any]]) -> tuple[int, dict[bytes, bytes], bytes]:
    start = next(message for message in sent if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
    return start["status"], dict(start["headers"]), body


async def _route_endpoint(_request) -> None:  # pragma: no cover - matching only.
    return None


def _limited_middleware(policy: RequestBodyLimitPolicy, inner: Any) -> RequestBodyLimitMiddleware:
    router = Router(routes=[Route(_LIMITED_PATH, _route_endpoint, methods=["POST"])])
    return RequestBodyLimitMiddleware(inner, router=router, policy=policy)


def _main_policy_entry() -> Any:
    return next(item for item in app.user_middleware if item.cls is RequestBodyLimitMiddleware)


def _main_policy() -> RequestBodyLimitPolicy:
    return _main_policy_entry().kwargs["policy"]


@contextmanager
def _temporary_main_policy(policy: RequestBodyLimitPolicy) -> Iterator[None]:
    entry = _main_policy_entry()
    original = entry.kwargs["policy"]
    entry.kwargs["policy"] = policy
    app.middleware_stack = None
    try:
        yield
    finally:
        entry.kwargs["policy"] = original
        app.middleware_stack = None


def _policy_with_limits(
    overrides: dict[tuple[str, str], int], *, fallback_limit: int | None = None
) -> RequestBodyLimitPolicy:
    original = _main_policy()
    limits = dict(original.route_limits)
    limits.update(overrides)
    return RequestBodyLimitPolicy(
        limits,
        fallback_limit=original.fallback_limit if fallback_limit is None else fallback_limit,
    )


def _persistence_snapshot() -> tuple[dict[str, int], frozenset[str]]:
    """Inspect only pytest's temporary DATA_ROOT for forbidden side effects."""

    init_db()
    table_names = (
        "asset",
        "photo",
        "knowledge_document",
        "ingestion_job",
        "audit_event",
        "mail_message",
        "mail_nonce",
        "mail_attachment",
    )
    with get_conn() as conn:
        counts = {
            table: conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]
            for table in table_names
        }
    files = frozenset(
        str(path.relative_to(settings.data_root))
        for path in settings.data_root.rglob("*")
        if path.is_file()
    )
    return counts, files


def test_default_policy_uses_exact_snapshotted_route_budgets(monkeypatch: pytest.MonkeyPatch) -> None:
    policy = build_request_body_limit_policy(
        file_max_bytes=101,
        image_max_bytes=202,
        document_max_bytes=303,
        mail_max_bytes=404,
    )
    assert policy.route_limits == {
        ("POST", "/api/v1/files"): 101 + 64 * 1024,
        ("POST", "/api/v1/photos"): 202 + 64 * 1024,
        ("POST", "/api/v1/knowledge/documents"): 303 + 64 * 1024,
        ("POST", "/api/v1/mail/receive"): 404,
        ("POST", "/api/v1/rag/query"): 128 * 1024,
        ("POST", "/api/v1/auth/login"): 64 * 1024,
        ("POST", "/api/v1/conversations"): 64 * 1024,
        ("POST", "/api/v1/knowledge/documents/{document_id}/rebuild"): 64 * 1024,
        ("POST", "/api/v1/mail/{message_id}/read"): 64 * 1024,
        ("POST", "/api/v1/notes"): 128 * 1024,
        ("PATCH", "/api/v1/notes/{note_id}"): 128 * 1024,
        ("POST", "/api/v1/notes/preview"): 128 * 1024,
        ("PUT", "/api/v1/notes/diary/{day}"): 4 * 1024,
    }

    main_policy = _main_policy()
    original_mail_limit = main_policy.route_limits[("POST", "/api/v1/mail/receive")]
    monkeypatch.setattr(settings, "mail_max_bytes", 1)
    assert main_policy.route_limits[("POST", "/api/v1/mail/receive")] == original_mail_limit


def test_route_resolution_uses_full_match_canonical_path_and_safe_fallbacks() -> None:
    router = Router(
        routes=[
            Route("/items/{item_id}", _route_endpoint, methods=["POST"]),
            Route("/bodyless", _route_endpoint, methods=["POST"]),
        ]
    )
    policy = RequestBodyLimitPolicy({("POST", "/items/{item_id}"): 9}, fallback_limit=7)

    direct = _resolve_route_decision(router, policy, _scope("/items/decoded-value"))
    assert getattr(direct.route, "path", None) == "/items/{item_id}"
    assert direct.limit == 9

    decoded = _resolve_route_decision(router, policy, _scope("/items/中文"))
    assert getattr(decoded.route, "path", None) == "/items/{item_id}"
    assert decoded.limit == 9

    rooted = _resolve_route_decision(router, policy, _scope("/mount/items/a", root_path="/mount"))
    assert getattr(rooted.route, "path", None) == "/items/{item_id}"
    assert rooted.limit == 9

    trailing = _resolve_route_decision(router, policy, _scope("/items/a/"))
    assert getattr(trailing.route, "path", None) == "/items/{item_id}"
    assert trailing.limit == 9

    method_mismatch = _resolve_route_decision(router, policy, _scope("/items/a", method="GET"))
    assert method_mismatch.route is None and method_mismatch.limit == 7

    unmatched = _resolve_route_decision(router, policy, _scope("/unknown"))
    assert unmatched.route is None and unmatched.limit == 7

    full_bodyless = _resolve_route_decision(router, policy, _scope("/bodyless"))
    assert getattr(full_bodyless.route, "path", None) == "/bodyless"
    assert full_bodyless.limit == 0

    options = _resolve_route_decision(router, policy, _scope("/unknown", method="OPTIONS"))
    assert options.route is None and options.limit == 0


def test_main_fastapi_route_contexts_produce_prefixed_dynamic_canonical_paths() -> None:
    policy = _main_policy()
    rebuild = _resolve_route_decision(
        app.router,
        policy,
        _scope("/api/v1/knowledge/documents/doc-123/rebuild"),
    )
    assert getattr(rebuild.route, "path", None) == "/api/v1/knowledge/documents/{document_id}/rebuild"
    assert rebuild.limit == 64 * 1024

    mail_read = _resolve_route_decision(
        app.router,
        policy,
        _scope("/api/v1/mail/message-123/read"),
    )
    assert getattr(mail_read.route, "path", None) == "/api/v1/mail/{message_id}/read"
    assert mail_read.limit == 64 * 1024

    wrong_method = _resolve_route_decision(app.router, policy, _scope("/api/v1/files", method="PUT"))
    assert wrong_method.route is None and wrong_method.limit == 64 * 1024


def test_declared_oversize_rejects_without_receive_or_downstream_and_sets_safe_local_id() -> None:
    called = 0

    async def downstream(_scope, _receive, _send) -> None:
        nonlocal called
        called += 1

    middleware = _limited_middleware(RequestBodyLimitPolicy({("POST", _LIMITED_PATH): 2}), downstream)
    scope = _scope(_LIMITED_PATH, headers=[(b"content-length", b"3")])
    sent, receive_calls = _run_asgi(middleware, scope, _request_messages(b"abc"))
    status, headers, body = _response(sent)
    payload = json.loads(body)

    assert called == 0 and receive_calls == 0
    assert status == 413 and headers[b"connection"] == b"close"
    assert _HEX_12.fullmatch(headers[b"x-request-id"].decode())
    assert payload == {
        "error": {
            **PAYLOAD_TOO_LARGE_DETAIL,
            "requestId": headers[b"x-request-id"].decode(),
        }
    }
    assert scope["state"]["request_id"] == headers[b"x-request-id"].decode()


@pytest.mark.parametrize(
    "headers, needle",
    [
        ([(b"content-length", b"1"), (b"CONTENT-LENGTH", b"1")], None),
        ([(b"content-length", b"-1")], b"-1"),
        ([(b"content-length", b"+1")], b"+1"),
        ([(b"content-length", b"")], None),
        ([(b"content-length", b" 1")], b" 1"),
        ([(b"content-length", b"1,2")], b"1,2"),
        ([(b"content-length", b"12x")], b"12x"),
        ([(b"content-length", b"1" * 21)], b"1" * 21),
        ([(b"content-length", b"\xff")], b"\xff"),
        ([(b"transfer-encoding", b"chunked"), (b"content-length", b"1")], b"chunked"),
    ],
)
def test_invalid_request_framing_is_fixed_400_without_receive_or_input_echo(
    headers: list[tuple[bytes, bytes]], needle: bytes | None
) -> None:
    called = 0

    async def downstream(_scope, _receive, _send) -> None:
        nonlocal called
        called += 1

    middleware = _limited_middleware(RequestBodyLimitPolicy({("POST", _LIMITED_PATH): 2}), downstream)
    sent, receive_calls = _run_asgi(middleware, _scope(_LIMITED_PATH, headers=headers), _request_messages(b"x"))
    status, response_headers, body = _response(sent)
    payload = json.loads(body)

    assert status == 400 and called == 0 and receive_calls == 0
    assert response_headers[b"connection"] == b"close"
    assert payload["error"]["code"] == BAD_REQUEST_DETAIL["code"]
    assert payload["error"]["message"] == BAD_REQUEST_DETAIL["message"]
    if needle is not None:
        assert needle not in body


def test_valid_declared_length_still_counts_actual_streamed_chunks_without_joining() -> None:
    received: list[bytes] = []
    chunks = (b"a", b"b")

    async def downstream(_scope, receive, send) -> None:
        while True:
            message = await receive()
            received.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = _limited_middleware(RequestBodyLimitPolicy({("POST", _LIMITED_PATH): 2}), downstream)
    messages = _request_messages(*chunks)
    sent, receive_calls = _run_asgi(
        middleware,
        _scope(_LIMITED_PATH, headers=[(b"content-length", b"2")]),
        messages,
    )
    status, _headers, _body = _response(sent)

    assert status == 204 and receive_calls == 2
    assert received == list(chunks)
    assert received[0] is chunks[0] and received[1] is chunks[1]
    assert set(vars(middleware)) == {"app", "router", "policy"}


def test_content_length_zero_and_te_only_exact_cap_are_valid() -> None:
    received: list[bytes] = []

    async def downstream(_scope, receive, send) -> None:
        while True:
            message = await receive()
            received.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = _limited_middleware(RequestBodyLimitPolicy({("POST", _LIMITED_PATH): 2}), downstream)
    zero_sent, zero_receives = _run_asgi(
        middleware,
        _scope(_LIMITED_PATH, headers=[(b"content-length", b"0")]),
        _request_messages(b""),
    )
    exact_sent, exact_receives = _run_asgi(
        middleware,
        _scope(_LIMITED_PATH, headers=[(b"transfer-encoding", b"chunked")]),
        _request_messages(b"a", b"b"),
    )

    assert _response(zero_sent)[0] == 204 and zero_receives == 1
    assert _response(exact_sent)[0] == 204 and exact_receives == 2
    assert received == [b"", b"a", b"b"]


def test_small_declared_length_cannot_bypass_actual_chunk_cap() -> None:
    received: list[bytes] = []

    async def downstream(_scope, receive, _send) -> None:
        received.append((await receive()).get("body", b""))
        await receive()

    middleware = _limited_middleware(RequestBodyLimitPolicy({("POST", _LIMITED_PATH): 2}), downstream)
    with pytest.raises(RequestBodyLimitExceeded):
        _run_asgi(
            middleware,
            _scope(_LIMITED_PATH, headers=[(b"content-length", b"1")]),
            _request_messages(b"a", b"bc"),
        )
    assert received == [b"a"]


@pytest.mark.parametrize(
    "headers",
    [
        [(b"transfer-encoding", b"chunked")],
        [(b"content-encoding", b"gzip")],
    ],
)
def test_framing_without_content_length_is_lazily_counted_at_cap_plus_one(
    headers: list[tuple[bytes, bytes]]
) -> None:
    calls = 0

    async def downstream(_scope, receive, _send) -> None:
        nonlocal calls
        calls += 1
        await receive()
        await receive()

    middleware = _limited_middleware(RequestBodyLimitPolicy({("POST", _LIMITED_PATH): 2}), downstream)
    with pytest.raises(RequestBodyLimitExceeded):
        _run_asgi(middleware, _scope(_LIMITED_PATH, headers=headers), _request_messages(b"ab", b"c"))
    assert calls == 1


def test_response_started_overflow_propagates_without_a_second_response_start() -> None:
    async def response_started(_scope, receive, send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await receive()
        await receive()

    exception_wrapped = ExceptionMiddleware(response_started)
    middleware = _limited_middleware(RequestBodyLimitPolicy({("POST", _LIMITED_PATH): 1}), exception_wrapped)
    scope = _scope(_LIMITED_PATH)
    pending = _request_messages(b"a", b"b")
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return pending.pop(0) if pending else {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    with pytest.raises(RuntimeError, match="response already started"):
        asyncio.run(middleware(scope, receive, send))
    assert [message["type"] for message in sent] == ["http.response.start"]


def test_non_http_scope_bypasses_limiter_unchanged() -> None:
    seen: list[tuple[dict[str, Any], object]] = []

    async def downstream(scope, receive, _send) -> None:
        seen.append((scope, receive))

    middleware = _limited_middleware(RequestBodyLimitPolicy({("POST", _LIMITED_PATH): 0}), downstream)
    scope = _scope("/socket", scope_type="websocket")

    async def receive() -> dict[str, Any]:
        return {"type": "websocket.disconnect"}

    async def send(_message: dict[str, Any]) -> None:
        return None

    asyncio.run(middleware(scope, receive, send))
    assert seen == [(scope, receive)]


@pytest.mark.parametrize(
    ("http_version", "has_connection_close"),
    [("1.0", True), ("1.1", True), ("2", False)],
)
def test_early_errors_keep_request_context_id_and_safe_observability(
    capsys: pytest.CaptureFixture[str], http_version: str, has_connection_close: bool
) -> None:
    policy = _policy_with_limits({("POST", "/api/v1/auth/login"): 2})
    safe_id = "trusted.request-id"
    scope = _scope(
        "/api/v1/auth/login",
        headers=[(b"content-length", b"3"), (b"x-request-id", safe_id.encode())],
        http_version=http_version,
    )
    with _temporary_main_policy(policy):
        sent, receive_calls = _run_asgi(app, scope, _request_messages(b"abc"))
    status, headers, body = _response(sent)
    payload = json.loads(body)

    assert status == 413 and receive_calls == 0
    assert headers[b"x-request-id"].decode() == safe_id
    assert payload["error"]["requestId"] == safe_id
    assert (b"connection" in headers) is has_connection_close
    captured = capsys.readouterr()
    events = [json.loads(line) for line in captured.out.splitlines() if line]
    assert events[-1]["route"] == "/api/v1/auth/login"
    assert events[-1]["status"] == 413
    assert "/api/v1/auth/login" in captured.out
    assert b"abc" not in body


def test_early_invalid_framing_replaces_unsafe_request_id_without_echo(
    capsys: pytest.CaptureFixture[str],
) -> None:
    unsafe = b"unsafe request id"
    policy = _policy_with_limits({("POST", "/api/v1/auth/login"): 2})
    scope = _scope(
        "/api/v1/auth/login",
        headers=[(b"content-length", b"-1"), (b"x-request-id", unsafe)],
    )
    with _temporary_main_policy(policy):
        sent, receive_calls = _run_asgi(app, scope, _request_messages(b"x"))
    status, headers, body = _response(sent)
    request_id = headers[b"x-request-id"].decode()

    assert status == 400 and receive_calls == 0
    assert _HEX_12.fullmatch(request_id)
    assert json.loads(body)["error"] == {**BAD_REQUEST_DETAIL, "requestId": request_id}
    assert unsafe not in body and unsafe.decode() not in str(headers)
    captured = capsys.readouterr().out
    events = [json.loads(line) for line in captured.splitlines() if line]
    assert events[-1]["route"] == "/api/v1/auth/login"
    assert events[-1]["status"] == 400
    assert unsafe.decode() not in captured


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/auth/login",
        "/api/v1/conversations",
        "/api/v1/knowledge/documents/doc-1/rebuild",
        "/api/v1/mail/message-1/read",
        "/api/v1/rag/query",
    ],
)
def test_actual_fastapi_json_routes_reject_before_receive_dependencies_and_persistence(path: str) -> None:
    policy = _policy_with_limits({("POST", _resolve_route_decision(app.router, _main_policy(), _scope(path)).route.path): 3})
    before = _persistence_snapshot()
    scope = _scope(path, headers=[(b"content-type", b"application/json"), (b"content-length", b"4")])
    with _temporary_main_policy(policy):
        sent, receive_calls = _run_asgi(app, scope, _request_messages(b"xxxx"))
    status, _headers, body = _response(sent)

    assert status == 413 and receive_calls == 0
    assert json.loads(body)["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert _persistence_snapshot() == before


def test_actual_fastapi_small_json_still_uses_existing_validation_contract() -> None:
    policy = _policy_with_limits({("POST", "/api/v1/auth/login"): 64})
    with _temporary_main_policy(policy):
        response = TestClient(app).post("/api/v1/auth/login", content=b"{}", headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UNPROCESSABLE"


@pytest.mark.parametrize(
    "path",
    ["/api/v1/files", "/api/v1/photos", "/api/v1/knowledge/documents"],
)
def test_actual_multipart_overflow_closes_spool_and_leaves_no_persistent_side_effects(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    created: list[Any] = []
    original_spooled = formparsers.SpooledTemporaryFile

    class TrackingSpooledTemporaryFile(original_spooled):
        closed_by_limit = False

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            created.append(self)

        def close(self) -> None:
            self.closed_by_limit = True
            super().close()

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", TrackingSpooledTemporaryFile)
    boundary = b"body-limit"
    prefix = (
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="file"; filename="tiny.txt"\r\n'
        b"Content-Type: text/plain\r\n\r\n"
        b"x"
    )
    policy = _policy_with_limits({("POST", path): len(prefix)})
    before = _persistence_snapshot()
    scope = _scope(
        path,
        headers=[(b"content-type", b"multipart/form-data; boundary=" + boundary)],
    )
    with _temporary_main_policy(policy):
        sent, receive_calls = _run_asgi(app, scope, _request_messages(prefix, b"y"))
    status, _headers, body = _response(sent)

    assert status == 413 and receive_calls == 2
    assert json.loads(body)["error"] == {**PAYLOAD_TOO_LARGE_DETAIL, "requestId": json.loads(body)["error"]["requestId"]}
    assert len(created) == 1 and created[0].closed_by_limit
    assert _persistence_snapshot() == before


def test_mail_global_fast_reject_and_stream_overflow_are_not_converted_to_503() -> None:
    path = "/api/v1/mail/receive"
    policy = _policy_with_limits({("POST", path): 3})
    before = _persistence_snapshot()

    direct_scope = _scope(path, headers=[(b"content-length", b"4")])
    with _temporary_main_policy(policy):
        direct_sent, direct_receives = _run_asgi(app, direct_scope, _request_messages(b"xxxx"))
    direct_status, _direct_headers, direct_body = _response(direct_sent)
    assert direct_status == 413 and direct_receives == 0
    assert json.loads(direct_body)["error"]["code"] == "PAYLOAD_TOO_LARGE"

    stream_scope = _scope(
        path,
        headers=[
            (b"transfer-encoding", b"chunked"),
            (b"x-mail-timestamp", str(int(time.time())).encode()),
            (b"x-mail-nonce", b"body-limit-nonce"),
            (b"x-mail-signature", b"0" * 64),
        ],
    )
    with _temporary_main_policy(policy):
        stream_sent, stream_receives = _run_asgi(app, stream_scope, _request_messages(b"abc", b"d"))
    stream_status, stream_headers, stream_body = _response(stream_sent)
    assert stream_status == 413 and stream_status != 503 and stream_receives == 2
    assert stream_headers[b"connection"] == b"close"
    assert json.loads(stream_body)["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert _persistence_snapshot() == before


def test_streamed_overflow_uses_unified_handler_and_http2_does_not_close_connection() -> None:
    policy = _policy_with_limits({("POST", "/api/v1/auth/login"): 2})
    scope = _scope(
        "/api/v1/auth/login",
        headers=[(b"content-encoding", b"gzip")],
        http_version="2",
    )
    with _temporary_main_policy(policy):
        sent, receive_calls = _run_asgi(app, scope, _request_messages(b"ab", b"c"))
    status, headers, body = _response(sent)

    assert status == 413 and receive_calls == 2
    assert b"connection" not in headers
    assert json.loads(body)["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_body_gate_and_downstream_requests_keep_one_dynamic_canonical_route_label(
    capsys: pytest.CaptureFixture[str],
) -> None:
    init_db()
    path = "/api/v1/mail/private-message/read"
    route_template = "/api/v1/mail/{message_id}/read"
    policy = _policy_with_limits({("POST", route_template): 3})
    with _temporary_main_policy(policy):
        declared_sent, _ = _run_asgi(
            app,
            _scope(path, headers=[(b"content-length", b"4")]),
            _request_messages(b"xxxx"),
        )
        stream_sent, _ = _run_asgi(
            app,
            _scope(
                path,
                headers=[(b"content-type", b"application/json"), (b"transfer-encoding", b"chunked")],
            ),
            _request_messages(b"abc", b"d"),
        )
        ordinary_sent, _ = _run_asgi(
            app,
            _scope(path, headers=[(b"content-type", b"application/json")]),
            _request_messages(b"{}"),
        )

    assert _response(declared_sent)[0] == 413
    assert _response(stream_sent)[0] == 413
    assert _response(ordinary_sent)[0] in {401, 422}
    events = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line
    ]
    request_events = [
        event for event in events if event.get("component") == "api" and event.get("event") == "request_completed"
    ]
    assert [(event["status"], event["route"]) for event in request_events] == [
        (413, route_template),
        (413, route_template),
        (_response(ordinary_sent)[0], route_template),
    ]


def test_body_gate_trailing_slash_candidate_uses_canonical_route_label(
    capsys: pytest.CaptureFixture[str],
) -> None:
    policy = _policy_with_limits({("POST", "/api/v1/auth/login"): 3})
    with _temporary_main_policy(policy):
        sent, _ = _run_asgi(
            app,
            _scope("/api/v1/auth/login/", headers=[(b"content-length", b"4")]),
            _request_messages(b"xxxx"),
        )

    assert _response(sent)[0] == 413
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    request_event = next(event for event in events if event.get("event") == "request_completed")
    assert request_event["route"] == "/api/v1/auth/login"


def test_regular_fastapi_http_exception_and_validation_remain_unified_and_do_not_leak_marker() -> None:
    client = TestClient(app)
    unauthorized = client.get("/api/v1/auth/me")
    validation = client.post("/api/v1/auth/login", content=b"{}", headers={"Content-Type": "application/json"})

    assert unauthorized.status_code == 401
    assert unauthorized.json()["error"]["code"] == "AUTH_REQUIRED"
    assert validation.status_code == 422
    assert validation.json()["error"]["code"] == "UNPROCESSABLE"
    assert "request_body_limit" not in unauthorized.text + validation.text


def test_starlette_base_http_handler_preserves_existing_safe_headers() -> None:
    request = Request(_scope("/api/v1/auth/login"))
    response = asyncio.run(
        http_exception_handler(
            request,
            StarletteHTTPException(
                status_code=418,
                detail={"code": "TEAPOT", "message": "固定错误。"},
                headers={"X-Safe-Header": "kept"},
            ),
        )
    )

    assert response.status_code == 418
    assert response.headers["x-safe-header"] == "kept"
    assert json.loads(response.body)["error"]["code"] == "TEAPOT"
