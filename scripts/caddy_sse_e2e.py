"""Real, isolated Caddy SSE lifecycle checks with a synthetic chat upstream.

Run with apps/api/.venv Python (httpx, argon2 and Playwright installed), pnpm,
Chromium and a running Linux Docker engine. Own resources only; no real dotenv,
data or models. Evidence is written to a fresh ignored logs/caddy-sse-* directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import time
import uuid
from pathlib import Path

import httpx
from argon2 import PasswordHasher
from playwright.sync_api import expect, sync_playwright

from harness_safety import has_link_or_reparse_component, owned_temporary_directory

ROOT = Path(__file__).resolve().parents[1]


def assert_safe_runtime_logs(text: str) -> dict[str, int]:
    """Check actual Caddy warning JSON, not just the Caddyfile source."""
    warnings = 0
    request_logs = 0
    for line in text.splitlines():
        prefix, separator, payload = line.partition("|")
        if not separator or not prefix.strip().startswith("caddy-"):
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        request = event.get("request")
        if isinstance(request, dict):
            request_logs += 1
            assert not request.get("headers"), "Caddy runtime log contains request headers"
        if event.get("msg") == "aborting with incomplete response":
            warnings += 1
    assert warnings >= 1, "cancellation warning evidence missing"
    return {"cancellation_warnings": warnings, "request_logs_without_headers": request_logs}


def fingerprint(directory: Path) -> dict[str, str]:
    return {
        str(path.relative_to(directory)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*")) if path.is_file()
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", default="desktop-linux" if os.name == "nt" else "default")
    args = parser.parse_args()
    project = "starislesse" + uuid.uuid4().hex[:10]
    image = project + ":api"
    out = ROOT / "logs" / project
    if has_link_or_reparse_component(out):
        raise RuntimeError("unsafe evidence directory")
    out.mkdir(parents=True, exist_ok=False)
    docker = ["docker", "--context", args.context]
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("COMPOSE_", "STAR_ISLE_", "PUBLIC_"))}
    for key in ("DATA_HOST_ROOT", "MODEL_HOST_ROOT", "CADDY_HOST_PORT", "AUTH_JWT_SIGNING_KEY",
                "ADMIN_BOOTSTRAP_PASSWORD_HASH", "MAIL_HMAC_KEY", "EMBEDDING_DIM"):
        env.pop(key, None)
    env.update(COMPOSE_DISABLE_ENV_FILE="1", PYTHON_DOTENV_DISABLED="1")
    result = {"project": project, "context": args.context, "result": "running", "checks": {}}
    result["source_sha256"] = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in ("scripts/caddy_sse_e2e.py", "scripts/fixtures/caddy_slow_chat.py",
                     "apps/api/app/routers/rag.py", "apps/api/app/services/rag_engine.py",
                     "infra/caddy/Caddyfile", "infra/compose/compose.yml", "infra/compose/compose.local.yml")
    }
    public_before = fingerprint(ROOT / "apps/web/dist")

    def save():
        (out / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    def run(command: list[str], label: str, *, command_env=None) -> str:
        completed = subprocess.run(command, cwd=ROOT, env=command_env or env,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace")
        (out / (label + ".log")).write_text(completed.stdout + completed.stderr, encoding="utf-8")
        if completed.returncode:
            raise RuntimeError(f"{label} failed; inspect owned evidence")
        return completed.stdout

    def checked(name: str, details):
        result["checks"][name] = details
        save()
        print("CADDY_SSE: " + name, flush=True)

    try:
        result["engine"] = run([*docker, "version", "--format", "{{.Server.Version}}"], "engine").strip()
        with owned_temporary_directory("starisle-sse-") as temporary:
            owned = Path(temporary)
            data, web = owned / "runtime", owned / "web"
            data.mkdir()
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            base = f"http://127.0.0.1:{port}"
            result["port"] = port
            password = secrets.token_urlsafe(28)
            api_env = owned / "api.env"
            api_env.write_text("\n".join((
                "PYTHON_DOTENV_DISABLED=1",
                "AUTH_JWT_SIGNING_KEY=" + secrets.token_urlsafe(48),
                "AUTH_JWT_ACTIVE_KID=caddy-sse-test",
                "MAIL_HMAC_KEY=" + secrets.token_urlsafe(48),
                "ADMIN_BOOTSTRAP_PASSWORD_HASH=" + PasswordHasher().hash(password),
            )) + "\n", encoding="utf-8")
            topology = owned / "compose.env"
            topology.write_text("\n".join((
                "DATA_HOST_ROOT=" + data.as_posix(),
                "STAR_ISLE_API_ENV_FILE=" + api_env.as_posix(),
                "CADDY_HOST_PORT=" + str(port),
            )) + "\n", encoding="utf-8")
            overlay = owned / "accept.json"
            overlay.write_text(json.dumps({"services": {
                "api": {"image": image, "restart": "no", "environment": {
                    "LLAMA_CHAT_BASE_URL": "http://chat-fixture:8090", "CHAT_MODEL_ID": "synthetic-only",
                }},
                "worker": {"image": image, "restart": "no"},
                "caddy": {"restart": "no", "volumes": [{
                    "type": "bind", "source": web.as_posix(), "target": "/srv/web", "read_only": True,
                    "bind": {"create_host_path": False},
                }]},
                "chat-fixture": {"image": image, "restart": "no", "command": [
                    "uvicorn", "caddy_slow_chat:app", "--app-dir", "/fixture", "--host", "0.0.0.0",
                    "--port", "8090", "--no-access-log",
                ], "volumes": [{
                    "type": "bind", "source": str(ROOT / "scripts/fixtures/caddy_slow_chat.py"),
                    "target": "/fixture/caddy_slow_chat.py", "read_only": True,
                    "bind": {"create_host_path": False},
                }]},
            }}), encoding="utf-8")
            compose = [*docker, "compose", "--project-name", project, "--env-file", str(topology),
                       "-f", str(ROOT / "infra/compose/compose.yml"),
                       "-f", str(ROOT / "infra/compose/compose.local.yml"), "-f", str(overlay)]
            up_attempted = False
            image_built = False
            functional_checks_completed = False
            try:
                pnpm = shutil.which("pnpm")
                if not pnpm:
                    raise RuntimeError("pnpm is required")
                run([pnpm, "--filter", "@star-isle/web", "astro", "build", "--outDir", str(web)],
                    "frontend-build", command_env=dict(env, PUBLIC_SITE_URL=base,
                        PUBLIC_PRIVATE_AREA_ENABLED="true", PUBLIC_API_BASE="/api/v1"))
                run([*docker, "build", "--tag", image, str(ROOT / "apps/api")], "api-build")
                image_built = True
                # Config is inspected in memory: never write expanded API secrets to evidence.
                config = subprocess.run([*compose, "config", "--format", "json"], cwd=ROOT, env=env,
                                        capture_output=True, text=True, encoding="utf-8", check=True)
                services = json.loads(config.stdout)["services"]
                assert len(services) == 4
                for name, service in services.items():
                    if name == "caddy":
                        assert len(service["ports"]) == 1 and service["ports"][0]["host_ip"] == "127.0.0.1"
                    else:
                        assert not service.get("ports")
                checked("isolated_topology", {"services": 4, "only_loopback_caddy_port": True})
                up_attempted = True
                run([*compose, "up", "--detach", "--no-build", "--wait", "--wait-timeout", "150"], "up")
                result["api_image"] = run([*docker, "image", "inspect", image, "--format", "{{.Id}}"], "image").strip()

                def fixture_status():
                    # Fixture has no host port or public route; inspect on its private network.
                    raw = run([*compose, "exec", "-T", "api", "python", "-c",
                        "import urllib.request; print(urllib.request.urlopen('http://chat-fixture:8090/status').read().decode())"],
                        "fixture-status")
                    return json.loads(raw)

                def wait_closed(mode: str):
                    started = time.monotonic()
                    while time.monotonic() - started < 5:
                        state = fixture_status()[mode]
                        if state["opened"] == 1 and state["active"] == 0 and state["interrupted"] == 1:
                            return {**state, "observed_close_ms": round((time.monotonic() - started) * 1000, 2)}
                        time.sleep(0.2)
                    raise AssertionError("upstream remained active after " + mode)

                with httpx.Client(base_url=base, trust_env=False, timeout=40, follow_redirects=True) as client:
                    csrf = client.get("/api/v1/auth/csrf").json()["csrfToken"]
                    headers = {"X-CSRF-Token": csrf, "Origin": base}
                    response = client.post("/api/v1/auth/login", json={"password": password}, headers=headers)
                    assert response.status_code == 200

                    def conversation(mode: str) -> str:
                        response = client.post("/api/v1/conversations", json={"title": mode}, headers=headers)
                        assert response.status_code == 201
                        return response.json()["id"]

                    def assert_messages(cid: str, assistants: int):
                        response = client.get("/api/v1/conversations/" + cid)
                        assert response.status_code == 200
                        messages = response.json()["messages"]
                        assert sum(m["role"] == "user" for m in messages) == 1
                        assert sum(m["role"] == "assistant" for m in messages) == assistants

                    def collect(mode: str):
                        cid = conversation(mode)
                        events, timing = [], []
                        started = time.monotonic()
                        with client.stream("POST", "/api/v1/rag/query", headers=headers,
                                           json={"query": mode, "conversationId": cid}) as stream:
                            assert stream.status_code == 200
                            assert "text/event-stream" in stream.headers["content-type"]
                            assert "content-encoding" not in stream.headers
                            for line in stream.iter_lines():
                                if line.startswith("data: "):
                                    events.append(json.loads(line[6:]))
                                    timing.append(round((time.monotonic() - started) * 1000, 2))
                        return cid, events, timing

                    cid, events, timing = collect("heartbeat-complete")
                    heartbeats = [t for event, t in zip(events, timing) if event["event"] == "heartbeat"]
                    assert len(heartbeats) >= 2 and 8000 <= heartbeats[0] <= 15000
                    assert 8000 <= heartbeats[1] - heartbeats[0] <= 15000
                    assert events[-1]["event"] == "done" and not any(e["event"] == "error" for e in events)
                    assert timing[0] < 1000
                    assert_messages(cid, 1)
                    checked("two_real_heartbeats_and_complete", {"heartbeat_ms": heartbeats, "first_data_ms": timing[0]})

                    for mode in ("close-http", "cancel-before-token"):
                        cid = conversation(mode)
                        with client.stream("POST", "/api/v1/rag/query", headers=headers,
                                           json={"query": mode, "conversationId": cid}) as stream:
                            assert stream.status_code == 200
                            for line in stream.iter_lines():
                                if line.startswith("data: "):
                                    event = json.loads(line[6:])
                                    if (mode == "close-http" and event["event"] == "token") or (
                                        mode == "cancel-before-token" and event["event"] == "heartbeat"
                                    ):
                                        break
                        state = wait_closed(mode)
                        assert_messages(cid, 0)
                        checked(mode, state)

                    with sync_playwright() as playwright:
                        browser = playwright.chromium.launch(headless=True)
                        try:
                            context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
                            page = context.new_page()
                            errors = []
                            page.on("pageerror", lambda error: errors.append(type(error).__name__))
                            page.goto(base + "/enter/", wait_until="networkidle")
                            page.get_by_role("link", name="撕开信封，进入私人登录页", exact=True).press("Enter")
                            page.wait_for_url(re.compile(re.escape(base) + r"/login/?$"))
                            page.wait_for_load_state("networkidle")
                            page.get_by_label("密码", exact=True).fill(password)
                            with page.expect_response(lambda r: r.url.endswith("/api/v1/auth/login") and r.status == 200):
                                page.get_by_role("button", name="登录", exact=True).click()
                            page.wait_for_url(re.compile(re.escape(base) + r"/console/?$"))
                            for mode in ("cancel-browser", "navigate-browser"):
                                page.goto(base + "/console/kb/", wait_until="networkidle")
                                page.get_by_role("textbox", name="问题输入框").fill(mode)
                                with page.expect_response(lambda r: r.request.method == "POST" and
                                        r.url.endswith("/api/v1/conversations") and r.status == 201) as created:
                                    page.get_by_role("button", name="发送", exact=True).click()
                                cid = created.value.json()["id"]
                                expect(page.get_by_test_id("kb-chat-history")).to_contain_text(f"fixture:{mode}:partial")
                                if mode == "cancel-browser":
                                    page.get_by_role("button", name="取消", exact=True).click()
                                    expect(page.get_by_role("alert")).to_contain_text("已取消")
                                else:
                                    page.goto(base + "/console/", wait_until="domcontentloaded")
                                state = wait_closed(mode)
                                assert_messages(cid, 0)
                                checked(mode, state)
                            assert not errors
                            checked("browser_page_errors", len(errors))
                        finally:
                            browser.close()

                    cid, events, _ = collect("partial-error")
                    assert any(e["event"] == "token" for e in events)
                    assert any(e["event"] == "error" and e["code"] == "CHAT_STREAM_INCOMPLETE" for e in events)
                    assert not any(e["event"] == "done" for e in events)
                    assert_messages(cid, 0)
                    checked("partial_error_not_persisted", True)
                    cid, events, _ = collect("normal-after-cancel")
                    assert events[-1]["event"] == "done"
                    assert "".join(e["token"] for e in events if e["event"] == "token") == (
                        "fixture:normal-after-cancel:partial:complete"
                    )
                    assert_messages(cid, 1)
                    states = fixture_status()
                    assert all(s["active"] == 0 for s in states.values())
                    assert all(s["opened"] == 1 for s in states.values())
                    assert states["normal-after-cancel"]["completed"] == 1
                    assert states["heartbeat-complete"]["completed"] == 1
                    assert states["partial-error"]["incomplete"] == 1
                    checked("normal_request_after_cancellations", states)
                    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
                    functional_checks_completed = True
            finally:
                runtime_logs = ""
                if up_attempted:
                    try:
                        runtime_logs = run([*compose, "logs", "--no-color"], "service-logs")
                    finally:
                        run([*compose, "down", "--volumes", "--remove-orphans"], "down")
                counts = {}
                for resource, command in (("containers", ["ps", "--all", "--quiet"]),
                                          ("volumes", ["volume", "ls", "--quiet"]),
                                          ("networks", ["network", "ls", "--quiet"])):
                    remaining = run([*docker, *command, "--filter", "label=com.docker.compose.project=" + project],
                                    "remaining-" + resource).strip()
                    counts[resource] = len(remaining.splitlines()) if remaining else 0
                assert not any(counts.values())
                checked("owned_resources_removed", counts)
                if image_built:
                    run([*docker, "image", "rm", image], "image-remove")
                if functional_checks_completed:
                    checked("caddy_runtime_headers_removed", assert_safe_runtime_logs(runtime_logs))
        assert not owned.exists()
        checked("temporary_root_removed", True)
        assert fingerprint(ROOT / "apps/web/dist") == public_before
        checked("public_dist_unchanged", {"files": len(public_before)})
        result["result"] = "passed"
        return 0
    except Exception as error:
        result["result"] = "failed"
        result["failure_type"] = type(error).__name__
        raise
    finally:
        save()
        print("CADDY_SSE_EVIDENCE: " + str(out), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
