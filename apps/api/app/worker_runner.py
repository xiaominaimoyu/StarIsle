"""worker：轮询 durable job，先验证完整 embedding batch，再原子替换索引。"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_conn  # noqa: E402
from app.observability import emit_worker_event  # noqa: E402
from app.security import iso, now_utc, sha256_hex  # noqa: E402
from app.services import rag_engine, vector_store  # noqa: E402
from app.services.asset_gc import queue_deleted_asset, run_asset_gc  # noqa: E402
from app.services.audit_retention import run_audit_retention  # noqa: E402
from app.services.idempotency import run_idempotency_cleanup  # noqa: E402
from app.services.jobs import JobCancelled, claim_next_job, fail_job, require_running_writer, succeed_job  # noqa: E402
from app.services.mail_retention import run_mail_retention  # noqa: E402
from app.storage import storage  # noqa: E402


POLL_INTERVAL = 5.0
MAIL_RETENTION_INTERVAL_SECONDS = 60 * 60
ASSET_GC_INTERVAL_SECONDS = 60 * 60
IDEMPOTENCY_CLEANUP_INTERVAL_SECONDS = 60 * 60
AUDIT_RETENTION_INTERVAL_SECONDS = 60 * 60


class JobProcessingError(RuntimeError):
    """带稳定错误码和重试语义的 worker 失败。"""

    def __init__(self, code: str, *, retryable: bool, document_id: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.document_id = document_id


def _safe_worker_event(
    *,
    event: str,
    result: str,
    job_type: object | None = None,
    error_code: object | None = None,
    retryable: object | None = None,
) -> None:
    """JSONL 是旁路；输出故障不得改变 durable job 的状态机。"""
    try:
        emit_worker_event(
            event=event,
            result=result,
            job_type=job_type,
            error_code=error_code,
            retryable=retryable,
        )
    except Exception:
        pass


def _job_type_for_event(job: dict[str, Any]) -> str:
    value = job.get("job_type")
    return value if isinstance(value, str) and value in {"ingest", "rebuild", "purge"} else "unknown"


def extract_text(document_type: str, data: bytes) -> str:
    if document_type == "pdf":
        try:
            from pypdf import PdfReader

            import io

            reader = PdfReader(io.BytesIO(data))
            return "\n\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception:
            return ""
    # markdown / txt
    return data.decode("utf-8", errors="replace")


def _required_payload_id(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise JobProcessingError("INVALID_JOB_PAYLOAD", retryable=False)
    return value


def _prepared_chunks(document_id: str, text: str) -> list[vector_store.VectorChunk]:
    text_chunks = rag_engine.split_text(text)
    if not text_chunks:
        raise JobProcessingError("EMPTY_TEXT", retryable=False, document_id=document_id)
    prepared: list[vector_store.VectorChunk] = []
    for seq, chunk_text in enumerate(text_chunks):
        if not chunk_text.strip():
            raise JobProcessingError("EMPTY_TEXT", retryable=False, document_id=document_id)
        chunk_id = f"chunk_{sha256_hex(f'{document_id}:{seq}'.encode())[:16]}"
        prepared.append(
            vector_store.VectorChunk(
                id=chunk_id,
                seq=seq,
                text=chunk_text,
                token_count=rag_engine.token_count(chunk_text),
                text_hash=sha256_hex(chunk_text.encode()),
            )
        )
    return prepared


def _replace_document_atomically(
    conn,
    job_id: str,
    document_id: str,
    source_bytes: bytes,
    chunks: list[vector_store.VectorChunk],
    batch: rag_engine.EmbeddingBatch,
) -> None:
    """三种索引行和 ready 状态必须在同一个 savepoint 内一起切换。"""
    if len(chunks) != len(batch.vectors):
        raise JobProcessingError("EMBED_RESPONSE_COUNT", retryable=False, document_id=document_id)
    conn.execute("SAVEPOINT ingest_replace")
    try:
        # Embedding 已完成；在任何索引写入前取得条件 writer lock，取消方要么先
        # 写成 cancelled，要么被本事务串行化，二者不能交错成 cancelled + ready。
        require_running_writer(conn, job_id)
        vector_store.VectorStore(conn).replace_document(document_id, chunks, batch.vectors, batch.profile)
        conn.execute(
            "UPDATE knowledge_document SET index_state='ready', index_error_code=NULL, hash=? "
            "WHERE id=? AND deleted_at IS NULL",
            (sha256_hex(source_bytes), document_id),
        )
        updated = conn.execute("SELECT changes()").fetchone()[0]
        if updated != 1:
            raise JobProcessingError("DOCUMENT_MISSING", retryable=False, document_id=document_id)
        conn.execute("RELEASE SAVEPOINT ingest_replace")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT ingest_replace")
        conn.execute("RELEASE SAVEPOINT ingest_replace")
        raise


def process_ingest(conn, job_id: str, payload: dict[str, Any]) -> None:
    """先完整生成/校验 batch，之后才在一个事务中替换旧 chunk/ref/blob。"""
    doc_id = _required_payload_id(payload, "documentId")
    asset_id = _required_payload_id(payload, "assetId")
    row = conn.execute("SELECT * FROM knowledge_document WHERE id=?", (doc_id,)).fetchone()
    if row is None or row["deleted_at"]:
        raise JobProcessingError("DOCUMENT_MISSING", retryable=False, document_id=doc_id)
    if asset_id != row["asset_id"]:
        raise JobProcessingError("INVALID_JOB_PAYLOAD", retryable=False, document_id=doc_id)
    asset = conn.execute("SELECT * FROM asset WHERE id=?", (asset_id,)).fetchone()
    if (
        asset is None
        or asset["status"] != "active"
        or asset["storage_class"] != "knowledge"
        or asset["object_purged_at"] is not None
    ):
        raise JobProcessingError("ASSET_MISSING", retryable=False, document_id=doc_id)

    try:
        data = storage.open("originals", asset["object_key"])
    except FileNotFoundError as exc:
        raise JobProcessingError("ASSET_MISSING", retryable=False, document_id=doc_id) from exc
    except OSError as exc:
        raise JobProcessingError("ASSET_READ_FAILED", retryable=True, document_id=doc_id) from exc

    text = extract_text(row["document_type"], data)
    if not text.strip():
        raise JobProcessingError("EMPTY_TEXT", retryable=False, document_id=doc_id)
    chunks = _prepared_chunks(doc_id, text)

    # 远程返回、维度、数量和每个 index 全部通过后，才允许进入写入 savepoint。
    try:
        batch = rag_engine.prepare_embeddings([chunk.text for chunk in chunks])
    except rag_engine.EmbeddingError:
        raise
    _replace_document_atomically(conn, job_id, doc_id, data, chunks, batch)


def process_rebuild(conn, job_id: str, payload: dict[str, Any]) -> None:
    doc_id = _required_payload_id(payload, "documentId")
    row = conn.execute("SELECT * FROM knowledge_document WHERE id=?", (doc_id,)).fetchone()
    if row is None or row["deleted_at"]:
        raise JobProcessingError("DOCUMENT_MISSING", retryable=False, document_id=doc_id)
    process_ingest(conn, job_id, {"documentId": doc_id, "assetId": row["asset_id"]})


def process_purge(conn, job_id: str, payload: dict[str, Any]) -> None:
    doc_id = _required_payload_id(payload, "documentId")
    row = conn.execute("SELECT id, asset_id FROM knowledge_document WHERE id=?", (doc_id,)).fetchone()
    if row is None:
        raise JobProcessingError("DOCUMENT_MISSING", retryable=False, document_id=doc_id)
    supplied_asset_id = payload.get("assetId")
    if supplied_asset_id is not None:
        if not isinstance(supplied_asset_id, str) or not supplied_asset_id or supplied_asset_id != row["asset_id"]:
            raise JobProcessingError("INVALID_JOB_PAYLOAD", retryable=False, document_id=doc_id)
    require_running_writer(conn, job_id)
    vector_store.delete_document(conn, doc_id)
    current = now_utc()
    conn.execute(
        "UPDATE knowledge_document SET deleted_at=?, index_state='queued' WHERE id=?",
        (iso(current), doc_id),
    )
    # 新写入与迁移后的知识库资产都必须是 knowledge。遇到历史/损坏的
    # 跨类别关联时宁可隔离，不能因删除文档而回收一个普通 file 等资产。
    asset = conn.execute(
        "SELECT storage_class FROM asset WHERE id=?", (row["asset_id"],)
    ).fetchone()
    if asset is not None and asset["storage_class"] == "knowledge":
        # 文档关联才是 purge 的唯一资产权威；仅无其它 live 引用的资产进入通用 GC。
        queue_deleted_asset(conn, row["asset_id"], now=current)


def _record_failure(
    job: dict[str, Any],
    code: str,
    retryable: bool,
    document_id: str | None = None,
) -> None:
    """业务写事务已回滚；在新事务中仅记录安全的 job/document 状态。"""
    if document_id is None:
        try:
            payload = json.loads(job["payload"])
            if isinstance(payload, dict) and isinstance(payload.get("documentId"), str):
                document_id = payload["documentId"]
        except (TypeError, ValueError, json.JSONDecodeError):
            document_id = None
    with get_conn() as conn:
        final_status = fail_job(conn, job["id"], code, retryable=retryable)
        if document_id and final_status in {"queued", "failed"}:
            state = final_status
            conn.execute(
                "UPDATE knowledge_document SET index_state=?, index_error_code=? "
                "WHERE id=? AND deleted_at IS NULL",
                (state, code, document_id),
            )


def run_once(worker_id: str = "worker-1") -> bool:
    """一次认领与处理；失败绝不让 succeed 覆盖 queued/failed 状态。"""
    with get_conn() as conn:
        job = claim_next_job(conn, worker_id)
    if job is None:
        return False

    try:
        try:
            payload = json.loads(job["payload"])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise JobProcessingError("INVALID_JOB_PAYLOAD", retryable=False) from exc
        if not isinstance(payload, dict):
            raise JobProcessingError("INVALID_JOB_PAYLOAD", retryable=False)
        with get_conn() as conn:
            if job["job_type"] == "ingest":
                process_ingest(conn, job["id"], payload)
            elif job["job_type"] == "rebuild":
                process_rebuild(conn, job["id"], payload)
            elif job["job_type"] == "purge":
                process_purge(conn, job["id"], payload)
            else:
                raise JobProcessingError("UNKNOWN_JOB_TYPE", retryable=False)
            if not succeed_job(conn, job["id"]):
                raise JobCancelled(job["id"])
        _safe_worker_event(
            event="job_succeeded",
            result="succeeded",
            job_type=_job_type_for_event(job),
        )
    except JobCancelled:
        _safe_worker_event(
            event="job_cancelled",
            result="cancelled",
            job_type=_job_type_for_event(job),
        )
    except JobProcessingError as exc:
        _record_failure(job, exc.code, exc.retryable, exc.document_id)
        _safe_worker_event(
            event="job_failed",
            result="failed",
            job_type=_job_type_for_event(job),
            error_code=exc.code,
            retryable=exc.retryable,
        )
    except rag_engine.RagError as exc:
        _record_failure(job, exc.code, exc.retryable)
        _safe_worker_event(
            event="job_failed",
            result="failed",
            job_type=_job_type_for_event(job),
            error_code=exc.code,
            retryable=exc.retryable,
        )
    except vector_store.VectorStoreError as exc:
        _record_failure(job, exc.code, False)
        _safe_worker_event(
            event="job_failed",
            result="failed",
            job_type=_job_type_for_event(job),
            error_code=exc.code,
            retryable=False,
        )
    except Exception:
        _record_failure(job, "PROCESSING_FAILED", True)
        _safe_worker_event(
            event="job_failed",
            result="failed",
            job_type=_job_type_for_event(job),
            error_code="PROCESSING_FAILED",
            retryable=True,
        )
    return True


def run_maintenance() -> bool:
    """邮件保留期、资产 GC、幂等清理与审计保留期相互隔离，任一失败都不阻断 durable job。"""
    ok = True
    try:
        run_mail_retention()
        _safe_worker_event(event="mail_retention_completed", result="ok")
    except Exception:
        # 维护可能接触历史待清理数据，绝不把对象键、邮件内容或异常原文输出到日志。
        _safe_worker_event(
            event="mail_retention_failed",
            result="failed",
            error_code="MAINTENANCE_FAILED",
        )
        ok = False
    try:
        run_asset_gc()
        _safe_worker_event(event="asset_gc_completed", result="ok")
    except Exception:
        _safe_worker_event(
            event="asset_gc_failed",
            result="failed",
            error_code="MAINTENANCE_FAILED",
        )
        ok = False
    try:
        run_idempotency_cleanup()
        _safe_worker_event(event="idempotency_cleanup_completed", result="ok")
    except Exception:
        _safe_worker_event(
            event="idempotency_cleanup_failed",
            result="failed",
            error_code="MAINTENANCE_FAILED",
        )
        ok = False
    try:
        run_audit_retention()
        _safe_worker_event(event="audit_retention_completed", result="ok")
    except Exception:
        _safe_worker_event(
            event="audit_retention_failed",
            result="failed",
            error_code="MAINTENANCE_FAILED",
        )
        ok = False
    return ok


def main() -> None:
    _safe_worker_event(event="started", result="started")
    next_maintenance_at = time.monotonic()
    while True:
        try:
            current = time.monotonic()
            if current >= next_maintenance_at:
                run_maintenance()
                next_maintenance_at = current + min(
                    MAIL_RETENTION_INTERVAL_SECONDS,
                    ASSET_GC_INTERVAL_SECONDS,
                    IDEMPOTENCY_CLEANUP_INTERVAL_SECONDS,
                    AUDIT_RETENTION_INTERVAL_SECONDS,
                )
            if not run_once():
                time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            _safe_worker_event(event="stopped", result="stopped")
            break
        except Exception:
            _safe_worker_event(event="loop_error", result="failed", error_code="LOOP_FAILED")
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
