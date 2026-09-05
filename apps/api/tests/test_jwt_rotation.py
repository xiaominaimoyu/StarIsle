"""HS256 access-token kid rotation: one selected key, never a fallback scan."""

from __future__ import annotations

from datetime import timedelta

import jwt

from app.config import settings
from app.security import now_utc, sign_access_token, verify_access_token


def _access_claims(*, kid: str) -> dict[str, object]:
    now = now_utc()
    return {
        "sub": "admin",
        "sid": "session",
        "type": "access",
        "kid": kid,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }


def _configure_rotation(monkeypatch, *, active_kid: str = "current") -> tuple[str, str]:
    active_secret = "current-signing-secret-for-rotation-tests-0123456789abcdef"
    old_secret = "previous-signing-secret-for-rotation-tests-0123456789abcdef"
    monkeypatch.setattr(settings, "auth_jwt_signing_key", active_secret)
    monkeypatch.setattr(settings, "auth_jwt_active_kid", active_kid)
    monkeypatch.setattr(settings, "auth_jwt_previous_keys", {"previous": old_secret})
    return active_secret, old_secret


def test_new_access_tokens_use_only_the_active_kid(monkeypatch) -> None:
    active_secret, _old_secret = _configure_rotation(monkeypatch)

    token = sign_access_token("admin", "session")

    assert jwt.get_unverified_header(token) == {"alg": "HS256", "kid": "current", "typ": "JWT"}
    assert jwt.decode(token, active_secret, algorithms=["HS256"])["kid"] == "current"


def test_pre_rotation_access_token_is_accepted_via_its_old_kid(monkeypatch) -> None:
    _active_secret, old_secret = _configure_rotation(monkeypatch)
    token = jwt.encode(_access_claims(kid="previous"), old_secret, algorithm="HS256", headers={"kid": "previous"})

    payload = verify_access_token(token)

    assert payload is not None
    assert payload["kid"] == "previous"


def test_unknown_kid_never_falls_back_to_the_active_key(monkeypatch) -> None:
    active_secret, _old_secret = _configure_rotation(monkeypatch)
    token = jwt.encode(_access_claims(kid="unknown"), active_secret, algorithm="HS256", headers={"kid": "unknown"})

    assert verify_access_token(token) is None


def test_missing_kid_never_falls_back_to_the_active_key(monkeypatch) -> None:
    active_secret, _old_secret = _configure_rotation(monkeypatch)
    claims = _access_claims(kid="current")
    token = jwt.encode(claims, active_secret, algorithm="HS256", headers={"typ": "JWT"})

    assert verify_access_token(token) is None


def test_header_and_payload_kid_must_match(monkeypatch) -> None:
    _active_secret, old_secret = _configure_rotation(monkeypatch)
    token = jwt.encode(_access_claims(kid="current"), old_secret, algorithm="HS256", headers={"kid": "previous"})

    assert verify_access_token(token) is None


def test_non_hs256_header_is_rejected_before_key_verification(monkeypatch) -> None:
    active_secret, _old_secret = _configure_rotation(monkeypatch)
    token = jwt.encode(_access_claims(kid="current"), active_secret, algorithm="HS384", headers={"kid": "current"})

    assert verify_access_token(token) is None
