"""Loopback-only KB 403 diagnostic with private, temporary browser artifacts."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from harness_safety import is_loopback_origin, owned_temporary_directory, safe_request_label
try:
    from playwright.sync_api import sync_playwright
except ImportError:  # Preserve fixed diagnostic output when Playwright is absent.
    sync_playwright = None


PASSWORD_ENV = "STAR_ISLE_TEST_PASSWORD"
BASE_ENV = "STAR_ISLE_KB_DEBUG_BASE"


def _loopback_base(value: str) -> str | None:
    if not is_loopback_origin(value):
        return None
    return value.rstrip("/")


def _safe_http_status_line(method: str, url: str, status: int) -> str:
    return f"HTTP {int(status)} {safe_request_label(method, url)}"


def _is_api_url(url: str) -> bool:
    try:
        return urlsplit(str(url)).path.startswith("/api/")
    except (TypeError, ValueError):
        return False


def _close_owned_browser(page, context, browser) -> bool:
    cleanup_ok = True
    for resource in (page, context, browser):
        if resource is None:
            continue
        try:
            resource.close()
        except Exception:
            cleanup_ok = False
    return cleanup_ok


def _finish(result: int, cleanup_ok: bool) -> int:
    if not cleanup_ok:
        print("KB_DEBUG_CLEANUP_FAILED", file=sys.stderr)
        return 1
    if result == 0:
        print("KB_DEBUG_COMPLETE")
        return 0
    print("KB_DEBUG_FAILED", file=sys.stderr)
    return 1


def main() -> int:
    password = os.environ.get(PASSWORD_ENV, "")
    if not password:
        print("KB_DEBUG_TEST_PASSWORD_REQUIRED", file=sys.stderr)
        return 2
    base = _loopback_base(os.environ.get(BASE_ENV, "http://127.0.0.1:4321"))
    if base is None:
        print("KB_DEBUG_LOOPBACK_BASE_REQUIRED", file=sys.stderr)
        return 2
    if sync_playwright is None:
        print("KB_DEBUG_BROWSER_UNAVAILABLE", file=sys.stderr)
        return 2

    browser = None
    context = None
    page = None
    result = 1
    cleanup_ok = True
    try:
        with owned_temporary_directory("starisle-kb-debug-") as temporary:
            artifact_root = Path(temporary)
            sample = artifact_root / "sample.txt"
            sample.write_text("private local KB diagnostic sample", encoding="utf-8")
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True)
                    context = browser.new_context(viewport={"width": 1440, "height": 900})
                    page = context.new_page()

                    def record_response(response) -> None:
                        if response.status >= 400 and _is_api_url(response.url):
                            print(_safe_http_status_line(response.request.method, response.url, response.status))

                    page.on("response", record_response)
                    page.goto(base + "/console", wait_until="networkidle")
                    page.get_by_role("textbox", name="密码").fill(password)
                    page.get_by_role("button", name="登录").click()
                    page.wait_for_url("**/console", timeout=15_000)
                    page.goto(base + "/console/kb", wait_until="networkidle")
                    page.locator("input[type=file]").first.set_input_files(str(sample))
                    page.wait_for_timeout(4_000)
                    page.locator("textarea[aria-label='问题输入框']").fill("local diagnostic query")
                    page.get_by_role("button", name="发送").click()
                    page.wait_for_timeout(12_000)
                    page.screenshot(path=artifact_root / "kb_debug.png")
                    result = 0
                except Exception:
                    result = 1
                finally:
                    cleanup_ok = _close_owned_browser(page, context, browser)
                    page = None
                    context = None
                    browser = None
    except Exception:
        result = 1
    return _finish(result, cleanup_ok)


if __name__ == "__main__":
    raise SystemExit(main())
