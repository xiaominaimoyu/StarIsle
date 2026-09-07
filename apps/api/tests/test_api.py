"""端到端契约测试:鉴权 / 文件 / 照片 / 邮件 / 知识库。

使用临时 DATA_ROOT 与独立密钥,不触碰真实数据。
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import time

import pytest
from fastapi.testclient import TestClient

TEST_PASSWORD = "test-password-12345"

from app.main import app  # noqa: E402

from app.db import get_conn  # noqa: E402
from app.security import sha256_hex  # noqa: E402

client = TestClient(app)


@pytest.fixture(scope="session", autouse=True)
def bootstrap():
    from app.db import init_db

    init_db()
    yield


def _csrf() -> str:
    resp = client.get("/api/v1/auth/csrf")
    assert resp.status_code == 200
    return resp.json()["csrfToken"]


def _login() -> None:
    token = _csrf()
    resp = client.post(
        "/api/v1/auth/login",
        json={"password": TEST_PASSWORD},
        headers={"X-CSRF-Token": token, "Origin": "http://testserver"},
    )
    assert resp.status_code == 200, "login must succeed"
    return token


def test_health():
    resp = client.get("/api/v1/health/live")
    assert resp.status_code == 200
    resp = client.get("/api/v1/health/ready")
    assert resp.status_code == 200
    assert resp.json()["checks"]["db"] is True


def test_unauthorized_returns_401():
    resp = client.get("/api/v1/files")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_REQUIRED"


def test_login_wrong_password():
    token = _csrf()
    resp = client.post(
        "/api/v1/auth/login",
        json={"password": "wrong-password"},
        headers={"X-CSRF-Token": token, "Origin": "http://testserver"},
    )
    assert resp.status_code == 401
    assert "检查密码" in resp.json()["error"]["message"]


def test_login_missing_csrf():
    resp = client.post("/api/v1/auth/login", json={"password": TEST_PASSWORD})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "CSRF_REQUIRED"


def test_login_flow_and_me():
    _login()
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


def test_file_upload_download_delete():
    _login()
    token = _csrf()
    resp = client.post(
        "/api/v1/files",
        files={"file": ("hello.txt", io.BytesIO(b"hello star-isle"), "text/plain")},
        headers={"X-CSRF-Token": token, "Origin": "http://testserver", "Idempotency-Key": "key-1"},
    )
    assert resp.status_code == 201, "file upload must succeed"
    file_id = resp.json()["id"]
    with get_conn() as conn:
        assert conn.execute("SELECT storage_class FROM asset WHERE id=?", (file_id,)).fetchone()[0] == "file"

    # 幂等:同一 key 返回同一资源
    resp2 = client.post(
        "/api/v1/files",
        files={"file": ("hello.txt", io.BytesIO(b"hello star-isle"), "text/plain")},
        headers={"X-CSRF-Token": token, "Origin": "http://testserver", "Idempotency-Key": "key-1"},
    )
    assert resp2.status_code == 201
    assert resp2.json()["id"] == file_id

    # 列表与下载
    listing = client.get("/api/v1/files")
    assert listing.status_code == 200
    assert any(f["id"] == file_id for f in listing.json()["items"])
    dl = client.get(f"/api/v1/files/{file_id}/download")
    assert dl.status_code == 200
    assert dl.content == b"hello star-isle"

    # 伪装 MIME 负例:声明 png 但内容不是图片
    bad = client.post(
        "/api/v1/files",
        files={"file": ("fake.png", io.BytesIO(b"not-a-png"), "image/png")},
        headers={"X-CSRF-Token": _csrf(), "Origin": "http://testserver"},
    )
    assert bad.status_code in (413, 415)

    # 删除
    resp3 = client.delete(
        f"/api/v1/files/{file_id}",
        headers={"X-CSRF-Token": token, "Origin": "http://testserver"},
    )
    assert resp3.status_code == 204
    assert client.get(f"/api/v1/files/{file_id}/download").status_code == 404


def test_file_list_cursor_pagination():
    """files 游标分页:全量遍历不漏不重(nextCursor 须指向当前页末行)。"""
    from urllib.parse import quote

    _login()
    token = _csrf()
    uploaded = []
    for i in range(3):
        resp = client.post(
            "/api/v1/files",
            files={"file": (f"page-{i}.txt", io.BytesIO(b"pagination"), "text/plain")},
            headers={"X-CSRF-Token": token, "Origin": "http://testserver"},
        )
        assert resp.status_code == 201
        uploaded.append(resp.json()["id"])
    with get_conn() as conn:
        for i, fid in enumerate(uploaded):
            conn.execute("UPDATE asset SET created_at=? WHERE id=?", (f"2026-02-0{i + 1}T10:00:00+00:00", fid))
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM asset WHERE status='active' AND storage_class='file'"
        ).fetchone()["c"]

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        qs = "/api/v1/files?limit=2" + (f"&cursor={quote(cursor, safe='')}" if cursor else "")
        resp = client.get(qs)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) <= 2
        seen.extend(f["id"] for f in body["items"])
        pages += 1
        assert pages <= total + 1  # 防御死循环
        cursor = body["nextCursor"]
        if not cursor:
            break
    assert len(seen) == total
    assert len(set(seen)) == total
    for fid in uploaded:
        assert fid in seen


def test_photo_upload_and_thumbnail():
    _login()
    token = _csrf()
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (200, 40, 60)).save(buf, format="PNG")
    png = buf.getvalue()
    resp = client.post(
        "/api/v1/photos",
        files={"file": ("pixel.png", io.BytesIO(png), "image/png")},
        headers={"X-CSRF-Token": token, "Origin": "http://testserver"},
    )
    assert resp.status_code == 201, "photo upload must succeed"
    photo_id = resp.json()["id"]
    with get_conn() as conn:
        classes = {
            row["storage_class"]
            for row in conn.execute(
                "SELECT a.storage_class FROM photo p JOIN asset a ON a.id IN (p.asset_id, p.thumbnail_asset_id) WHERE p.id=?",
                (photo_id,),
            )
        }
    assert classes == {"photo-original", "photo-thumbnail"}
    thumb = client.get(f"/api/v1/photos/{photo_id}/thumbnail")
    assert thumb.status_code == 200
    assert thumb.content[:4] == b"RIFF"  # webp

    listing = client.get("/api/v1/photos")
    assert any(p["id"] == photo_id for p in listing.json()["items"])
    assert "GPS" not in json.dumps(listing.json())  # GPS 不展示

    with get_conn() as conn:
        assets = conn.execute(
            "SELECT a.id, a.object_key, a.storage_class FROM photo p "
            "JOIN asset a ON a.id IN (p.asset_id, p.thumbnail_asset_id) WHERE p.id=?",
            (photo_id,),
        ).fetchall()
    deleted = client.delete(
        f"/api/v1/photos/{photo_id}",
        headers={"X-CSRF-Token": _csrf(), "Origin": "http://testserver"},
    )
    assert deleted.status_code == 204
    with get_conn() as conn:
        pending = conn.execute(
            "SELECT status, deleted_at, gc_next_run_at FROM asset WHERE id IN (?, ?) ORDER BY id",
            tuple(row["id"] for row in assets),
        ).fetchall()
    assert all(row["status"] == "deleted" and row["deleted_at"] and row["gc_next_run_at"] for row in pending)

    from app.services import asset_gc

    asset_gc.run_asset_gc()
    with get_conn() as conn:
        purged = conn.execute(
            "SELECT object_purged_at, gc_next_run_at, gc_error_code FROM asset WHERE id IN (?, ?) ORDER BY id",
            tuple(row["id"] for row in assets),
        ).fetchall()
    assert all(row["object_purged_at"] and row["gc_next_run_at"] is None and row["gc_error_code"] is None for row in purged)
    for row in assets:
        subdir = "originals" if row["storage_class"] == "photo-original" else "thumbnails"
        assert not asset_gc.storage.exists(subdir, row["object_key"])


def test_photo_upload_normalizes_exif_without_guessing_timezone():
    _login()
    token = _csrf()
    from PIL import ExifTags, Image

    image = Image.new("RGB", (4, 4), (40, 80, 120))
    exif = Image.Exif()
    exif[ExifTags.IFD.Exif] = {
        0x9003: "2026:09:05 10:02:03",  # DateTimeOriginal
        0x9011: "+08:00",  # OffsetTimeOriginal is intentionally not inferred into capture_at.
        0x9291: "456",  # SubSecTimeOriginal is outside the seconds-level contract.
    }
    buf = io.BytesIO()
    image.save(buf, format="JPEG", exif=exif)

    response = client.post(
        "/api/v1/photos",
        files={"file": ("with-exif.jpg", io.BytesIO(buf.getvalue()), "image/jpeg")},
        headers={"X-CSRF-Token": token, "Origin": "http://testserver"},
    )
    assert response.status_code == 201
    photo_id = response.json()["id"]

    with get_conn() as conn:
        stored = conn.execute(
            "SELECT capture_at FROM photo WHERE id=?",
            (photo_id,),
        ).fetchone()
    assert stored is not None
    assert stored["capture_at"] == "2026-09-05T10:02:03"

    listing = client.get("/api/v1/photos", params={"limit": 200})
    assert listing.status_code == 200
    summary = next(item for item in listing.json()["items"] if item["id"] == photo_id)
    assert summary["captureAt"] == "2026-09-05T10:02:03"


def test_photo_exif_uses_top_level_fallback_when_nested_ifd_is_unreadable(monkeypatch):
    from PIL import Image
    from app.routers import files as files_router

    class BrokenNestedExif:
        def get_ifd(self, _ifd):
            raise OSError("synthetic malformed Exif IFD")

        def get(self, key):
            return "2026:09:05 11:12:13" if key == 0x9003 else None

    class FakeImage:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> bool:
            return False

        def getexif(self):
            return BrokenNestedExif()

    monkeypatch.setattr(Image, "open", lambda *_args, **_kwargs: FakeImage())
    assert files_router._read_exif_capture(b"synthetic image bytes") == "2026-09-05T11:12:13"


def test_photo_upload_applies_exif_orientation_to_dimensions_and_thumbnail():
    _login()
    token = _csrf()
    from PIL import Image

    image = Image.new("RGB", (8, 12), (40, 80, 120))
    exif = Image.Exif()
    exif[0x0112] = 6  # 旋转 90 度；视觉尺寸应从 8x12 变为 12x8。
    buf = io.BytesIO()
    image.save(buf, format="JPEG", exif=exif)

    response = client.post(
        "/api/v1/photos",
        files={"file": ("oriented.jpg", io.BytesIO(buf.getvalue()), "image/jpeg")},
        headers={"X-CSRF-Token": token, "Origin": "http://testserver"},
    )
    assert response.status_code == 201
    photo_id = response.json()["id"]

    listing = client.get("/api/v1/photos", params={"limit": 200})
    summary = next(item for item in listing.json()["items"] if item["id"] == photo_id)
    assert (summary["width"], summary["height"]) == (12, 8)

    thumbnail_response = client.get(f"/api/v1/photos/{photo_id}/thumbnail")
    assert thumbnail_response.status_code == 200
    with Image.open(io.BytesIO(thumbnail_response.content)) as thumbnail:
        assert thumbnail.size == (12, 8)
        assert thumbnail.getexif().get(0x0112) is None


def test_photo_list_cursor_pagination():
    """photos 游标分页:全量遍历不漏不重,排序键与 nextCursor 契约一致。"""
    from urllib.parse import quote

    _login()
    token = _csrf()
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (10, 20, 30)).save(buf, format="PNG")
    png = buf.getvalue()
    uploaded = []
    for i in range(3):
        resp = client.post(
            "/api/v1/photos",
            files={"file": (f"page-{i}.png", io.BytesIO(png), "image/png")},
            headers={"X-CSRF-Token": token, "Origin": "http://testserver"},
        )
        assert resp.status_code == 201
        uploaded.append(resp.json()["id"])
    # 显式拉开排序键,避免同秒创建时间并列影响断言
    with get_conn() as conn:
        for i, pid in enumerate(uploaded):
            conn.execute("UPDATE photo SET capture_at=? WHERE id=?", (f"2026:01:0{i + 1} 10:00:00", pid))
        total = conn.execute("SELECT COUNT(*) AS c FROM photo").fetchone()["c"]

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        qs = "/api/v1/photos?limit=2" + (f"&cursor={quote(cursor, safe='')}" if cursor else "")
        resp = client.get(qs)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) <= 2
        seen.extend(p["id"] for p in body["items"])
        pages += 1
        assert pages <= total + 1  # 防御死循环
        cursor = body["nextCursor"]
        if not cursor:
            break
    assert len(seen) == total
    assert len(set(seen)) == total
    for pid in uploaded:
        assert pid in seen
    if total > 2:
        first = client.get("/api/v1/photos?limit=2")
        assert first.json()["nextCursor"] is not None


def test_mail_receive_hmac_chain():
    _login()
    mime = (
        "From: sender@example.com\r\n"
        "To: me@example.com\r\n"
        "Subject: 测试邮件\r\n"
        "Message-ID: <test-123@example.com>\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\n"
        "你好,星屿。"
    ).encode("utf-8")
    body_sha = sha256_hex(mime)
    ts = str(int(time.time()))
    nonce = "nonce-abc-123"
    sig = hmac.new(
        b"test-mail-hmac-key",
        f"{ts}.{nonce}.{body_sha}".encode(),
        hashlib.sha256,
    ).hexdigest()

    resp = client.post(
        "/api/v1/mail/receive",
        content=mime,
        headers={
            "Content-Type": "message/rfc822",
            "x-mail-timestamp": ts,
            "x-mail-nonce": nonce,
            "x-mail-signature": sig,
        },
    )
    assert resp.status_code == 200, "mail receipt must succeed"
    mail_id = resp.json()["id"]

    # 重放 nonce 拒绝
    resp2 = client.post(
        "/api/v1/mail/receive",
        content=mime,
        headers={
            "Content-Type": "message/rfc822",
            "x-mail-timestamp": ts,
            "x-mail-nonce": nonce,
            "x-mail-signature": sig,
        },
    )
    assert resp2.status_code == 401

    # 错误签名拒绝
    resp3 = client.post(
        "/api/v1/mail/receive",
        content=mime,
        headers={
            "Content-Type": "message/rfc822",
            "x-mail-timestamp": ts,
            "x-mail-nonce": "nonce-other",
            "x-mail-signature": "deadbeef",
        },
    )
    assert resp3.status_code == 401

    # 收件箱可见
    listing = client.get("/api/v1/mail")
    assert any(m["id"] == mail_id for m in listing.json()["items"])
    detail = client.get(f"/api/v1/mail/{mail_id}")
    assert "你好,星屿" in detail.json()["textBody"]

    # 已读
    r = client.post(
        f"/api/v1/mail/{mail_id}/read",
        json={"read": True},
        headers={"X-CSRF-Token": _csrf(), "Origin": "http://testserver"},
    )
    assert r.status_code == 200


def test_mail_attachment_uses_mail_attachment_storage_class():
    _login()
    boundary = "starisle-attachment-boundary"
    mime = (
        "From: sender@example.com\r\n"
        "To: me@example.com\r\n"
        "Subject: attachment class\r\n"
        "Message-ID: <attachment-class@example.com>\r\n"
        f'Content-Type: multipart/mixed; boundary="{boundary}"\r\n\r\n'
        f"--{boundary}\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\n"
        "body\r\n"
        f"--{boundary}\r\n"
        "Content-Type: text/plain\r\n"
        "Content-Disposition: attachment; filename=note.txt\r\n"
        "Content-Transfer-Encoding: base64\r\n\r\n"
        "YXR0YWNobWVudCBieXRlcw==\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")
    ts = str(int(time.time()))
    nonce = f"nonce-attachment-{time.time_ns()}"
    sig = hmac.new(
        b"test-mail-hmac-key",
        f"{ts}.{nonce}.{sha256_hex(mime)}".encode(),
        hashlib.sha256,
    ).hexdigest()
    response = client.post(
        "/api/v1/mail/receive",
        content=mime,
        headers={
            "Content-Type": "message/rfc822",
            "x-mail-timestamp": ts,
            "x-mail-nonce": nonce,
            "x-mail-signature": sig,
        },
    )
    assert response.status_code == 200, "mail attachment receipt must succeed"
    with get_conn() as conn:
        classes = {
            row["storage_class"]
            for row in conn.execute(
                "SELECT a.storage_class FROM mail_attachment ma JOIN asset a ON a.id=ma.asset_id "
                "WHERE ma.message_id=?",
                (response.json()["id"],),
            )
        }
    assert classes == {"mail-attachment"}


def test_knowledge_upload_and_job():
    _login()
    token = _csrf()
    md = "# 星屿手册\n\n星屿是个人全栈门户。\n\n它分为公开面与私有面。"
    resp = client.post(
        "/api/v1/knowledge/documents",
        files={"file": ("manual.md", io.BytesIO(md.encode()), "text/markdown")},
        headers={"X-CSRF-Token": token, "Origin": "http://testserver", "Idempotency-Key": "doc-1"},
    )
    assert resp.status_code == 202, "knowledge upload must be accepted"
    doc_id = resp.json()["documentId"]
    job_id = resp.json()["jobId"]
    with get_conn() as conn:
        assert conn.execute(
            "SELECT a.storage_class FROM knowledge_document d JOIN asset a ON a.id=d.asset_id WHERE d.id=?",
            (doc_id,),
        ).fetchone()[0] == "knowledge"

    listing = client.get("/api/v1/knowledge/documents")
    assert any(d["id"] == doc_id for d in listing.json()["items"])

    # 非支持类型负例
    bad = client.post(
        "/api/v1/knowledge/documents",
        files={"file": ("evil.docx", io.BytesIO(b"x"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        headers={"X-CSRF-Token": token, "Origin": "http://testserver"},
    )
    assert bad.status_code == 415

    # job 存在(worker 未运行时为 queued)
    job = client.get(f"/api/v1/jobs/{job_id}")
    assert job.status_code == 200
    assert job.json()["status"] in ("queued", "running", "succeeded")


def test_rag_query_stream_requires_csrf():
    _login()
    resp = client.post("/api/v1/rag/query", json={"query": "你好"})
    assert resp.status_code == 403


def test_files_api_hides_other_classes_and_rejects_shared_file_delete():
    _login()
    now = "2026-08-30T12:00:00+00:00"
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO asset (id, original_name, mime_type, size, object_key, sha256, storage_class, status, created_at) "
            "VALUES ('shared-file-asset', 'shared.txt', 'text/plain', 1, 'shared.bin', ?, 'file', 'active', ?)",
            ("a" * 64, now),
        )
        conn.execute(
            "INSERT INTO photo (id, asset_id, thumbnail_asset_id, created_at) "
            "VALUES ('shared-file-photo', 'shared-file-asset', 'shared-file-asset', ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO asset (id, original_name, mime_type, size, object_key, sha256, storage_class, status, created_at) "
            "VALUES ('hidden-photo-asset', 'hidden.jpg', 'image/jpeg', 1, 'hidden.bin', ?, 'photo-original', 'active', ?)",
            ("b" * 64, now),
        )

    token = _csrf()
    listing = client.get("/api/v1/files")
    assert listing.status_code == 200
    assert "hidden-photo-asset" not in {item["id"] for item in listing.json()["items"]}
    assert client.get("/api/v1/files/hidden-photo-asset/download").status_code == 404
    response = client.delete(
        "/api/v1/files/shared-file-asset",
        headers={"X-CSRF-Token": token, "Origin": "http://testserver"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ASSET_IN_USE"
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status, deleted_at, object_purged_at, gc_next_run_at FROM asset WHERE id='shared-file-asset'"
        ).fetchone()
    assert tuple(row) == ("active", None, None, None)
