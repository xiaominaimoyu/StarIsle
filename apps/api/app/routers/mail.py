"""邮件:经过 HMAC 认证的 Worker 接收端点与私有收件箱。"""

from __future__ import annotations

import re
import time
import unicodedata
from datetime import timedelta
from email.errors import MessageError
from email import policy
from email.parser import BytesParser
from typing import Annotated

import nh3
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from ..config import settings
from ..db import audit, get_conn
from ..deps import get_request_id, require_auth
from ..services.keyset_cursor import decode_keyset_cursor, encode_keyset_cursor
from ..observability import emit_mail_audit_unavailable, emit_mail_object_cleanup_unavailable
from ..security import (
    hmac_payload_v1,
    iso,
    now_utc,
    random_id,
    sha256_hex,
    verify_hmac,
    verify_hmac_with_key,
)
from ..storage import StorageQuotaExceeded, storage

router = APIRouter(tags=["mail"])

HMAC_WINDOW_SECONDS = 5 * 60
NONCE_TTL_HOURS = 24
MAX_ATTACHMENTS = 100
MAX_MESSAGE_ID_LENGTH = 998
_TIMESTAMP_RE = re.compile(r"^[0-9]{1,16}$")
_NONCE_RE = re.compile(r"^[A-Za-z0-9._~-]{8,128}$")
_SIGNATURE_RE = re.compile(r"^[0-9a-f]{64}$")
_MAIL_KID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _mail_html_attribute_filter(tag: str, attribute: str, value: str) -> str | None:
    """邮件链接可为 mailto，图片源只能是绝对 http/https。"""
    if tag == "img" and attribute == "src" and not value.lower().startswith(("http://", "https://")):
        return None
    return value


# 与当前前端邮件正文容器保持相近的最小标签集。服务端负责保存安全摘要，浏览器
# 仍会在插入 DOM 前进行第二层清洗和默认图片阻止。
MAIL_HTML_CLEANER = nh3.Cleaner(
    tags={
        "p", "br", "a", "strong", "em", "ul", "ol", "li", "blockquote",
        "h1", "h2", "h3", "pre", "code", "img", "table", "thead", "tbody",
        "tr", "th", "td",
    },
    clean_content_tags={"script", "style", "iframe", "object", "embed", "form", "link", "meta", "svg", "math"},
    attributes={
        "a": {"href", "title"},
        "img": {"src", "alt", "title"},
    },
    attribute_filter=_mail_html_attribute_filter,
    link_rel="noopener noreferrer nofollow",
    url_schemes={"http", "https", "mailto"},
    # 相对、协议相对与同源 URL 都不能作为邮件摘要中的可加载 URL 保留。
    url_relative="deny",
)


class MailValidationError(ValueError):
    """不安全或无法可靠解析的单封邮件。"""


def _http_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


def _unprocessable() -> HTTPException:
    return _http_error(422, "UNPROCESSABLE", "邮件格式无效。")


def _unauthorized() -> HTTPException:
    return _http_error(401, "AUTH_REQUIRED", "邮件签名校验失败。")


def _service_unavailable() -> HTTPException:
    return _http_error(503, "INTERNAL", "邮件入口暂不可用。")


def _payload_too_large() -> HTTPException:
    return _http_error(413, "PAYLOAD_TOO_LARGE", "邮件超过大小上限。")


def _safe_verified_audit(action: str, request: Request, result: str) -> None:
    """仅记录已通过 HMAC 的邮件流程；审计失败绝不回滚已提交邮件。"""
    request_id = get_request_id(request)
    try:
        audit(action, request_id=request_id, result=result)
    except Exception:
        emit_mail_audit_unavailable(request_id=request_id)


async def _read_limited_body(request: Request) -> bytes:
    """从 ASGI 流逐块读取，内存中的缓冲绝不超过 MAIL_MAX_BYTES。"""
    max_bytes = settings.mail_max_bytes
    if not isinstance(max_bytes, int) or max_bytes <= 0:
        raise _service_unavailable()

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise _payload_too_large()
        except ValueError:
            # Content-Length 不可信时继续依靠流式硬上限，而不是把它当成正文。
            pass

    body = bytearray()
    async for chunk in request.stream():
        if not chunk:
            continue
        remaining = max_bytes - len(body)
        if len(chunk) > remaining:
            # 即使上游没有正确的 Content-Length，也不会把超过限制的字节放入内存。
            if remaining > 0:
                body.extend(chunk[:remaining])
            raise _payload_too_large()
        body.extend(chunk)

    if not body:
        raise _unprocessable()
    return bytes(body)


def sanitize_html(raw: str) -> str:
    """使用进程级 nh3 Cleaner 生成可供前端二次清洗的安全 HTML 摘要。"""
    return MAIL_HTML_CLEANER.clean(raw)


def _decoded_bytes(part) -> bytes:
    try:
        payload = part.get_payload(decode=True)
    except (TypeError, UnicodeError, ValueError) as exc:
        raise MailValidationError("invalid mime payload") from exc
    if payload is None:
        return b""
    if isinstance(payload, bytes):
        return payload
    return str(payload).encode("utf-8", errors="replace")


def extract_text(part) -> str:
    payload = _decoded_bytes(part)
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeError):
        return payload.decode("utf-8", errors="replace")


def _has_defects(message) -> bool:
    return any(getattr(part, "defects", ()) for part in message.walk())


def _normalize_attachment_name(filename: object, index: int) -> str:
    """文件名只用于展示；对象键仍由 StorageAdapter 随机生成。"""
    value = unicodedata.normalize("NFKC", str(filename or ""))
    value = "".join(char for char in value if not unicodedata.category(char).startswith("C"))
    value = value.replace("/", "_").replace("\\", "_").strip(" .")
    if not value:
        return f"attachment-{index}"
    return value[:255]


def _extract_content(message) -> tuple[str, str, list[tuple[str, str, bytes]]]:
    text_body = ""
    html_body = ""
    attachments: list[tuple[str, str, bytes]] = []

    if _has_defects(message):
        raise MailValidationError("mime defects")

    for part in message.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        disposition = (part.get_content_disposition() or "").lower()
        content_type = part.get_content_type().lower()

        # 先分类附件，避免 text/plain / text/html 附件被误当作正文。
        if filename or disposition == "attachment":
            if len(attachments) >= MAX_ATTACHMENTS:
                raise MailValidationError("attachment limit")
            attachments.append(
                (
                    _normalize_attachment_name(filename, len(attachments) + 1),
                    content_type,
                    _decoded_bytes(part),
                )
            )
            continue
        if content_type == "text/plain" and not text_body:
            text_body = extract_text(part)
        elif content_type == "text/html" and not html_body:
            html_body = sanitize_html(extract_text(part))

    return text_body, html_body, attachments


def _message_id(message, body_sha256: str) -> str:
    values = message.get_all("Message-ID", [])
    if len(values) > 1:
        raise MailValidationError("multiple message ids")
    if not values:
        # 缺失 Message-ID 时仍保持同一完整原始正文的确定性幂等。
        return f"generated-{body_sha256}"

    value_obj = values[0]
    if getattr(value_obj, "defects", ()):
        raise MailValidationError("message id defects")
    value = str(value_obj).strip()
    if (
        not value
        or len(value) > MAX_MESSAGE_ID_LENGTH
        or any(ord(char) < 33 or ord(char) == 127 for char in value)
    ):
        raise MailValidationError("invalid message id")
    if value.startswith("<") or value.endswith(">"):
        if not (value.startswith("<") and value.endswith(">")):
            raise MailValidationError("invalid message id")
        value = value[1:-1]
    if (
        not value
        or "<" in value
        or ">" in value
        or value.count("@") != 1
        or any(not piece for piece in value.split("@", 1))
    ):
        raise MailValidationError("invalid message id")
    return value


def _headers_are_valid(timestamp: str, nonce: str, signature: str) -> bool:
    return bool(
        _TIMESTAMP_RE.fullmatch(timestamp)
        and _NONCE_RE.fullmatch(nonce)
        and _SIGNATURE_RE.fullmatch(signature)
    )


def _mail_verification_secret(kid: str | None) -> str:
    """Select exactly one mail HMAC secret before reading a request body."""

    if kid is None:
        # Legacy requests never use the retry map: only the current secret may verify them.
        return settings.mail_hmac_key
    if not _MAIL_KID_RE.fullmatch(kid):
        raise _unauthorized()
    if kid == settings.mail_hmac_active_kid:
        return settings.mail_hmac_key
    secret = settings.mail_hmac_previous_keys.get(kid)
    if secret is None:
        raise _unauthorized()
    return secret


def _prevalidate_receive_request(request: Request) -> tuple[str, str, str, str | None, str]:
    """拒绝不需读取正文即可判定的配置、头语法和时间窗失败。"""
    if not isinstance(settings.mail_hmac_key, str) or not settings.mail_hmac_key.strip():
        raise _service_unavailable()
    if not isinstance(settings.mail_max_bytes, int) or settings.mail_max_bytes <= 0:
        raise _service_unavailable()

    timestamp = request.headers.get("x-mail-timestamp", "")
    nonce = request.headers.get("x-mail-nonce", "")
    signature = request.headers.get("x-mail-signature", "")
    if not _headers_are_valid(timestamp, nonce, signature):
        raise _unauthorized()

    if abs(time.time() - int(timestamp)) > HMAC_WINDOW_SECONDS:
        raise _unauthorized()

    kid = request.headers.get("x-mail-key-id")
    return timestamp, nonce, signature, kid, _mail_verification_secret(kid)


def _parse_mail_content(
    body_bytes: bytes,
    body_sha256: str,
) -> tuple[object, str, str, str, list[tuple[str, str, bytes]]]:
    """仅把已知 MIME 输入错误转换为 MailValidationError。"""
    try:
        message = BytesParser(policy=policy.default).parsebytes(body_bytes)
    except (MessageError, TypeError, UnicodeError, ValueError) as exc:
        raise MailValidationError("invalid mime") from exc
    if not list(message.items()) or _has_defects(message):
        raise MailValidationError("empty or malformed mime")
    message_id = _message_id(message, body_sha256)
    text_body, html_body, attachments = _extract_content(message)
    return message, message_id, text_body, html_body, attachments


def _nonce_already_used(nonce: str) -> bool:
    now = iso(now_utc())
    with get_conn() as conn:
        return conn.execute(
            "SELECT 1 FROM mail_nonce WHERE nonce=? AND expires_at >= ?", (nonce, now)
        ).fetchone() is not None


def _cleanup_saved_objects(saved_objects: list[tuple[str, str]]) -> None:
    """仅回收本次接收刚分配的私有原文/附件对象。"""
    for subdir, object_key in reversed(saved_objects):
        if subdir not in {"mail-raw", "mail-attachments"}:
            emit_mail_object_cleanup_unavailable()
            continue
        try:
            storage.delete(subdir, object_key)
        except Exception:
            # 清理只针对本次随机键；不能因清理失败泄露异常或影响 nonce 回滚语义。
            emit_mail_object_cleanup_unavailable()


# ---------- Worker 接收(HMAC 认证,非 Cookie) ----------

@router.post("/mail/receive")
async def receive_mail(request: Request):
    timestamp, nonce, signature, kid, verification_secret = _prevalidate_receive_request(request)

    try:
        body_bytes = await _read_limited_body(request)
    except HTTPException:
        raise
    except Exception:
        raise _service_unavailable()

    try:
        body_sha256 = sha256_hex(body_bytes)
        if kid is None:
            signature_valid = verify_hmac(timestamp, nonce, body_sha256, signature)
        else:
            signature_valid = verify_hmac_with_key(
                verification_secret,
                hmac_payload_v1(kid, timestamp, nonce, body_sha256),
                signature,
            )
    except Exception:
        raise _service_unavailable()
    if not signature_valid:
        raise _unauthorized()

    # 未验签失败仅由既有安全请求完成遥测记录，避免匿名请求打开 SQLite 审计写入。
    # HMAC 通过后的已存在 nonce 可用只读查询快速拒绝；不提前插入，避免暂时失败耗尽重试机会。
    try:
        if _nonce_already_used(nonce):
            _safe_verified_audit("mail_nonce_replay", request, "401")
            raise _unauthorized()
    except HTTPException:
        raise
    except Exception:
        _safe_verified_audit("mail_nonce_check_failed", request, "503")
        raise _service_unavailable()

    try:
        message, message_id, text_body, html_body, attachments = _parse_mail_content(body_bytes, body_sha256)
    except MailValidationError:
        _safe_verified_audit("mail_mime_rejected", request, "422")
        raise _unprocessable()
    except Exception:
        # 清洗器或其它内部故障不能伪装成邮件格式错误，也不能消耗 nonce。
        _safe_verified_audit("mail_mime_internal", request, "503")
        raise _service_unavailable()

    saved_objects: list[tuple[str, str]] = []
    replayed = False
    duplicate_id: str | None = None
    mail_id: str | None = None
    try:
        with get_conn() as conn:
            received_at = iso(now_utc())
            conn.execute("DELETE FROM mail_nonce WHERE expires_at < ?", (received_at,))
            nonce_insert = conn.execute(
                "INSERT OR IGNORE INTO mail_nonce (nonce, received_at, expires_at) VALUES (?, ?, ?)",
                (nonce, received_at, iso(now_utc() + timedelta(hours=NONCE_TTL_HOURS))),
            )
            if nonce_insert.rowcount == 0:
                replayed = True
            else:
                existing = conn.execute(
                    "SELECT id FROM mail_message WHERE message_id=?", (message_id,)
                ).fetchone()
                if existing:
                    duplicate_id = existing["id"]
                else:
                    raw_object_key = storage.save(body_bytes, "mail-raw")
                    saved_objects.append(("mail-raw", raw_object_key))
                    mail_id = random_id("mail_")
                    conn.execute(
                        "INSERT INTO mail_message ("
                        "id, message_id, from_addr, to_addr, subject, received_at, text_body, html_body, read_flag, raw_state"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 'stored')",
                        (
                            mail_id,
                            message_id,
                            str(message.get("From", "")),
                            str(message.get("To", "")),
                            str(message.get("Subject", ""))[:300],
                            received_at,
                            text_body[:2_000_000],
                            html_body[:2_000_000],
                        ),
                    )
                    conn.execute(
                        "INSERT INTO mail_raw (message_id, object_key, sha256, size, content_type, created_at) "
                        "VALUES (?, ?, ?, ?, 'message/rfc822', ?)",
                        (mail_id, raw_object_key, body_sha256, len(body_bytes), received_at),
                    )
                    for name, mime_type, payload in attachments:
                        asset_id = random_id("asset_")
                        object_key = storage.save(payload, "mail-attachments")
                        saved_objects.append(("mail-attachments", object_key))
                        conn.execute(
                            "INSERT INTO asset (id, original_name, mime_type, size, object_key, sha256, status, "
                            "storage_class, created_at) VALUES (?, ?, ?, ?, ?, ?, 'active', 'mail-attachment', ?)",
                            (
                                asset_id,
                                name,
                                mime_type,
                                len(payload),
                                object_key,
                                sha256_hex(payload),
                                received_at,
                            ),
                        )
                        conn.execute(
                            "INSERT INTO mail_attachment (id, message_id, asset_id, name, mime_type, size) VALUES (?, ?, ?, ?, ?, ?)",
                            (random_id("att_"), mail_id, asset_id, name, mime_type, len(payload)),
                        )
    except StorageQuotaExceeded:
        _cleanup_saved_objects(saved_objects)
        # 配额可能在释放空间后恢复，不能把本次 nonce 视为已成功消费，也不能向 Worker 发送 413。
        _safe_verified_audit("mail_storage_unavailable", request, "503")
        raise _service_unavailable()
    except Exception:
        _cleanup_saved_objects(saved_objects)
        _safe_verified_audit("mail_persist_failed", request, "503")
        raise _service_unavailable()

    if replayed:
        _safe_verified_audit("mail_nonce_replay", request, "401")
        raise _unauthorized()
    if duplicate_id:
        _safe_verified_audit("mail_idempotent", request, "dup")
        return {"id": duplicate_id, "duplicate": True}
    if mail_id is None:  # 防御性保护：不会暴露内部状态或异常细节。
        _safe_verified_audit("mail_persist_failed", request, "503")
        raise _service_unavailable()

    _safe_verified_audit("mail_receive", request, "ok")
    return {"id": mail_id, "duplicate": False}


# ---------- 收件箱(受保护) ----------

@router.get("/mail")
def list_mail(
    cursor: str | None = None,
    limit: int = 20,
    unread_only: bool = False,
    payload: dict = Depends(require_auth),
):
    limit = max(1, min(limit, 100))
    cursor_context = {"unreadOnly": unread_only}
    anchor = decode_keyset_cursor(cursor, scope="mail", context=cursor_context)
    where = "WHERE retention_queued_at IS NULL"
    params: list = []
    if unread_only:
        where += " AND read_flag=0"
    if anchor:
        where += " AND (received_at < ? OR (received_at = ? AND id < ?))"
        params.extend((anchor[0], anchor[0], anchor[1]))
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT id, from_addr, subject, received_at, read_flag FROM mail_message {where} "
            "ORDER BY received_at DESC, id DESC LIMIT ?",
            (*params, limit + 1),
        ).fetchall()
    items = [
        {
            "id": r["id"],
            "from": r["from_addr"],
            "subject": r["subject"],
            "receivedAt": r["received_at"],
            "read": bool(r["read_flag"]),
        }
        for r in rows[:limit]
    ]
    next_cursor = (
        encode_keyset_cursor(
            scope="mail",
            sort_value=rows[limit - 1]["received_at"],
            item_id=rows[limit - 1]["id"],
            context=cursor_context,
        )
        if len(rows) > limit
        else None
    )
    return {"items": items, "nextCursor": next_cursor}


@router.get("/mail/{message_id}")
def mail_detail(message_id: str, payload: dict = Depends(require_auth)):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM mail_message WHERE id=? AND retention_queued_at IS NULL",
            (message_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "邮件不存在"})
        atts = conn.execute(
            "SELECT id, name, mime_type, size FROM mail_attachment WHERE message_id=?", (message_id,)
        ).fetchall()
    return {
        "id": row["id"],
        "from": row["from_addr"],
        "to": row["to_addr"] or "",
        "subject": row["subject"],
        "receivedAt": row["received_at"],
        "read": bool(row["read_flag"]),
        "textBody": row["text_body"] or "",
        "htmlSummary": row["html_body"] or None,
        "attachments": [
            {"id": a["id"], "name": a["name"], "size": a["size"], "mimeType": a["mime_type"]}
            for a in atts
        ],
    }


class ReadBody(BaseModel):
    read: bool = True


@router.post("/mail/{message_id}/read")
def mark_read(
    message_id: str,
    body: ReadBody,
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    payload: dict = Depends(require_auth),
):
    from ..deps import require_csrf

    require_csrf(request, x_csrf_token)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM mail_message WHERE id=? AND retention_queued_at IS NULL",
            (message_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "邮件不存在"})
        conn.execute("UPDATE mail_message SET read_flag=? WHERE id=?", (1 if body.read else 0, message_id))
    return {"id": message_id, "read": body.read}
