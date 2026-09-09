"""本轮关键失败路径回归：会话绑定、RAG、worker、向量清理与 readiness。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import io
import json
import sys
from types import ModuleType
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

TEST_PASSWORD = "test-password-12345"

from app.config import settings  # noqa: E402
from app.db import get_conn, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.routers import files as files_router  # noqa: E402
from app.routers import misc  # noqa: E402
from app.security import ACCESS_COOKIE, REFRESH_COOKIE, iso, now_utc, sha256_hex  # noqa: E402
from app.services import rag_engine, vector_store  # noqa: E402
from app.services.jobs import (  # noqa: E402
    claim_next_job,
    enqueue_job,
    fail_job,
    recover_stale_running_jobs,
    succeed_job,
)
import app.storage as storage_module  # noqa: E402
import app.deps as deps  # noqa: E402
from app.storage import QUOTA_LOCK_NAME, StorageAdapter, StorageKeyAllocationError, storage  # noqa: E402


def test_config_does_not_call_dotenv_when_disabled(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []
    dotenv = ModuleType("dotenv")
    dotenv.load_dotenv = lambda: calls.append("dotenv")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "dotenv", dotenv)
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    module_name = "starisle_config_dotenv_disabled_test"
    config_path = Path(__file__).resolve().parents[1] / "app" / "config.py"
    spec = importlib.util.spec_from_file_location(module_name, config_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        assert calls == []
    finally:
        sys.modules.pop(module_name, None)


def _csrf(client: TestClient) -> str:
    response = client.get("/api/v1/auth/csrf")
    assert response.status_code == 200
    return response.json()["csrfToken"]


def _login(client: TestClient) -> str:
    token = _csrf(client)
    response = client.post(
        "/api/v1/auth/login",
        json={"password": TEST_PASSWORD},
        headers={"X-CSRF-Token": token, "Origin": "http://testserver"},
    )
    assert response.status_code == 200, "login must succeed"
    return token


def _small_png_magic_payload() -> bytes:
    """A small PNG-signature payload for fake-Pillow header tests."""

    return b"\x89PNG\r\n\x1a\nsmall-png-payload"


def _photo_persistence_state() -> tuple[int, int, int, int]:
    with get_conn() as conn:
        return (
            conn.execute("SELECT COUNT(*) AS c FROM asset").fetchone()["c"],
            conn.execute("SELECT COUNT(*) AS c FROM photo").fetchone()["c"],
            conn.execute("SELECT COUNT(*) AS c FROM idempotency").fetchone()["c"],
            conn.execute("SELECT COUNT(*) AS c FROM audit_event WHERE action='photo_upload'").fetchone()["c"],
        )


def _photo_object_files() -> set[Path]:
    return {
        path
        for path in storage.root.rglob("*")
        if path.is_file() and path.name != QUOTA_LOCK_NAME
    }


@pytest.mark.parametrize(
    ("dimensions", "idempotency_key"),
    [((7000, 6000), "oversized-photo-area-header"), ((8001, 1), "oversized-photo-edge-header")],
)
def test_photo_header_dimension_gate_rejects_before_full_decode(
    monkeypatch: pytest.MonkeyPatch, dimensions: tuple[int, int], idempotency_key: str
):
    init_db()
    client = TestClient(app, raise_server_exceptions=False)
    _login(client)
    from PIL import Image

    calls = {"load": 0}

    class HeaderImage:
        size = dimensions

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> bool:
            return False

        def load(self) -> None:
            calls["load"] += 1
            pytest.fail("oversized image header must be rejected before load")

    monkeypatch.setattr(Image, "open", lambda *_args, **_kwargs: HeaderImage())
    before_objects = _photo_object_files()
    before_state = _photo_persistence_state()
    response = client.post(
        "/api/v1/photos",
        files={"file": ("oversized.png", io.BytesIO(_small_png_magic_payload()), "image/png")},
        headers={
            "X-CSRF-Token": _csrf(client),
            "Origin": "http://testserver",
            "Idempotency-Key": idempotency_key,
        },
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert calls["load"] == 0
    assert _photo_object_files() == before_objects
    assert _photo_persistence_state() == before_state
    with get_conn() as conn:
        assert conn.execute(
            "SELECT 1 FROM idempotency WHERE key_hash=? AND resource_type='photo'",
            (sha256_hex(idempotency_key.encode()),),
        ).fetchone() is None


def test_photo_thumbnail_header_gate_cleans_temporary_original(monkeypatch: pytest.MonkeyPatch):
    init_db()
    client = TestClient(app, raise_server_exceptions=False)
    _login(client)
    from PIL import Image, ImageOps

    calls = {"load": 0, "convert": 0}
    saved_subdirs: list[str] = []

    class SafeHeaderImage:
        size = (3, 3)

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> bool:
            return False

        def load(self) -> None:
            calls["load"] += 1

        def close(self) -> None:
            pass

    class OversizedHeaderImage:
        size = (8001, 1)

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> bool:
            return False

        def convert(self, *_args, **_kwargs):
            calls["convert"] += 1
            pytest.fail("thumbnail header must be rejected before convert")

    opened = iter((SafeHeaderImage(), OversizedHeaderImage()))
    saved = storage.save

    def record_save(data: bytes, subdir: str, suffix: str = "") -> str:
        saved_subdirs.append(subdir)
        return saved(data, subdir, suffix)

    monkeypatch.setattr(Image, "open", lambda *_args, **_kwargs: next(opened))
    monkeypatch.setattr(ImageOps, "exif_transpose", lambda image: image)
    monkeypatch.setattr(storage, "save", record_save)
    monkeypatch.setattr(files_router, "_read_exif_capture", lambda _data: pytest.fail("EXIF must not run"))
    idempotency_key = "oversized-thumbnail-header"
    before_objects = _photo_object_files()
    before_state = _photo_persistence_state()
    response = client.post(
        "/api/v1/photos",
        files={"file": ("thumbnail-guard.png", io.BytesIO(_small_png_magic_payload()), "image/png")},
        headers={
            "X-CSRF-Token": _csrf(client),
            "Origin": "http://testserver",
            "Idempotency-Key": idempotency_key,
        },
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert calls == {"load": 1, "convert": 0}
    assert saved_subdirs == ["originals"]
    assert _photo_object_files() == before_objects
    assert _photo_persistence_state() == before_state
    with get_conn() as conn:
        assert conn.execute(
            "SELECT 1 FROM idempotency WHERE key_hash=? AND resource_type='photo'",
            (sha256_hex(idempotency_key.encode()),),
        ).fetchone() is None


def test_photo_header_dimension_gate_allows_exact_limits():
    class BoundaryHeaderImage:
        size = (8000, 5000)

    assert files_router._validate_image_header_dimensions(BoundaryHeaderImage()) == (8000, 5000)


def test_anonymous_csrf_retention_prunes_expired_and_preserves_latest(monkeypatch: pytest.MonkeyPatch):
    init_db()
    client = TestClient(app)
    issued_at = now_utc()
    with get_conn() as conn:
        conn.execute("DELETE FROM csrf WHERE session_id IS NULL")
        for csrf_id, session_id in (("expired-anonymous", None), ("expired-other-session", "expired-session")):
            conn.execute(
                "INSERT INTO csrf (id, session_id, token_hash, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    csrf_id,
                    session_id,
                    sha256_hex(csrf_id.encode()),
                    iso(issued_at - timedelta(seconds=1)),
                    iso(issued_at - timedelta(minutes=1)),
                ),
            )
        sessions_before = conn.execute("SELECT COUNT(*) AS c FROM session").fetchone()["c"]

    timestamps = iter(issued_at + timedelta(microseconds=offset) for offset in range(1_000))
    monkeypatch.setattr(deps, "now_utc", lambda: next(timestamps))
    tokens = [_csrf(client) for _ in range(101)]

    with get_conn() as conn:
        anonymous_count = conn.execute(
            "SELECT COUNT(*) AS c FROM csrf WHERE session_id IS NULL"
        ).fetchone()["c"]
        expired_count = conn.execute(
            "SELECT COUNT(*) AS c FROM csrf WHERE id IN (?, ?)",
            ("expired-anonymous", "expired-other-session"),
        ).fetchone()["c"]
    assert anonymous_count == 100
    assert expired_count == 0

    trimmed = client.post(
        "/api/v1/auth/login",
        json={"password": TEST_PASSWORD},
        headers={"X-CSRF-Token": tokens[0], "Origin": "http://testserver"},
    )
    assert trimmed.status_code == 403
    assert trimmed.json()["error"]["code"] == "CSRF_REQUIRED"
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM session").fetchone()["c"] == sessions_before

    latest = client.post(
        "/api/v1/auth/login",
        json={"password": TEST_PASSWORD},
        headers={"X-CSRF-Token": tokens[-1], "Origin": "http://testserver"},
    )
    assert latest.status_code == 200


def test_session_csrf_retention_is_bounded_and_latest_token_can_mutate():
    init_db()
    client = TestClient(app)
    _login(client)
    refresh_token = client.cookies.get(REFRESH_COOKIE)
    assert refresh_token
    with get_conn() as conn:
        session = conn.execute(
            "SELECT id FROM session WHERE refresh_hash=?", (sha256_hex(refresh_token.encode()),)
        ).fetchone()
    assert session is not None

    tokens = [_csrf(client) for _ in range(101)]
    with get_conn() as conn:
        session_count = conn.execute(
            "SELECT COUNT(*) AS c FROM csrf WHERE session_id=?", (session["id"],)
        ).fetchone()["c"]
    assert session_count == 100

    created = client.post(
        "/api/v1/conversations",
        json={"title": "会话 CSRF 保留边界"},
        headers={"X-CSRF-Token": tokens[-1], "Origin": "http://testserver"},
    )
    assert created.status_code == 201


def test_csrf_retention_keeps_new_token_when_created_at_ties(monkeypatch: pytest.MonkeyPatch):
    init_db()
    client = TestClient(app)
    issued_at = now_utc()
    monkeypatch.setattr(deps, "now_utc", lambda: issued_at)
    with get_conn() as conn:
        conn.execute("DELETE FROM csrf WHERE session_id IS NULL")

    tokens = [deps.issue_csrf() for _ in range(101)]
    with get_conn() as conn:
        anonymous_count = conn.execute(
            "SELECT COUNT(*) AS c FROM csrf WHERE session_id IS NULL"
        ).fetchone()["c"]
        latest_row = conn.execute(
            "SELECT 1 FROM csrf WHERE token_hash=?", (sha256_hex(tokens[-1].encode()),)
        ).fetchone()
    assert anonymous_count == 100
    assert latest_row is not None

    latest = client.post(
        "/api/v1/auth/login",
        json={"password": TEST_PASSWORD},
        headers={"X-CSRF-Token": tokens[-1], "Origin": "http://testserver"},
    )
    assert latest.status_code == 200


def test_csrf_rejects_missing_or_mismatched_origin():
    init_db()
    client = TestClient(app)
    token = _csrf(client)
    missing = client.post("/api/v1/auth/login", json={"password": TEST_PASSWORD}, headers={"X-CSRF-Token": token})
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "CSRF_REQUIRED"

    mismatch = client.post(
        "/api/v1/auth/login",
        json={"password": TEST_PASSWORD},
        headers={"X-CSRF-Token": token, "Origin": "https://elsewhere.invalid"},
    )
    assert mismatch.status_code == 403
    assert mismatch.json()["error"]["code"] == "CSRF_REQUIRED"


def test_login_refresh_logout_bind_and_revoke_session():
    init_db()
    client = TestClient(app)
    login_csrf = _login(client)
    first_refresh = client.cookies.get(REFRESH_COOKIE)
    assert first_refresh
    with get_conn() as conn:
        first = conn.execute(
            "SELECT id FROM session WHERE refresh_hash=?", (sha256_hex(first_refresh.encode()),)
        ).fetchone()
        assert first is not None
        bound = conn.execute(
            "SELECT session_id FROM csrf WHERE token_hash=?", (sha256_hex(login_csrf.encode()),)
        ).fetchone()
        assert bound and bound["session_id"] == first["id"]

    refresh_csrf = _csrf(client)
    refreshed = client.post(
        "/api/v1/auth/refresh",
        headers={"X-CSRF-Token": refresh_csrf, "Origin": "http://testserver"},
    )
    assert refreshed.status_code == 200, "refresh must succeed"
    current_access = client.cookies.get(ACCESS_COOKIE)
    assert current_access
    second_refresh = client.cookies.get(REFRESH_COOKIE)
    assert second_refresh and second_refresh != first_refresh
    with get_conn() as conn:
        old = conn.execute("SELECT revoked_at FROM session WHERE id=?", (first["id"],)).fetchone()
        assert old and old["revoked_at"]
        second = conn.execute(
            "SELECT id FROM session WHERE refresh_hash=?", (sha256_hex(second_refresh.encode()),)
        ).fetchone()
        assert second is not None
        migrated = conn.execute(
            "SELECT COUNT(*) AS c FROM csrf WHERE session_id=?", (second["id"],)
        ).fetchone()
        assert migrated and migrated["c"] >= 1

    logout_csrf = _csrf(client)
    logged_out = client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": logout_csrf, "Origin": "http://testserver"},
    )
    assert logged_out.status_code == 204
    set_cookies = "\n".join(logged_out.headers.get_list("set-cookie"))
    assert "star_isle_access=" in set_cookies and "star_isle_refresh=" in set_cookies
    with get_conn() as conn:
        current = conn.execute(
            "SELECT id, revoked_at FROM session WHERE refresh_hash=?", (sha256_hex(second_refresh.encode()),)
        ).fetchone()
        assert current and current["revoked_at"]
        assert conn.execute("SELECT COUNT(*) AS c FROM csrf WHERE session_id=?", (current["id"],)).fetchone()["c"] == 0
    stale_client = TestClient(app)
    stale_client.cookies.set(ACCESS_COOKIE, current_access)
    assert stale_client.get("/api/v1/auth/me").status_code == 401


def test_validation_error_and_readiness_failure_use_real_status(monkeypatch):
    init_db()
    client = TestClient(app)
    _login(client)
    invalid = client.post(
        "/api/v1/rag/query",
        json={"query": ""},
        headers={"X-CSRF-Token": _csrf(client), "Origin": "http://testserver"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "UNPROCESSABLE"

    monkeypatch.setattr(misc.storage, "healthcheck", lambda: False)
    not_ready = client.get("/api/v1/health/ready")
    assert not_ready.status_code == 503
    assert not_ready.json()["status"] == "not_ready"


def test_create_conversation_accepts_csrf_header_after_real_login():
    init_db()
    client = TestClient(app)
    _login(client)
    response = client.post(
        "/api/v1/conversations",
        json={"title": "CSRF Header 回归"},
        headers={"X-CSRF-Token": _csrf(client), "Origin": "http://testserver"},
    )
    assert response.status_code == 201, "csrf protected write must succeed"
    assert response.json()["title"] == "CSRF Header 回归"
    with get_conn() as conn:
        stored = conn.execute(
            "SELECT created_at FROM conversation WHERE id=?",
            (response.json()["id"],),
        ).fetchone()
    assert stored is not None
    assert response.json()["createdAt"] == stored["created_at"]


def test_rag_producer_error_never_writes_assistant_or_done(monkeypatch):
    init_db()
    client = TestClient(app)
    _login(client)
    with get_conn() as conn:
        before = conn.execute("SELECT COUNT(*) AS c FROM message WHERE role='assistant'").fetchone()["c"]

    async def broken_stream(*_args, **_kwargs):
        if False:  # 保持 async generator 类型
            yield {"event": "token", "token": "unused"}
        raise RuntimeError("model failed")

    monkeypatch.setattr(rag_engine, "stream_chat", broken_stream)
    response = client.post(
        "/api/v1/rag/query",
        json={"query": "失败路径"},
        headers={"X-CSRF-Token": _csrf(client), "Origin": "http://testserver"},
    )
    events = [json.loads(part[6:]) for part in response.text.split("\n\n") if part.startswith("data: ")]
    assert any(event["event"] == "error" for event in events)
    assert not any(event["event"] == "done" for event in events)
    with get_conn() as conn:
        after = conn.execute("SELECT COUNT(*) AS c FROM message WHERE role='assistant'").fetchone()["c"]
    assert after == before


def test_rag_zero_token_stream_never_writes_assistant_or_done(monkeypatch):
    init_db()
    client = TestClient(app)
    _login(client)
    with get_conn() as conn:
        before = conn.execute("SELECT COUNT(*) AS c FROM message WHERE role='assistant'").fetchone()["c"]

    async def empty_stream(*_args, **_kwargs):
        if False:  # 保持 async generator 类型，并正常结束而不产生 token。
            yield {"event": "token", "token": "unused"}

    monkeypatch.setattr(rag_engine, "stream_chat", empty_stream)
    response = client.post(
        "/api/v1/rag/query",
        json={"query": "零 token 路径"},
        headers={"X-CSRF-Token": _csrf(client), "Origin": "http://testserver"},
    )
    events = [json.loads(part[6:]) for part in response.text.split("\n\n") if part.startswith("data: ")]
    assert any(event["event"] == "error" for event in events)
    assert not any(event["event"] == "done" for event in events)
    with get_conn() as conn:
        after = conn.execute("SELECT COUNT(*) AS c FROM message WHERE role='assistant'").fetchone()["c"]
    assert after == before


def test_rag_partial_stream_incomplete_never_writes_assistant_or_done(monkeypatch):
    init_db()
    client = TestClient(app)
    _login(client)
    with get_conn() as conn:
        before = conn.execute("SELECT COUNT(*) AS c FROM message WHERE role='assistant'").fetchone()["c"]

    async def partial_stream(*_args, **_kwargs):
        yield {"event": "token", "token": "partial"}
        raise rag_engine.ChatStreamError("CHAT_STREAM_INCOMPLETE")

    monkeypatch.setattr(rag_engine, "stream_chat", partial_stream)
    response = client.post(
        "/api/v1/rag/query",
        json={"query": "partial 路径"},
        headers={"X-CSRF-Token": _csrf(client), "Origin": "http://testserver"},
    )
    events = [json.loads(part[6:]) for part in response.text.split("\n\n") if part.startswith("data: ")]
    assert any(event["event"] == "token" and event.get("token") == "partial" for event in events)
    assert any(event["event"] == "error" and event.get("code") == "CHAT_STREAM_INCOMPLETE" for event in events)
    assert not any(event["event"] == "done" for event in events)
    with get_conn() as conn:
        after = conn.execute("SELECT COUNT(*) AS c FROM message WHERE role='assistant'").fetchone()["c"]
    assert after == before


def test_jobs_are_conditionally_claimed_recovered_and_not_overwritten():
    init_db()
    with get_conn() as conn:
        # 只让本用例 job 处于可认领状态，随后用两个独立连接同时竞争它。
        conn.execute("UPDATE ingestion_job SET next_run_at='2999-01-01T00:00:00+00:00' WHERE status='queued'")
        job_id = enqueue_job(conn, "ingest", {"documentId": "missing", "assetId": "missing"})
        # 与其他测试留下的队列任务隔离：该任务明确是本用例的最早可认领项。
        conn.execute(
            "UPDATE ingestion_job SET next_run_at='1970-01-01T00:00:00+00:00', created_at='1970-01-01T00:00:00+00:00' WHERE id=?",
            (job_id,),
        )
    def claim(worker_id: str):
        with get_conn() as conn:
            return claim_next_job(conn, worker_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(claim, ["one", "two"]))
    assert sum(1 for row in claimed if row and row["id"] == job_id) == 1

    with get_conn() as conn:
        fail_job(conn, job_id, "EXPECTED")
        succeed_job(conn, job_id)
        row = conn.execute("SELECT status FROM ingestion_job WHERE id=?", (job_id,)).fetchone()
        assert row and row["status"] == "queued"
        conn.execute(
            "UPDATE ingestion_job SET status='running', updated_at=? WHERE id=?",
            (iso(now_utc() - timedelta(hours=1)), job_id),
        )
        assert recover_stale_running_jobs(conn, stale_after_seconds=1) == 1
        recovered = conn.execute("SELECT status FROM ingestion_job WHERE id=?", (job_id,)).fetchone()
        assert recovered and recovered["status"] == "queued"


def test_delete_chunk_vectors_removes_ref_and_blob_before_chunks():
    init_db()
    document_id = "doc-vector-cleanup"
    chunk_id = "chunk-vector-cleanup"
    with get_conn() as conn:
        now = iso(now_utc())
        conn.execute(
            "INSERT OR REPLACE INTO asset (id, original_name, mime_type, size, object_key, sha256, status, created_at) "
            "VALUES ('asset-vector-cleanup', 'cleanup.txt', 'text/plain', 1, 'cleanup', 'h', 'active', ?)",
            (now,),
        )
        conn.execute(
            "INSERT OR REPLACE INTO knowledge_document (id, asset_id, title, document_type, hash, index_state, created_at) "
            "VALUES (?, 'asset-vector-cleanup', 'cleanup', 'txt', 'h', 'ready', ?)",
            (document_id, now),
        )
        conn.execute(
            "INSERT OR REPLACE INTO chunk (id, document_id, seq, text, token_count, hash) VALUES (?, ?, 0, ?, 1, ?)",
            (chunk_id, document_id, "可检索文本", "h"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO vector_ref (chunk_id, store, vector_id, model_id, dim) VALUES (?, 'sqlite-vec', ?, 'hash-v1', 1)",
            (chunk_id, chunk_id),
        )
        conn.execute(
            "INSERT OR REPLACE INTO chunk_embedding (chunk_id, embedding) VALUES (?, ?)",
            (chunk_id, vector_store.serialize_float32([1.0], 1)),
        )
        rag_engine.delete_chunk_vectors(conn, document_id)
        assert conn.execute("SELECT 1 FROM chunk WHERE id=?", (chunk_id,)).fetchone() is None
        assert conn.execute("SELECT 1 FROM vector_ref WHERE chunk_id=?", (chunk_id,)).fetchone() is None
        assert conn.execute("SELECT 1 FROM chunk_embedding WHERE chunk_id=?", (chunk_id,)).fetchone() is None


def test_photo_idempotency_quota_and_thumbnail_failure_leave_no_orphans(monkeypatch):
    init_db()
    client = TestClient(app, raise_server_exceptions=False)
    _login(client)
    from PIL import Image

    def png_bytes() -> bytes:
        import io

        out = io.BytesIO()
        Image.new("RGB", (3, 3), (1, 2, 3)).save(out, format="PNG")
        return out.getvalue()

    def object_files() -> set[Path]:
        return {
            path
            for path in storage.root.rglob("*")
            if path.is_file() and path.name != QUOTA_LOCK_NAME
        }

    token = _csrf(client)
    first = client.post(
        "/api/v1/photos",
        files={"file": ("idempotent.png", io.BytesIO(png_bytes()), "image/png")},
        headers={"X-CSRF-Token": token, "Origin": "http://testserver", "Idempotency-Key": "same-photo"},
    )
    assert first.status_code == 201, "first idempotent upload must succeed"
    after_first = object_files()
    duplicate = client.post(
        "/api/v1/photos",
        files={"file": ("idempotent.png", io.BytesIO(png_bytes()), "image/png")},
        headers={"X-CSRF-Token": token, "Origin": "http://testserver", "Idempotency-Key": "same-photo"},
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == first.json()["id"]
    assert object_files() == after_first

    quota_before = object_files()
    previous_quota = settings.storage_quota_bytes
    try:
        settings.storage_quota_bytes = 0
        exhausted = client.post(
            "/api/v1/files",
            files={"file": ("quota.txt", io.BytesIO(b"x"), "text/plain")},
            headers={"X-CSRF-Token": _csrf(client), "Origin": "http://testserver"},
        )
        assert exhausted.status_code == 413
        assert object_files() == quota_before
    finally:
        settings.storage_quota_bytes = previous_quota

    saved = storage.save
    failure_before = object_files()

    def fail_thumbnail(data: bytes, subdir: str, suffix: str = "") -> str:
        if subdir == "thumbnails":
            raise OSError("thumbnail write failed")
        return saved(data, subdir, suffix)

    monkeypatch.setattr(storage, "save", fail_thumbnail)
    failed = client.post(
        "/api/v1/photos",
        files={"file": ("broken.png", io.BytesIO(png_bytes()), "image/png")},
        headers={"X-CSRF-Token": _csrf(client), "Origin": "http://testserver"},
    )
    assert failed.status_code == 500
    assert object_files() == failure_before


def test_storage_save_retries_collisions_and_only_cleans_its_own_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "objects"
    adapter = StorageAdapter(root)
    existing = root / "originals" / "taken.bin"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"existing")
    before = existing.stat()

    keys = iter(("taken", "fresh"))
    monkeypatch.setattr(storage_module, "random_id", lambda: next(keys))
    assert adapter.save(b"new", "originals", ".bin") == "fresh.bin"
    assert existing.read_bytes() == b"existing"
    assert existing.stat().st_mtime_ns == before.st_mtime_ns
    assert (root / "originals" / "fresh.bin").read_bytes() == b"new"

    monkeypatch.setattr(storage_module, "random_id", lambda: "taken")
    with pytest.raises(StorageKeyAllocationError, match="无法分配"):
        adapter.save(b"never-overwrite", "originals", ".bin")
    assert existing.read_bytes() == b"existing"
    assert existing.stat().st_mtime_ns == before.st_mtime_ns

    monkeypatch.setattr(storage_module, "random_id", lambda: "write-fail")

    def fail_fdopen(*_args, **_kwargs):
        raise OSError("injected write failure")

    monkeypatch.setattr(storage_module.os, "fdopen", fail_fdopen)
    with pytest.raises(OSError, match="injected"):
        adapter.save(b"partial", "originals", ".bin")
    assert not (root / "originals" / "write-fail.bin").exists()
    assert existing.read_bytes() == b"existing"
    assert existing.stat().st_mtime_ns == before.st_mtime_ns
