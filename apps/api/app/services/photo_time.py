"""Canonical photo capture-time handling without guessing a timezone."""

from __future__ import annotations

import re
from datetime import datetime


_LEGACY_EXIF_CAPTURE = re.compile(r"^\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}$")
_CANONICAL_LOCAL_CAPTURE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")


def normalize_photo_capture_at(value: object) -> str | None:
    """Return a strict timezone-free wall-clock value for valid EXIF timestamps."""

    if not isinstance(value, str):
        return None
    if _LEGACY_EXIF_CAPTURE.fullmatch(value):
        source_format = "%Y:%m:%d %H:%M:%S"
    elif _CANONICAL_LOCAL_CAPTURE.fullmatch(value):
        source_format = "%Y-%m-%dT%H:%M:%S"
    else:
        return None
    try:
        parsed = datetime.strptime(value, source_format)
    except ValueError:
        return None
    return parsed.strftime("%Y-%m-%dT%H:%M:%S")
