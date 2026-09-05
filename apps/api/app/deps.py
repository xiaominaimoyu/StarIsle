"""依赖:鉴权(access Cookie)、CSRF + Origin 校验、请求标识。"""

from __future__ import annotations

import secrets
import uuid
from datetime import timedelta
from typing import Annotated
from urllib.parse import urlparse

from fastapi import Cookie, Header, HTTPException, Request

from .config import settings
from .db import get_conn
from .observability import safe_request_id
from .security import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    iso,
    new_csrf_token,
    now_utc,
    sha256_hex,
    verify_access_token,
    verify_csrf,
)

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_MAX_TOKENS_PER_SCOPE = 100
CSRF_RETAINED_TOKENS_BEFORE_ISSUE = CSRF_MAX_TOKENS_PER_SCOPE - 1


class AuthError(HTTPException):
    def __init__(self, code: str, message: str, http_status: int = 401) -> None:
        super().__init__(
            status_code=http_status,
            detail={"code": code, "message": message, "requestId": uuid.uuid4().hex[:12]},
        )


def ensure_bootstrap():
    """单用户初始化:首次启动以 ADMIN_BOOTSTRAP_PASSWORD_HASH 创建 admin;未配置则拒绝登录。"""
    with get_conn() as conn:
        admin = conn.execute("SELECT id FROM admin LIMIT 1").fetchone()
        if admin is None:
            if not settings.admin_bootstrap_password_hash:
                return
            conn.execute(
                "INSERT INTO admin (id, password_hash, role, created_at, updated_at) VALUES (?, ?, 'admin', ?, ?)",
                ("admin", settings.admin_bootstrap_password_hash, iso(now_utc()), iso(now_utc())),
            )


def require_auth(access_token: Annotated[str | None, Cookie(alias=ACCESS_COOKIE)] = None):
    ensure_bootstrap()
    if not access_token:
        raise AuthError("AUTH_REQUIRED", "需要有效会话")
    payload = verify_access_token(access_token)
    if not payload:
        raise AuthError("AUTH_REQUIRED", "会话已过期,请重新登录")
    session_id = payload.get("sid")
    if not isinstance(session_id, str):
        raise AuthError("AUTH_REQUIRED", "会话已失效,请重新登录")
    with get_conn() as conn:
        session = conn.execute(
            "SELECT 1 FROM session WHERE id=? AND admin_id=? AND revoked_at IS NULL AND expires_at > ?",
            (session_id, payload.get("sub"), iso(now_utc())),
        ).fetchone()
    if session is None:
        raise AuthError("AUTH_REQUIRED", "会话已失效,请重新登录")
    return payload


def require_csrf(
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> None:
    """所有 Cookie unsafe 请求必须携带会话匹配的 CSRF 与同源 Origin。"""
    origin = request.headers.get("origin")
    host = request.headers.get("host", "")
    if not origin:
        raise AuthError("CSRF_REQUIRED", "缺少 Origin", 403)
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"} or parsed.netloc != host:
        raise AuthError("CSRF_REQUIRED", "Origin 校验失败", 403)
    if not x_csrf_token:
        raise AuthError("CSRF_REQUIRED", "缺少 CSRF 令牌", 403)

    refresh_token = request.cookies.get(REFRESH_COOKIE)
    with get_conn() as conn:
        session_id: str | None = None
        if refresh_token:
            session = conn.execute(
                "SELECT id FROM session WHERE refresh_hash=? AND revoked_at IS NULL AND expires_at > ?",
                (sha256_hex(refresh_token.encode()), iso(now_utc())),
            ).fetchone()
            if session is None:
                raise AuthError("CSRF_REQUIRED", "会话 CSRF 绑定已失效", 403)
            session_id = session["id"]
        if session_id is None:
            rows = conn.execute(
                "SELECT token_hash FROM csrf WHERE session_id IS NULL AND expires_at > ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (iso(now_utc()), CSRF_MAX_TOKENS_PER_SCOPE),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT token_hash FROM csrf WHERE session_id=? AND expires_at > ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (session_id, iso(now_utc()), CSRF_MAX_TOKENS_PER_SCOPE),
            ).fetchall()
    valid = any(verify_csrf(x_csrf_token, row["token_hash"]) for row in rows)
    if not valid:
        raise AuthError("CSRF_REQUIRED", "CSRF 校验失败", 403)


def issue_csrf(session_id: str | None = None) -> str:
    token, token_hash = new_csrf_token()
    with get_conn() as conn:
        issued_at = now_utc()
        issued_at_iso = iso(issued_at)
        expires_at = iso(issued_at + timedelta(hours=8))
        conn.execute("DELETE FROM csrf WHERE expires_at <= ?", (issued_at_iso,))
        if session_id is None:
            conn.execute(
                "DELETE FROM csrf WHERE id IN ("
                "SELECT id FROM csrf WHERE session_id IS NULL AND expires_at > ? "
                "ORDER BY created_at DESC, id DESC LIMIT -1 OFFSET ?"
                ")",
                (issued_at_iso, CSRF_RETAINED_TOKENS_BEFORE_ISSUE),
            )
        else:
            conn.execute(
                "DELETE FROM csrf WHERE id IN ("
                "SELECT id FROM csrf WHERE session_id=? AND expires_at > ? "
                "ORDER BY created_at DESC, id DESC LIMIT -1 OFFSET ?"
                ")",
                (session_id, issued_at_iso, CSRF_RETAINED_TOKENS_BEFORE_ISSUE),
            )
        conn.execute(
            "INSERT INTO csrf (id, session_id, token_hash, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (secrets.token_hex(8), session_id, token_hash, expires_at, issued_at_iso),
        )
    return token


def active_refresh_session_id(refresh_token: str | None) -> str | None:
    """只返回尚未撤销且未过期的 refresh session，用于 CSRF 握手绑定。"""
    if not refresh_token:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM session WHERE refresh_hash=? AND revoked_at IS NULL AND expires_at > ?",
            (sha256_hex(refresh_token.encode()), iso(now_utc())),
        ).fetchone()
    return row["id"] if row else None


def make_cookie(name: str, value: str, max_age: int) -> dict:
    return {
        "key": name,
        "value": value,
        "max_age": max_age,
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": "lax",
        "path": "/",
    }


def get_request_id(request: Request) -> str:
    """优先使用 middleware 已净化的 state；直接调用也绝不回显原始 header。"""
    try:
        state_value = getattr(request.state, "request_id", None)
    except Exception:
        state_value = None
    if state_value is not None:
        return safe_request_id(state_value)
    try:
        header_value = request.headers.get("x-request-id")
    except Exception:
        header_value = None
    return safe_request_id(header_value)
