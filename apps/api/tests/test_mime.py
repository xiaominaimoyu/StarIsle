"""MIME 魔数与图片尺寸边界的直接单元测试。"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.routers.files import _validate_image_header_dimensions
from app.services.mime import ext_for, sniff_mime, validate_upload


@pytest.mark.parametrize(
    ("declared", "data"),
    [
        ("image/jpeg", b"\xff\xd8\xff\xe0"),
        ("image/png", b"\x89PNG\r\n\x1a\n"),
        ("image/gif", b"GIF87a"),
        ("image/gif", b"GIF89a"),
        ("application/pdf", b"%PDF-1.7"),
        ("text/plain", b"plain utf-8 text"),
        ("text/markdown", b"\xef\xbb\xbf# heading"),
    ],
)
def test_sniff_mime_accepts_supported_declared_content(declared: str, data: bytes) -> None:
    assert sniff_mime(data, declared) == declared


def test_sniff_mime_accepts_only_a_structurally_valid_webp_header() -> None:
    assert sniff_mime(b"RIFF\x04\x00\x00\x00WEBPVP8 ", "image/webp") == "image/webp"


@pytest.mark.parametrize(
    "data",
    [
        b"RIFF",
        b"RIFF\x04\x00\x00\x00NOPEVP8 ",
        b"RIFFWEBPpayload",
    ],
)
def test_sniff_mime_rejects_incomplete_or_misplaced_webp_markers(data: bytes) -> None:
    assert sniff_mime(data, "image/webp") == "application/octet-stream"


@pytest.mark.parametrize(
    ("data", "declared"),
    [
        (b"", "image/png"),
        (b"not-a-png", "image/png"),
        (b"\x89PNG\r\n\x1a\n", "image/jpeg"),
        (b"\xff", "text/plain"),
        (b"any", "application/x-unknown"),
    ],
)
def test_sniff_mime_rejects_empty_disguised_or_unsupported_content(data: bytes, declared: str) -> None:
    assert sniff_mime(data, declared) == "application/octet-stream"


def test_empty_text_is_valid_utf8_content() -> None:
    assert sniff_mime(b"", "text/plain") == "text/plain"


def test_validate_upload_enforces_size_and_rejects_generic_binary() -> None:
    assert validate_upload(b"abc", "text/plain", 3) == (True, None)
    assert validate_upload(b"abcd", "text/plain", 3) == (False, "文件超过大小上限,请压缩后重试。")
    assert validate_upload(b"abc", "application/octet-stream", 3) == (False, "暂不支持该文件类型。")


@pytest.mark.parametrize(
    ("mime", "suffix"),
    [("image/jpeg", ".jpg"), ("image/png", ".png"), ("text/markdown", ".md"), ("unknown", ".bin")],
)
def test_ext_for_supported_and_unknown_mime(mime: str, suffix: str) -> None:
    assert ext_for(mime) == suffix


class _ImageHeader:
    def __init__(self, size: tuple[int, int]):
        self.size = size


@pytest.mark.parametrize("size", [(8000, 5000), (8000, 1)])
def test_image_header_dimension_boundary_allows_limit(size: tuple[int, int]) -> None:
    assert _validate_image_header_dimensions(_ImageHeader(size)) == size


@pytest.mark.parametrize("size", [(8000, 5001), (8001, 1)])
def test_image_header_dimension_boundary_rejects_excess(size: tuple[int, int]) -> None:
    with pytest.raises(HTTPException) as exc_info:
        _validate_image_header_dimensions(_ImageHeader(size))
    assert exc_info.value.status_code == 413
