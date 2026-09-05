"""SQLite durable job 表:入队 / 认领 / 完成 / 失败重试。"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import timedelta
from typing import Any

from ..security import iso, now_utc

MAX_ATTEMPTS = 5
STALE_RUNNING_SECONDS = 15 * 60
TERMINAL_ERROR_CODES = {
    "ASSET_MISSING",
    "DOCUMENT_MISSING",
    "EMPTY_TEXT",
    "INVALID_JOB_PAYLOAD",
    "UNKNOWN_JOB_TYPE",
    "EMBED_DIM_INVALID",
    "EMBED_MODEL_ID_REQUIRED",
    "EMBED_PROFILE_MISMATCH",
    "EMBED_TEXT_INVALID",
    "EMBED_HASH_INVALID",
    "VECTOR_EXTENSION_UNAVAILABLE",
}


class JobCancelled(RuntimeError):
    """写入前发现 cooperative cancel 已生效。"""


def _document_id_from_payload(payload_text: object) -> str | None:
    """只从格式正确的内部 JSON 提取关联键，坏 payload 仍可安全列出。"""
    try:
        payload = json.loads(payload_text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    document_id = payload.get("documentId") if isinstance(payload, dict) else None
    return document_id if isinstance(document_id, str) and document_id else None


def _is_terminal_error(error_code: str, retryable: bool | None) -> bool:
    """响应/配置契约失败不能靠重试掩盖；网络类错误可按次数重试。"""
    if retryable is not None:
        return not retryable
    return (
        error_code in TERMINAL_ERROR_CODES
        or error_code.startswith("EMBED_RESPONSE_")
        or error_code.startswith("VECTOR_")
    )


def enqueue_job(conn: sqlite3.Connection, job_type: str, payload: dict[str, Any]) -> str:
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    conn.execute(
        "INSERT INTO ingestion_job (id, job_type, payload, status, attempts, next_run_at, created_at, updated_at) "
        "VALUES (?, ?, ?, 'queued', 0, ?, ?, ?)",
        (job_id, job_type, json.dumps(payload, ensure_ascii=False), iso(now_utc()), iso(now_utc()), iso(now_utc())),
    )
    return job_id


def recover_stale_running_jobs(
    conn: sqlite3.Connection, stale_after_seconds: int = STALE_RUNNING_SECONDS
) -> int:
    """安全回收 worker 崩溃留下的 running job，不新增 schema/租约列。"""
    cutoff = iso(now_utc() - timedelta(seconds=stale_after_seconds))
    exhausted_rows = conn.execute(
        "UPDATE ingestion_job SET status='failed', error_code='STALE_MAX_ATTEMPTS', updated_at=? "
        "WHERE status='running' AND updated_at <= ? AND attempts >= ? RETURNING payload",
        (iso(now_utc()), cutoff, MAX_ATTEMPTS),
    ).fetchall()
    if exhausted_rows:
        # RETURNING 保证只处理本次由 running 转为 failed 的 payload。
        for row in exhausted_rows:
            try:
                payload_text = row["payload"] if isinstance(row, sqlite3.Row) else row[0]
                payload = json.loads(payload_text)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            document_id = payload.get("documentId") if isinstance(payload, dict) else None
            if isinstance(document_id, str) and document_id:
                conn.execute(
                    "UPDATE knowledge_document SET index_state='failed', index_error_code='STALE_MAX_ATTEMPTS' "
                    "WHERE id=? AND deleted_at IS NULL",
                    (document_id,),
                )
    cursor = conn.execute(
        "UPDATE ingestion_job SET status='queued', error_code='STALE_RECOVERED', next_run_at=?, updated_at=? "
        "WHERE status='running' AND updated_at <= ? AND attempts < ?",
        (iso(now_utc()), iso(now_utc()), cutoff, MAX_ATTEMPTS),
    )
    return cursor.rowcount


def claim_next_job(conn: sqlite3.Connection, worker_id: str) -> dict | None:
    """使用单条条件 UPDATE … RETURNING 原子认领，竞争 worker 至多一方成功。"""
    del worker_id  # schema 未记录 worker 标识，保留调用接口避免改变现有使用方。
    recover_stale_running_jobs(conn)
    now = iso(now_utc())
    row = conn.execute(
        "UPDATE ingestion_job SET status='running', attempts=attempts+1, updated_at=? "
        "WHERE id=(SELECT id FROM ingestion_job WHERE status='queued' AND next_run_at <= ? "
        "ORDER BY created_at LIMIT 1) AND status='queued' "
        "RETURNING *",
        (now, now),
    ).fetchone()
    return dict(row) if row is not None else None


def fail_job(
    conn: sqlite3.Connection,
    job_id: str,
    error_code: str,
    retry_after_seconds: int = 60,
    *,
    retryable: bool | None = None,
) -> str | None:
    row = conn.execute("SELECT attempts, status FROM ingestion_job WHERE id=?", (job_id,)).fetchone()
    if row is None or row["status"] != "running":
        return None
    attempts = row["attempts"] if row else 0
    if _is_terminal_error(error_code, retryable) or attempts >= MAX_ATTEMPTS:
        cursor = conn.execute(
            "UPDATE ingestion_job SET status='failed', error_code=?, updated_at=? WHERE id=? AND status='running'",
            (error_code, iso(now_utc()), job_id),
        )
        return "failed" if cursor.rowcount == 1 else None
    else:
        next_run = now_utc() + timedelta(seconds=retry_after_seconds)
        cursor = conn.execute(
            "UPDATE ingestion_job SET status='queued', next_run_at=?, error_code=?, updated_at=? "
            "WHERE id=? AND status='running'",
            (iso(next_run), error_code, iso(now_utc()), job_id),
        )
        return "queued" if cursor.rowcount == 1 else None


def succeed_job(conn: sqlite3.Connection, job_id: str) -> bool:
    cursor = conn.execute(
        "UPDATE ingestion_job SET status='succeeded', error_code=NULL, updated_at=? WHERE id=? AND status='running'",
        (iso(now_utc()), job_id),
    )
    return cursor.rowcount == 1


def require_running_writer(conn: sqlite3.Connection, job_id: str) -> None:
    """在业务写入前以条件 no-op UPDATE 锁定 writer，并确认尚未取消。"""
    cursor = conn.execute(
        "UPDATE ingestion_job SET updated_at=updated_at WHERE id=? AND status='running'",
        (job_id,),
    )
    if cursor.rowcount != 1:
        raise JobCancelled(job_id)


def job_summary(row: sqlite3.Row) -> dict[str, object]:
    return {
        "jobId": row["id"],
        "jobType": row["job_type"],
        "status": row["status"],
        "progress": None,
        "errorCode": row["error_code"],
        "updatedAt": row["updated_at"],
        "documentId": _document_id_from_payload(row["payload"]),
    }


def list_jobs(conn: sqlite3.Connection, *, limit: int, active_only: bool) -> dict[str, object]:
    where = "WHERE status IN ('queued', 'running')" if active_only else ""
    rows = conn.execute(
        f"SELECT id, job_type, payload, status, error_code, updated_at FROM ingestion_job {where} "
        "ORDER BY updated_at DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    count_rows = conn.execute(
        "SELECT status, COUNT(*) AS count FROM ingestion_job "
        "WHERE status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled') GROUP BY status"
    ).fetchall()
    counts = {status: 0 for status in ("queued", "running", "succeeded", "failed", "cancelled")}
    for row in count_rows:
        counts[row["status"]] = row["count"]
    return {"items": [job_summary(row) for row in rows], "counts": counts}


def _has_complete_document_index(conn: sqlite3.Connection, document_id: str) -> bool:
    """确认保留索引的每个 chunk 都仍有 ref 与 embedding 关联。

    取消重建时可以保留旧索引；但孤立 chunk 可能来自中断/损坏的旧写入，
    不能把它当作可查询的 ready 索引。
    """
    row = conn.execute(
        "SELECT "
        "EXISTS(SELECT 1 FROM chunk c "
        "JOIN vector_ref v ON v.chunk_id=c.id "
        "JOIN chunk_embedding e ON e.chunk_id=c.id "
        "WHERE c.document_id=?) "
        "AND NOT EXISTS(SELECT 1 FROM chunk c "
        "LEFT JOIN vector_ref v ON v.chunk_id=c.id "
        "LEFT JOIN chunk_embedding e ON e.chunk_id=c.id "
        "WHERE c.document_id=? AND (v.chunk_id IS NULL OR e.chunk_id IS NULL))",
        (document_id, document_id),
    ).fetchone()
    return bool(row[0])


def cancel_job(conn: sqlite3.Connection, job_id: str) -> dict[str, object] | None:
    """取消 ingest/rebuild，恢复关联文档的真实索引状态；None 表示不存在。"""
    row = conn.execute("SELECT * FROM ingestion_job WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return None
    if row["job_type"] not in {"ingest", "rebuild"} or row["status"] in {"succeeded", "failed"}:
        raise ValueError("conflict")
    if row["status"] == "cancelled":
        return job_status(conn, job_id)
    cursor = conn.execute(
        "UPDATE ingestion_job SET status='cancelled', error_code='JOB_CANCELLED', updated_at=? "
        "WHERE id=? AND status IN ('queued', 'running')",
        (iso(now_utc()), job_id),
    )
    if cursor.rowcount != 1:
        raise ValueError("conflict")
    document_id = _document_id_from_payload(row["payload"])
    if document_id is not None:
        active = conn.execute(
            "SELECT payload FROM ingestion_job WHERE status IN ('queued', 'running')"
        ).fetchall()
        has_other_active = any(_document_id_from_payload(candidate["payload"]) == document_id for candidate in active)
        if not has_other_active:
            indexed = _has_complete_document_index(conn, document_id)
            conn.execute(
                "UPDATE knowledge_document SET index_state=?, index_error_code=? "
                "WHERE id=? AND deleted_at IS NULL",
                ("ready" if indexed else "failed", None if indexed else "JOB_CANCELLED", document_id),
            )
    return job_status(conn, job_id)


def job_status(conn: sqlite3.Connection, job_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM ingestion_job WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return None
    return {
        "status": row["status"],
        "progress": None,
        "errorCode": row["error_code"],
        "updatedAt": row["updated_at"],
    }
