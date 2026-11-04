"""Isolated Caddy uploads, refresh and Linux backup/restore acceptance.

Requires existing API Python dependencies, Playwright Chromium, pnpm and Linux
Docker. Uses only owned temporary configuration/data and an unbound model path.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import shutil
import socket
import struct
import subprocess
import time
import uuid
import zlib
from pathlib import Path

import httpx
from argon2 import PasswordHasher
from harness_safety import has_link_or_reparse_component, owned_temporary_directory
from PIL import Image
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    project = "starislestore" + uuid.uuid4().hex[:10]
    image = project + ":api"
    out = ROOT / "logs" / project
    assert not has_link_or_reparse_component(out)
    out.mkdir(parents=True, exist_ok=False)
    docker = ["docker", "--context", "desktop-linux"] if os.name == "nt" else ["docker"]
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("COMPOSE_", "STAR_ISLE_", "PUBLIC_"))}
    for key in ("DATA_HOST_ROOT", "MODEL_HOST_ROOT", "AUTH_JWT_SIGNING_KEY",
                "ADMIN_BOOTSTRAP_PASSWORD_HASH", "MAIL_HMAC_KEY"):
        env.pop(key, None)
    env.update(COMPOSE_DISABLE_ENV_FILE="1", PYTHON_DOTENV_DISABLED="1")
    result = {"project": project, "status": "running", "checks": {}}

    def save():
        (out / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    def checked(name, details=True):
        result["checks"][name] = details
        save()
        print("CADDY_STORAGE: " + name, flush=True)

    def run(command, label, *, input_text=None, allowed=(0,), command_env=None):
        process = subprocess.run(command, cwd=ROOT, env=command_env or env, input=input_text,
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        (out / (label + ".log")).write_text(process.stdout + process.stderr, encoding="utf-8")
        if process.returncode not in allowed:
            raise RuntimeError(label + " failed; retained owned log")
        return process.stdout

    def dist_hashes():
        dist = ROOT / "apps/web/dist"
        return {p.relative_to(dist).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in dist.rglob("*") if p.is_file()}

    before_dist = dist_hashes()
    try:
        with owned_temporary_directory("starisle-storage-") as temporary:
            owned = Path(temporary)
            data, drill, web = owned / "runtime", owned / "drill", owned / "web"
            data.mkdir()
            drill.mkdir()
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            base = f"http://127.0.0.1:{port}"
            result["port"] = port
            password = secrets.token_urlsafe(28)
            api_env = owned / "api.env"
            api_env.write_text("\n".join((
                "AUTH_JWT_SIGNING_KEY=" + secrets.token_urlsafe(48),
                "AUTH_JWT_ACTIVE_KID=storage-test", "PYTHON_DOTENV_DISABLED=1",
                "MAIL_HMAC_KEY=" + secrets.token_urlsafe(48),
                "ADMIN_BOOTSTRAP_PASSWORD_HASH=" + PasswordHasher().hash(password),
            )) + "\n", encoding="utf-8")
            topology = owned / "compose.env"

            def write_topology(data_path):
                topology.write_text("\n".join(("DATA_HOST_ROOT=" + data_path.as_posix(),
                    "STAR_ISLE_API_ENV_FILE=" + api_env.as_posix(), "CADDY_HOST_PORT=" + str(port))), encoding="utf-8")

            write_topology(data)

            def mount(source, target):
                return {"type": "bind", "source": str(source), "target": target,
                        "read_only": True, "bind": {"create_host_path": False}}

            overlay = owned / "accept.json"
            overlay.write_text(json.dumps({"services": {
                "api": {"image": image, "restart": "no", "environment": {
                    "FILE_MAX_BYTES": "2048", "IMAGE_MAX_BYTES": "2048", "STORAGE_QUOTA_BYTES": "4096",
                }, "healthcheck": {"interval": "1s", "start_period": "2s", "retries": 30}, "volumes": [
                    mount(ROOT / "scripts/backup_restore.py", "/workspace/scripts/backup_restore.py"),
                    mount(ROOT / "scripts/recovery_drill.py", "/workspace/scripts/recovery_drill.py"),
                    mount(ROOT / "apps/api/app", "/workspace/apps/api/app"),
                    {"type": "bind", "source": str(drill), "target": "/drill", "bind": {"create_host_path": False}},
                ]},
                "worker": {"image": image, "restart": "no"},
                "caddy": {"restart": "no", "healthcheck": {"interval": "1s"}, "volumes": [mount(web, "/srv/web")]},
            }}), encoding="utf-8")
            compose = [*docker, "compose", "--project-name", project, "--env-file", str(topology),
                       "-f", str(ROOT / "infra/compose/compose.yml"),
                       "-f", str(ROOT / "infra/compose/compose.local.yml"), "-f", str(overlay)]
            exec_api = [*compose, "exec", "-T", "-e", "PYTHONPATH=/app", "api", "python", "-B"]
            image_built = up_attempted = False
            try:
                pnpm = shutil.which("pnpm")
                assert pnpm
                run([pnpm, "--filter", "@star-isle/web", "astro", "build", "--outDir", str(web)],
                    "build-web", command_env=dict(env, PUBLIC_PRIVATE_AREA_ENABLED="true", PUBLIC_API_BASE="/api/v1", PUBLIC_SITE_URL=base))
                run([*docker, "build", "--tag", image, str(ROOT / "apps/api")], "build-api")
                image_built = True
                config = json.loads(subprocess.check_output([*compose, "config", "--format", "json"], env=env, cwd=ROOT))
                assert len(config["services"]) == 3
                for name, service in config["services"].items():
                    if name == "caddy":
                        assert len(service["ports"]) == 1 and service["ports"][0]["host_ip"] == "127.0.0.1"
                    else:
                        assert not service.get("ports")
                up_attempted = True
                run([*compose, "up", "-d", "--no-build", "--wait", "--wait-timeout", "150"], "up")

                def client_login(client):
                    csrf = client.get("/api/v1/auth/csrf").json()["csrfToken"]
                    headers = {"Origin": base, "X-CSRF-Token": csrf}
                    assert client.post("/api/v1/auth/login", json={"password": password}, headers=headers).status_code == 200
                    return headers

                def objects():
                    return {p.relative_to(data).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in (data / "objects").rglob("*") if p.is_file() and "health" not in p.parts}

                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    try:
                        context = browser.new_context(reduced_motion="reduce")
                        page = context.new_page()
                        page.goto(base + "/login/", wait_until="networkidle")
                        page.get_by_label("密码", exact=True).fill(password)
                        with page.expect_response(lambda r: r.url.endswith("/api/v1/auth/login") and r.status == 200):
                            page.get_by_role("button", name="登录", exact=True).click()
                        page.wait_for_url(re.compile(re.escape(base) + r"/console/?$"))
                        expect(page.locator(".tear-portal-link")).to_be_visible()
                        context.clear_cookies(name="star_isle_access")
                        with page.expect_response(lambda r: r.url.endswith("/api/v1/auth/refresh") and r.status == 200):
                            page.reload(wait_until="networkidle")
                        expect(page.locator(".tear-portal-link")).to_be_visible()
                        assert page.request.get(base + "/api/v1/auth/me").status == 200
                        checked("real_browser_caddy_refresh")
                    finally:
                        browser.close()

                with httpx.Client(base_url=base, trust_env=False, timeout=40) as client:
                    headers = client_login(client)
                    png_buffer = io.BytesIO()
                    Image.new("RGB", (8, 6), (20, 80, 140)).save(png_buffer, format="PNG")
                    png = png_buffer.getvalue()
                    oversized_ihdr = struct.pack(">IIBBBBB", 9001, 1, 8, 2, 0, 0, 0)
                    huge_png = png[:16] + oversized_ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + oversized_ihdr)) + png[33:]
                    baseline = objects()
                    for label, endpoint, filename, body, mime, status in (
                        ("size", "files", "large.txt", b"x" * 2049, "text/plain", 413),
                        ("mime", "files", "fake.png", b"not-an-image", "image/png", 415),
                        ("photo-mime", "photos", "wrong.txt", b"not-an-image", "text/plain", 415),
                        ("photo-magic", "photos", "fake.png", b"not-an-image", "image/png", 413),
                        ("pixels", "photos", "huge.png", huge_png, "image/png", 413),
                    ):
                        response = client.post("/api/v1/" + endpoint, headers=headers, files={"file": (filename, body, mime)})
                        assert response.status_code == status, (label, response.status_code)
                        assert objects() == baseline
                    checked("rejected_uploads_leave_no_objects")
                    file_bytes = b"StarIsle restore acceptance file."
                    response = client.post("/api/v1/files", headers=headers, files={"file": ("restore.txt", file_bytes, "text/plain")})
                    assert response.status_code == 201
                    file_id = response.json()["id"]
                    response = client.post("/api/v1/photos", headers=headers, files={"file": ("restore.png", png, "image/png")})
                    assert response.status_code == 201
                    photo_id = response.json()["id"]
                    thumb = client.get(f"/api/v1/photos/{photo_id}/thumbnail").content
                    Image.open(io.BytesIO(thumb)).verify()
                    doc = b"# StarIsle restore\n\nRecovery requires independent restore verification.\n"
                    response = client.post("/api/v1/knowledge/documents", headers=headers, files={"file": ("restore.md", doc, "text/markdown")})
                    assert response.status_code == 202
                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline:
                        if client.get("/api/v1/knowledge/documents").json()["items"][0]["indexState"] == "ready":
                            break
                        time.sleep(0.25)
                    else:
                        raise AssertionError("worker did not index document")
                    with httpx.Client(base_url=base, trust_env=False) as anonymous:
                        for path in (f"/api/v1/files/{file_id}/download", f"/api/v1/photos/{photo_id}/thumbnail"):
                            assert anonymous.get(path).status_code == 401
                    checked("valid_uploads_worker_and_anonymous_guards")
                    backup = json.loads(run([*exec_api, "/workspace/scripts/backup_restore.py", "backup", "--data-root", "/data",
                        "--output", "/drill/backup", "--verify", "--json"], "backup"))
                    assert backup["status"] == "ok"
                    recovered = json.loads(run([*exec_api, "/workspace/scripts/recovery_drill.py", "--backup", "/drill/backup",
                        "--target", "/drill/restored", "--report", "/drill/report.json", "--knowledge-query-stdin"],
                        "restore-drill", input_text="StarIsle recovery verification"))
                    assert recovered["status"] == "passed" and recovered["productionAccepted"] is False
                    assert recovered["checks"]["photo"]["count"] == 1
                    assert recovered["checks"]["knowledge"]["searchHits"] >= 1
                    checked("linux_backup_and_independent_restore", recovered)
                    failure_probe = """
import json, sys
from pathlib import Path
sys.path.insert(0, '/workspace/scripts')
import recovery_drill as drill
original = drill.backup_restore.shutil.copy2
copies = 0
def fail_second(*args, **kwargs):
    global copies
    copies += 1
    if copies == 2:
        raise OSError('synthetic copy failure')
    return original(*args, **kwargs)
drill.backup_restore.shutil.copy2 = fail_second
result = drill.run('/drill/backup', '/drill/partial', '/drill/partial-report.json', 'StarIsle recovery')
assert result['status'] == 'failed' and result['failureCode'] == 'RESTORE_FAILED'
assert Path('/drill/partial-report.json.star-isle-recovery-drill-incomplete').is_file()
assert any(p.is_file() for p in Path('/drill/partial').rglob('*'))
again = drill.run('/drill/backup', '/drill/partial', '/drill/partial-report.json', 'StarIsle recovery')
assert again['status'] == 'failed'
print(json.dumps({'status': 'passed', 'injectedFailure': 'second-copy', 'partialPreserved': True, 'reuseRejected': True}))
"""
                    checked("injected_restore_failure_preserves_partial_and_rejects_reuse", json.loads(run(
                        [*exec_api, "-c", failure_probe], "partial-failure")))
                    for count in range(3):
                        before_reject = objects()
                        response = client.post("/api/v1/files", headers=headers, files={"file": (f"quota-{count}.txt", b"q" * 1536, "text/plain")})
                        if count < 2:
                            assert response.status_code == 201
                        else:
                            assert response.status_code == 413 and objects() == before_reject
                    checked("quota_rejection_has_no_residue")

                run([*compose, "down", "--volumes", "--remove-orphans"], "down-before-restore")
                write_topology(drill / "restored")
                run([*compose, "up", "-d", "--no-build", "--wait", "--wait-timeout", "150"], "restored-up")
                with httpx.Client(base_url=base, trust_env=False, timeout=40) as client:
                    headers = client_login(client)
                    assert len(client.get("/api/v1/files").json()["items"]) == 1
                    assert client.get(f"/api/v1/files/{file_id}/download").content == file_bytes
                    assert client.get(f"/api/v1/photos/{photo_id}/thumbnail").content == thumb
                    response = client.post("/api/v1/rag/query", headers=headers, json={"query": "StarIsle recovery verification"})
                    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
                    assert any(e["event"] == "citation" for e in events) and events[-1]["event"] == "done"
                    checked("restored_stack_download_thumbnail_and_retrieval")
                    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
            finally:
                if up_attempted:
                    try:
                        run([*compose, "logs", "--no-color"], "service-logs")
                    finally:
                        run([*compose, "down", "--volumes", "--remove-orphans"], "final-down")
                for label, command in (("containers", ["ps", "-aq"]), ("volumes", ["volume", "ls", "-q"]), ("networks", ["network", "ls", "-q"])):
                    assert not run([*docker, *command, "--filter", "label=com.docker.compose.project=" + project], "remaining-" + label).strip()
                if image_built:
                    run([*docker, "image", "rm", image], "remove-image")
                checked("owned_docker_resources_removed")
        checked("owned_temporary_root_removed", not owned.exists())
        checked("published_dist_unchanged", dist_hashes() == before_dist)
        result["status"] = "passed"
        return 0
    except Exception as error:
        result.update(status="failed", failure_type=type(error).__name__)
        raise
    finally:
        save()
        print("CADDY_STORAGE_EVIDENCE: " + str(out), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
