"""MIME 魔数校验与扩展名一致性。"""

from __future__ import annotations

MAGIC: dict[str, list[bytes]] = {
    "image/jpeg": [b"\xff\xd8\xff"],
    "image/png": [b"\x89PNG\r\n\x1a\n"],
    "image/webp": [b"RIFF", b"WEBP"],
    "image/gif": [b"GIF87a", b"GIF89a"],
    "application/pdf": [b"%PDF-"],
    "text/plain": [b"", b"\xef\xbb\xbf"],
    "text/markdown": [b"", b"\xef\xbb\xbf"],
    "application/octet-stream": [b""],
}

EXT_BY_TYPE: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/markdown": ".md",
}


def sniff_mime(data: bytes, declared: str) -> str:
    """校验魔数与声明类型一致性;不一致返回 application/octet-stream。"""
    if declared not in MAGIC:
        return "application/octet-stream"
    if declared == "image/webp":
        if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return declared
        return "application/octet-stream"
    for magic in MAGIC[declared]:
        if magic and data.startswith(magic):
            return declared
    if declared in ("text/plain", "text/markdown"):
        try:
            data[:4096].decode("utf-8")
            return declared
        except UnicodeDecodeError:
            return "application/octet-stream"
    return "application/octet-stream"


def ext_for(mime: str) -> str:
    return EXT_BY_TYPE.get(mime, ".bin")


def validate_upload(data: bytes, declared: str, max_bytes: int) -> tuple[bool, str | None]:
    """返回 (是否允许, 拒绝原因)。大小 / 魔数 / 扩展名一致性。"""
    if len(data) > max_bytes:
        return False, "文件超过大小上限,请压缩后重试。"
    if declared == "application/octet-stream":
        return False, "暂不支持该文件类型。"
    if sniff_mime(data, declared) != declared:
        return False, "文件内容与声明类型不一致,已拒绝。"
    return True, None
