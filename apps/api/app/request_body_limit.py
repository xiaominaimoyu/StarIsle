"""Route-aware ASGI request-body limits before FastAPI parses a request."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from fastapi import HTTPException as FastAPIHTTPException

try:
    from fastapi.routing import iter_route_contexts as _fastapi_iter_route_contexts
except ImportError:  # pragma: no cover - FastAPI versions before route contexts.
    _fastapi_iter_route_contexts = None

from starlette.formparsers import MultiPartException
from starlette.responses import JSONResponse
from starlette.routing import Match, get_route_path

from .observability import _ROUTE_TEMPLATE_SCOPE_KEY, safe_request_id


MULTIPART_OVERHEAD_BYTES = 64 * 1024
FALLBACK_BODY_LIMIT_BYTES = 64 * 1024

BAD_REQUEST_DETAIL = {"code": "BAD_REQUEST", "message": "请求头格式无效。"}
PAYLOAD_TOO_LARGE_DETAIL = {"code": "PAYLOAD_TOO_LARGE", "message": "请求体超过大小上限。"}

# This identity-only sentinel moves through Starlette's multipart-to-
# HTTPException conversion. It is private to this module and never serializable.
_BODY_LIMIT_MARKER = object()


class RequestBodyLimitExceeded(MultiPartException, FastAPIHTTPException):
    """A receive-time overflow that also makes multipart close its spool files."""

    _request_body_limit_marker = _BODY_LIMIT_MARKER

    def __init__(self) -> None:
        MultiPartException.__init__(self, _BODY_LIMIT_MARKER)
        FastAPIHTTPException.__init__(self, status_code=413, detail=_BODY_LIMIT_MARKER)


def is_request_body_limit_overflow(exc: object) -> bool:
    """Recognize the private marker after Starlette converts multipart errors."""

    return (
        isinstance(exc, RequestBodyLimitExceeded)
        or getattr(exc, "_request_body_limit_marker", None) is _BODY_LIMIT_MARKER
        or getattr(exc, "detail", None) is _BODY_LIMIT_MARKER
    )


def should_close_connection(scope: Mapping[str, Any]) -> bool:
    """HTTP/1 early request failures must not reuse an unread request body."""

    return scope.get("http_version") in {"1.0", "1.1"}


@dataclass(frozen=True)
class RequestBodyLimitPolicy:
    """Immutable raw-byte budgets keyed by canonical method and route path."""

    route_limits: Mapping[tuple[str, str], int]
    fallback_limit: int = FALLBACK_BODY_LIMIT_BYTES

    def __post_init__(self) -> None:
        if type(self.fallback_limit) is not int or self.fallback_limit < 0:
            raise ValueError("fallback body limit must be a non-negative integer")
        normalized: dict[tuple[str, str], int] = {}
        for key, limit in self.route_limits.items():
            method, path = key
            if type(method) is not str or type(path) is not str:
                raise ValueError("route body limit keys must be method/path strings")
            if type(limit) is not int or limit < 0:
                raise ValueError("route body limits must be non-negative integers")
            normalized[(method, path)] = limit
        object.__setattr__(self, "route_limits", normalized)

    def limit_for(self, method: object, route: object | None) -> int:
        if method == "OPTIONS":
            return 0
        path = getattr(route, "path", None)
        if not isinstance(method, str) or not isinstance(path, str):
            return self.fallback_limit
        return self.route_limits.get((method, path), 0)


def build_request_body_limit_policy(
    *,
    file_max_bytes: int,
    image_max_bytes: int,
    document_max_bytes: int,
    mail_max_bytes: int,
) -> RequestBodyLimitPolicy:
    """Snapshot the configured route budgets once while the application starts."""

    return RequestBodyLimitPolicy(
        {
            ("POST", "/api/v1/files"): file_max_bytes + MULTIPART_OVERHEAD_BYTES,
            ("POST", "/api/v1/photos"): image_max_bytes + MULTIPART_OVERHEAD_BYTES,
            ("POST", "/api/v1/knowledge/documents"): document_max_bytes + MULTIPART_OVERHEAD_BYTES,
            ("POST", "/api/v1/mail/receive"): mail_max_bytes,
            ("POST", "/api/v1/rag/query"): 128 * 1024,
            ("POST", "/api/v1/auth/login"): 64 * 1024,
            ("POST", "/api/v1/conversations"): 64 * 1024,
            ("POST", "/api/v1/knowledge/documents/{document_id}/rebuild"): 64 * 1024,
            ("POST", "/api/v1/mail/{message_id}/read"): 64 * 1024,
            ("POST", "/api/v1/notes"): 128 * 1024,
            ("PATCH", "/api/v1/notes/{note_id}"): 128 * 1024,
            ("POST", "/api/v1/notes/preview"): 128 * 1024,
            ("PUT", "/api/v1/notes/diary/{day}"): 4 * 1024,
            ("POST", "/api/v1/desk-ask/query"): 128 * 1024,
            ("POST", "/api/v1/desk-ask/index"): 4 * 1024,
            ("POST", "/api/v1/desk-todos"): 16 * 1024,
            ("PATCH", "/api/v1/desk-todos/{todo_id}"): 16 * 1024,
        }
    )


@dataclass(frozen=True)
class _RouteDecision:
    route: object | None
    limit: int


def _iter_route_candidates(router: object) -> Iterable[object]:
    """Yield public FastAPI route contexts, or flattened older-framework routes."""

    routes = getattr(router, "routes", ())
    if _fastapi_iter_route_contexts is not None:
        yield from _fastapi_iter_route_contexts(routes)
        return
    yield from routes


def _first_full_route(router: object, scope: Mapping[str, Any]) -> object | None:
    for route in _iter_route_candidates(router):
        matches = getattr(route, "matches", None)
        if not callable(matches):
            continue
        match, _child_scope = matches(scope)
        if match == Match.FULL:
            return route
    return None


def _alternate_slash_scope(scope: Mapping[str, Any]) -> dict[str, Any] | None:
    """Mirror Starlette's redirect-slash scope transformation without redirecting."""

    path = scope.get("path")
    if not isinstance(path, str):
        return None
    try:
        route_path = get_route_path(scope)
    except (KeyError, TypeError):
        return None
    if route_path == "/":
        return None
    alternate = dict(scope)
    alternate["path"] = path.rstrip("/") if route_path.endswith("/") else path + "/"
    return alternate


def _resolve_route_decision(
    router: object, policy: RequestBodyLimitPolicy, scope: Mapping[str, Any]
) -> _RouteDecision:
    route = _first_full_route(router, scope)
    if route is None:
        alternate_scope = _alternate_slash_scope(scope)
        if alternate_scope is not None:
            route = _first_full_route(router, alternate_scope)
    return _RouteDecision(route=route, limit=policy.limit_for(scope.get("method"), route))


def _parse_framing(scope: Mapping[str, Any]) -> tuple[int | None, bool]:
    """Return declared Content-Length and whether the headers are valid."""

    content_lengths: list[bytes] = []
    has_transfer_encoding = False
    for header in scope.get("headers", ()):  # ASGI specifies raw byte pairs here.
        try:
            name, value = header
        except (TypeError, ValueError):
            return None, False
        if not isinstance(name, bytes) or not isinstance(value, bytes):
            return None, False
        lowered_name = name.lower()
        if lowered_name == b"content-length":
            content_lengths.append(value)
        elif lowered_name == b"transfer-encoding":
            has_transfer_encoding = True

    if len(content_lengths) > 1 or (has_transfer_encoding and content_lengths):
        return None, False
    if not content_lengths:
        return None, True

    raw_value = content_lengths[0]
    if (
        not raw_value
        or len(raw_value) > 20
        or any(byte < ord("0") or byte > ord("9") for byte in raw_value)
    ):
        return None, False
    return int(raw_value), True


def _scope_request_id(scope: dict[str, Any]) -> str:
    """Reuse only request_context's sanitized state, or create standalone-safe state."""

    state = scope.get("state")
    if not isinstance(state, dict):
        state = {}
        scope["state"] = state
    request_id = safe_request_id(state.get("request_id"))
    state["request_id"] = request_id
    return request_id


async def _send_early_error(
    scope: dict[str, Any],
    receive: Any,
    send: Any,
    *,
    status_code: int,
    detail: Mapping[str, str],
    route: object | None,
) -> None:
    if route is not None:
        # route_template() consumes only route.path, never an untrusted raw URL.
        scope["route"] = route
    headers = {"X-Request-Id": _scope_request_id(scope)}
    if should_close_connection(scope):
        headers["Connection"] = "close"
    response = JSONResponse(
        status_code=status_code,
        content={"error": {**detail, "requestId": headers["X-Request-Id"]}},
        headers=headers,
    )
    await response(scope, receive, send)


class RequestBodyLimitMiddleware:
    """Count raw ASGI body chunks lazily, before parser/dependency execution."""

    def __init__(self, app: Any, *, router: object, policy: RequestBodyLimitPolicy) -> None:
        self.app = app
        self.router = router
        self.policy = policy

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        decision = _resolve_route_decision(self.router, self.policy, scope)
        if decision.route is not None:
            route_path = getattr(decision.route, "path", None)
            if isinstance(route_path, str):
                scope[_ROUTE_TEMPLATE_SCOPE_KEY] = route_path
        declared_length, framing_is_valid = _parse_framing(scope)
        if not framing_is_valid:
            await _send_early_error(
                scope,
                receive,
                send,
                status_code=400,
                detail=BAD_REQUEST_DETAIL,
                route=decision.route,
            )
            return
        if declared_length is not None and declared_length > decision.limit:
            await _send_early_error(
                scope,
                receive,
                send,
                status_code=413,
                detail=PAYLOAD_TOO_LARGE_DETAIL,
                route=decision.route,
            )
            return

        total = 0

        async def limited_receive() -> Any:
            nonlocal total
            message = await receive()
            if message.get("type") == "http.request":
                body = message.get("body", b"")
                if body:
                    total += len(body)
                    if total > decision.limit:
                        # Do not catch this here: ExceptionMiddleware can render a
                        # single 413 before response start, and will propagate after it.
                        raise RequestBodyLimitExceeded()
            return message

        await self.app(scope, limited_receive, send)
