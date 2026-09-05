"""邮件保留期 v3：边界、两阶段清理、共享引用与安全审计。"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import deps, worker_runner  # noqa: E402
from app.config import Settings, settings  # noqa: E402
from app.db import get_conn, init_db  # noqa: E402
from app.routers import mail  # noqa: E402
from app.security import iso  # noqa: E402
from app.services import mail_retention  # noqa: E402
from app.storage import StorageAdapter  # noqa: E402


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def retention_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = tmp_path / "data"
    monkeypatch.setattr(settings, "data_root", root)
    monkeypatch.setattr(settings, "db_path", root / "db" / "star-isle.db")
    monkeypatch.setattr(settings, "objects_root", root / "objects")
    monkeypatch.setattr(settings, "originals_dir", root / "objects" / "originals")
    monkeypatch.setattr(settings, "thumbnails_dir", root / "objects" / "thumbnails")
    monkeypatch.setattr(settings, "mail_attachments_dir", root / "objects" / "mail-attachments")
    monkeypatch.setattr(settings, "mail_raw_dir", root / "objects" / "mail-raw")
    monkeypatch.setattr(settings, "mail_retention_days", 1)
    adapter = StorageAdapter(root / "objects")
    monkeypatch.setattr(mail_retention, "storage", adapter)
    init_db()
    return root, adapter


def _insert_message(
    conn,
    message_id: str,
    *,
    received_at: datetime,
    db_id: str | None = None,
    queued_at: datetime | None = None,
    from_addr: str = "sender@example.test",
    to_addr: str | None = "inbox@example.test",
    subject: str | None = "private subject",
    text_body: str | None = "private text",
    html_body: str | None = "<p>private html</p>",
    read_flag: int = 0,
) -> str:
    db_id = db_id or message_id.replace("@", "-").replace(".", "-")
    conn.execute(
        "INSERT INTO mail_message ("
        "id, message_id, from_addr, to_addr, subject, received_at, text_body, html_body, read_flag, "
        "retention_queued_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            db_id,
            message_id,
            from_addr,
            to_addr,
            subject,
            iso(received_at),
            text_body,
            html_body,
            read_flag,
            iso(queued_at) if queued_at else None,
        ),
    )
    return db_id


def _add_attachment(
    conn,
    adapter: StorageAdapter,
    message_id: str,
    asset_id: str,
    attachment_id: str,
    payload: bytes,
) -> str:
    key = adapter.save(payload, "mail-attachments")
    conn.execute(
        "INSERT INTO asset (id, original_name, mime_type, size, object_key, sha256, status, created_at) "
        "VALUES (?, 'private.bin', 'application/octet-stream', ?, ?, 'hash', 'active', ?)",
        (asset_id, len(payload), key, iso(NOW)),
    )
    conn.execute(
        "INSERT INTO mail_attachment (id, message_id, asset_id, name, mime_type, size) "
        "VALUES (?, ?, ?, 'private.bin', 'application/octet-stream', ?)",
        (attachment_id, message_id, asset_id, len(payload)),
    )
    return key


def _add_raw(
    conn,
    adapter: StorageAdapter,
    message_id: str,
    payload: bytes = b"private raw message",
) -> str:
    key = adapter.save(payload, "mail-raw")
    conn.execute("UPDATE mail_message SET raw_state='stored' WHERE id=?", (message_id,))
    conn.execute(
        "INSERT INTO mail_raw (message_id, object_key, sha256, size, content_type, created_at) "
        "VALUES (?, ?, ?, ?, 'message/rfc822', ?)",
        (message_id, key, hashlib.sha256(payload).hexdigest(), len(payload), iso(NOW)),
    )
    return key


def _mail_row(message_id: str):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM mail_message WHERE id=?", (message_id,)).fetchone()


def test_settings_rejects_nonpositive_retention_days(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MAIL_RETENTION_DAYS", "0")
    with pytest.raises(ValueError, match="MAIL_RETENTION_DAYS"):
        Settings()

    monkeypatch.setenv("MAIL_RETENTION_DAYS", "-1")
    with pytest.raises(ValueError, match="MAIL_RETENTION_DAYS"):
        Settings()

    monkeypatch.setenv("MAIL_RETENTION_DAYS", "not-a-number")
    with pytest.raises(ValueError):
        Settings()


def test_expiry_boundary_queue_redacts_hides_and_releases_message_id(
    retention_env, monkeypatch: pytest.MonkeyPatch
):
    del retention_env
    original_message_id = "same-message@example.test"
    with get_conn() as conn:
        expired_id = _insert_message(
            conn,
            original_message_id,
            db_id="expired-mail",
            received_at=NOW - timedelta(days=1, seconds=1),
        )
        _add_attachment(conn, mail_retention.storage, expired_id, "asset-expired", "att-expired", b"old")
        exact_id = _insert_message(
            conn,
            "exact@example.test",
            db_id="exact-mail",
            received_at=NOW - timedelta(days=1),
        )
        fresh_id = _insert_message(
            conn,
            "fresh@example.test",
            db_id="fresh-mail",
            received_at=NOW - timedelta(hours=1),
        )

    original_delete = mail_retention.storage.delete

    def fail_delete(*_args, **_kwargs):
        raise OSError("object-key-and-private-error-must-not-leak")

    monkeypatch.setattr(mail_retention.storage, "delete", fail_delete)
    first = mail_retention.run_mail_retention(now=NOW)
    assert first.queued_messages == 1
    assert first.failed_attachments == 1
    assert first.deferred_messages == 1
    assert first.deleted_messages == 0

    expired = _mail_row(expired_id)
    assert expired is not None
    assert expired["retention_queued_at"] == iso(NOW + timedelta(microseconds=1))
    assert expired["message_id"] == f"retained-{expired_id}"
    assert expired["from_addr"] == ""
    assert expired["to_addr"] is None
    assert expired["subject"] is None
    assert expired["text_body"] is None
    assert expired["html_body"] is None
    assert expired["read_flag"] == 1
    with get_conn() as conn:
        assert conn.execute("SELECT name FROM mail_attachment WHERE id='att-expired'").fetchone()[0] == ""

    listing = mail.list_mail(payload={})
    assert {item["id"] for item in listing["items"]} == {exact_id, fresh_id}
    with pytest.raises(HTTPException) as detail_error:
        mail.mail_detail(expired_id, payload={})
    assert detail_error.value.status_code == 404
    monkeypatch.setattr(deps, "require_csrf", lambda *_args, **_kwargs: None)
    request = Request({"type": "http", "method": "POST", "path": "/mail/expired-mail/read", "headers": []})
    with pytest.raises(HTTPException) as read_error:
        mail.mark_read(expired_id, mail.ReadBody(), request, "test", payload={})
    assert read_error.value.status_code == 404

    with get_conn() as conn:
        _insert_message(
            conn,
            original_message_id,
            db_id="redelivered-mail",
            received_at=NOW,
        )

    monkeypatch.setattr(mail_retention.storage, "delete", original_delete)
    second = mail_retention.run_mail_retention(now=NOW)
    assert second.deleted_messages == 1
    assert _mail_row(expired_id) is None
    assert _mail_row("redelivered-mail") is not None
    third = mail_retention.run_mail_retention(now=NOW)
    assert third.queued_messages == 0
    assert third.deleted_messages == 0
    assert third.deleted_attachments == 0


def test_failed_pending_message_rotates_behind_later_batch_item(
    retention_env, monkeypatch: pytest.MonkeyPatch
):
    """首项持续对象删除失败时，批次仍能在下一轮处理后续 pending 邮件。"""
    _, adapter = retention_env
    with get_conn() as conn:
        failed_id = _insert_message(
            conn,
            "first-pending@example.test",
            db_id="first-pending",
            received_at=NOW - timedelta(days=2),
        )
        failed_key = _add_attachment(
            conn,
            adapter,
            failed_id,
            "asset-first-pending",
            "att-first-pending",
            b"first-pending",
        )
        later_id = _insert_message(
            conn,
            "second-pending@example.test",
            db_id="second-pending",
            received_at=NOW - timedelta(days=2),
        )
        later_key = _add_attachment(
            conn,
            adapter,
            later_id,
            "asset-second-pending",
            "att-second-pending",
            b"second-pending",
        )

    original_delete = adapter.delete

    def fail_first_delete(subdir: str, key: str) -> None:
        if subdir == "mail-attachments" and key == failed_key:
            raise OSError("private object deletion failure")
        original_delete(subdir, key)

    monkeypatch.setattr(adapter, "delete", fail_first_delete)
    first = mail_retention.run_mail_retention(now=NOW, batch_size=1)
    assert first.queued_messages == 2
    assert first.failed_attachments == 1
    failed = _mail_row(failed_id)
    assert failed is not None
    assert failed["retention_queued_at"] == iso(NOW + timedelta(microseconds=1))
    assert failed["from_addr"] == ""
    assert failed["subject"] is None
    assert failed["text_body"] is None
    assert _mail_row(later_id) is not None
    assert adapter.exists("mail-attachments", failed_key)
    assert adapter.exists("mail-attachments", later_key)

    second = mail_retention.run_mail_retention(now=NOW, batch_size=1)
    assert second.deleted_messages == 1
    assert _mail_row(later_id) is None
    assert _mail_row(failed_id) is not None
    assert not adapter.exists("mail-attachments", later_key)
    assert adapter.exists("mail-attachments", failed_key)
    with get_conn() as conn:
        assert conn.execute("SELECT 1 FROM asset WHERE id='asset-second-pending'").fetchone() is None
        assert conn.execute("SELECT 1 FROM mail_attachment WHERE id='att-second-pending'").fetchone() is None

    third = mail_retention.run_mail_retention(now=NOW, batch_size=1)
    assert third.deleted_messages == 0
    assert third.failed_attachments == 1
    assert _mail_row(failed_id) is not None
    assert adapter.exists("mail-attachments", failed_key)


def test_nonce_expiry_is_strict_and_runs_without_new_mail(retention_env):
    del retention_env
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO mail_nonce (nonce, received_at, expires_at) VALUES (?, ?, ?)",
            ("expired-nonce", iso(NOW - timedelta(days=1)), iso(NOW - timedelta(seconds=1))),
        )
        conn.execute(
            "INSERT INTO mail_nonce (nonce, received_at, expires_at) VALUES (?, ?, ?)",
            ("exact-nonce", iso(NOW - timedelta(days=1)), iso(NOW)),
        )
        conn.execute(
            "INSERT INTO mail_nonce (nonce, received_at, expires_at) VALUES (?, ?, ?)",
            ("future-nonce", iso(NOW), iso(NOW + timedelta(seconds=1))),
        )

    result = mail_retention.run_mail_retention(now=NOW)
    assert result.deleted_nonces == 1
    with get_conn() as conn:
        assert conn.execute("SELECT 1 FROM mail_nonce WHERE nonce='expired-nonce'").fetchone() is None
        assert conn.execute("SELECT 1 FROM mail_nonce WHERE nonce='exact-nonce'").fetchone()
        assert conn.execute("SELECT 1 FROM mail_nonce WHERE nonce='future-nonce'").fetchone()


def test_no_attachment_and_missing_asset_messages_converge(retention_env):
    del retention_env
    with get_conn() as conn:
        no_attachment = _insert_message(
            conn,
            "no-attachment@example.test",
            db_id="no-attachment",
            received_at=NOW - timedelta(days=2),
        )
        missing_asset = _insert_message(
            conn,
            "missing-asset@example.test",
            db_id="missing-asset",
            received_at=NOW - timedelta(days=2),
        )
        conn.execute(
            "INSERT INTO mail_attachment (id, message_id, asset_id, name, mime_type, size) "
            "VALUES ('missing-att', ?, 'asset-not-present', 'private.bin', 'application/octet-stream', 1)",
            (missing_asset,),
        )

    result = mail_retention.run_mail_retention(now=NOW)
    assert result.queued_messages == 2
    assert result.deleted_messages == 2
    assert result.missing_assets == 1
    assert _mail_row(no_attachment) is None
    assert _mail_row(missing_asset) is None
    with get_conn() as conn:
        assert conn.execute("SELECT 1 FROM mail_attachment WHERE id='missing-att'").fetchone() is None


def test_raw_retention_converges_for_stored_legacy_and_missing_object_states(retention_env):
    _, adapter = retention_env
    with get_conn() as conn:
        stored_id = _insert_message(
            conn,
            "raw-stored@example.test",
            db_id="raw-stored",
            received_at=NOW - timedelta(days=2),
        )
        stored_key = _add_raw(conn, adapter, stored_id, b"stored raw")
        legacy_id = _insert_message(
            conn,
            "raw-legacy@example.test",
            db_id="raw-legacy",
            received_at=NOW - timedelta(days=2),
        )
        missing_id = _insert_message(
            conn,
            "raw-missing@example.test",
            db_id="raw-missing",
            received_at=NOW - timedelta(days=2),
        )
        missing_key = _add_raw(conn, adapter, missing_id, b"missing raw")
    adapter.delete("mail-raw", missing_key)

    result = mail_retention.run_mail_retention(now=NOW)
    assert result.deleted_messages == 3
    assert result.deleted_raw_records == 2
    assert result.deleted_raw_objects == 2
    assert _mail_row(stored_id) is None
    assert _mail_row(legacy_id) is None
    assert _mail_row(missing_id) is None
    assert not adapter.exists("mail-raw", stored_key)


def test_raw_failure_does_not_block_attachment_cleanup_and_retries(retention_env, monkeypatch: pytest.MonkeyPatch):
    _, adapter = retention_env
    with get_conn() as conn:
        message_id = _insert_message(
            conn,
            "raw-retry@example.test",
            db_id="raw-retry",
            received_at=NOW - timedelta(days=2),
        )
        raw_key = _add_raw(conn, adapter, message_id, b"retry raw")
        attachment_key = _add_attachment(
            conn,
            adapter,
            message_id,
            "asset-raw-retry",
            "att-raw-retry",
            b"retry attachment",
        )

    original_delete = adapter.delete

    def fail_raw(subdir: str, key: str) -> None:
        if subdir == "mail-raw" and key == raw_key:
            raise OSError("raw deletion unavailable")
        original_delete(subdir, key)

    monkeypatch.setattr(adapter, "delete", fail_raw)
    first = mail_retention.run_mail_retention(now=NOW)
    assert first.failed_raw == 1
    assert first.deferred_messages == 1
    assert first.deleted_attachments == 1
    assert first.deleted_messages == 0
    assert _mail_row(message_id) is not None
    assert adapter.exists("mail-raw", raw_key)
    assert not adapter.exists("mail-attachments", attachment_key)
    with get_conn() as conn:
        assert conn.execute("SELECT 1 FROM mail_attachment WHERE id='att-raw-retry'").fetchone() is None
        assert conn.execute("SELECT raw_state FROM mail_message WHERE id=?", (message_id,)).fetchone()[0] == "stored"

    monkeypatch.setattr(adapter, "delete", original_delete)
    second = mail_retention.run_mail_retention(now=NOW)
    assert second.deleted_messages == 1
    assert second.deleted_raw_records == 1
    assert _mail_row(message_id) is None
    assert not adapter.exists("mail-raw", raw_key)


@pytest.mark.parametrize("failing_helper", ["_attachment_ids", "_delete_completed_message"])
def test_internal_defer_errors_emit_safe_partial_counts(
    retention_env,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failing_helper: str,
):
    del retention_env
    canary = "PRIVATE_CANARY C:\\private\\mail\n"
    with get_conn() as conn:
        message_id = _insert_message(
            conn,
            "defer-error@example.test",
            db_id=f"defer-{failing_helper}",
            received_at=NOW - timedelta(days=2),
        )

    def fail_with_canary(*_args, **_kwargs):
        raise RuntimeError(canary)

    monkeypatch.setattr(mail_retention, failing_helper, fail_with_canary)
    result = mail_retention.run_mail_retention(now=NOW)

    assert result.deleted_messages == 0
    assert result.deferred_messages == 1
    assert "deferred_messages=1" in result.audit_result()
    assert _mail_row(message_id) is not None

    output = capsys.readouterr()
    assert canary not in output.out + output.err
    events = [json.loads(line) for line in output.out.splitlines() if line]
    assert len(events) == 1
    assert events[0]["component"] == "mail_retention"
    assert events[0]["result"] == "partial"
    assert events[0]["auditStatus"] == "written"
    assert events[0]["deferredMessages"] == 1


def test_raw_state_mismatches_stay_pending_without_object_deletion(retention_env):
    _, adapter = retention_env
    with get_conn() as conn:
        stored_without_row = _insert_message(
            conn,
            "raw-stored-missing@example.test",
            db_id="raw-stored-missing",
            received_at=NOW - timedelta(days=2),
        )
        conn.execute("UPDATE mail_message SET raw_state='stored' WHERE id=?", (stored_without_row,))
        legacy_with_row = _insert_message(
            conn,
            "raw-legacy-row@example.test",
            db_id="raw-legacy-row",
            received_at=NOW - timedelta(days=2),
        )
        legacy_key = adapter.save(b"legacy row raw", "mail-raw")
        conn.execute(
            "INSERT INTO mail_raw (message_id, object_key, sha256, size, content_type, created_at) "
            "VALUES (?, ?, ?, ?, 'message/rfc822', ?)",
            (
                legacy_with_row,
                legacy_key,
                hashlib.sha256(b"legacy row raw").hexdigest(),
                len(b"legacy row raw"),
                iso(NOW),
            ),
        )

    result = mail_retention.run_mail_retention(now=NOW)
    assert result.inconsistent_raw == 2
    assert result.deleted_messages == 0
    assert _mail_row(stored_without_row) is not None
    assert _mail_row(legacy_with_row) is not None
    assert adapter.exists("mail-raw", legacy_key)


def test_multiple_attachments_missing_object_crash_state_and_repeat_converge(retention_env):
    _, adapter = retention_env
    with get_conn() as conn:
        message_id = _insert_message(
            conn,
            "multi@example.test",
            db_id="multi-mail",
            received_at=NOW - timedelta(days=2),
        )
        _add_attachment(conn, adapter, message_id, "asset-one", "att-one", b"first-bytes")
        _add_attachment(conn, adapter, message_id, "asset-two", "att-two", b"second-bytes")
    allocated_before = adapter._allocated_bytes()

    first = mail_retention.run_mail_retention(now=NOW)
    assert first.deleted_messages == 1
    assert first.deleted_attachments == 2
    assert first.deleted_assets == 2
    assert first.deleted_objects == 2
    assert adapter._allocated_bytes() < allocated_before

    with get_conn() as conn:
        crashed_id = _insert_message(
            conn,
            "crashed@example.test",
            db_id="crashed-mail",
            received_at=NOW - timedelta(days=2),
            queued_at=NOW,
            from_addr="",
            to_addr=None,
            subject=None,
            text_body=None,
            html_body=None,
            read_flag=1,
        )
        key = _add_attachment(conn, adapter, crashed_id, "asset-crashed", "att-crashed", b"crash-state")
        conn.execute("UPDATE mail_attachment SET name='' WHERE id='att-crashed'")
    adapter.delete("mail-attachments", key)

    second = mail_retention.run_mail_retention(now=NOW)
    assert second.deleted_messages == 1
    assert second.deleted_attachments == 1
    assert second.deleted_assets == 1
    assert _mail_row(crashed_id) is None
    assert mail_retention.run_mail_retention(now=NOW).deleted_messages == 0


@pytest.mark.parametrize("reference", ["mail", "photo-original", "photo-thumbnail", "knowledge"])
def test_shared_asset_is_never_deleted(retention_env, reference: str):
    _, adapter = retention_env
    with get_conn() as conn:
        expired_id = _insert_message(
            conn,
            f"shared-{reference}@example.test",
            db_id=f"expired-{reference}",
            received_at=NOW - timedelta(days=2),
        )
        key = _add_attachment(
            conn,
            adapter,
            expired_id,
            f"asset-shared-{reference}",
            f"att-shared-{reference}",
            b"shared-data",
        )
        asset_id = f"asset-shared-{reference}"
        if reference == "mail":
            active_id = _insert_message(
                conn,
                "active-shared@example.test",
                db_id="active-shared",
                received_at=NOW,
            )
            conn.execute(
                "INSERT INTO mail_attachment (id, message_id, asset_id, name, mime_type, size) "
                "VALUES ('att-active-shared', ?, ?, 'active.bin', 'application/octet-stream', 11)",
                (active_id, asset_id),
            )
        elif reference == "photo-original":
            conn.execute(
                "INSERT INTO photo (id, asset_id, capture_at, thumbnail_asset_id, width, height, created_at) "
                "VALUES ('photo-original-shared', ?, NULL, NULL, 1, 1, ?)",
                (asset_id, iso(NOW)),
            )
        elif reference == "photo-thumbnail":
            original_key = adapter.save(b"original", "originals")
            conn.execute(
                "INSERT INTO asset (id, original_name, mime_type, size, object_key, sha256, status, created_at) "
                "VALUES ('asset-photo-original', 'original.png', 'image/png', 8, ?, 'hash', 'active', ?)",
                (original_key, iso(NOW)),
            )
            conn.execute(
                "INSERT INTO photo (id, asset_id, capture_at, thumbnail_asset_id, width, height, created_at) "
                "VALUES ('photo-thumbnail-shared', 'asset-photo-original', NULL, ?, 1, 1, ?)",
                (asset_id, iso(NOW)),
            )
        else:
            conn.execute(
                "INSERT INTO knowledge_document (id, asset_id, title, document_type, hash, index_state, created_at) "
                "VALUES ('knowledge-shared', ?, 'private', 'txt', 'hash', 'queued', ?)",
                (asset_id, iso(NOW)),
            )

    result = mail_retention.run_mail_retention(now=NOW)
    assert result.deleted_messages == 1
    assert result.shared_asset_references == 1
    assert result.deleted_assets == 0
    assert adapter.exists("mail-attachments", key)
    with get_conn() as conn:
        assert conn.execute("SELECT 1 FROM asset WHERE id=?", (asset_id,)).fetchone()
        assert conn.execute(
            "SELECT 1 FROM mail_attachment WHERE id=?",
            (f"att-shared-{reference}",),
        ).fetchone() is None


def test_audit_and_log_are_counts_only_and_audit_failure_does_not_rollback(
    retention_env, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    _, adapter = retention_env
    sensitive_from = "private-sender@example.test"
    sensitive_subject = "private subject must not be audited"
    with get_conn() as conn:
        message_id = _insert_message(
            conn,
            "audit@example.test",
            db_id="audit-mail",
            received_at=NOW - timedelta(days=2),
            from_addr=sensitive_from,
            subject=sensitive_subject,
            text_body="private body",
        )
        key = _add_attachment(conn, adapter, message_id, "asset-audit", "att-audit", b"audit-data")
        raw_key = _add_raw(conn, adapter, message_id, b"audit raw data")

    captured: list[dict] = []

    def record_audit(*_args, **kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(mail_retention, "audit", record_audit)
    with caplog.at_level(logging.INFO, logger="app.services.mail_retention"):
        result = mail_retention.run_mail_retention(now=NOW)
    assert result.deleted_messages == 1
    assert captured and captured[0]["result"] == result.audit_result()
    output = "\n".join(record.getMessage() for record in caplog.records)
    for forbidden in (sensitive_from, sensitive_subject, "private body", key, raw_key, "audit-data", "audit raw data"):
        assert forbidden not in output

    with get_conn() as conn:
        retry_id = _insert_message(
            conn,
            "audit-failure@example.test",
            db_id="audit-failure-mail",
            received_at=NOW - timedelta(days=2),
        )
    monkeypatch.setattr(
        mail_retention,
        "audit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("private audit error")),
    )
    with caplog.at_level(logging.ERROR, logger="app.services.mail_retention"):
        second = mail_retention.run_mail_retention(now=NOW)
    assert second.deleted_messages == 1
    assert _mail_row(retry_id) is None
    assert "private audit error" not in "\n".join(record.getMessage() for record in caplog.records)


def test_worker_maintenance_is_immediate_safe_and_does_not_starve_jobs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(
        worker_runner,
        "run_mail_retention",
        lambda: (_ for _ in ()).throw(RuntimeError("private worker maintenance error")),
    )
    assert worker_runner.run_maintenance() is False
    assert "private worker maintenance error" not in capsys.readouterr().out

    calls: list[str] = []
    monkeypatch.setattr(worker_runner, "run_maintenance", lambda: calls.append("maintenance") or True)

    def stop_after_one_job(*_args, **_kwargs):
        calls.append("job")
        raise KeyboardInterrupt

    monkeypatch.setattr(worker_runner, "run_once", stop_after_one_job)
    worker_runner.main()
    assert calls == ["maintenance", "job"]
