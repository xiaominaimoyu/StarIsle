"""作业取消、列表投影与 Console 存储统计的契约测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import worker_runner
from app.config import settings
from app.db import get_conn, init_db
from app.main import app
from app.security import iso, now_utc
from app.services.jobs import JobCancelled, cancel_job, enqueue_job, require_running_writer, succeed_job

from .test_api import _csrf, _login, client


@pytest.fixture(autouse=True)
def initialized_db():
    init_db()
    with get_conn() as conn:
        conn.execute("DELETE FROM ingestion_job")
    yield
    with get_conn() as conn:
        conn.execute("DELETE FROM ingestion_job")


def _insert_document(document_id: str) -> None:
    now = iso(now_utc())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO knowledge_document (id, asset_id, title, document_type, version, hash, index_state, created_at) "
            "VALUES (?, ?, ?, 'txt', 1, 'hash', 'queued', ?)",
            (document_id, f"asset-{document_id}", document_id, now),
        )


def test_jobs_require_auth_and_project_payload_safely():
    anonymous = TestClient(app)
    try:
        assert anonymous.get("/api/v1/jobs").status_code == 401
    finally:
        anonymous.close()

    _login()
    with get_conn() as conn:
        first = enqueue_job(conn, "ingest", {"documentId": "doc-visible", "secret": "never-return"})
        second = enqueue_job(conn, "rebuild", {"documentId": "doc-later"})
        conn.execute("UPDATE ingestion_job SET updated_at=? WHERE id=?", ("2026-09-04T00:00:02+00:00", first))
        conn.execute("UPDATE ingestion_job SET updated_at=? WHERE id=?", ("2026-09-04T00:00:01+00:00", second))

    response = client.get("/api/v1/jobs?limit=1&activeOnly=true")
    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["jobId"] == first
    assert body["items"][0]["documentId"] == "doc-visible"
    assert "payload" not in body["items"][0]
    assert body["counts"]["queued"] >= 2


def test_cancel_queued_is_idempotent_and_restores_document_state():
    _login()
    document_id = "doc-cancel-queued"
    _insert_document(document_id)
    with get_conn() as conn:
        job_id = enqueue_job(conn, "ingest", {"documentId": document_id})

    missing_csrf = client.post(f"/api/v1/jobs/{job_id}/cancel")
    assert missing_csrf.status_code == 403
    response = client.post(
        f"/api/v1/jobs/{job_id}/cancel",
        headers={"X-CSRF-Token": _csrf(), "Origin": "http://testserver"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert response.json()["errorCode"] == "JOB_CANCELLED"
    with get_conn() as conn:
        document = conn.execute("SELECT index_state, index_error_code FROM knowledge_document WHERE id=?", (document_id,)).fetchone()
        audit_event = conn.execute(
            "SELECT action, resource_id, result FROM audit_event WHERE resource_type='job' AND resource_id=?",
            (job_id,),
        ).fetchone()
    assert tuple(document) == ("failed", "JOB_CANCELLED")
    assert tuple(audit_event) == ("job_cancel", job_id, "ok")

    repeated = client.post(
        f"/api/v1/jobs/{job_id}/cancel",
        headers={"X-CSRF-Token": _csrf(), "Origin": "http://testserver"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "cancelled"


def test_cancel_does_not_restore_ready_for_partial_chunk_index():
    _login()
    document_id = "doc-cancel-partial-index"
    _insert_document(document_id)
    with get_conn() as conn:
        job_id = enqueue_job(conn, "rebuild", {"documentId": document_id})
        conn.execute(
            "INSERT INTO chunk (id, document_id, seq, text, token_count, hash) "
            "VALUES ('chunk-partial-index', ?, 0, 'partial', 1, 'hash')",
            (document_id,),
        )

    response = client.post(
        f"/api/v1/jobs/{job_id}/cancel",
        headers={"X-CSRF-Token": _csrf(), "Origin": "http://testserver"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert response.json()["errorCode"] == "JOB_CANCELLED"
    with get_conn() as conn:
        document = conn.execute(
            "SELECT index_state, index_error_code FROM knowledge_document WHERE id=?", (document_id,)
        ).fetchone()
    assert tuple(document) == ("failed", "JOB_CANCELLED")


def test_cancel_restores_ready_only_for_complete_previous_index():
    _login()
    document_id = "doc-cancel-complete-index"
    _insert_document(document_id)
    with get_conn() as conn:
        job_id = enqueue_job(conn, "rebuild", {"documentId": document_id})
        conn.execute(
            "INSERT INTO chunk (id, document_id, seq, text, token_count, hash) "
            "VALUES ('chunk-complete-index', ?, 0, 'complete', 1, 'hash')",
            (document_id,),
        )
        conn.execute(
            "INSERT INTO vector_ref (chunk_id, store, vector_id, model_id, dim) "
            "VALUES ('chunk-complete-index', 'sqlite-vec', 'chunk-complete-index', 'model', 1)"
        )
        conn.execute(
            "INSERT INTO chunk_embedding (chunk_id, embedding) VALUES ('chunk-complete-index', ?)",
            (b"\x00\x00\x80?",),
        )

    response = client.post(
        f"/api/v1/jobs/{job_id}/cancel",
        headers={"X-CSRF-Token": _csrf(), "Origin": "http://testserver"},
    )
    assert response.status_code == 200
    with get_conn() as conn:
        document = conn.execute(
            "SELECT index_state, index_error_code FROM knowledge_document WHERE id=?", (document_id,)
        ).fetchone()
    assert tuple(document) == ("ready", None)


def test_cancel_keeps_document_state_when_another_job_is_active():
    _login()
    document_id = "doc-cancel-has-other-active"
    _insert_document(document_id)
    with get_conn() as conn:
        first_job = enqueue_job(conn, "rebuild", {"documentId": document_id})
        enqueue_job(conn, "ingest", {"documentId": document_id})
        conn.execute(
            "UPDATE knowledge_document SET index_state='indexing', index_error_code='IN_PROGRESS' WHERE id=?",
            (document_id,),
        )

    response = client.post(
        f"/api/v1/jobs/{first_job}/cancel",
        headers={"X-CSRF-Token": _csrf(), "Origin": "http://testserver"},
    )
    assert response.status_code == 200
    with get_conn() as conn:
        document = conn.execute(
            "SELECT index_state, index_error_code FROM knowledge_document WHERE id=?", (document_id,)
        ).fetchone()
    assert tuple(document) == ("indexing", "IN_PROGRESS")


def test_cancel_running_writer_guard_and_terminal_conflicts():
    _login()
    with get_conn() as conn:
        running_id = enqueue_job(conn, "ingest", {"documentId": "doc-running"})
        conn.execute("UPDATE ingestion_job SET status='running' WHERE id=?", (running_id,))
        assert cancel_job(conn, running_id)["status"] == "cancelled"
        with pytest.raises(JobCancelled):
            require_running_writer(conn, running_id)
        assert not succeed_job(conn, running_id)
        terminal_id = enqueue_job(conn, "ingest", {"documentId": "doc-terminal"})
        purge_id = enqueue_job(conn, "purge", {"documentId": "doc-purge"})
        conn.execute("UPDATE ingestion_job SET status='succeeded' WHERE id=?", (terminal_id,))

    headers = {"X-CSRF-Token": _csrf(), "Origin": "http://testserver"}
    assert client.post(f"/api/v1/jobs/{terminal_id}/cancel", headers=headers).status_code == 409
    assert client.post(f"/api/v1/jobs/{purge_id}/cancel", headers=headers).status_code == 409
    assert client.post("/api/v1/jobs/job-does-not-exist/cancel", headers=headers).status_code == 404


def test_worker_does_not_overwrite_a_cooperatively_cancelled_job(monkeypatch: pytest.MonkeyPatch, tmp_path):
    root = tmp_path / "worker-cancel"
    monkeypatch.setattr(settings, "data_root", root)
    monkeypatch.setattr(settings, "db_path", root / "db" / "star-isle.db")
    monkeypatch.setattr(settings, "objects_root", root / "objects")
    monkeypatch.setattr(settings, "originals_dir", root / "objects" / "originals")
    monkeypatch.setattr(settings, "thumbnails_dir", root / "objects" / "thumbnails")
    monkeypatch.setattr(settings, "mail_attachments_dir", root / "objects" / "mail-attachments")
    monkeypatch.setattr(settings, "mail_raw_dir", root / "objects" / "mail-raw")
    init_db()
    with get_conn() as conn:
        job_id = enqueue_job(conn, "ingest", {"documentId": "doc-worker-cancel"})

    def cancel_before_index_write(_conn, claimed_job_id, _payload):
        with get_conn() as cancelling_conn:
            assert cancel_job(cancelling_conn, claimed_job_id)["status"] == "cancelled"

    monkeypatch.setattr(worker_runner, "process_ingest", cancel_before_index_write)
    assert worker_runner.run_once("cancel-race") is True
    with get_conn() as conn:
        row = conn.execute("SELECT status, error_code FROM ingestion_job WHERE id=?", (job_id,)).fetchone()
    assert tuple(row) == ("cancelled", "JOB_CANCELLED")


def test_storage_stats_counts_only_live_file_and_photo_assets():
    _login()
    before = client.get("/api/v1/stats/storage").json()
    now = iso(now_utc())
    with get_conn() as conn:
        rows = [
            ("asset-stats-file", 11, "active", "file", None),
            ("asset-stats-deleted", 22, "deleted", "file", None),
            ("asset-stats-photo", 33, "active", "photo-original", None),
            ("asset-stats-thumb", 44, "active", "photo-thumbnail", None),
            ("asset-stats-purged", 55, "active", "photo-original", now),
        ]
        for asset_id, size, status, storage_class, purged_at in rows:
            conn.execute(
                "INSERT INTO asset (id, original_name, mime_type, size, object_key, sha256, status, storage_class, created_at, object_purged_at) "
                "VALUES (?, 'x', 'text/plain', ?, ?, 'hash', ?, ?, ?, ?)",
                (asset_id, size, asset_id, status, storage_class, now, purged_at),
            )
        conn.execute(
            "INSERT INTO photo (id, asset_id, thumbnail_asset_id, created_at) VALUES ('photo-stats', 'asset-stats-photo', 'asset-stats-thumb', ?)",
            (now,),
        )

    after = client.get("/api/v1/stats/storage")
    assert after.status_code == 200
    assert after.json() == {
        "usedBytes": before["usedBytes"] + 88,
        "fileCount": before["fileCount"] + 1,
        "photoCount": before["photoCount"] + 1,
    }
