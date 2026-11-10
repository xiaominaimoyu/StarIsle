"""浏览器里复现：编辑后立刻切换，必须先保存当前篇。"""

from __future__ import annotations

import socket
import threading
import time
import uuid
from pathlib import Path

import httpx
import pytest
import uvicorn
from app.db import init_db
from app.main import app

from tests._isolation import TEST_PASSWORD

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

ARTIFACTS = Path("/opt/cursor/artifacts")


@pytest.fixture
def desk_origin():
    init_db()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{port}"
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            if httpx.get(origin + "/api/v1/health/live", timeout=0.2).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    else:
        server.should_exit = True
        raise RuntimeError("desk did not start")
    yield origin
    server.should_exit = True
    thread.join(timeout=5)


def _api(origin: str) -> tuple[httpx.Client, str]:
    client = httpx.Client(base_url=origin, timeout=5)
    token = client.get("/api/v1/auth/csrf").json()["csrfToken"]
    response = client.post(
        "/api/v1/auth/login",
        json={"password": TEST_PASSWORD},
        headers={"X-CSRF-Token": token, "Origin": origin},
    )
    assert response.status_code == 200
    token = client.get("/api/v1/auth/csrf").json()["csrfToken"]
    return client, token


def _create(client: httpx.Client, origin: str, token: str, title: str, body: str) -> dict:
    response = client.post(
        "/api/v1/notes",
        json={"title": title, "body": body, "tags": []},
        headers={"X-CSRF-Token": token, "Origin": origin},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _launch():
    playwright = sync_playwright().start()
    errors: list[str] = []
    for kwargs in ({"channel": "chrome"}, {"executable_path": "/usr/bin/google-chrome"}, {}):
        try:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"], **kwargs)
        except Exception as exc:
            errors.append(str(exc))
            continue
        return playwright, browser
    playwright.stop()
    pytest.skip("没有可用的 Chrome：" + " | ".join(errors))


def _open(page, origin: str) -> None:
    page.goto(origin + "/private/login?next=/private/notes")
    page.fill("#password", TEST_PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_function("() => location.pathname === '/private/notes'")
    page.wait_for_selector("[data-title]")


def _body_of(client: httpx.Client, note_id: str) -> str:
    response = client.get(f"/api/v1/notes/{note_id}")
    assert response.status_code == 200
    return response.json()["body"]


def test_rapid_switch_saves_the_edited_note_and_leaves_the_other(desk_origin, monkeypatch):
    from app.routers import desk_notes as notes_router

    original = notes_router.update_note

    def slow_update(*args, **kwargs):
        time.sleep(0.6)
        return original(*args, **kwargs)

    monkeypatch.setattr(notes_router, "update_note", slow_update)
    mark = uuid.uuid4().hex[:8]
    title_a = f"甲{mark}"
    title_b = f"乙{mark}"
    client, token = _api(desk_origin)
    note_a = _create(client, desk_origin, token, title_a, "甲原本")
    note_b = _create(client, desk_origin, token, title_b, "乙原本")
    patches: list[str] = []
    playwright, browser = _launch()
    try:
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("request", lambda request: patches.append(request.url) if request.method == "PATCH" else None)
        _open(page, desk_origin)
        page.locator(".letter-row", has_text=title_a).click()
        page.wait_for_function("(title) => document.querySelector('[data-title]').value === title", arg=title_a)
        page.fill("[data-body]", "甲原本\n刚改的一句")
        blocked = page.evaluate(
            """() => {
              const event = new Event('beforeunload', { cancelable: true });
              window.dispatchEvent(event);
              return event.defaultPrevented;
            }"""
        )
        assert blocked is True
        page.locator(".letter-row", has_text=title_b).click()
        page.wait_for_function("() => document.querySelector('[data-save]').dataset.state === 'saving'")
        assert page.locator("[data-title]").input_value() == title_a
        page.wait_for_function("(title) => document.querySelector('[data-title]').value === title", arg=title_b)
        assert page.locator("[data-save]").get_attribute("data-state") == "saved"
        page.locator(".letter-row", has_text=title_a).click()
        page.wait_for_function("() => document.querySelector('[data-body]').value.includes('刚改的一句')")
        assert page.locator("[data-save]").get_attribute("data-state") == "saved"
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        page.locator(".notes-editor").screenshot(path=str(ARTIFACTS / "notes-switch-kept.png"))
    finally:
        browser.close()
        playwright.stop()
    assert _body_of(client, note_a["id"]) == "甲原本\n刚改的一句"
    assert _body_of(client, note_b["id"]) == "乙原本"
    assert any(note_a["id"] in url for url in patches)
    assert all(note_b["id"] not in url for url in patches)
    client.close()


def test_failed_save_does_not_switch_notes(desk_origin):
    mark = uuid.uuid4().hex[:8]
    title_a = f"丙{mark}"
    title_b = f"丁{mark}"
    client, token = _api(desk_origin)
    note_a = _create(client, desk_origin, token, title_a, "丙原本")
    note_b = _create(client, desk_origin, token, title_b, "丁原本")
    playwright, browser = _launch()
    try:
        page = browser.new_page(viewport={"width": 1440, "height": 1000})

        def fail_patch(route):
            if route.request.method == "PATCH":
                route.fulfill(status=500, content_type="application/json", body="{}")
                return
            route.continue_()

        page.route("**/api/v1/notes/**", fail_patch)
        _open(page, desk_origin)
        page.locator(".letter-row", has_text=title_a).click()
        page.wait_for_function("(title) => document.querySelector('[data-title]').value === title", arg=title_a)
        page.fill("[data-body]", "丙原本\n不该丢掉")
        page.locator(".letter-row", has_text=title_b).click()
        page.wait_for_function("() => document.querySelector('[data-save]').textContent === '保存失败'")
        assert page.locator("[data-title]").input_value() == title_a
        assert "不该丢掉" in page.locator("[data-body]").input_value()
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        page.locator(".notes-editor").screenshot(path=str(ARTIFACTS / "notes-switch-blocked.png"))
    finally:
        browser.close()
        playwright.stop()
    assert _body_of(client, note_a["id"]) == "丙原本"
    assert _body_of(client, note_b["id"]) == "丁原本"
    client.close()
