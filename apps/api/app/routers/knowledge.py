"""知识库文档管理:上传(异步 job)、列表、删除、重建。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel

from ..db import audit, get_conn
from ..deps import get_request_id, require_auth
from ..security import iso, now_utc, random_id, sha256_hex
from ..services.jobs import enqueue_job
from ..services.keyset_cursor import decode_keyset_cursor, encode_keyset_cursor
from ..services.mime import ext_for, safe_display_name, upload_rejection, validate_upload
from ..storage import StorageQuotaExceeded, storage

router = APIRouter(tags=["knowledge"])

ALLOWED_TYPES = {"application/pdf": "pdf", "text/markdown": "markdown", "text/plain": "txt"}
DOC_MAX_BYTES = 50 * 1024 * 1024


def _doc_summary(row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "documentType": row["document_type"],
        "version": row["version"],
        "hash": row["hash"],
        "indexState": row["index_state"],
        "indexErrorCode": row["index_error_code"],
        "createdAt": row["created_at"],
    }


@router.get("/knowledge/documents")
def list_documents(
    cursor: str | None = None,
    limit: int = 20,
    payload: dict = Depends(require_auth),
):
    limit = max(1, min(limit, 100))
    anchor = decode_keyset_cursor(cursor, scope="knowledge-documents")
    where = "WHERE deleted_at IS NULL"
    params: list = []
    if anchor:
        where += " AND (created_at < ? OR (created_at = ? AND id < ?))"
        params.extend((anchor[0], anchor[0], anchor[1]))
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM knowledge_document {where} ORDER BY created_at DESC, id DESC LIMIT ?",
            (*params, limit + 1),
        ).fetchall()
    items = [_doc_summary(r) for r in rows[:limit]]
    next_cursor = (
        encode_keyset_cursor(
            scope="knowledge-documents",
            sort_value=rows[limit - 1]["created_at"],
            item_id=rows[limit - 1]["id"],
        )
        if len(rows) > limit
        else None
    )
    return {"items": items, "nextCursor": next_cursor}


@router.post("/knowledge/documents", status_code=202)
async def upload_document(
    request: Request,
    file: UploadFile,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    payload: dict = Depends(require_auth),
):
    from ..deps import require_csrf

    require_csrf(request, x_csrf_token)
    data = await file.read(DOC_MAX_BYTES + 1)
    if len(data) > DOC_MAX_BYTES:
        raise HTTPException(413, detail={"code": "PAYLOAD_TOO_LARGE", "message": "文档超过大小上限。"})
    declared = file.content_type or ""
    doc_type = ALLOWED_TYPES.get(declared)
    if doc_type is None:
        raise HTTPException(415, detail={"code": "UNSUPPORTED_MEDIA_TYPE", "message": "仅支持 PDF / Markdown / TXT。"})
    ok, reason = validate_upload(data, declared, DOC_MAX_BYTES)
    if not ok:
        status, code = upload_rejection(reason)
        raise HTTPException(status, detail={"code": code, "message": reason})

    from ..services.idempotency import with_idempotency

    display_name = safe_display_name(file.filename, "document")

    def _produce():
        object_key: str | None = None
        try:
            with get_conn() as conn:
                asset_id = random_id("asset_")
                object_key = storage.save(data, "originals", ext_for(declared))
                conn.execute(
                    "INSERT INTO asset (id, original_name, mime_type, size, object_key, sha256, status, "
                    "storage_class, created_at) VALUES (?, ?, ?, ?, ?, ?, 'active', 'knowledge', ?)",
                    (
                        asset_id,
                        display_name,
                        declared,
                        len(data),
                        object_key,
                        sha256_hex(data),
                        iso(now_utc()),
                    ),
                )
                doc_id = random_id("doc_")
                conn.execute(
                    "INSERT INTO knowledge_document (id, asset_id, title, document_type, version, hash, index_state, created_at) "
                    "VALUES (?, ?, ?, ?, 1, ?, 'queued', ?)",
                    (doc_id, asset_id, display_name, doc_type,
                     sha256_hex(data), iso(now_utc())),
                )
                job_id = enqueue_job(
                    conn,
                    job_type="ingest",
                    payload={"documentId": doc_id, "assetId": asset_id},
                )
                audit("doc_upload", actor=payload["sub"], resource_type="document", resource_id=doc_id,
                      request_id=get_request_id(request), result="ok", conn=conn)
                return doc_id, job_id
        except Exception:
            if object_key:
                try:
                    storage.delete("originals", object_key)
                except Exception:
                    pass
            raise

    try:
        doc_id, job_id = with_idempotency(idempotency_key, "document_upload", _produce)
    except StorageQuotaExceeded:
        raise HTTPException(413, detail={"code": "PAYLOAD_TOO_LARGE", "message": "对象存储配额不足。"})
    return {"documentId": doc_id, "jobId": job_id}


@router.delete("/knowledge/documents/{document_id}", status_code=202)
def delete_document(
    document_id: str,
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    payload: dict = Depends(require_auth),
):
    from ..deps import require_csrf

    require_csrf(request, x_csrf_token)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM knowledge_document WHERE id=? AND deleted_at IS NULL", (document_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "文档不存在"})
        conn.execute(
            "UPDATE knowledge_document SET deleted_at=?, index_state='queued' WHERE id=?", (iso(now_utc()), document_id)
        )
        job_id = enqueue_job(
            conn,
            job_type="purge",
            payload={"documentId": document_id, "assetId": row["asset_id"]},
        )
    audit("doc_delete", actor=payload["sub"], resource_type="document", resource_id=document_id,
          request_id=get_request_id(request), result="ok")
    return {"jobId": job_id, "status": "accepted"}


class RebuildBody(BaseModel):
    reason: str = ""


@router.post("/knowledge/documents/{document_id}/rebuild", status_code=202)
def rebuild_document(
    document_id: str,
    body: RebuildBody,
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    payload: dict = Depends(require_auth),
):
    from ..deps import require_csrf

    require_csrf(request, x_csrf_token)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM knowledge_document WHERE id=? AND deleted_at IS NULL", (document_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "文档不存在"})
        conn.execute("UPDATE knowledge_document SET index_state='queued' WHERE id=?", (document_id,))
        job_id = enqueue_job(conn, job_type="rebuild", payload={"documentId": document_id})
    audit("doc_rebuild", actor=payload["sub"], resource_type="document", resource_id=document_id,
          request_id=get_request_id(request), result="ok")
    return {"jobId": job_id}
