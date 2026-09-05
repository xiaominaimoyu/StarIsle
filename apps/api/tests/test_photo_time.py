"""Unit coverage for timezone-free photo capture timestamps."""

from __future__ import annotations

import pytest

from app.services.photo_time import normalize_photo_capture_at


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026:09:05 10:02:03", "2026-09-05T10:02:03"),
        ("2026-09-05T10:02:03", "2026-09-05T10:02:03"),
        ("2024:02:29 23:59:59", "2024-02-29T23:59:59"),
        ("2023:02:29 10:00:00", None),
        ("2026:00:05 10:00:00", None),
        ("2026:09:05 24:00:00", None),
        ("2026:09:05 10:00:00+08:00", None),
        (" 2026:09:05 10:00:00", None),
        (b"2026:09:05 10:00:00", None),
        (None, None),
        ("", None),
    ],
)
def test_normalize_photo_capture_at(value: object, expected: str | None) -> None:
    assert normalize_photo_capture_at(value) == expected
