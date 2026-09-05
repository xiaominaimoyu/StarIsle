"""Unit coverage for the opaque non-conversation keyset cursor codec."""

from __future__ import annotations

import base64
import json

import pytest
from fastapi import HTTPException

from app.services.keyset_cursor import decode_keyset_cursor, encode_keyset_cursor


def _raw_cursor(payload: str) -> str:
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def _invalid(cursor: str, **kwargs) -> None:
    with pytest.raises(HTTPException) as exc_info:
        decode_keyset_cursor(cursor, **kwargs)
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == {"code": "UNPROCESSABLE", "message": "游标无效。"}


def test_keyset_cursor_round_trip_binds_scope_and_context():
    cursor = encode_keyset_cursor(
        scope="files",
        sort_value="2026-09-05T10:00:00+00:00",
        item_id="asset-2",
        context={"mimeType": "text/plain"},
    )

    assert decode_keyset_cursor(
        cursor, scope="files", context={"mimeType": "text/plain"}
    ) == ("2026-09-05T10:00:00+00:00", "asset-2")
    _invalid(cursor, scope="mail", context={"unreadOnly": False})
    _invalid(cursor, scope="files", context={"mimeType": "image/png"})


@pytest.mark.parametrize(
    ("field", "value"),
    (("scope", " files"), ("sort_value", "time\nvalue"), ("item_id", " ")),
)
def test_keyset_cursor_encoder_rejects_values_its_decoder_cannot_accept(
    field: str, value: str
):
    arguments = {"scope": "files", "sort_value": "time", "item_id": "asset-1"}
    arguments[field] = value

    with pytest.raises(ValueError, match="invalid keyset cursor input"):
        encode_keyset_cursor(**arguments)


@pytest.mark.parametrize(
    "cursor",
    [
        "2026-09-05T10:00:00+00:00",  # old pure-time cursors have no versioned tuple
        "é",
        "a" * 513,
        _raw_cursor('{"v":1,"scope":"photos","sortValue":"x","id":"one","id":"two"}'),
        _raw_cursor(json.dumps({"v": 1, "scope": "photos", "sortValue": "x", "id": "p", "extra": 1})),
        _raw_cursor(json.dumps({"v": 1, "scope": "photos", "sortValue": "x", "id": " "})),
    ],
)
def test_keyset_cursor_rejects_malformed_or_ambiguous_values(cursor: str):
    _invalid(cursor, scope="photos")
