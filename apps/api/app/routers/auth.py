"""鉴权端点:CSRF / login / refresh / logout / me。JWT 仅存在于 HttpOnly Cookie。"""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, Request, Response
from pydantic import BaseModel, Field

from ..config import settings
from ..db import audit, get_conn
from ..deps import (
    AuthError,
    active_refresh_session_id,
    get_request_id,
    issue_csrf,
    make_cookie,
    require_auth,
)
from ..security import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    iso,
    now_utc,
    sha256_hex,
    sign_access_token,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class CsrfResponse(BaseModel):
    csrfToken: str
    expiresAt: str


class UserSummary(BaseModel):
    id: str
    role: str
    sessionExpiresAt: str


@router.get("/csrf", response_model=CsrfResponse)
def get_csrf(request: Request) -> CsrfResponse:
    """匿名握手可用；已有 refresh cookie 时 token 必须绑定其服务端 session。"""
    session_id = active_refresh_session_id(request.cookies.get(REFRESH_COOKIE))
    token = issue_csrf(session_id)
    return CsrfResponse(csrfToken=token, expiresAt=iso(now_utc() + timedelta(hours=8)))


def _refresh_ttl() -> timedelta:
    return timedelta(days=settings.auth_refresh_ttl_days)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _recent_login_failures(conn, ip: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM audit_event WHERE action='login_fail' AND actor=? AND created_at > ?",
        (f"ip:{ip}", iso(now_utc() - timedelta(minutes=5))),
    ).fetchone()
    return int(row["c"] if row else 0)


def _reject_if_login_limited(ip: str, request: Request) -> None:
    """提交限速审计后再抛出，避免异常回滚把 login_limited 记录抹掉。"""

    limited = False
    with get_conn(immediate=True) as conn:
        if _recent_login_failures(conn, ip) >= 10:
            audit(
                "login_limited",
                actor=f"ip:{ip}",
                request_id=get_request_id(request),
                result="429",
                conn=conn,
            )
            limited = True
    if limited:
        raise AuthError("RATE_LIMITED", "尝试过于频繁,请稍候再试。", 429)


def _new_refresh_session_values(admin_id: str, user_agent: str | None) -> tuple[str, str, str]:
    session_id = secrets.token_urlsafe(24)
    refresh_token = secrets.token_urlsafe(48)
    expires = now_utc() + _refresh_ttl()
    return session_id, refresh_token, iso(expires)


def _insert_refresh_session(
    conn, admin_id: str, user_agent: str | None
) -> tuple[str, str, str]:
    session_id, refresh_token, expires = _new_refresh_session_values(admin_id, user_agent)
    conn.execute(
        "INSERT INTO session (id, admin_id, refresh_hash, expires_at, revoked_at, user_agent, created_at) "
        "VALUES (?, ?, ?, ?, NULL, ?, ?)",
        (
            session_id,
            admin_id,
            sha256_hex(refresh_token.encode()),
            expires,
            (user_agent or "")[:200],
            iso(now_utc()),
        ),
    )
    return session_id, refresh_token, expires


def _set_token_cookies(response: Response, admin_id: str, session_id: str, refresh_token: str) -> None:
    access = sign_access_token(admin_id, session_id)
    response.set_cookie(**make_cookie(ACCESS_COOKIE, access, settings.auth_access_ttl_minutes * 60))
    response.set_cookie(**make_cookie(REFRESH_COOKIE, refresh_token, int(_refresh_ttl().total_seconds())))


@router.post("/login", response_model=UserSummary)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> UserSummary:
    """单密码登录;错误 401 不泄露账号细节;限速 429。"""
    # CSRF + Origin 校验由调用方在 main 中统一挂依赖,这里显式再校验一次
    from ..deps import require_csrf as _rc

    _rc(request, x_csrf_token)

    # 登录限速:按来源 IP 的 5 分钟窗口计数。历史 login_fail 留给审计保留期，不在这里删除。
    ip = _client_ip(request)
    _reject_if_login_limited(ip, request)

    ensure_bootstrap()
    with get_conn() as conn:
        admin = conn.execute("SELECT * FROM admin LIMIT 1").fetchone()

    if admin is None or not verify_password(admin["password_hash"], body.password):
        _reject_if_login_limited(ip, request)
        with get_conn(immediate=True) as conn:
            if _recent_login_failures(conn, ip) >= 10:
                audit(
                    "login_limited",
                    actor=f"ip:{ip}",
                    request_id=get_request_id(request),
                    result="429",
                    conn=conn,
                )
                limited = True
            else:
                audit("login_fail", actor=f"ip:{ip}", request_id=get_request_id(request), result="401", conn=conn)
                limited = False
        if limited:
            raise AuthError("RATE_LIMITED", "尝试过于频繁,请稍候再试。", 429)
        raise AuthError("AUTH_REQUIRED", "无法登录,请检查密码后重试。", 401)

    # 登录前 token 是匿名握手 token；在同一写事务内把它迁移到新 refresh session。
    with get_conn(immediate=True) as conn:
        session_id, refresh_token, session_expires_at = _insert_refresh_session(
            conn, admin["id"], request.headers.get("user-agent")
        )
        bound = conn.execute(
            "UPDATE csrf SET session_id=? WHERE token_hash=? AND expires_at > ?",
            (session_id, sha256_hex((x_csrf_token or "").encode()), iso(now_utc())),
        ).rowcount
        if bound != 1:
            raise AuthError("CSRF_REQUIRED", "CSRF 握手已失效,请重新获取。", 403)
    _set_token_cookies(response, admin["id"], session_id, refresh_token)
    audit("login", actor=admin["id"], request_id=get_request_id(request), result="ok")
    return UserSummary(id=admin["id"], role=admin["role"], sessionExpiresAt=session_expires_at)


def ensure_bootstrap():
    from ..deps import ensure_bootstrap as _eb

    _eb()


@router.post("/refresh", response_model=UserSummary)
def refresh(
    request: Request,
    response: Response,
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> UserSummary:
    from ..deps import require_csrf as _rc

    _rc(request, x_csrf_token)
    if not refresh_token:
        raise AuthError("AUTH_REQUIRED", "需要有效会话")
    hash_value = sha256_hex(refresh_token.encode())
    with get_conn(immediate=True) as conn:
        row = conn.execute(
            "SELECT * FROM session WHERE refresh_hash=? AND revoked_at IS NULL AND expires_at > ?",
            (hash_value, iso(now_utc())),
        ).fetchone()
        if row is None:
            raise AuthError("AUTH_REQUIRED", "会话已失效,请重新登录")
        admin = conn.execute("SELECT * FROM admin WHERE id=?", (row["admin_id"],)).fetchone()
        if admin is None:
            raise AuthError("AUTH_REQUIRED", "会话已失效,请重新登录")
        # 轮换与 CSRF 迁移必须同事务：旧 refresh/session 立即失效，新 token 保持可用。
        session_id, new_refresh_token, session_expires_at = _insert_refresh_session(
            conn, admin["id"], request.headers.get("user-agent")
        )
        conn.execute("UPDATE session SET revoked_at=? WHERE id=?", (iso(now_utc()), row["id"]))
        conn.execute("UPDATE csrf SET session_id=? WHERE session_id=?", (session_id, row["id"]))
    _set_token_cookies(response, admin["id"], session_id, new_refresh_token)
    return UserSummary(id=admin["id"], role=admin["role"], sessionExpiresAt=session_expires_at)


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> Response:
    from ..deps import require_csrf as _rc

    _rc(request, x_csrf_token)
    if refresh_token:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT id FROM session WHERE refresh_hash=? AND revoked_at IS NULL",
                (sha256_hex(refresh_token.encode()),),
            ).fetchone()
            if row is not None:
                conn.execute("UPDATE session SET revoked_at=? WHERE id=?", (iso(now_utc()), row["id"]))
                conn.execute("DELETE FROM csrf WHERE session_id=?", (row["id"],))
    # 必须在实际返回的同一个 Response 上清除 Cookie，保留 path/secure/samesite 匹配属性。
    response.delete_cookie(ACCESS_COOKIE, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax")
    response.delete_cookie(REFRESH_COOKIE, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax")
    response.status_code = 204
    audit("logout", actor="admin", request_id=get_request_id(request), result="ok")
    return response


@router.get("/me", response_model=UserSummary)
def me(payload: dict = Depends(require_auth)) -> UserSummary:
    with get_conn() as conn:
        admin = conn.execute(
            "SELECT a.id, a.role, s.expires_at FROM session s "
            "JOIN admin a ON a.id=s.admin_id "
            "WHERE s.id=? AND s.admin_id=? AND s.revoked_at IS NULL AND s.expires_at > ?",
            (payload["sid"], payload["sub"], iso(now_utc())),
        ).fetchone()
    if admin is None:
        raise AuthError("AUTH_REQUIRED", "会话已失效,请重新登录")
    return UserSummary(id=admin["id"], role=admin["role"], sessionExpiresAt=admin["expires_at"])
