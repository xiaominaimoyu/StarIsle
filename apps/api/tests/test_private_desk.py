"""私人书桌：鉴权、上传、缩略图、备份保留和只绑定回环地址。"""

from __future__ import annotations

import io
import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from app.daily_backup import prune_snapshots, run_daily_backup, snapshot_database
from app.deps import require_auth
from app.main import app
from app.owner_setup import main as setup_owner
from app.private_runtime import (
    DEFAULT_BIND_HOST,
    DEFAULT_BIND_PORT,
    WINDOWS_BACKUP_ROOT,
    WINDOWS_DATA_ROOT,
    PrivateRuntimeError,
    repository_root,
    resolve_bind_host,
    resolve_port,
)
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from PIL import Image

from tests._isolation import TEST_PASSWORD

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]
PUBLIC_API = {
    ("GET", "/api/v1/auth/csrf"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/refresh"),
    ("POST", "/api/v1/auth/logout"),
    ("POST", "/api/v1/mail/receive"),
    ("GET", "/api/v1/health/live"),
    ("GET", "/api/v1/health/ready"),
}


def _png(width: int = 8, height: int = 8) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (180, 70, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


def _csrf(client: TestClient) -> str:
    response = client.get("/api/v1/auth/csrf")
    assert response.status_code == 200
    return response.json()["csrfToken"]


def _login(client: TestClient) -> None:
    token = _csrf(client)
    response = client.post(
        "/api/v1/auth/login",
        json={"password": TEST_PASSWORD},
        headers={"X-CSRF-Token": token, "Origin": "http://testserver"},
    )
    assert response.status_code == 200


@pytest.fixture(autouse=True)
def _initialized_database():
    from app.db import init_db

    init_db()


def _iter_api_routes():
    for route in app.routes:
        if type(route).__name__ == "_IncludedRouter":
            prefix = route.include_context.prefix or ""
            children = route.original_router.routes
        elif isinstance(route, APIRoute):
            prefix = ""
            children = [route]
        else:
            continue
        for child in children:
            if isinstance(child, APIRoute):
                yield prefix + child.path, child


def _uses_require_auth(route: APIRoute) -> bool:
    pending = [route.dependant]
    while pending:
        dependant = pending.pop()
        if dependant.call is require_auth:
            return True
        pending.extend(dependant.dependencies)
    return False


def test_bind_defaults_to_loopback_and_refuses_other_addresses():
    assert resolve_bind_host(None) == DEFAULT_BIND_HOST == "127.0.0.1"
    assert resolve_bind_host("  ") == "127.0.0.1"
    assert resolve_port(None) == DEFAULT_BIND_PORT == 8787
    assert WINDOWS_DATA_ROOT == Path(r"D:\StarIsleData")
    assert WINDOWS_BACKUP_ROOT == Path(r"D:\ASUS\OneDrive\StarIsleBackup")
    for value in ("0.0.0.0", "::", "::1", "localhost", "192.168.1.20", "10.0.0.4"):
        with pytest.raises(PrivateRuntimeError) as caught:
            resolve_bind_host(value)
        assert caught.value.code == "BIND_HOST_REFUSED"
    for value in ("8000", "8001", "0", "70000", "desk"):
        with pytest.raises(PrivateRuntimeError):
            resolve_port(value)


def test_private_api_routes_require_auth():
    missing: list[str] = []
    seen: list[str] = []
    for path, route in _iter_api_routes():
        if not path.startswith("/api/v1"):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            seen.append(f"{method} {path}")
            if (method, path) in PUBLIC_API:
                continue
            if not _uses_require_auth(route):
                missing.append(f"{method} {path}")
    assert missing == []
    assert "GET /api/v1/photos" in seen
    assert "GET /api/v1/photos/{photo_id}/original" in seen
    assert not any("register" in item for item in seen)


def test_anonymous_private_routes_reject_and_pages_do_not_register():
    client = TestClient(app)
    for path in (
        "/api/v1/photos",
        "/api/v1/photos/missing/thumbnail",
        "/api/v1/photos/missing/original",
        "/api/v1/files",
        "/api/v1/auth/me",
    ):
        response = client.get(path)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTH_REQUIRED"
    upload = client.post(
        "/api/v1/photos",
        files={"file": ("pixel.png", io.BytesIO(_png()), "image/png")},
        headers={"Origin": "http://testserver"},
    )
    assert upload.status_code == 401
    assert upload.json()["error"]["code"] == "AUTH_REQUIRED"
    login = client.get("/private/login")
    assert login.status_code == 200
    assert "进入私人书桌" in login.text
    assert "/api/v1/auth/register" not in login.text
    assert "<form" in login.text
    album = client.get("/private/album", follow_redirects=False)
    assert album.status_code == 302
    assert album.headers["location"] == "/private/login?next=/private/album"
    assert client.get("/private/assets/missing.js").status_code == 404


def test_loopback_guard_blocks_non_loopback_clients(monkeypatch):
    monkeypatch.setenv("STAR_ISLE_PRIVATE_DESK_LOOPBACK_ONLY", "1")
    client = TestClient(app)
    denied = client.get("/api/v1/health/live")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "BIND_REFUSED"
    monkeypatch.delenv("STAR_ISLE_PRIVATE_DESK_LOOPBACK_ONLY", raising=False)
    assert client.get("/api/v1/health/live").status_code == 200


def test_photo_upload_validation_thumbnail_and_original(monkeypatch):
    client = TestClient(app)
    _login(client)
    token = _csrf(client)
    bad_type = client.post(
        "/api/v1/photos",
        files={"file": ("notes.png", io.BytesIO(b"not-an-image"), "image/png")},
        headers={"X-CSRF-Token": token, "Origin": "http://testserver"},
    )
    assert bad_type.status_code == 415
    assert bad_type.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"

    monkeypatch.setattr("app.routers.files.settings.image_max_bytes", 32)
    too_big = client.post(
        "/api/v1/photos",
        files={"file": ("big.png", io.BytesIO(_png()), "image/png")},
        headers={"X-CSRF-Token": _csrf(client), "Origin": "http://testserver"},
    )
    assert too_big.status_code == 413
    monkeypatch.undo()

    uploaded = client.post(
        "/api/v1/photos",
        files={"file": ("..\\..\\secret.png", io.BytesIO(_png(600, 40)), "image/png")},
        headers={"X-CSRF-Token": _csrf(client), "Origin": "http://testserver"},
    )
    assert uploaded.status_code == 201
    photo_id = uploaded.json()["id"]
    from app.db import get_conn

    with get_conn() as conn:
        name = conn.execute(
            "SELECT a.original_name FROM photo p JOIN asset a ON a.id=p.asset_id WHERE p.id=?",
            (photo_id,),
        ).fetchone()["original_name"]
    assert "/" not in name and "\\" not in name and not name.startswith(".")
    thumbnail = client.get(f"/api/v1/photos/{photo_id}/thumbnail")
    assert thumbnail.status_code == 200
    assert thumbnail.content[:4] == b"RIFF"
    with Image.open(io.BytesIO(thumbnail.content)) as image:
        assert max(image.size) <= 480
    original = client.get(f"/api/v1/photos/{photo_id}/original")
    assert original.status_code == 200
    assert original.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert "secret" in original.headers["content-disposition"]
    stranger = TestClient(app)
    assert stranger.get(f"/api/v1/photos/{photo_id}/original").status_code == 401
    page = client.get("/private/album")
    assert page.status_code == 200
    assert "确认删除" in page.text
    assert "来信" in page.text


def test_public_web_tree_does_not_reference_the_private_desk():
    root = REPO_ROOT / "apps" / "web"
    needles = ("/private/album", "private_desk", "STAR_ISLE_BACKUP_ROOT", "StarIsleData")
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".astro", ".ts", ".tsx", ".js", ".mjs", ".css", ".html", ".json"}:
            continue
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            assert needle not in text, path.name


def test_snapshot_uses_backup_api_and_keeps_seven(monkeypatch, tmp_path: Path):
    def refuse_copy(*_args, **_kwargs):
        raise AssertionError("file copy")

    monkeypatch.setattr(shutil, "copyfile", refuse_copy)
    monkeypatch.setattr(shutil, "copy", refuse_copy)
    monkeypatch.setattr(shutil, "copy2", refuse_copy)
    data = tmp_path / "data"
    live = data / "db" / "star-isle.db"
    live.parent.mkdir(parents=True)
    with closing(sqlite3.connect(live)) as holder:
        holder.execute("CREATE TABLE marker (label TEXT)")
        holder.execute("INSERT INTO marker VALUES ('kept')")
        holder.commit()
        backup = tmp_path / "backup"
        snapshot = snapshot_database(live, backup)
        holder.execute("INSERT INTO marker VALUES ('later')")
        holder.commit()
        with closing(sqlite3.connect(snapshot)) as copied:
            labels = [row[0] for row in copied.execute("SELECT label FROM marker")]
        assert labels == ["kept"]
        assert snapshot.resolve() != live.resolve()
        with closing(sqlite3.connect(snapshot)) as check_conn:
            check = check_conn.execute("PRAGMA integrity_check").fetchone()
        assert check[0] == "ok"

    folder = backup / "db-snapshots"
    for index in range(1, 10):
        (folder / f"star-isle-202601{index:02d}T000000Z-01.db").write_bytes(b"x")
    (folder / "star-isle-19990101T000000Z-01.db.partial").write_bytes(b"partial")
    prune_snapshots(backup, keep=7)
    names = sorted(path.name for path in folder.glob("star-isle-*.db"))
    assert len(names) == 7
    assert "star-isle-20260101T000000Z-01.db" not in names
    assert "star-isle-20260109T000000Z-01.db" in names
    assert (folder / "star-isle-19990101T000000Z-01.db.partial").is_file()

    with pytest.raises(PrivateRuntimeError) as caught:
        snapshot_database(backup / "db-snapshots" / names[-1], backup)
    assert caught.value.code == "BACKUP_DB_INSIDE_BACKUP_ROOT"


def test_daily_backup_retention_and_incremental_photos(tmp_path: Path):
    data = tmp_path / "data"
    backup = tmp_path / "backup"
    live = data / "db" / "star-isle.db"
    live.parent.mkdir(parents=True)
    with closing(sqlite3.connect(live)) as connection:
        connection.execute("CREATE TABLE asset (storage_class TEXT, object_key TEXT, status TEXT)")
        connection.executemany(
            "INSERT INTO asset VALUES (?, ?, 'active')",
            (
                ("photo-original", "pic.jpg"),
                ("photo-thumbnail", "pic.webp"),
                ("file", "skip.bin"),
                ("photo-original", "../evil.jpg"),
            ),
        )
        connection.commit()
    originals = data / "objects" / "originals"
    thumbs = data / "objects" / "thumbnails"
    originals.mkdir(parents=True)
    thumbs.mkdir(parents=True)
    (originals / "pic.jpg").write_bytes(b"jpeg-bytes")
    (thumbs / "pic.webp").write_bytes(b"webp-bytes")
    (originals / "skip.bin").write_bytes(b"not-a-photo")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    (originals / "link.jpg").symlink_to(outside)
    with closing(sqlite3.connect(live)) as connection:
        connection.execute("INSERT INTO asset VALUES ('photo-original', 'link.jpg', 'active')")
        connection.commit()

    with pytest.raises(PrivateRuntimeError) as overlap:
        run_daily_backup(backup / "nested", backup)
    assert overlap.value.code == "ROOTS_OVERLAP"

    first = run_daily_backup(data, backup)
    assert first.photos_copied == 2
    assert first.photos_refused == 2
    assert first.snapshot_count == 1
    second = run_daily_backup(data, backup)
    assert second.photos_copied == 0
    assert second.photos_skipped == 2
    (originals / "pic.jpg").write_bytes(b"jpeg-bytes-2")
    third = run_daily_backup(data, backup)
    assert third.photos_copied == 1
    assert (backup / "photos" / "originals" / "pic.jpg").read_bytes() == b"jpeg-bytes-2"
    assert not (backup / "photos" / "originals" / "skip.bin").exists()
    assert not (backup / "photos" / "originals" / "link.jpg").exists()
    for _ in range(6):
        run_daily_backup(data, backup)
    assert len(list((backup / "db-snapshots").glob("star-isle-*.db"))) == 7
    assert live.is_file()
    assert not any(path.resolve() == live.resolve() for path in backup.rglob("*") if path.is_file())


def test_owner_setup_rejects_open_bind_password_flag_and_repo_root(tmp_path: Path, capsys):
    backup = tmp_path / "backup"
    code = setup_owner(
        ["--data-root", str(tmp_path / "data"), "--backup-root", str(backup), "--bind-host", "0.0.0.0"]
    )
    assert code == 2
    assert capsys.readouterr().out.strip() == "BIND_HOST_REFUSED"
    code = setup_owner(["--data-root", str(repository_root()), "--backup-root", str(backup)])
    assert code == 2
    assert capsys.readouterr().out.strip() == "ROOT_INSIDE_REPO"
    completed = subprocess.run(
        [sys.executable, "-B", "-m", "app.owner_setup", "--password", "not-a-real-password"],
        cwd=API_ROOT,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 2
    assert b"ARGUMENT_ERROR" in completed.stdout
    assert b"not-a-real-password" not in completed.stdout + completed.stderr


def test_owner_setup_creates_one_account_outside_the_repo(tmp_path: Path):
    data = tmp_path / "data"
    backup = tmp_path / "backup"
    password = "owner-password-12"
    env = os.environ.copy()
    env["PYTHON_DOTENV_DISABLED"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "app.owner_setup",
            "--data-root",
            str(data),
            "--backup-root",
            str(backup),
            "--password-stdin",
        ],
        cwd=API_ROOT,
        input=f"{password}\n{password}\n".encode(),
        capture_output=True,
        env=env,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode()
    assert completed.stdout.strip() == b"OWNER_CREATED"
    assert password.encode() not in completed.stdout + completed.stderr
    runtime = (data / "runtime.env").read_text(encoding="utf-8")
    assert password not in runtime
    assert "STAR_ISLE_BIND_HOST=127.0.0.1" in runtime
    assert "STAR_ISLE_BIND_PORT=8787" in runtime
    assert f"STAR_ISLE_BACKUP_ROOT={backup}" in runtime
    assert "$argon2id$" in runtime
    with closing(sqlite3.connect(data / "db" / "star-isle.db")) as connection:
        row = connection.execute("SELECT id, password_hash FROM admin").fetchone()
    assert row[0] == "admin"
    assert row[1].startswith("$argon2id$")
    assert password not in row[1]
    again = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "app.owner_setup",
            "--data-root",
            str(data),
            "--backup-root",
            str(backup),
            "--password-stdin",
        ],
        cwd=API_ROOT,
        input=f"{password}\n{password}\n".encode(),
        capture_output=True,
        env=env,
        check=False,
    )
    assert again.returncode == 2
    assert again.stdout.strip() == b"RUNTIME_ENV_EXISTS"
    (data / "runtime.env").unlink()
    third = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "app.owner_setup",
            "--data-root",
            str(data),
            "--backup-root",
            str(backup),
            "--password-stdin",
        ],
        cwd=API_ROOT,
        input=f"{password}\n{password}\n".encode(),
        capture_output=True,
        env=env,
        check=False,
    )
    assert third.returncode == 2
    assert third.stdout.strip() == b"OWNER_EXISTS"


def test_owner_setup_refuses_non_tty_without_hanging(tmp_path: Path):
    env = os.environ.copy()
    env.pop("STAR_ISLE_OWNER_PASSWORD", None)
    env["PYTHON_DOTENV_DISABLED"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "app.owner_setup",
            "--data-root",
            str(tmp_path / "data"),
            "--backup-root",
            str(tmp_path / "backup"),
        ],
        cwd=API_ROOT,
        input=b"",
        capture_output=True,
        env=env,
        check=False,
        timeout=5,
    )
    assert completed.returncode == 2
    assert completed.stdout.strip() == b"PASSWORD_TTY_REQUIRED"


def test_owner_setup_accepts_password_from_env_not_argv(tmp_path: Path):
    data = tmp_path / "data"
    backup = tmp_path / "backup"
    password = "env-password-12"
    env = os.environ.copy()
    env["PYTHON_DOTENV_DISABLED"] = "1"
    env["STAR_ISLE_OWNER_PASSWORD"] = password
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "app.owner_setup",
            "--data-root",
            str(data),
            "--backup-root",
            str(backup),
        ],
        cwd=API_ROOT,
        input=b"",
        capture_output=True,
        env=env,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr.decode()
    assert completed.stdout.strip() == b"OWNER_CREATED"
    assert password.encode() not in completed.stdout + completed.stderr
    assert password not in (data / "runtime.env").read_text(encoding="utf-8")


def test_desk_log_is_utf8_and_windows_scripts_do_not_capture_stderr(tmp_path: Path):
    from app.private_server import configure_desk_log, write_pid_file

    log = tmp_path / "logs" / "private-desk.log"
    old_out, old_err = sys.stdout, sys.stderr
    handle = None
    try:
        handle = configure_desk_log(log)
        write_pid_file(log.with_name("private-desk.pid"))
        print("PRIVATE_DESK_LISTENING")
        sys.stdout.flush()
    finally:
        sys.stdout = old_out
        sys.stderr = old_err
        if handle is not None:
            handle.close()
    raw = log.read_bytes()
    assert not raw.startswith((b"\xff\xfe", b"\xfe\xff"))
    assert "PRIVATE_DESK_LISTENING" in raw.decode("utf-8")
    assert (log.with_name("private-desk.pid")).read_text(encoding="ascii").strip() == str(os.getpid())
    folder = REPO_ROOT / "scripts" / "windows"
    names = {path.name for path in folder.glob("*.ps1")}
    assert {"start-private.ps1", "stop-private.ps1", "register-logon.ps1"} <= names
    for path in folder.glob("*.ps1"):
        text = path.read_text(encoding="utf-8")
        for token in ("*>>", "*>&", "2>&1", "2>>"):
            assert token not in text
    start = (folder / "start-private.ps1").read_text(encoding="utf-8")
    assert "STAR_ISLE_DESK_LOG" in start
    assert "UTF8Encoding" in start
    assert "Continue" in start
