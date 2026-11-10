"""回归:登录审计、刷新写锁、Origin、上传错误码、展示名、响应头与配置下限。"""

from __future__ import annotations

import io
import threading
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

from app.config import Settings
from app.db import get_conn, init_db
from app.deps import origin_matches_request
from app.main import app, apply_security_headers, expose_api_docs
from app.security import REFRESH_COOKIE, iso, now_utc
from app.services.mime import safe_display_name, upload_rejection

TEST_PASSWORD = "test-password-12345"


@pytest.fixture(scope="module", autouse=True)
def _ready_database():
    init_db()
    yield


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
    assert response.status_code == 200, response.text
    return token


def _png() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), (12, 24, 36)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_safe_display_name_strips_paths_and_controls():
    assert safe_display_name(None, "photo.jpg") == "photo.jpg"
    assert safe_display_name("../a\\b\r\n.txt", "download") == "_a_b.txt"
    assert safe_display_name("  ", "document") == "document"
    assert len(safe_display_name("n" * 400, "document")) == 255


def test_upload_rejection_distinguishes_type_and_size():
    assert upload_rejection("文件内容与声明类型不一致,已拒绝。") == (415, "UNSUPPORTED_MEDIA_TYPE")
    assert upload_rejection("文件超过大小上限,请压缩后重试。") == (413, "PAYLOAD_TOO_LARGE")


def test_origin_match_is_case_insensitive_and_scheme_aware():
    assert origin_matches_request("http://TestServer", "testserver", None)
    assert not origin_matches_request("http://testserver/phish", "testserver", None)
    assert not origin_matches_request("http://user@testserver", "testserver", None)
    assert not origin_matches_request("http://testserver", "testserver", "https")
    assert origin_matches_request("https://testserver", "testserver", "https, http")
    assert not origin_matches_request("https://testserver", "testserver", "bogus")


def test_login_keeps_historical_failures_and_persists_rate_limit_audit():
    client = TestClient(app)
    actor = "ip:testclient"
    old_id = "audit-old-login-fail"
    recent_ids = [f"audit-recent-login-fail-{index}" for index in range(10)]
    old_at = iso(now_utc() - timedelta(minutes=10))
    recent_at = iso(now_utc() - timedelta(minutes=1))
    try:
        with get_conn() as conn:
            conn.execute(
                "DELETE FROM audit_event WHERE actor=? AND action IN ('login_fail', 'login_limited')",
                (actor,),
            )
            conn.execute(
                "INSERT INTO audit_event (id, actor, action, result, created_at) VALUES (?, ?, 'login_fail', '401', ?)",
                (old_id, actor, old_at),
            )
            conn.executemany(
                "INSERT INTO audit_event (id, actor, action, result, created_at) VALUES (?, ?, 'login_fail', '401', ?)",
                [(item_id, actor, recent_at) for item_id in recent_ids],
            )
        limited = client.post(
            "/api/v1/auth/login",
            json={"password": "wrong-password"},
            headers={"X-CSRF-Token": _csrf(client), "Origin": "http://testserver"},
        )
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "RATE_LIMITED"
        with get_conn() as conn:
            assert conn.execute("SELECT 1 FROM audit_event WHERE id=?", (old_id,)).fetchone() is not None
            assert conn.execute(
                "SELECT 1 FROM audit_event WHERE action='login_limited' AND actor=?",
                (actor,),
            ).fetchone() is not None

        with get_conn() as conn:
            conn.execute("DELETE FROM audit_event WHERE id=?", (recent_ids[0],))
        kept = client.post(
            "/api/v1/auth/login",
            json={"password": "still-wrong"},
            headers={"X-CSRF-Token": _csrf(client), "Origin": "http://TestServer"},
        )
        assert kept.status_code == 401
        with get_conn() as conn:
            assert conn.execute("SELECT 1 FROM audit_event WHERE id=?", (old_id,)).fetchone() is not None
    finally:
        with get_conn() as conn:
            conn.execute(
                "DELETE FROM audit_event WHERE actor=? AND action IN ('login_fail', 'login_limited')",
                (actor,),
            )


def test_case_variant_origin_can_login_and_bad_origin_cannot():
    client = TestClient(app)
    token = _csrf(client)
    rejected = client.post(
        "/api/v1/auth/login",
        json={"password": TEST_PASSWORD},
        headers={
            "X-CSRF-Token": token,
            "Origin": "http://testserver/phish",
            "X-Forwarded-Proto": "https",
        },
    )
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "CSRF_REQUIRED"

    allowed = client.post(
        "/api/v1/auth/login",
        json={"password": TEST_PASSWORD},
        headers={"X-CSRF-Token": _csrf(client), "Origin": "http://TestServer"},
    )
    assert allowed.status_code == 200


def test_concurrent_refresh_keeps_a_single_active_session():
    client = TestClient(app)
    with get_conn() as conn:
        before = conn.execute(
            "SELECT COUNT(*) AS c FROM session WHERE revoked_at IS NULL AND expires_at > ?",
            (iso(now_utc()),),
        ).fetchone()["c"]
    _login(client)
    refresh_cookie = client.cookies.get(REFRESH_COOKIE)
    assert refresh_cookie
    csrf = _csrf(client)
    cookie_header = f"{REFRESH_COOKIE}={refresh_cookie}"
    barrier = threading.Barrier(2)
    statuses: list[int] = []
    lock = threading.Lock()

    def refresh_once() -> None:
        local = TestClient(app)
        barrier.wait()
        response = local.post(
            "/api/v1/auth/refresh",
            headers={
                "Cookie": cookie_header,
                "X-CSRF-Token": csrf,
                "Origin": "http://testserver",
            },
        )
        with lock:
            statuses.append(response.status_code)

    threads = [threading.Thread(target=refresh_once) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert statuses.count(200) == 1
    assert all(status in {200, 401, 403} for status in statuses)
    with get_conn() as conn:
        after = conn.execute(
            "SELECT COUNT(*) AS c FROM session WHERE revoked_at IS NULL AND expires_at > ?",
            (iso(now_utc()),),
        ).fetchone()["c"]
    assert after == before + 1


def test_photo_type_mismatch_is_unsupported_media_and_missing_name_is_safe():
    client = TestClient(app)
    token = _login(client)
    headers = {"X-CSRF-Token": token, "Origin": "http://testserver"}
    mismatch = client.post(
        "/api/v1/photos",
        files={"file": ("fake.png", io.BytesIO(b"not-a-png"), "image/png")},
        headers=headers,
    )
    assert mismatch.status_code == 415
    assert mismatch.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"

    uploaded = client.post(
        "/api/v1/photos",
        files={"file": ("  ..", io.BytesIO(_png()), "image/png")},
        headers=headers,
    )
    assert uploaded.status_code == 201, uploaded.text
    with get_conn() as conn:
        names = [row["original_name"] for row in conn.execute("SELECT original_name FROM asset")]
    assert "thumb_None" not in names
    assert "photo.jpg" in names
    assert "thumb_photo.jpg" in names


def test_file_download_sanitizes_name_and_sets_nosniff():
    client = TestClient(app)
    token = _login(client)
    uploaded = client.post(
        "/api/v1/files",
        files={"file": ("../a\\b.txt", io.BytesIO(b"hello star-isle"), "text/plain")},
        headers={"X-CSRF-Token": token, "Origin": "http://testserver"},
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["name"] == "_a_b.txt"
    file_id = uploaded.json()["id"]
    downloaded = client.get(f"/api/v1/files/{file_id}/download")
    assert downloaded.status_code == 200
    disposition = downloaded.headers["content-disposition"]
    assert "\r" not in disposition and "\n" not in disposition
    assert "filename*=UTF-8''_a_b.txt" in disposition
    assert downloaded.headers["x-content-type-options"] == "nosniff"


def test_knowledge_title_uses_safe_display_name():
    client = TestClient(app)
    token = _login(client)
    response = client.post(
        "/api/v1/knowledge/documents",
        files={"file": ("../notes.md", io.BytesIO(b"# hello\n"), "text/markdown")},
        headers={"X-CSRF-Token": token, "Origin": "http://testserver"},
    )
    assert response.status_code == 202, response.text
    with get_conn() as conn:
        title = conn.execute(
            "SELECT title FROM knowledge_document WHERE id=?",
            (response.json()["documentId"],),
        ).fetchone()["title"]
    assert title == "_notes.md"


def test_rag_query_reports_empty_and_overlong_separately():
    client = TestClient(app)
    token = _login(client)
    headers = {"X-CSRF-Token": token, "Origin": "http://testserver"}
    empty = client.post("/api/v1/rag/query", json={"query": "   "}, headers=headers)
    assert empty.status_code == 422
    assert empty.json()["error"]["message"] == "问题不能为空。"
    overlong = client.post("/api/v1/rag/query", json={"query": "问" * 8001}, headers=headers)
    assert overlong.status_code == 422
    assert overlong.json()["error"]["code"] == "UNPROCESSABLE"
    assert overlong.json()["error"]["message"] == "问题过长，请缩短后重试。"


def test_private_api_sets_baseline_security_headers_without_clobbering_stream_cache():
    client = TestClient(app)
    live = client.get("/api/v1/health/live")
    assert live.status_code == 200
    assert live.headers["x-content-type-options"] == "nosniff"
    assert live.headers["x-frame-options"] == "DENY"
    assert live.headers["referrer-policy"] == "no-referrer"
    assert live.headers["cache-control"] == "no-store"
    preserved = JSONResponse({"ok": True}, headers={"Cache-Control": "no-cache"})
    apply_security_headers(preserved)
    assert preserved.headers["cache-control"] == "no-cache"
    assert preserved.headers["x-content-type-options"] == "nosniff"


def test_logout_clearance_keeps_httponly():
    client = TestClient(app)
    token = _login(client)
    response = client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": token, "Origin": "http://testserver"},
    )
    assert response.status_code == 204
    cleared = response.headers.get_list("set-cookie")
    assert len(cleared) >= 2
    assert all("httponly" in value.lower() for value in cleared)


def test_positive_settings_reject_zero_and_blank(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FILE_MAX_BYTES", "-1")
    with pytest.raises(ValueError, match="FILE_MAX_BYTES:positive-integer"):
        Settings()
    monkeypatch.setenv("FILE_MAX_BYTES", "1024")
    monkeypatch.setenv("AUTH_ACCESS_TTL_MINUTES", "0")
    with pytest.raises(ValueError, match="AUTH_ACCESS_TTL_MINUTES:positive-integer"):
        Settings()
    monkeypatch.setenv("AUTH_ACCESS_TTL_MINUTES", "15")
    monkeypatch.setenv("EMBEDDING_DIM", "nope")
    with pytest.raises(ValueError, match="EMBEDDING_DIM:positive-integer"):
        Settings()


def test_production_hides_interactive_docs():
    assert expose_api_docs("development") == ("/api/docs", "/api/openapi.json", "/redoc")
    assert expose_api_docs("production") == (None, None, None)
    assert app.docs_url == "/api/docs"
