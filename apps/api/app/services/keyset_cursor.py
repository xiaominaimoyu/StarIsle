"""Opaque, tie-safe keyset cursors for the non-conversation list endpoints."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping

from fastapi import HTTPException


_CURSOR_VERSION = 1
_CURSOR_MAX_LENGTH = 512
_SCOPE_MAX_LENGTH = 64
_SORT_VALUE_MAX_LENGTH = 256
_ID_MAX_LENGTH = 128
_CONTEXT_MAX_LENGTH = 256
_INVALID_CURSOR_DETAIL = {"code": "UNPROCESSABLE", "message": "游标无效。"}
_BASE64URL_BYTES = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def _invalid_cursor() -> None:
    """Keep malformed and mismatched cursors intentionally indistinguishable."""

    raise HTTPException(422, detail=_INVALID_CURSOR_DETAIL)


def _object_pairs(pairs: list[tuple[object, object]]) -> dict[str, object]:
    """Reject duplicate keys at every JSON object level."""

    result: dict[str, object] = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise ValueError("invalid cursor object")
        result[key] = value
    return result


def _valid_string(value: object, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and value.isprintable()
        and len(value) <= maximum
    )


def _valid_context(context: object) -> bool:
    if not isinstance(context, dict) or not context or len(context) > 8:
        return False
    for key, value in context.items():
        if not _valid_string(key, _CONTEXT_MAX_LENGTH):
            return False
        if isinstance(value, str) and (
            len(value) > _CONTEXT_MAX_LENGTH
            or value != value.strip()
            or not value.isprintable()
        ):
            return False
        if value is not None and type(value) not in (str, bool, int):
            return False
    return True


def encode_keyset_cursor(
    *,
    scope: str,
    sort_value: str,
    item_id: str,
    context: Mapping[str, str | bool | int | None] | None = None,
) -> str:
    """Encode a versioned page anchor generated from a row returned by SQL."""

    if (
        not _valid_string(scope, _SCOPE_MAX_LENGTH)
        or not _valid_string(sort_value, _SORT_VALUE_MAX_LENGTH)
        or not _valid_string(item_id, _ID_MAX_LENGTH)
        or (context is not None and not _valid_context(dict(context)))
    ):
        raise ValueError("invalid keyset cursor input")

    payload: dict[str, object] = {
        "v": _CURSOR_VERSION,
        "scope": scope,
        "sortValue": sort_value,
        "id": item_id,
    }
    if context is not None:
        payload["context"] = dict(context)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_keyset_cursor(
    cursor: str | None,
    *,
    scope: str,
    context: Mapping[str, str | bool | int | None] | None = None,
) -> tuple[str, str] | None:
    """Return ``(sort_value, id)`` or reject a cursor outside this exact query."""

    if cursor is None:
        return None
    if not cursor or len(cursor) > _CURSOR_MAX_LENGTH:
        _invalid_cursor()
    try:
        encoded = cursor.encode("ascii")
        if any(char not in _BASE64URL_BYTES for char in encoded):
            raise ValueError("not base64url")
        decoded = base64.b64decode(
            encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True
        )
        parsed = json.loads(decoded.decode("utf-8"), object_pairs_hook=_object_pairs)
        if not isinstance(parsed, dict) or type(parsed.get("v")) is not int:
            raise ValueError("cursor version is invalid")
        expected_fields = {"v", "scope", "sortValue", "id"}
        if context is not None:
            expected_fields.add("context")
        if set(parsed) != expected_fields or parsed["v"] != _CURSOR_VERSION:
            raise ValueError("cursor fields are invalid")
        if not _valid_string(parsed.get("scope"), _SCOPE_MAX_LENGTH) or parsed["scope"] != scope:
            raise ValueError("cursor scope is invalid")
        if not _valid_string(parsed.get("sortValue"), _SORT_VALUE_MAX_LENGTH):
            raise ValueError("cursor sort value is invalid")
        if not _valid_string(parsed.get("id"), _ID_MAX_LENGTH):
            raise ValueError("cursor id is invalid")
        if context is not None:
            parsed_context = parsed.get("context")
            if not _valid_context(parsed_context) or parsed_context != dict(context):
                raise ValueError("cursor context is invalid")
        return parsed["sortValue"], parsed["id"]
    except (
        UnicodeEncodeError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
        OverflowError,
        TypeError,
        ValueError,
    ):
        _invalid_cursor()
