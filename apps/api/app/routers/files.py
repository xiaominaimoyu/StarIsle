"""文件 / 照片端点:cursor 分页、上传(魔数+大小校验)、下载、软删。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, UploadFile
from fastapi.responses import Response

from ..config import settings
from ..db import audit, get_conn
from ..deps import get_request_id, require_auth
from ..security import iso, now_utc, random_id, sha256_hex
from ..services.asset_gc import queue_deleted_asset
from ..services.keyset_cursor import decode_keyset_cursor, encode_keyset_cursor
from ..services.mime import ext_for, validate_upload
from ..services.photo_time import normalize_photo_capture_at
from ..storage import StorageQuotaExceeded, storage

router = APIRouter(tags=["files"])

IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_IMAGE_MAX_PIXELS = 40_000_000
_IMAGE_MAX_EDGE = 8000
_PHOTO_CURSOR_SCOPE = "photos.timeline-local.v2"


def _validate_image_header_dimensions(image) -> tuple[int, int]:
    """Reject oversized image headers before decoding their pixel data."""

    width, height = image.size
    if width * height > _IMAGE_MAX_PIXELS or max(width, height) > _IMAGE_MAX_EDGE:
        raise HTTPException(413, detail={"code": "PAYLOAD_TOO_LARGE", "message": "图片尺寸过大,请压缩后重试。"})
    return width, height


async def _read_upload(file: UploadFile, max_bytes: int) -> tuple[bytes, str]:
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(413, detail={"code": "PAYLOAD_TOO_LARGE", "message": "文件超过大小上限,请压缩后重试。"})
    return data, file.content_type or "application/octet-stream"


def _register_asset(
    conn, data: bytes, mime: str, original_name: str, *, storage_class: str, status: str = "active"
) -> tuple[str, str]:
    asset_id = random_id("asset_")
    object_key: str | None = None
    try:
        object_key = storage.save(data, "originals", ext_for(mime))
        conn.execute(
            "INSERT INTO asset (id, original_name, mime_type, size, object_key, sha256, status, "
            "storage_class, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                asset_id,
                original_name[:255],
                mime,
                len(data),
                object_key,
                sha256_hex(data),
                status,
                storage_class,
                iso(now_utc()),
            ),
        )
    except Exception:
        if object_key:
            try:
                storage.delete("originals", object_key)
            except Exception:
                pass
        raise
    assert object_key is not None
    return asset_id, object_key


# ---------- 文件 ----------

@router.get("/files")
def list_files(
    cursor: str | None = None,
    limit: int = 20,
    mime_type: str | None = None,
    payload: dict = Depends(require_auth),
):
    limit = max(1, min(limit, 100))
    cursor_context = {"mimeType": mime_type or None}
    anchor = decode_keyset_cursor(cursor, scope="files", context=cursor_context)
    with get_conn() as conn:
        params: list = []
        where = "WHERE status='active' AND storage_class='file'"
        if mime_type:
            where += " AND mime_type=?"
            params.append(mime_type)
        if anchor:
            where += " AND (created_at < ? OR (created_at = ? AND id < ?))"
            params.extend((anchor[0], anchor[0], anchor[1]))
        rows = conn.execute(
            f"SELECT * FROM asset {where} ORDER BY created_at DESC, id DESC LIMIT ?", (*params, limit + 1)
        ).fetchall()
    items = [
        {
            "id": r["id"],
            "name": r["original_name"],
            "size": r["size"],
            "mimeType": r["mime_type"],
            "createdAt": r["created_at"],
            "tags": [],
        }
        for r in rows[:limit]
    ]
    next_cursor = (
        encode_keyset_cursor(
            scope="files",
            sort_value=rows[limit - 1]["created_at"],
            item_id=rows[limit - 1]["id"],
            context=cursor_context,
        )
        if len(rows) > limit
        else None
    )
    return {"items": items, "nextCursor": next_cursor}


@router.post("/files", status_code=201)
async def upload_file(
    request: Request,
    file: UploadFile,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    payload: dict = Depends(require_auth),
):
    from ..deps import require_csrf

    require_csrf(request, x_csrf_token)
    data, declared = await _read_upload(file, settings.file_max_bytes)
    ok, reason = validate_upload(data, declared, settings.file_max_bytes)
    if not ok:
        raise HTTPException(415 if "类型" in (reason or "") else 413,
                            detail={"code": "UNSUPPORTED_MEDIA_TYPE" if "类型" in (reason or "") else "PAYLOAD_TOO_LARGE",
                                    "message": reason})

    # 幂等:同一 Idempotency-Key 返回首次资源
    from ..services.idempotency import with_idempotency

    def _produce():
        with get_conn() as conn:
            asset_id, _ = _register_asset(
                conn,
                data,
                declared,
                file.filename or "unnamed",
                storage_class="file",
            )
            row = conn.execute("SELECT * FROM asset WHERE id=?", (asset_id,)).fetchone()
            audit("file_upload", actor=payload["sub"], resource_type="asset", resource_id=asset_id,
                  request_id=get_request_id(request), result="ok", conn=conn)
            return _file_summary(row)

    try:
        return with_idempotency(idempotency_key, "asset", _produce)
    except StorageQuotaExceeded:
        raise HTTPException(413, detail={"code": "PAYLOAD_TOO_LARGE", "message": "对象存储配额不足。"})


def _file_summary(row) -> dict:
    return {
        "id": row["id"],
        "name": row["original_name"],
        "size": row["size"],
        "mimeType": row["mime_type"],
        "createdAt": row["created_at"],
        "tags": [],
    }


@router.get("/files/{file_id}/download")
def download_file(file_id: str, payload: dict = Depends(require_auth)):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM asset WHERE id=? AND status='active' AND storage_class='file'",
            (file_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "文件不存在"})
    data = storage.open("originals", row["object_key"])
    from urllib.parse import quote

    filename = quote(row["original_name"])
    return Response(
        content=data,
        media_type=row["mime_type"],
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.delete("/files/{file_id}", status_code=204)
def delete_file(
    file_id: str,
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    payload: dict = Depends(require_auth),
):
    from ..deps import require_csrf

    require_csrf(request, x_csrf_token)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM asset WHERE id=? AND status='active' AND storage_class='file'",
            (file_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "文件不存在"})
        if not queue_deleted_asset(conn, file_id, now=now_utc()):
            raise HTTPException(409, detail={"code": "ASSET_IN_USE", "message": "文件仍被其它资源使用"})
    audit("file_delete", actor=payload["sub"], resource_type="asset", resource_id=file_id,
          request_id=get_request_id(request), result="ok")
    return Response(status_code=204)


# ---------- 照片 ----------

@router.get("/photos")
def list_photos(cursor: str | None = None, limit: int = 60, payload: dict = Depends(require_auth)):
    limit = max(1, min(limit, 200))
    anchor = decode_keyset_cursor(cursor, scope=_PHOTO_CURSOR_SCOPE)
    with get_conn() as conn:
        params: list = []
        where = ""
        if anchor:
            where = (
                "WHERE (sort_key < ? "
                "OR (sort_key = ? AND id < ?))"
            )
            params.extend((anchor[0], anchor[0], anchor[1]))
        rows = conn.execute(
            "WITH normalized AS ("
            "SELECT p.*, a.object_key AS thumb_key, "
            "photo_capture_norm(p.capture_at) AS capture_norm FROM photo p "
            "JOIN asset a ON a.id = p.thumbnail_asset_id"
            "), ranked AS ("
            "SELECT normalized.*, CASE WHEN capture_norm IS NOT NULL "
            "THEN '1|' || capture_norm ELSE '0|' || created_at END AS sort_key "
            "FROM normalized"
            ") SELECT * FROM ranked "
            f"{where} ORDER BY sort_key DESC, id DESC LIMIT ?",
            (*params, limit + 1),
        ).fetchall()
    items = [
        {
            "id": r["id"],
            "thumbnailUrl": f"/api/v1/photos/{r['id']}/thumbnail",
            "captureAt": r["capture_norm"],
            "width": r["width"],
            "height": r["height"],
        }
        for r in rows[:limit]
    ]
    next_cursor = (
        encode_keyset_cursor(
            scope=_PHOTO_CURSOR_SCOPE,
            sort_value=rows[limit - 1]["sort_key"],
            item_id=rows[limit - 1]["id"],
        )
        if len(rows) > limit
        else None
    )
    return {"items": items, "nextCursor": next_cursor}


@router.post("/photos", status_code=201)
async def upload_photo(
    request: Request,
    file: UploadFile,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    payload: dict = Depends(require_auth),
):
    from ..deps import require_csrf

    require_csrf(request, x_csrf_token)
    data, declared = await _read_upload(file, settings.image_max_bytes)
    if declared not in IMAGE_TYPES:
        raise HTTPException(415, detail={"code": "UNSUPPORTED_MEDIA_TYPE", "message": "暂不支持该文件类型。"})
    ok, reason = validate_upload(data, declared, settings.image_max_bytes)
    if not ok:
        raise HTTPException(413, detail={"code": "PAYLOAD_TOO_LARGE", "message": reason})

    # 解码资源校验:先依据 header 拒绝超大图，再完整解码验证内容。
    try:
        from PIL import Image
        import io

        img = Image.open(io.BytesIO(data))
        w, h = _validate_image_header_dimensions(img)
        img.load()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(415, detail={"code": "UNSUPPORTED_MEDIA_TYPE", "message": "图片解码失败,暂不支持。"})

    def _produce_photo():
        created: list[tuple[str, str]] = []
        try:
            with get_conn() as conn:
                asset_id, object_key = _register_asset(
                    conn,
                    data,
                    declared,
                    file.filename or "photo.jpg",
                    storage_class="photo-original",
                )
                created.append(("originals", object_key))
                # 缩略图(原件 / 缩略图分离)
                thumb = _make_thumbnail(data, declared)
                thumb_asset_id = random_id("asset_")
                thumb_key = storage.save(thumb, "thumbnails", ".webp")
                created.append(("thumbnails", thumb_key))
                conn.execute(
                    "INSERT INTO asset (id, original_name, mime_type, size, object_key, sha256, status, "
                    "storage_class, created_at) VALUES (?, ?, ?, ?, ?, ?, 'active', 'photo-thumbnail', ?)",
                    (
                        thumb_asset_id,
                        f"thumb_{file.filename}",
                        "image/webp",
                        len(thumb),
                        thumb_key,
                        sha256_hex(thumb),
                        iso(now_utc()),
                    ),
                )
                capture_at = _read_exif_capture(data)
                photo_id = random_id("photo_")
                conn.execute(
                    "INSERT INTO photo (id, asset_id, capture_at, thumbnail_asset_id, width, height, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (photo_id, asset_id, capture_at, thumb_asset_id, w, h, iso(now_utc())),
                )
                audit("photo_upload", actor=payload["sub"], resource_type="photo", resource_id=photo_id,
                      request_id=get_request_id(request), result="ok", conn=conn)
                return photo_id
        except Exception:
            for subdir, key in reversed(created):
                try:
                    storage.delete(subdir, key)
                except Exception:
                    pass
            raise

    from ..services.idempotency import with_idempotency

    try:
        photo_id = with_idempotency(idempotency_key, "photo", _produce_photo)
    except StorageQuotaExceeded:
        raise HTTPException(413, detail={"code": "PAYLOAD_TOO_LARGE", "message": "对象存储配额不足。"})
    return {"id": photo_id, "status": "ready", "thumbnailState": "ready"}


@router.get("/photos/{photo_id}/thumbnail")
def photo_thumbnail(photo_id: str, payload: dict = Depends(require_auth)):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT a.object_key, a.mime_type FROM photo p JOIN asset a ON a.id=p.thumbnail_asset_id WHERE p.id=?",
            (photo_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "照片不存在"})
    data = storage.open("thumbnails", row["object_key"])
    return Response(content=data, media_type=row["mime_type"])


@router.delete("/photos/{photo_id}", status_code=204)
def delete_photo(
    photo_id: str,
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    payload: dict = Depends(require_auth),
):
    from ..deps import require_csrf

    require_csrf(request, x_csrf_token)
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM photo WHERE id=?", (photo_id,)).fetchone()
        if row is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "照片不存在"})
        conn.execute("DELETE FROM photo WHERE id=?", (photo_id,))
        current = now_utc()
        role_assets = (
            ("photo-original", str(row["asset_id"])),
            ("photo-thumbnail", str(row["thumbnail_asset_id"])),
        )
        # 一个 asset 同时承担原图和缩略图角色是损坏关系；删除元数据但不接管其生命周期。
        if role_assets[0][1] != role_assets[1][1]:
            for expected_class, asset_id in role_assets:
                asset = conn.execute("SELECT storage_class FROM asset WHERE id=?", (asset_id,)).fetchone()
                if asset is not None and asset["storage_class"] == expected_class:
                    queue_deleted_asset(conn, asset_id, now=current)
    audit("photo_delete", actor=payload["sub"], resource_type="photo", resource_id=photo_id,
          request_id=get_request_id(request), result="ok")
    return Response(status_code=204)


def _make_thumbnail(data: bytes, mime: str) -> bytes:
    from PIL import Image
    import io

    img = Image.open(io.BytesIO(data))
    _validate_image_header_dimensions(img)
    img = img.convert("RGB")
    img.thumbnail((settings.thumbnail_max_edge, settings.thumbnail_max_edge))
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=settings.thumbnail_quality)
    return buf.getvalue()


def _read_exif_capture(data: bytes) -> str | None:
    """仅读取拍摄时间摘要;GPS 默认不投影到 UI。"""
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS
        import io

        img = Image.open(io.BytesIO(data))
        exif = img.getexif()
        for tag_id, value in exif.items():
            if TAGS.get(tag_id) == "DateTimeOriginal" and value:
                return normalize_photo_capture_at(value)
    except Exception:
        pass
    return None
