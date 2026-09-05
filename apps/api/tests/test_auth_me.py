"""认证会话摘要回归测试。"""

from __future__ import annotations

from datetime import timedelta

import jwt
from fastapi.testclient import TestClient

from app.config import settings
from app.db import get_conn, init_db
from app.main import app
from app.security import ACCESS_COOKIE, iso, now_utc, sha256_hex, sign_access_token


TEST_PASSWORD = "test-password-12345"


def _login(client: TestClient) -> tuple[str, str]:
    csrf = client.get("/api/v1/auth/csrf").json()["csrfToken"]
    response = client.post(
        "/api/v1/auth/login",
        json={"password": TEST_PASSWORD},
        headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
    )
    assert response.status_code == 200
    refresh = client.cookies.get("star_isle_refresh")
    assert refresh
    with get_conn() as conn:
        session = conn.execute(
            "SELECT id FROM session WHERE refresh_hash=?", (sha256_hex(refresh.encode()),)
        ).fetchone()
    assert session is not None
    return response.json()["id"], session["id"]


def test_me_returns_the_authenticated_sessions_real_near_expiry() -> None:
    init_db()
    client = TestClient(app)
    _, session_id = _login(client)
    near_expiry = iso(now_utc() + timedelta(seconds=90))
    with get_conn() as conn:
        conn.execute("UPDATE session SET expires_at=? WHERE id=?", (near_expiry, session_id))
        stored = conn.execute("SELECT expires_at FROM session WHERE id=?", (session_id,)).fetchone()
    assert stored is not None

    response = client.get("/api/v1/auth/me")

    assert response.status_code == 200
    assert response.json()["sessionExpiresAt"] == stored["expires_at"]


def test_me_rejects_a_revoked_session() -> None:
    init_db()
    client = TestClient(app)
    _, session_id = _login(client)
    with get_conn() as conn:
        conn.execute("UPDATE session SET revoked_at=? WHERE id=?", (iso(now_utc()), session_id))

    response = client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


def test_me_rejects_an_access_token_with_an_unknown_session_id() -> None:
    init_db()
    client = TestClient(app)
    admin_id, _ = _login(client)
    client.cookies.set(ACCESS_COOKIE, sign_access_token(admin_id, "missing-session"))

    response = client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


def test_me_accepts_a_real_pre_rotation_cookie_and_new_tokens_use_active_kid(
    monkeypatch,  # pytest fixture intentionally scopes the temporary key ring.
) -> None:
    init_db()
    client = TestClient(app)
    admin_id, session_id = _login(client)
    pre_rotation_access = client.cookies.get(ACCESS_COOKIE)
    assert pre_rotation_access
    old_header = jwt.get_unverified_header(pre_rotation_access)
    old_payload = jwt.decode(pre_rotation_access, settings.auth_jwt_signing_key, algorithms=["HS256"])

    active_secret = "rotated-active-signing-secret-for-test"
    monkeypatch.setattr(settings, "auth_jwt_signing_key", active_secret)
    monkeypatch.setattr(settings, "auth_jwt_active_kid", "rotated")
    monkeypatch.setattr(settings, "auth_jwt_previous_keys", {old_header["kid"]: "test-only-secret-key-0123456789abcdef"})
    client.cookies.set(ACCESS_COOKIE, pre_rotation_access)

    response = client.get("/api/v1/auth/me")
    new_access = sign_access_token(admin_id, session_id)

    assert old_payload["kid"] == old_header["kid"] == "test"
    assert response.status_code == 200
    assert jwt.get_unverified_header(new_access)["kid"] == "rotated"
    assert jwt.decode(new_access, active_secret, algorithms=["HS256"])["kid"] == "rotated"
