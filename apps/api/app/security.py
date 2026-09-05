"""安全原语:Argon2id、JWT(kid 轮换)、CSRF、HMAC。不记录任何 secret。"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from .config import settings

_ph = PasswordHasher()

ACCESS_COOKIE = "star_isle_access"
REFRESH_COOKIE = "star_isle_refresh"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(hash_str: str, password: str) -> bool:
    try:
        return _ph.verify(hash_str, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------- JWT ----------

def _active_jwt_secret() -> str:
    return settings.auth_jwt_signing_key


def _verification_jwt_secret(token: str) -> str | None:
    """Select exactly one configured HS256 key from the protected header kid."""

    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        return None
    if not isinstance(header, dict) or header.get("alg") != "HS256":
        return None
    kid = header.get("kid")
    if not isinstance(kid, str):
        return None
    if kid == settings.auth_jwt_active_kid:
        return settings.auth_jwt_signing_key
    return settings.auth_jwt_previous_keys.get(kid)


def sign_access_token(admin_id: str, session_id: str) -> str:
    now = now_utc()
    payload = {
        "sub": admin_id,
        "iat": now,
        "exp": now + timedelta(minutes=settings.auth_access_ttl_minutes),
        "type": "access",
        # access JWT 只引用服务端可撤销 refresh session；不把 JWT 本身存到浏览器可读位置。
        "sid": session_id,
        "kid": settings.auth_jwt_active_kid,
    }
    return jwt.encode(payload, _active_jwt_secret(), algorithm="HS256", headers={"kid": settings.auth_jwt_active_kid})


def verify_access_token(token: str) -> dict[str, Any] | None:
    secret = _verification_jwt_secret(token)
    if secret is None:
        return None
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        header = jwt.get_unverified_header(token)
        if (
            payload.get("type") != "access"
            or not isinstance(header.get("kid"), str)
            or payload.get("kid") != header["kid"]
        ):
            return None
        return payload
    except jwt.PyJWTError:
        return None


# ---------- CSRF ----------

def new_csrf_token() -> tuple[str, str]:
    """返回 (明文 token, token 哈希);数据库只存哈希。"""
    token = secrets.token_urlsafe(32)
    return token, sha256_hex(token.encode())


def verify_csrf(token: str, token_hash: str) -> bool:
    return hmac.compare_digest(sha256_hex(token.encode()), token_hash)


# ---------- 邮件 HMAC ----------

def hmac_payload(timestamp: str, nonce: str, body_sha256: str) -> str:
    return f"{timestamp}.{nonce}.{body_sha256}"


def hmac_payload_v1(kid: str, timestamp: str, nonce: str, body_sha256: str) -> str:
    """Return the kid-bound mail HMAC payload for the rotation protocol."""

    return f"v1.{kid}.{timestamp}.{nonce}.{body_sha256}"


def compute_hmac_for_secret(secret: str, payload: str) -> str:
    """Sign one already-canonical payload with one explicitly selected secret."""

    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def compute_hmac(timestamp: str, nonce: str, body_sha256: str) -> str:
    return compute_hmac_for_secret(settings.mail_hmac_key, hmac_payload(timestamp, nonce, body_sha256))


def verify_hmac(timestamp: str, nonce: str, body_sha256: str, signature: str) -> bool:
    expected = compute_hmac(timestamp, nonce, body_sha256)
    return hmac.compare_digest(expected, signature)


def verify_hmac_with_key(secret: str, payload: str, signature: str) -> bool:
    """Verify with one caller-selected mail key; callers must never scan a key set."""

    return hmac.compare_digest(compute_hmac_for_secret(secret, payload), signature)


def random_id(prefix: str = "") -> str:
    return f"{prefix}{secrets.token_hex(12)}"
