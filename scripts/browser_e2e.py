"""浏览器端到端联调:公开页 → 会话守卫 → 登录(Cookie+CSRF)→ 控制台 → 上传 → RAG → 登出。

默认启动本轮隔离的 loopback API、worker 与 Astro dev，并使用系统临时数据/证据目录。
用法:python -B scripts/browser_e2e.py --headless
     python -B scripts/browser_e2e.py --base-url http://127.0.0.1:4321 --public-only
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

from harness_safety import (
    HarnessSafetyError,
    OwnedProcess,
    OwnedProcessError,
    has_link_or_reparse_component,
    is_within,
    is_loopback_origin,
    new_temporary_directory_path,
    new_temporary_file_path,
    owned_temporary_directory,
    safe_request_label,
    spawn_owned_process,
    stop_owned_process,
    validated_system_temp_root,
    wait_for_owned_process,
)
try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, expect, sync_playwright
except ImportError:  # Allows stdlib lifecycle regression tests without browser dependencies.
    PlaywrightTimeoutError = TimeoutError
    expect = None
    sync_playwright = None

EVIDENCE: Path | None = None
BROWSER_TEST_PASSWORD_ENV = "STAR_ISLE_E2E_TEST_PASSWORD"
FIXTURE_CAPABILITY_ENV = "STAR_ISLE_E2E_FIXTURE_CAPABILITY"
FIXTURE_CAPABILITY_FILE = ".starisle-browser-e2e-capability"
FIXTURE_CANCELLED_UI_TEXT = "已取消。已生成的部分不会保存为完整回答。"

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

FAILURES: list[str] = []
PAGE_ERRORS: list[str] = []
FAILED_REQUESTS: list[str] = []
PUBLIC_API_REQUESTS: list[str] = []
COMPLETED_REQUESTS: dict[int, object] = {}
RESPONSE_COUNTS: Counter[tuple[str, str]] = Counter()
_RAW_RESPONSE_COUNTS: Counter[tuple[str, str]] = Counter()
ACTIVE_EXPECTED_ERROR_RESPONSES: set[tuple[str, str, int]] = set()
PUBLIC_STAGE_ACTIVE = False
UNKNOWN_ROUTE_EXPECTED_ERROR_RESPONSES = {
    ("GET", "/definitely-not-a-page", 404),
}
AUTH_GUARD_EXPECTED_ERROR_RESPONSES = {
    ("GET", "/api/v1/auth/me", 401),
    ("POST", "/api/v1/auth/refresh", 401),
}


class BrowserCleanupError(RuntimeError):
    """Raised when an owned browser resource could not be closed."""


class _SafeArgumentParser(argparse.ArgumentParser):
    """Keep invalid CLI values out of normal diagnostic stderr."""

    def error(self, message: str) -> None:
        del message
        self.exit(2, "BROWSER_E2E_ARGUMENTS_INVALID\n")


def _internal_pathname(url: str) -> str:
    """Keep an exact path only for in-memory assertions, never diagnostics."""

    try:
        path = urlparse(str(url)).path
    except (TypeError, ValueError):
        return "/"
    return path if path.startswith("/") else "/"


def check(cond: bool, name: str, detail: str = "") -> None:
    del detail
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}")
        FAILURES.append(name)


def response_count(method: str, path: str) -> int:
    """Return an exact in-memory count for a fixed harness assertion.

    The public ``RESPONSE_COUNTS`` collection is deliberately template-only so
    accidental diagnostic use cannot reveal a dynamic identifier.  Exact paths
    stay private in this separate counter for the handful of fixed assertions.
    """

    return _RAW_RESPONSE_COUNTS[(method, path)]


@contextmanager
def allow_expected_error_responses(*responses: tuple[str, str, int]):
    """仅在一个显式浏览器阶段内允许对应的 HTTP 失败；无论异常与否均恢复。"""
    previous = set(ACTIVE_EXPECTED_ERROR_RESPONSES)
    ACTIVE_EXPECTED_ERROR_RESPONSES.clear()
    ACTIVE_EXPECTED_ERROR_RESPONSES.update(responses)
    try:
        yield
    finally:
        ACTIVE_EXPECTED_ERROR_RESPONSES.clear()
        ACTIVE_EXPECTED_ERROR_RESPONSES.update(previous)


# 仅供浏览器页面内的本地候选分支使用。它不安装服务端端点，也不改变真实成功链路；
# 每次退出都恢复同一个 window 的原生 fetch identity。
FIXTURE_FETCH_INSTALL = r"""
({ rebuildPath, deletePath }) => {
  const originalFetch = window.fetch;
  const state = {
    originalFetch,
    fixtureFetch: null,
    hits: {},
    rebuildCount: 0,
    cancelledPollCount: 0,
    deletePollCount: 0,
    ragCount: 0,
    firstFourConversationId: null,
    firstFourSameNonEmptyConversation: true,
  };
  const hit = (name) => { state.hits[name] = (state.hits[name] || 0) + 1; };
  const json = (value, status = 200) => new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
  const stream = (events, signal, keepOpen = false) => new Response(new ReadableStream({
    start(controller) {
      const encoder = new TextEncoder();
      const timers = [];
      let closed = false;
      const later = (callback, delay) => timers.push(window.setTimeout(callback, delay));
      const cleanup = () => { for (const timer of timers) window.clearTimeout(timer); };
      const close = () => {
        if (closed) return;
        closed = true;
        cleanup();
        controller.close();
      };
      let elapsed = 0;
      for (const item of events) {
        elapsed += item.after;
        later(() => {
          if (closed) return;
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(item.event)}\n\n`));
        }, elapsed);
      }
      if (!keepOpen) later(close, elapsed + 20);
      if (signal) {
        signal.addEventListener("abort", () => {
          if (closed) return;
          closed = true;
          cleanup();
          controller.error(new DOMException("fixture stream cancelled", "AbortError"));
        }, { once: true });
      }
    },
  }), { status: 200, headers: { "Content-Type": "text/event-stream" } });
  state.fixtureFetch = (input, init) => {
    const rawUrl = typeof input === "string" ? input : input.url;
    const url = new URL(rawUrl, window.location.href);
    const method = (init && init.method) || (input instanceof Request ? input.method : "GET");
    const signal = (init && init.signal) || (input instanceof Request ? input.signal : undefined);
    if (method === "POST" && url.pathname === rebuildPath) {
      hit("rebuild");
      state.rebuildCount += 1;
      return Promise.resolve(json({ jobId: state.rebuildCount === 1 ? "fixture-job-failed" : "fixture-job-cancelled" }, 202));
    }
    if (method === "DELETE" && url.pathname === deletePath) {
      hit("delete");
      return Promise.resolve(json({ jobId: "fixture-job-delete" }, 202));
    }
    if (method === "GET" && url.pathname === "/api/v1/jobs/fixture-job-failed") {
      hit("job_failed");
      const count = state.hits.job_failed;
      return Promise.resolve(json({
        status: count === 1 ? "running" : "failed",
        progress: count === 1 ? 1 : null,
        errorCode: count === 1 ? null : "FIXTURE_FAILED",
        updatedAt: "2026-01-01T00:00:00.000Z",
      }));
    }
    if (method === "GET" && url.pathname === "/api/v1/jobs/fixture-job-cancelled") {
      hit("job_cancelled");
      state.cancelledPollCount += 1;
      if (state.cancelledPollCount === 1) {
        return Promise.resolve(json({ error: { code: "INTERNAL", message: "fixture poll failure" } }, 503));
      }
      return Promise.resolve(json({
        status: "cancelled",
        progress: null,
        errorCode: null,
        updatedAt: "2026-01-01T00:00:00.000Z",
      }));
    }
    if (method === "GET" && url.pathname === "/api/v1/jobs/fixture-job-delete") {
      hit("job_delete");
      state.deletePollCount += 1;
      return Promise.resolve(json({
        status: state.deletePollCount === 1 ? "queued" : "succeeded",
        progress: null,
        errorCode: null,
        updatedAt: "2026-01-01T00:00:00.000Z",
      }));
    }
    if (method === "POST" && url.pathname === "/api/v1/rag/query") {
      hit("rag");
      state.ragCount += 1;
      if (state.ragCount <= 4) {
        let conversationId = null;
        try {
          const body = typeof init?.body === "string" ? JSON.parse(init.body) : null;
          conversationId = typeof body?.conversationId === "string" ? body.conversationId : null;
        } catch {
          state.firstFourSameNonEmptyConversation = false;
        }
        if (!conversationId) {
          state.firstFourSameNonEmptyConversation = false;
        } else if (state.firstFourConversationId === null) {
          state.firstFourConversationId = conversationId;
        } else if (state.firstFourConversationId !== conversationId) {
          state.firstFourSameNonEmptyConversation = false;
        }
      }
      if (state.ragCount === 1) {
        return Promise.resolve(stream([
          { after: 0, event: { event: "stage", stage: "retrieving" } },
          { after: 80, event: { event: "heartbeat", ts: 99 } },
          { after: 20, event: { event: "token", token: "fixture-partial" } },
          { after: 60, event: { event: "error", code: "FIXTURE", message: "FIXTURE_RAW_SSE_CANARY" } },
        ], signal));
      }
      if (state.ragCount === 2) {
        return Promise.resolve(stream([
          { after: 0, event: { event: "stage", stage: "generating" } },
          { after: 20, event: { event: "token", token: "fixture-no-done" } },
        ], signal));
      }
      if (state.ragCount === 3) {
        // 先给出 partial token，再保持 reader pending；AbortSignal 必须清 timer 并以 AbortError 终止流。
        return Promise.resolve(stream([
          { after: 0, event: { event: "stage", stage: "generating" } },
          { after: 20, event: { event: "token", token: "fixture-cancel-partial" } },
        ], signal, true));
      }
      if (state.ragCount === 4) {
        return Promise.resolve(stream([
          { after: 0, event: { event: "stage", stage: "retrieving" } },
          { after: 60, event: { event: "heartbeat", ts: 100 } },
          { after: 20, event: { event: "token", token: "fixture-retry-success" } },
          { after: 20, event: { event: "citation", citation: { documentId: "fixture-doc", documentName: "fixture-source", version: 1, chunkId: "fixture-chunk", snippet: "fixture-snippet" } } },
          { after: 20, event: { event: "done", messageId: "fixture-message", citations: [{ documentId: "fixture-doc", documentName: "fixture-source", version: 1, chunkId: "fixture-chunk", snippet: "fixture-snippet" }] } },
        ], signal));
      }
      // 第五次仅用于切换/卸载后的 stale 回调防护，永不完成，直到 AbortSignal 收到为止。
      return Promise.resolve(stream([
        { after: 0, event: { event: "stage", stage: "generating" } },
        { after: 20, event: { event: "token", token: "fixture-stale-token" } },
        { after: 160, event: { event: "error", code: "FIXTURE", message: "FIXTURE_RAW_SSE_CANARY" } },
      ], signal, true));
    }
    return originalFetch(input, init);
  };
  window.fetch = state.fixtureFetch;
  window.__starIsleKbRagFixture = state;
  return window.fetch === state.fixtureFetch;
}
"""

FIXTURE_FETCH_STATS = r"""() => {
  const state = window.__starIsleKbRagFixture;
  return state ? {
    hits: state.hits,
    patched: window.fetch === state.fixtureFetch,
    firstFourSameNonEmptyConversation: state.ragCount >= 4 && state.firstFourSameNonEmptyConversation,
  } : null;
}"""

FIXTURE_FETCH_RESTORE = r"""() => {
  const state = window.__starIsleKbRagFixture;
  if (!state) return false;
  const wasPatched = window.fetch === state.fixtureFetch;
  window.fetch = state.originalFetch;
  const restored = window.fetch === state.originalFetch;
  delete window.__starIsleKbRagFixture;
  return wasPatched && restored;
}"""


class FixtureFetchScope:
    """Narrow, page-local fetch replacement with an explicit restoration assertion."""

    def __init__(self, page, rebuild_path: str, delete_path: str) -> None:
        self._page = page
        self._restored = False
        installed = page.evaluate(
            FIXTURE_FETCH_INSTALL,
            {"rebuildPath": rebuild_path, "deletePath": delete_path},
        )
        if not installed:
            raise BrowserCleanupError("fixture fetch install failed")

    def stats(self) -> dict[str, object]:
        stats = self._page.evaluate(FIXTURE_FETCH_STATS)
        if not isinstance(stats, dict):
            raise BrowserCleanupError("fixture fetch state missing")
        return stats

    def restore(self) -> None:
        if self._restored:
            return
        restored = self._page.evaluate(FIXTURE_FETCH_RESTORE)
        self._restored = True
        if restored is not True:
            raise BrowserCleanupError("fixture fetch restore failed")


@contextmanager
def fixture_fetch_context(page, rebuild_path: str, delete_path: str):
    """Restore native fetch in finally even when a browser assertion raises."""

    scope = FixtureFetchScope(page, rebuild_path, delete_path)
    try:
        yield scope
    finally:
        scope.restore()


@contextmanager
def conversation_pagination_fixture_context(page):
    """Route only deterministic conversation pages, then remove the route in finally.

    This fixture runs after the real upload/worker/RAG path.  It never writes a
    database and only answers the fixed browser requests needed to exercise
    ChatWindow's client-side pagination behavior.
    """

    route_pattern = "**/api/v1/conversations**"
    rag_route_pattern = "**/api/v1/rag/query"
    calls: Counter[str] = Counter()
    latest_older_marker = "fixture-page-latest-older"
    latest_newer_marker = "fixture-page-latest-newer"
    oldest_marker = "fixture-page-oldest"
    middle_marker = "fixture-page-middle"
    filler = " 可滚动分页锚点内容"

    list_first = {
        "items": [
            {"id": "fixture-conv-3", "title": "分页会话三", "createdAt": "2026-09-04T12:00:03+00:00"},
            {"id": "fixture-conv-2", "title": "分页会话二", "createdAt": "2026-09-04T12:00:02+00:00"},
        ],
        "nextCursor": "fixture-list-page-2",
    }
    list_second = {
        "items": [{"id": "fixture-conv-1", "title": "分页会话一", "createdAt": "2026-09-04T12:00:01+00:00"}],
        "nextCursor": None,
    }
    latest_messages = {
        "id": "fixture-conv-3",
        "title": "分页会话三",
        "createdAt": "2026-09-04T12:00:03+00:00",
        "messages": [
            {
                "id": "fixture-message-latest-older",
                "role": "user",
                "content": latest_older_marker + filler * 90,
                "citations": [],
                "createdAt": "2026-09-04T12:00:04+00:00",
            },
            {
                "id": "fixture-message-latest-newer",
                "role": "assistant",
                "content": latest_newer_marker + filler * 90,
                "citations": [],
                "createdAt": "2026-09-04T12:00:05+00:00",
            },
        ],
        "nextCursor": "fixture-history-page-2",
    }
    older_messages = {
        "id": "fixture-conv-3",
        "title": "分页会话三",
        "createdAt": "2026-09-04T12:00:03+00:00",
        "messages": [
            {
                "id": "fixture-message-oldest",
                "role": "user",
                "content": oldest_marker + filler * 90,
                "citations": [],
                "createdAt": "2026-09-04T12:00:02+00:00",
            },
            {
                "id": "fixture-message-middle",
                "role": "user",
                "content": middle_marker + filler * 90,
                "citations": [],
                "createdAt": "2026-09-04T12:00:03+00:00",
            },
        ],
        "nextCursor": None,
    }
    rag_fallback = (
        'data: {"event":"token","token":"fixture-concurrency-fallback"}\n\n'
        'data: {"event":"done","messageId":"fixture-concurrency-fallback","citations":[]}\n\n'
    )

    def fulfill(route, body: dict[str, object], name: str) -> None:
        calls[name] += 1
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(body, ensure_ascii=False),
        )

    def handle(route) -> None:
        request = route.request
        parsed = urlparse(request.url)
        if request.method != "GET":
            route.continue_()
            return
        cursor = parse_qs(parsed.query, keep_blank_values=True).get("cursor", [None])[0]
        if parsed.path == "/api/v1/conversations":
            if cursor is None:
                fulfill(route, list_first, "list_first")
                return
            if cursor == "fixture-list-page-2":
                fulfill(route, list_second, "list_second")
                return
        if parsed.path == "/api/v1/conversations/fixture-conv-3":
            if cursor is None:
                fulfill(route, latest_messages, "messages_latest")
                return
            if cursor == "fixture-history-page-2":
                fulfill(route, older_messages, "messages_older")
                return
        route.continue_()

    def handle_rag(route) -> None:
        if route.request.method != "POST":
            route.continue_()
            return
        # 错误实现若真的发送 RAG，也只由本地确定性 SSE 收口，绝不触及真实 API/DB。
        calls["rag_during_history"] += 1
        route.fulfill(
            status=200,
            content_type="text/event-stream",
            body=rag_fallback,
        )

    page.route(route_pattern, handle)
    page.route(rag_route_pattern, handle_rag)
    try:
        yield {
            "calls": calls,
            "latestOlder": latest_older_marker,
            "latestNewer": latest_newer_marker,
            "oldest": oldest_marker,
            "middle": middle_marker,
        }
    finally:
        page.unroute(rag_route_pattern, handle_rag)
        page.unroute(route_pattern, handle)


@contextmanager
def document_pagination_fixture_context(page):
    """Route two deterministic document pages without writing the test database."""

    route_pattern = "**/api/v1/knowledge/documents**"
    calls: Counter[str] = Counter()

    def document(index: int) -> dict[str, object]:
        return {
            "id": f"fixture-document-{index:03d}",
            "title": f"分页文档 {index}",
            "documentType": "txt",
            "version": 1,
            "hash": "fixture-hash",
            "indexState": "ready",
            "indexErrorCode": None,
            "createdAt": f"2026-09-05T10:00:{index:02d}+00:00",
        }

    first_page = {
        "items": [document(index) for index in range(21, 1, -1)],
        "nextCursor": "fixture-doc-page-2",
    }
    # The overlap proves client-side append de-duplication rather than just a second render.
    second_page = {"items": [document(2), document(1)], "nextCursor": None}

    def handle(route) -> None:
        request = route.request
        if request.method != "GET":
            route.continue_()
            return
        cursor = parse_qs(urlparse(request.url).query).get("cursor")
        if cursor is None:
            calls["first"] += 1
            route.fulfill(status=200, content_type="application/json", body=json.dumps(first_page))
            return
        if cursor == ["fixture-doc-page-2"]:
            calls["second"] += 1
            route.fulfill(status=200, content_type="application/json", body=json.dumps(second_page))
            return
        route.continue_()

    page.route(route_pattern, handle)
    try:
        yield calls
    finally:
        page.unroute(route_pattern, handle)


def mail_recovery_browser_checks(page, base: str) -> None:
    """Client-only recovery fixture after real mail ingestion/read checks succeed."""
    pattern = "**/api/v1/mail**"
    calls: Counter[str] = Counter()

    def mail(index):
        return {"id": f"fixture-recovery-mail-{index}", "from": "fixture@example.com",
                "subject": f"恢复邮件 {index}", "receivedAt": "2026-11-04T00:00:00Z", "read": True}

    def handle(route):
        request = route.request
        parsed = urlparse(request.url)
        if request.method == "GET" and parsed.path == "/api/v1/mail":
            cursor = parse_qs(parsed.query).get("cursor", [None])[0]
            if cursor == "fixture-mail-next":
                calls["append"] += 1
                if calls["append"] == 1:
                    route.fulfill(status=503, json={"error": {"code": "INTERNAL", "message": "fixture page failure"}})
                    return
                payload = {"items": [mail(2), mail(1)], "nextCursor": None}
            elif cursor is None:
                calls["head"] += 1
                if calls["head"] == 2:
                    route.fulfill(status=503, json={"error": {"code": "INTERNAL", "message": "fixture refresh failure"}})
                    return
                payload = {"items": [mail(3), mail(2)], "nextCursor": "fixture-mail-next"}
            else:
                route.continue_()
                return
            route.fulfill(status=200, json=payload)
        elif parsed.path.startswith("/api/v1/mail/fixture-recovery-mail-"):
            if request.method != "GET" or parsed.path.endswith("/read"):
                calls["unexpected_write"] += 1
                route.fulfill(status=500, json={"error": {"code": "INTERNAL", "message": "fixture write blocked"}})
            elif parsed.path.endswith("-3"):
                calls["detail_error"] += 1
                route.fulfill(status=503, json={"error": {"code": "INTERNAL", "message": "fixture detail failure"}})
            else:
                calls["detail_ok"] += 1
                route.fulfill(status=200, json={**mail(1), "to": "owner@example.com",
                    "textBody": "fixture-readable-mail-body", "htmlSummary": None, "attachments": []})
        else:
            route.continue_()

    page.route(pattern, handle)
    try:
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(base + "/console/mail", wait_until="networkidle")
        items = page.get_by_test_id("mail-item")
        expect(items).to_have_count(2)
        with allow_expected_error_responses(("GET", "/api/v1/mail", 503)):
            page.get_by_role("button", name="加载更多", exact=True).click()
            expect(page.get_by_role("button", name="重试加载更多", exact=True)).to_be_visible()
            check(items.count() == 2, "fixture_mail_page_failure_keeps_existing_rows")
        page.get_by_role("button", name="重试加载更多", exact=True).click()
        expect(items).to_have_count(3)
        check(calls["append"] == 2, "fixture_mail_page_retry_uses_same_cursor_without_duplicates")
        with allow_expected_error_responses(("GET", "/api/v1/mail", 503)):
            page.get_by_role("button", name="刷新", exact=True).click()
            expect(page.get_by_role("button", name="重试刷新", exact=True)).to_be_visible()
            check(items.count() == 3, "fixture_mail_refresh_failure_keeps_existing_rows")
        with allow_expected_error_responses(("GET", "/api/v1/mail/fixture-recovery-mail-3", 503)):
            items.filter(has_text="恢复邮件 3").click()
            expect(page.get_by_text("邮件内容加载失败,请重试。", exact=True)).to_be_visible()
            back = page.get_by_role("button", name="← 返回列表", exact=True)
            expect(back).to_be_visible()
            back.press("Enter")
            expect(items.filter(has_text="恢复邮件 3")).to_be_visible()
            check(True, "fixture_mobile_mail_detail_failure_returns_to_existing_list")
        items.filter(has_text="恢复邮件 1").click()
        expect(page.get_by_text("fixture-readable-mail-body", exact=True)).to_be_visible()
        check(True, "fixture_mobile_mail_can_read_another_message_after_failure")
        page.get_by_role("button", name="← 返回列表", exact=True).click()
        page.get_by_role("button", name="重试刷新", exact=True).click()
        expect(page.get_by_role("button", name="刷新", exact=True)).to_be_enabled()
        check(items.count() == 3 and page.get_by_role("button", name="加载更多", exact=True).count() == 0,
              "fixture_mail_refresh_retry_preserves_rows_and_exhausted_cursor")
        check(calls["unexpected_write"] == 0 and calls["detail_ok"] == 1 and calls["detail_error"] == 1,
              "fixture_mail_requests_are_read_only_and_expected")
    finally:
        page.unroute(pattern, handle)


def photo_recovery_browser_checks(page, base: str, sample_png: Path) -> None:
    """Synthetic list/mutations exercise UI only; original upload/worker paths stay real."""
    payload = page.request.get(base + "/api/v1/photos").json()
    thumbnail_url = payload["items"][0]["thumbnailUrl"]
    pattern = "**/api/v1/photos**"
    calls: Counter[str] = Counter()

    def photo(index):
        return {"id": f"fixture-recovery-photo-{index}", "thumbnailUrl": thumbnail_url,
                "captureAt": f"2026-11-0{index + 1}T12:00:00", "width": 1, "height": 1}

    def handle(route):
        request = route.request
        parsed = urlparse(request.url)
        if parsed.path == "/api/v1/photos" and request.method == "POST":
            calls["upload"] += 1
            route.fulfill(status=201, json={"id": "fixture-recovery-photo-4", "status": "ready", "thumbnailState": "ready"})
        elif parsed.path == "/api/v1/photos" and request.method == "GET":
            cursor = parse_qs(parsed.query).get("cursor", [None])[0]
            if cursor == "fixture-photo-next":
                calls["append"] += 1
                response = {"items": [photo(2), photo(1)], "nextCursor": "fixture-photo-final"}
            elif cursor == "fixture-photo-final":
                calls["final"] += 1
                response = {"items": [photo(0)], "nextCursor": None}
            elif cursor is None:
                calls["head"] += 1
                if calls["delete"] and calls["head"] == 3:
                    route.fulfill(status=503, json={"error": {"code": "INTERNAL", "message": "fixture refresh failure"}})
                    return
                response = {"items": [photo(4), photo(3), photo(2)] if calls["upload"] else [photo(3), photo(2)],
                            "nextCursor": "fixture-photo-next"}
            else:
                route.continue_()
                return
            route.fulfill(status=200, json=response)
        elif parsed.path.startswith("/api/v1/photos/fixture-recovery-photo-"):
            if request.method == "DELETE" and parsed.path.endswith("-2"):
                calls["delete"] += 1
                route.fulfill(status=204)
            else:
                calls["unexpected_mutation"] += 1
                route.fulfill(status=500, json={"error": {"code": "INTERNAL", "message": "fixture mutation blocked"}})
        else:
            route.continue_()

    page.route(pattern, handle)
    try:
        page.goto(base + "/console/gallery", wait_until="networkidle")
        images = page.locator("img[alt^='照片 ']")
        expect(images).to_have_count(2)
        page.get_by_role("button", name="加载更多", exact=True).click()
        expect(images).to_have_count(3)
        page.locator("input[type=file]").first.set_input_files(str(sample_png))
        expect(images).to_have_count(4)
        expect(page.get_by_role("img", name="照片 2026/11/02", exact=True)).to_be_visible()
        check(True, "fixture_photo_upload_refresh_preserves_browsed_tail_without_duplicates")
        # Uploads can enter an already-browsed EXIF range, so rescan the server's
        # current head cursor while keeping the rendered tail and de-duplicating.
        with page.expect_response(
            lambda response: (
                response.request.method == "GET"
                and urlparse(response.url).path == "/api/v1/photos"
                and parse_qs(urlparse(response.url).query).get("cursor") == ["fixture-photo-next"]
                and response.status == 200
            )
        ):
            page.get_by_role("button", name="加载更多", exact=True).click()
        expect(page.get_by_role("button", name="加载更多", exact=True)).to_be_enabled()
        expect(images).to_have_count(4)
        check(calls["append"] == 2, "fixture_photo_upload_cursor_rescan_deduplicates_existing_rows")
        target = page.get_by_role("img", name="照片 2026/11/03", exact=True).locator("..").get_by_role("button", name="删除这张照片", exact=True)
        target.click()
        with allow_expected_error_responses(("GET", "/api/v1/photos", 503)):
            page.get_by_role("button", name="确认删除", exact=True).click()
            expect(page.get_by_role("button", name="重试刷新", exact=True)).to_be_visible()
            expect(images).to_have_count(3)
            check(True, "fixture_photo_delete_refresh_failure_keeps_survivors_visible")
        page.get_by_role("button", name="重试刷新", exact=True).click()
        expect(page.get_by_role("button", name="重试刷新", exact=True)).to_have_count(0)
        expect(images).to_have_count(3)
        check(page.get_by_role("img", name="照片 2026/11/03", exact=True).count() == 0,
              "fixture_photo_refresh_retry_does_not_resurrect_deleted_photo")
        page.get_by_role("button", name="加载更多", exact=True).click()
        expect(images).to_have_count(4)
        expect(page.get_by_role("button", name="加载更多", exact=True)).to_have_count(0)
        check(calls["append"] == 2 and calls["final"] == 1,
              "fixture_photo_refresh_preserves_advanced_cursor_and_exhaustion")
        check(calls["upload"] == 1 and calls["delete"] == 1 and calls["unexpected_mutation"] == 0,
              "fixture_photo_mutations_are_fully_intercepted_once")
        page.wait_for_load_state("networkidle")
    finally:
        page.unroute(pattern, handle)


def record_console_error(msg) -> None:
    # HTTP 状态失败由 response 监听器按 URL/方法/状态精确判断；这里仅保留实际 JS console error。
    if msg.type == "error" and not msg.text.startswith("Failed to load resource: the server responded with a status of"):
        PAGE_ERRORS.append("console_error")


def _is_browser_stream_cleanup_noise(err) -> bool:
    # Chromium 在 SSE/fetch 流 abort 或 error 后,内部 ReadableStream 清理会异步调用
    # ReadableStreamDefaultController.close() 并对已 errored 的流抛出二次错误。该错误不来自
    # 用户代码(streamLines 的 reader.cancel() 已 try/catch),不影响功能,仅在较新 Chromium 暴露。
    text = str(err)
    return "ReadableStreamDefaultController" in text and "Cannot close an errored readable stream" in text



def record_public_api_request(request) -> None:
    if PUBLIC_STAGE_ACTIVE and _internal_pathname(request.url).startswith("/api/v1/"):
        PUBLIC_API_REQUESTS.append(safe_request_label(request.method, request.url))


def missing_aria_labelledby_ids(page) -> list[str]:
    return page.locator("[aria-labelledby]").evaluate_all(
        """
        elements => elements.flatMap((element) =>
          (element.getAttribute("aria-labelledby") || "")
            .trim()
            .split(/\\s+/)
            .filter(Boolean)
            .filter((id) => !document.getElementById(id))
            .map((id) => `${element.tagName.toLowerCase()} -> #${id}`)
        )
        """
    )


def record_response(response) -> None:
    request = response.request
    raw_path = _internal_pathname(response.url)
    _RAW_RESPONSE_COUNTS[(request.method, raw_path)] += 1
    safe_method, safe_path = safe_request_label(request.method, response.url).split(" ", 1)
    RESPONSE_COUNTS[(safe_method, safe_path)] += 1
    if response.status < 400:
        # 保存对象引用并以 is 判断，避免 id 重用，也绝不按 method/URL 跨请求关联。
        COMPLETED_REQUESTS[id(request)] = request
        return
    key = (request.method, raw_path, response.status)
    if key not in ACTIVE_EXPECTED_ERROR_RESPONSES:
        FAILED_REQUESTS.append(f"{safe_request_label(request.method, response.url)} HTTP {response.status}")


def record_failed_request(req) -> None:
    failure = req.failure or ""
    path = _internal_pathname(req.url)
    # 已收到同一请求的成功响应后出现的 abort 是后续导航的浏览器事件，不是请求失败。
    if COMPLETED_REQUESTS.get(id(req)) is req:
        return
    # 仅忽略导航期间 Vite 开发态模块的主动取消；API 或静态资源取消仍计入回归失败。
    if failure == "net::ERR_ABORTED" and path.startswith("/src/"):
        return
    FAILED_REQUESTS.append(f"{safe_request_label(req.method, req.url)} request_failed")


def public_native_interaction_checks(page, base: str) -> None:
    """静态预览可运行的公开页渐进增强回归，不依赖 API 或容器。"""
    print("== 公开页原生交互 ==")
    page.goto(base + "/", wait_until="networkidle")
    if "/enter" in page.url:
        page.evaluate("sessionStorage.setItem('star-isle:enter-completed', 'true')")
        page.goto(base + "/", wait_until="networkidle")
    page.evaluate(
        """
        () => {
          localStorage.removeItem("star-isle:theme");
          localStorage.removeItem("star-isle:background-settings");
        }
        """
    )
    page.reload(wait_until="networkidle")

    theme = page.get_by_role("button", name="切换到亮色主题")
    theme.click()
    check(page.locator("html").get_attribute("data-theme") == "light", "主题切换为亮色")
    check(
        page.evaluate("localStorage.getItem('star-isle:theme')") == "light",
        "主题写入 star-isle:theme",
    )
    page.reload(wait_until="networkidle")
    check(page.locator("html").get_attribute("data-theme") == "light", "主题刷新后恢复")

    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(base + "/", wait_until="networkidle")
    page.get_by_role("button", name="打开导航菜单").click()
    nav_dialog = page.get_by_role("dialog", name="夜航目录")
    nav_dialog.wait_for(state="visible")
    check(nav_dialog.is_visible(), "移动导航对话框打开")
    check(
        page.get_by_role("button", name="打开导航菜单").get_attribute("aria-expanded") == "true",
        "移动导航 aria-expanded 同步",
    )
    check(
        page.locator("[data-public-nav-panel]").evaluate("panel => panel.contains(document.activeElement)"),
        "移动导航打开后焦点进入对话框",
    )
    page.keyboard.press("Shift+Tab")
    check(
        page.locator("[data-public-nav-panel]").evaluate("panel => panel.contains(document.activeElement)"),
        "移动导航 Shift+Tab 焦点圈闭",
    )
    page.keyboard.press("Escape")
    check(not nav_dialog.is_visible(), "移动导航 Escape 关闭")
    check(
        page.evaluate("document.activeElement?.getAttribute('data-public-nav-trigger') === ''"),
        "移动导航关闭后恢复触发器焦点",
    )

    page.get_by_role("button", name="打开导航菜单").click()
    page.set_viewport_size({"width": 1440, "height": 900})
    expect(nav_dialog).to_be_hidden()
    check(not page.locator("body").evaluate("body => body.classList.contains('public-modal-open')"), "mobile_nav_resize_releases_scroll_lock")
    check(page.locator("[data-public-desktop-nav]").evaluate("nav => nav.contains(document.activeElement)"), "mobile_nav_resize_restores_visible_focus")
    page.set_viewport_size({"width": 390, "height": 844})

    background_button = page.get_by_role("button", name="背景设置")
    background_button.click()
    background_dialog = page.get_by_role("dialog", name="自定义背景")
    check(background_dialog.is_visible(), "背景对话框打开")
    page.get_by_role("button", name="使用预设背景：深蓝缎面", exact=True).click()
    background_settings = page.evaluate("JSON.parse(localStorage.getItem('star-isle:background-settings'))")
    check(
        background_settings.get("url") == "/presets/backgrounds/starry-night.svg" and not background_settings.get("custom"),
        "背景预设与设置持久化",
    )
    page.get_by_label("选择背景图片").set_input_files(
        {"name": "background.png", "mimeType": "image/png", "buffer": PNG_1PX}
    )
    page.wait_for_function(
        """
        () => {
          try { return JSON.parse(localStorage.getItem('star-isle:background-settings')).custom === true; }
          catch { return false; }
        }
        """
    )
    check(
        not page.locator("[data-public-background-layer]").evaluate("layer => layer.hidden"),
        "上传背景显示且不依赖 Dexie",
    )
    page.get_by_role("button", name="恢复默认").click()
    check(
        page.evaluate("JSON.parse(localStorage.getItem('star-isle:background-settings')).custom") is False,
        "背景重置清除自定义状态",
    )
    page.keyboard.press("Escape")
    check(not background_dialog.is_visible(), "背景对话框 Escape 关闭")
    check(
        page.evaluate("document.activeElement?.id === 'background'"),
        "背景对话框关闭后恢复触发器焦点",
    )

    player = page.locator("[data-public-player]")
    if player.count():
        tracks = json.loads(player.get_attribute("data-playlist") or "[]")
        check(bool(tracks), "已挂载播放器具有真实歌单")
        page.locator("[data-player-open]").first.click()
        player_dialog = page.get_by_role("dialog", name="歌单")
        check(player_dialog.is_visible(), "歌单对话框打开")
        page.keyboard.press("Escape")
        check(not player_dialog.is_visible(), "歌单 Escape 关闭")
        check(
            page.evaluate("document.activeElement?.hasAttribute('data-player-open')"),
            "歌单关闭后恢复触发器焦点",
        )
    else:
        check(page.locator("[data-open-public-playlist]").count() == 0, "空歌单隐藏导航播放器入口")
        check(page.locator('a[href="/#player"]').count() == 0, "空歌单隐藏模块播放器入口")
        check(page.get_by_role("dialog", name="歌单").count() == 0, "空歌单不挂载播放器对话框")

    page.goto(base + "/projects/", wait_until="networkidle")
    grid = page.locator("[data-public-repo-grid]")
    if grid.count():
        cards = grid.locator("[data-repo-card]")
        total = cards.count()
        for source in ("github", "gitee"):
            button = grid.locator(f'button[data-repo-source="{source}"]')
            button.click()
            expected = grid.locator(f'[data-repo-card][data-repo-source="{source}"]').count()
            expect(grid.locator("[data-repo-card]:visible")).to_have_count(expected)
            check(button.get_attribute("aria-pressed") == "true", f"仓库来源筛选_{source}")
            check(grid.locator("[data-repo-empty]").is_visible() == (expected == 0), f"仓库筛选空态_{source}")
        grid.locator('button[data-repo-source="all"]').click()
        expect(grid.locator("[data-repo-card]:visible")).to_have_count(total)
        grid.locator('button[data-repo-sort="updated"]').click()
        dates = grid.locator("[data-repo-card]:visible").evaluate_all(
            "cards => cards.map(card => Date.parse(card.dataset.repoUpdatedAt) || 0)"
        )
        check(dates == sorted(dates, reverse=True), "仓库按最近更新时间排序")
        check(grid.locator("[data-repo-empty]").is_visible() == (total == 0), "仓库全部来源空态正确")
    else:
        check(page.get_by_text("暂未配置仓库来源", exact=True).is_visible(), "未配置来源时展示仓库空态")


def public_only_result() -> int:
    print("== 公开页错误观察 ==")
    check(len(PAGE_ERRORS) == 0, "公开页无页面 JS 错误")
    check(len(FAILED_REQUESTS) == 0, "公开页无失败请求")
    print("\n=== 结果 ===")
    print(f"公开页回归失败:{len(FAILURES)} 项")
    if FAILURES:
        print("\n".join(FAILURES))
    return 1 if FAILURES else 0


def _reset_results() -> None:
    global PUBLIC_STAGE_ACTIVE
    FAILURES.clear()
    PAGE_ERRORS.clear()
    FAILED_REQUESTS.clear()
    PUBLIC_API_REQUESTS.clear()
    COMPLETED_REQUESTS.clear()
    RESPONSE_COUNTS.clear()
    _RAW_RESPONSE_COUNTS.clear()
    ACTIVE_EXPECTED_ERROR_RESPONSES.clear()
    PUBLIC_STAGE_ACTIVE = False


@contextmanager
def _evidence_directory(args):
    """Yield an owned temporary directory or an exclusive explicit opt-in."""

    explicit = args.evidence_dir or os.environ.get("STAR_ISLE_E2E_EVIDENCE")
    if explicit:
        protected_roots = [Path(__file__).resolve().parents[1]]
        protected_roots.extend(
            Path(value)
            for value in (os.environ.get("DATA_ROOT"), os.environ.get("DATA_HOST_ROOT"))
            if value and value.strip()
        )
        evidence = new_temporary_directory_path(explicit, protected_roots=tuple(protected_roots))
        evidence.mkdir(mode=0o700)
        yield evidence
        return
    with owned_temporary_directory("starisle-browser-evidence-") as temporary:
        yield Path(temporary)


def _receipt_protected_roots() -> tuple[Path, ...]:
    """Return every live/root location that must never hold a private receipt."""

    repository_root = Path(__file__).resolve().parents[1]
    roots = [repository_root, repository_root / "data"]
    roots.extend(
        Path(value)
        for value in (os.environ.get("DATA_ROOT"), os.environ.get("DATA_HOST_ROOT"))
        if value and value.strip()
    )
    return tuple(roots)


@contextmanager
def _mail_receipt_file(args):
    """Yield a private, exclusive receipt exchange file path owned by this harness."""

    supplied = getattr(args, "owned_mail_receipt", None)
    temporary_owner = None
    receipt: Path | None = None
    try:
        if supplied:
            receipt = new_temporary_file_path(supplied, protected_roots=_receipt_protected_roots())
        else:
            temporary_owner = owned_temporary_directory("starisle-browser-mail-")
            receipt = new_temporary_file_path(
                Path(temporary_owner.name) / "mail-receipt.json",
                protected_roots=_receipt_protected_roots(),
            )
        yield receipt
    finally:
        cleanup_ok = True
        if receipt is not None and (receipt.exists() or receipt.is_symlink()):
            try:
                receipt.unlink()
            except OSError:
                cleanup_ok = False
        if temporary_owner is not None:
            try:
                temporary_owner.cleanup()
            except OSError:
                cleanup_ok = False
        if not cleanup_ok:
            raise HarnessSafetyError("mail receipt cleanup failed") from None


def _screenshot(page, name: str) -> None:
    if EVIDENCE is not None:
        page.screenshot(path=EVIDENCE / name)


def _safe_summary() -> dict[str, object]:
    return {
        "failures": list(FAILURES),
        "page_errors": list(PAGE_ERRORS),
        "failed_requests": list(FAILED_REQUESTS),
        "public_api_requests": list(PUBLIC_API_REQUESTS),
        "response_counts": {
            f"{method} {path}": count for (method, path), count in sorted(RESPONSE_COUNTS.items())
        },
    }


def _isolated_exit_code(
    child_result: int,
    child_stopped: bool,
    raw_verified: bool,
    ports_released: bool,
    api_stopped: bool,
    worker_stopped: bool,
    web_stopped: bool,
    logs_closed: bool,
) -> int:
    return 0 if (
        child_result == 0
        and child_stopped
        and raw_verified
        and ports_released
        and api_stopped
        and worker_stopped
        and web_stopped
        and logs_closed
    ) else 1


def console_tear_interaction_checks(page, base: str) -> None:
    """Exercise the authenticated Console-only tear without fixture APIs."""

    panel = page.locator(".tear-portal-link")
    panel.wait_for(state="visible", timeout=15_000)
    expect(panel).to_have_count(1)
    handle = page.get_by_role("button", name="向右撕开纸页前往星", exact=True)
    fallback = panel.get_by_role("link", name="普通前往星", exact=True)
    panel_box = panel.bounding_box()
    handle_box = handle.bounding_box()
    check(
        fallback.is_visible() and fallback.get_attribute("href") == "/",
        "console_tear_native_fallback_visible",
    )
    check(
        panel_box is not None
        and handle_box is not None
        and handle_box["width"] >= 44
        and handle_box["height"] >= 44,
        "console_tear_semantic_handle_min_target",
    )
    if panel_box is None or handle_box is None:
        return

    start_x = handle_box["x"] + handle_box["width"] / 2
    start_y = handle_box["y"] + handle_box["height"] / 2
    travel = max(120, panel_box["width"] - handle_box["width"] - 8)

    # Below-threshold drag must roll back, and its synthetic click must not
    # turn that failed gesture into a navigation.
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    page.mouse.move(start_x + travel * 0.35, start_y, steps=4)
    expect(panel).to_have_attribute("data-phase", "tearing")
    page.mouse.up()
    expect(panel).to_have_attribute("data-phase", "isleIdle", timeout=1_000)
    check(page.url == base + "/console", "console_tear_below_threshold_rolls_back")

    # Escape releases capture and restores focus without navigation.
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    page.mouse.move(start_x + travel * 0.5, start_y, steps=4)
    page.keyboard.press("Escape")
    page.mouse.up()
    expect(panel).to_have_attribute("data-phase", "isleIdle", timeout=1_000)
    expect(handle).to_be_focused(timeout=1_000)
    check(page.url == base + "/console", "console_tear_escape_rolls_back")

    # A completed tear enters its committed visual state before one native
    # document navigation to the public root.
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    page.mouse.move(start_x + travel * 0.72, start_y, steps=5)
    page.mouse.up()
    expect(panel).to_have_attribute("data-phase", "starCommit", timeout=1_000)
    page.wait_for_url(base + "/", timeout=5_000)
    check(page.url == base + "/", "console_tear_commit_uses_root_navigation")

    page.goto(base + "/console", wait_until="networkidle")
    panel.wait_for(state="visible", timeout=15_000)
    handle = page.get_by_role("button", name="向右撕开纸页前往星", exact=True)

    # Reduced motion skips continuous tracking while retaining keyboard
    # activation and the same native destination.
    page.emulate_media(reduced_motion="reduce")
    try:
        expect(panel).to_have_attribute("data-motion", "reduced", timeout=1_000)
        handle.focus()
        page.keyboard.press("Enter")
        expect(panel).to_have_attribute("data-phase", "starCommit", timeout=1_000)
        page.wait_for_url(base + "/", timeout=5_000)
        check(True, "console_tear_reduced_motion_keyboard_navigation")
    finally:
        page.emulate_media(reduced_motion="no-preference")

    page.goto(base + "/console", wait_until="networkidle")
    panel.wait_for(state="visible", timeout=15_000)
    page.emulate_media(forced_colors="active")
    try:
        viewport_display = panel.locator(".tear-portal-link__viewport").evaluate(
            "element => getComputedStyle(element).display"
        )
        check(viewport_display == "none", "console_tear_forced_colors_static_fallback")
    finally:
        page.emulate_media(forced_colors="none")


@contextmanager
def _browser_session(headless: bool):
    """Close page, context, and browser on every normal or exceptional path."""

    if sync_playwright is None:
        raise RuntimeError("Playwright is unavailable")
    with sync_playwright() as playwright:
        browser = None
        context = None
        page = None
        try:
            browser = playwright.chromium.launch(headless=headless)
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            yield page
        finally:
            cleanup_failed = False
            if page is not None:
                try:
                    page.close()
                except Exception:
                    cleanup_failed = True
            if context is not None:
                try:
                    context.close()
                except Exception:
                    cleanup_failed = True
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    cleanup_failed = True
            if cleanup_failed:
                raise BrowserCleanupError("browser cleanup failed") from None


def _required_mail_hmac_key() -> str:
    value = os.environ.get("MAIL_HMAC_KEY", "")
    if not value.strip():
        raise RuntimeError("完整邮件浏览器联调需要 MAIL_HMAC_KEY 测试值")
    return value


def _required_external_credentials() -> tuple[str, str]:
    """Load external-services credentials only from explicit test-only env."""

    password = os.environ.get(BROWSER_TEST_PASSWORD_ENV, "")
    hmac_key = os.environ.get("MAIL_HMAC_KEY", "")
    if not password.strip() or not hmac_key.strip():
        raise RuntimeError("browser external test credentials unavailable")
    return password, hmac_key


def _run_local_mail_smoke(api_base: str, receipt_file: Path) -> dict[str, object]:
    """Use a private, parent-owned receipt file; never reflect child output."""
    _required_mail_hmac_key()
    root = Path(__file__).resolve().parents[1]
    try:
        completed = subprocess.run(
            ["node", str(root / "scripts" / "email-worker-smoke.mjs"), api_base, "--receipt-file", str(receipt_file)],
            cwd=root,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("本地邮件 smoke 无法完成") from exc
    if completed.returncode != 0:
        raise RuntimeError("本地邮件 smoke 无法完成")
    try:
        receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("本地邮件 smoke 回执无效") from exc
    if not isinstance(receipt, dict):
        raise RuntimeError("本地邮件 smoke 回执无效")
    if not all(
        isinstance(receipt.get(key), expected)
        for key, expected in (("id", str), ("subject", str), ("messageId", str), ("status", int), ("elapsedMs", int))
    ):
        raise RuntimeError("本地邮件 smoke 回执无效")
    return receipt


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_http(url: str, timeout_seconds: float = 30) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=2) as response:
                if 200 <= response.status < 500:
                    return
        except OSError:
            pass
        time.sleep(0.2)
    raise RuntimeError("本地服务在超时前未就绪")


def _spawn_owned_process(*args, **kwargs):
    """Create a process whose lifecycle is fully owned by this harness."""

    return spawn_owned_process(*args, **kwargs)


def _stop_owned_process(process: OwnedProcess | None) -> bool:
    """Reap this owned process (and its owned child tree) without leaking output."""

    return stop_owned_process(process)


def _port_released(port: int) -> bool:
    for _ in range(20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return True
            except OSError:
                time.sleep(0.2)
    return False


def _api_python(root: Path) -> Path:
    candidates = [
        root / "apps" / "api" / ".venv" / "Scripts" / "python.exe",
        root / "apps" / "api" / ".venv" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("找不到 apps/api 专用 Python 环境")


def _password_hash(api_python: Path, password: str) -> str:
    try:
        completed = subprocess.run(
            [
                str(api_python),
                "-B",
                "-c",
                "from argon2 import PasswordHasher; import sys; print(PasswordHasher().hash(sys.stdin.read()))",
            ],
            input=password,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=True,
        )
        value = completed.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("无法准备本地浏览器联调认证") from exc
    if not value:
        raise RuntimeError("无法准备本地浏览器联调认证")
    return value


def _verify_isolated_mail_raw(data_root: Path) -> bool:
    """只在默认临时栈内验证一条已接收邮件的私有原文关联，不输出内容或标识。"""
    db_path = data_root / "db" / "star-isle.db"
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT r.object_key, r.sha256, r.size, r.content_type "
                "FROM mail_message m JOIN mail_raw r ON r.message_id=m.id "
                "WHERE m.raw_state='stored' LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return False
        object_key = str(row["object_key"])
        if (
            not object_key
            or Path(object_key).name != object_key
            or row["content_type"] != "message/rfc822"
            or not isinstance(row["size"], int)
            or row["size"] <= 0
            or not isinstance(row["sha256"], str)
            or len(row["sha256"]) != 64
        ):
            return False
        path = data_root / "objects" / "mail-raw" / object_key
        if not path.is_file() or path.is_symlink() or path.stat().st_size != row["size"]:
            return False
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return digest == row["sha256"]
    except (OSError, sqlite3.Error, ValueError):
        return False


def _validated_isolated_fixture_db(value: str | Path | None) -> Path | None:
    """Accept a fixture DB only from this harness's parent-owned temporary stack."""

    if value is None:
        return None
    capability = os.environ.pop(FIXTURE_CAPABILITY_ENV, "")
    data_root_value = os.environ.get("DATA_ROOT", "")
    if not capability or not data_root_value:
        raise HarnessSafetyError("isolated fixture capability unavailable")

    supplied = Path(value).expanduser()
    configured_data_root = Path(data_root_value).expanduser()
    if has_link_or_reparse_component(supplied) or has_link_or_reparse_component(configured_data_root):
        raise HarnessSafetyError("isolated fixture path contains a link or reparse point")
    try:
        fixture_db = supplied.resolve(strict=True)
        data_root = configured_data_root.resolve(strict=True)
        expected_db = (data_root / "db" / "star-isle.db").resolve(strict=True)
    except OSError as exc:
        raise HarnessSafetyError("isolated fixture path is unavailable") from exc

    temporary_root = data_root.parent
    system_temp = validated_system_temp_root()
    if (
        not fixture_db.is_file()
        or fixture_db != expected_db
        or data_root.name != "data"
        or not temporary_root.name.startswith("starisle-browser-e2e-")
        or temporary_root.parent != system_temp
        or not is_within(fixture_db, temporary_root)
    ):
        raise HarnessSafetyError("isolated fixture path is not parent-owned")

    marker = temporary_root / FIXTURE_CAPABILITY_FILE
    if has_link_or_reparse_component(marker) or not marker.is_file() or marker.stat().st_size > 256:
        raise HarnessSafetyError("isolated fixture capability is invalid")
    try:
        marker_value = marker.read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise HarnessSafetyError("isolated fixture capability is unreadable") from exc
    if not secrets.compare_digest(marker_value, capability):
        raise HarnessSafetyError("isolated fixture capability does not match")
    return fixture_db


def _wait_for_owned_child(
    process: OwnedProcess, *, timeout_seconds: float = 240
) -> tuple[int, bool]:
    """Bound a browser child and always reap its complete owned process tree."""

    return wait_for_owned_process(process, timeout_seconds=timeout_seconds)


def _seed_photo_capture_group(db_path: Path) -> None:
    """Add legacy/canonical same-day rows only to the owned disposable E2E DB."""

    if not db_path.is_file():
        raise RuntimeError("isolated e2e database missing")
    with sqlite3.connect(db_path, timeout=5) as conn:
        conn.row_factory = sqlite3.Row
        source = conn.execute(
            "SELECT asset_id, thumbnail_asset_id, width, height FROM photo ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if source is None:
            raise RuntimeError("isolated e2e photo fixture missing")
        suffix = secrets.token_hex(8)
        rows = (
            (f"photo_e2e_legacy_{suffix}", "2026:09:05 08:00:00"),
            (f"photo_e2e_canonical_{suffix}", "2026-09-05T18:30:00"),
        )
        conn.executemany(
            "INSERT INTO photo (id, asset_id, capture_at, thumbnail_asset_id, width, height, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, '2026-09-05T20:00:00+00:00')",
            [
                (
                    photo_id,
                    source["asset_id"],
                    capture_at,
                    source["thumbnail_asset_id"],
                    source["width"],
                    source["height"],
                )
                for photo_id, capture_at in rows
            ],
        )


def _seed_deferred_rebuild_job(db_path: Path, document_id: str) -> str:
    """Only the parent-created isolated stack gets a durable active-job fixture.

    It is deliberately due far in the future so its worker cannot race the
    browser recovery/cancel assertions.  The browser still reads, cancels, and
    observes the job exclusively through the real HTTP API.
    """
    if not db_path.is_file():
        raise RuntimeError("isolated e2e database missing")
    job_id = f"job_e2e_{secrets.token_hex(8)}"
    created_at = "2099-01-01T00:00:00+00:00"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO ingestion_job (id, job_type, payload, status, attempts, next_run_at, created_at, updated_at) "
            "VALUES (?, 'rebuild', ?, 'queued', 0, '2100-01-01T00:00:00+00:00', ?, ?)",
            (job_id, json.dumps({"documentId": document_id}), created_at, created_at),
        )
    return job_id


def auth_recovery_interaction_checks(page, base: str, test_password: str) -> None:
    """Real API refresh plus explicitly bounded CSRF/forbidden fault injection."""
    print("== 4.2 会话恢复:真实 refresh 与受控故障 ==")
    context = page.context
    before = {cookie["name"]: cookie for cookie in context.cookies()}
    context.clear_cookies(name="star_isle_access")
    with allow_expected_error_responses(("GET", "/api/v1/auth/me", 401)):
        with page.expect_response(lambda response: response.url.endswith("/api/v1/auth/refresh") and response.status == 200):
            page.reload(wait_until="networkidle")
    expect(page.locator(".tear-portal-link")).to_be_visible()
    after = {cookie["name"]: cookie for cookie in context.cookies()}
    check(bool(after.get("star_isle_access", {}).get("httpOnly")), "refresh_restores_http_only_access")
    check(
        before["star_isle_refresh"]["value"] != after["star_isle_refresh"]["value"],
        "refresh_rotates_real_refresh_cookie",
    )
    check(page.request.get(base + "/api/v1/auth/me").status == 200, "refresh_real_api_me_is_authenticated")

    csrf_pattern = "**/api/v1/auth/csrf"
    handshakes = {"count": 0}

    def stale_handshake(route) -> None:
        handshakes["count"] += 1
        if handshakes["count"] == 1:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "csrfToken": "fixture-server-rejected-csrf", "expiresAt": "2099-01-01T00:00:00Z",
            }))
        else:
            route.continue_()

    context.clear_cookies(name="star_isle_access")
    page.route(csrf_pattern, stale_handshake)
    try:
        with allow_expected_error_responses(
            ("GET", "/api/v1/auth/me", 401), ("POST", "/api/v1/auth/refresh", 403),
        ):
            refreshes_before = response_count("POST", "/api/v1/auth/refresh")
            with page.expect_response(lambda response: response.url.endswith("/api/v1/auth/refresh") and response.status == 403):
                page.reload(wait_until="networkidle")
            expect(page.get_by_role("heading", name="会话校验失败", exact=True)).to_be_visible()
            check(urlparse(page.url).path.rstrip("/") == "/console", "csrf_failure_preserves_current_page")
            check(response_count("POST", "/api/v1/auth/refresh") == refreshes_before + 1, "csrf_failure_does_not_auto_replay_refresh")
            with page.expect_response(lambda response: response.url.endswith("/api/v1/auth/refresh") and response.status == 200):
                page.get_by_role("button", name="重试", exact=True).click()
            expect(page.locator(".tear-portal-link")).to_be_visible()
            check(handshakes["count"] == 2, "csrf_retry_performs_fresh_real_handshake")
            check(page.request.get(base + "/api/v1/auth/me").status == 200, "csrf_retry_restores_real_authenticated_api")
    finally:
        page.unroute(csrf_pattern, stale_handshake)

    me_pattern = "**/api/v1/auth/me"

    def forbidden_me(route) -> None:
        route.fulfill(status=403, content_type="application/json", body=json.dumps({
            "error": {"code": "FORBIDDEN", "message": "受控无权访问", "requestId": "fixture-forbidden"},
        }))

    page.route(me_pattern, forbidden_me)
    try:
        with allow_expected_error_responses(("GET", "/api/v1/auth/me", 403)):
            page.goto(base + "/console/files?view=recent", wait_until="networkidle")
            expect(page.get_by_role("button", name="重新登录", exact=True)).to_be_visible()
    finally:
        page.unroute(me_pattern, forbidden_me)
    page.get_by_role("button", name="重新登录", exact=True).click()
    page.wait_for_url(re.compile(re.escape(base) + r"/login\?redirect="))
    check(parse_qs(urlparse(page.url).query).get("redirect") == ["/console/files?view=recent"], "relogin_preserves_path_and_query")
    page.wait_for_load_state("networkidle")
    page.get_by_label("密码", exact=True).fill(test_password)
    with page.expect_response(lambda response: response.url.endswith("/api/v1/auth/login") and response.status == 200):
        page.get_by_role("button", name="登录", exact=True).click()
    page.wait_for_url(base + "/console/files?view=recent")
    expect(page.locator(".tear-portal-link")).to_be_visible()
    expect(page.locator("input[type=file]").first).to_be_attached()
    check(True, "relogin_returns_to_requested_real_private_page")
    page.goto(base + "/console", wait_until="networkidle")


def _run_isolated_local_e2e(args) -> int:
    """启动临时 API/worker/Web，再由子进程执行现有浏览器步骤。"""
    test_password = secrets.token_urlsafe(32)
    hmac_key = secrets.token_urlsafe(48)
    root = Path(__file__).resolve().parents[1]
    api_python = _api_python(root)
    api_port = _free_loopback_port()
    web_port = _free_loopback_port()
    api_process = None
    worker_process = None
    web_process = None
    api_log = None
    worker_log = None
    web_log = None
    child_process = None
    child_result = 1
    child_stopped = True
    ports_released = True
    api_stopped = True
    worker_stopped = True
    web_stopped = True
    logs_closed = True
    raw_verified = False

    with owned_temporary_directory("starisle-browser-e2e-") as temporary:
        temporary_root = Path(temporary)
        data_root = temporary_root / "data"
        data_host_root = temporary_root / "data-host"
        evidence_root = temporary_root / "evidence"
        receipt_file = temporary_root / "mail-receipt.json"
        api_base = f"http://127.0.0.1:{api_port}"
        web_base = f"http://127.0.0.1:{web_port}"
        api_env = os.environ.copy()
        api_env.update(
            {
                "DATA_ROOT": str(data_root),
                "DATA_HOST_ROOT": str(data_host_root),
                "AUTH_JWT_SIGNING_KEY": "browser-e2e-jwt-signing-key-0123456789abcdef",
                "AUTH_JWT_ACTIVE_KID": "browser-e2e",
                "ADMIN_BOOTSTRAP_PASSWORD_HASH": _password_hash(api_python, test_password),
                "COOKIE_SECURE": "false",
                "MAIL_HMAC_KEY": hmac_key,
                "MAIL_MAX_BYTES": os.environ.get("MAIL_MAX_BYTES", str(5 * 1024 * 1024)),
                "LLAMA_CHAT_BASE_URL": "",
                "LLAMA_EMBED_BASE_URL": "",
                "EMBEDDING_MODEL_ID": "",
                "PYTHON_DOTENV_DISABLED": "1",
            }
        )
        web_env = os.environ.copy()
        web_env.update({"API_DEV_PROXY_TARGET": api_base})
        child_env = os.environ.copy()
        fixture_capability = secrets.token_urlsafe(48)
        child_env.update(
            {
                "DATA_ROOT": str(data_root),
                "DATA_HOST_ROOT": str(data_host_root),
                "MAIL_HMAC_KEY": hmac_key,
                "MAIL_MAX_BYTES": api_env["MAIL_MAX_BYTES"],
                BROWSER_TEST_PASSWORD_ENV: test_password,
                FIXTURE_CAPABILITY_ENV: fixture_capability,
            }
        )
        child_env.pop("STAR_ISLE_E2E_EVIDENCE", None)

        try:
            data_root.mkdir(mode=0o700)
            data_host_root.mkdir(mode=0o700)
            capability_file = new_temporary_file_path(temporary_root / FIXTURE_CAPABILITY_FILE)
            with capability_file.open("x", encoding="ascii") as handle:
                handle.write(fixture_capability)
            new_temporary_file_path(
                receipt_file,
                protected_roots=(*_receipt_protected_roots(), data_root, data_host_root),
            )
            api_log = (temporary_root / "api.log").open("wb")
            worker_log = (temporary_root / "worker.log").open("wb")
            web_log = (temporary_root / "web.log").open("wb")
            api_process = _spawn_owned_process(
                [
                    str(api_python),
                    "-B",
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(api_port),
                    "--no-access-log",
                ],
                cwd=root / "apps" / "api",
                env=api_env,
                stdout=api_log,
                stderr=subprocess.STDOUT,
            )
            _wait_for_http(api_base + "/api/v1/health/live")
            worker_process = _spawn_owned_process(
                [str(api_python), "-B", "-m", "app.worker_runner"],
                cwd=root / "apps" / "api",
                env=api_env,
                stdout=worker_log,
                stderr=subprocess.STDOUT,
            )
            if worker_process.poll() is not None:
                raise RuntimeError("本轮知识库 worker 未能启动")
            pnpm = shutil.which("pnpm")
            if not pnpm:
                raise RuntimeError("找不到 pnpm，无法启动本地 Web 联调")
            web_process = _spawn_owned_process(
                [pnpm, "--filter", "@star-isle/web", "dev", "--host", "127.0.0.1", "--port", str(web_port)],
                cwd=root,
                env=web_env,
                stdout=web_log,
                stderr=subprocess.STDOUT,
            )
            _wait_for_http(web_base + "/")
            child_command = [
                sys.executable,
                "-B",
                str(Path(__file__).resolve()),
                "--external-services",
                "--base",
                web_base,
                "--api-base",
                api_base,
                "--owned-mail-receipt",
                str(receipt_file),
                "--isolated-fixture-db",
                str(data_root / "db" / "star-isle.db"),
            ]
            explicit_evidence = args.evidence_dir or os.environ.get("STAR_ISLE_E2E_EVIDENCE")
            if explicit_evidence:
                child_command.extend(("--evidence-dir", explicit_evidence))
            else:
                # The child exclusively creates this still-nonexistent path.
                child_command.extend(("--evidence-dir", str(evidence_root)))
            child_process = _spawn_owned_process(
                child_command,
                cwd=root,
                env=child_env,
            )
            child_result, child_stopped = _wait_for_owned_child(child_process)
            if child_result == 0:
                raw_verified = _verify_isolated_mail_raw(data_root)
                if raw_verified:
                    print("隔离邮件原文归档校验通过。")
                else:
                    print("隔离邮件原文归档校验失败。", file=sys.stderr)
        except (HarnessSafetyError, OwnedProcessError, OSError, RuntimeError, subprocess.SubprocessError):
            print("隔离本地浏览器联调无法完成。", file=sys.stderr)
            child_result = 1
        finally:
            try:
                if child_process is not None:
                    child_stopped = _stop_owned_process(child_process) and child_stopped
            except Exception:
                child_stopped = False
            try:
                web_stopped = _stop_owned_process(web_process)
            except Exception:
                web_stopped = False
            try:
                worker_stopped = _stop_owned_process(worker_process)
            except Exception:
                worker_stopped = False
            try:
                api_stopped = _stop_owned_process(api_process)
            except Exception:
                api_stopped = False
            try:
                ports_released = _port_released(web_port) and _port_released(api_port)
            except Exception:
                ports_released = False
            for log_handle in (web_log, api_log, worker_log):
                if log_handle is not None:
                    try:
                        log_handle.close()
                    except Exception:
                        logs_closed = False

    if not ports_released:
        print("隔离本地浏览器联调未能释放本轮端口。", file=sys.stderr)
    if not worker_stopped:
        print("隔离本地浏览器联调未能停止本轮 worker。", file=sys.stderr)
    if not web_stopped or not api_stopped:
        print("隔离本地浏览器联调未能停止本轮服务。", file=sys.stderr)
    if not child_stopped:
        print("隔离本地浏览器联调未能停止本轮 child。", file=sys.stderr)
    if not logs_closed:
        print("隔离本地浏览器联调未能关闭本轮日志。", file=sys.stderr)
    return _isolated_exit_code(
        child_result,
        child_stopped,
        raw_verified,
        ports_released,
        api_stopped,
        worker_stopped,
        web_stopped,
        logs_closed,
    )


def _browser_flow(args, sample_txt: Path, sample_png: Path, receipt_file: Path) -> int:
    global PUBLIC_STAGE_ACTIVE
    mail_smoke: dict[str, object] | None = None
    with _browser_session(args.headless) as page:

        tear_module_requests = {"count": 0}

        def record_tear_module_request(request) -> None:
            if "TearPortalLink" in request.url and request.resource_type == "script":
                tear_module_requests["count"] += 1

        page.on("console", record_console_error)
        page.on("pageerror", lambda _err: PAGE_ERRORS.append("page_error") if not _is_browser_stream_cleanup_noise(_err) else None)
        page.on("request", record_public_api_request)
        page.on("response", record_response)
        page.on("requestfailed", record_failed_request)
        page.on("request", record_tear_module_request)

        print("== 1. 公开页 ==")
        PUBLIC_API_REQUESTS.clear()
        PUBLIC_STAGE_ACTIVE = True
        try:
            page.goto(args.base + "/", wait_until="networkidle")
            page.evaluate("sessionStorage.setItem('star-isle:enter-completed', 'true')")
            for path, text, markdown_text, label, screenshot_name in [
                ("/", "星屿", "我相信技术不是炫技", "public_home", "public_home.png"),
                ("/projects", "项目", "一个构建期脚本", "public_projects", "public_projects.png"),
                ("/resume", "简历", "独立设计并实现个人全栈门户", "public_resume", "public_resume.png"),
            ]:
                resp = page.goto(args.base + path, wait_until="networkidle")
                content = page.content()
                check(resp and resp.status == 200, f"{label}_status")
                check(text in content, f"{label}_content")
                check("[object Object]" not in content, f"{label}_no_object")
                check(markdown_text in content, f"{label}_markdown")
                missing_ids = missing_aria_labelledby_ids(page)
                check(not missing_ids, f"{label}_aria_labelledby")
                if path == "/":
                    home_skill = page.get_by_text("TypeScript", exact=True)
                    check(home_skill.count() > 0 and home_skill.first.is_visible(), "首页技能区显示 frontmatter skill")
                if not args.public_only:
                    _screenshot(page, screenshot_name)

            # 当前 /enter 通过封蜡进入公开页、撕裂进入既有登录页；
            # 以真实键盘操作验证导航和降级，不依赖历史私有事件或嵌入登录 UI。
            page.emulate_media(reduced_motion="reduce", forced_colors="active")
            try:
                enter_response = page.goto(args.base + "/enter", wait_until="networkidle")
                check(enter_response and enter_response.status == 200, "enter_status")
                check(
                    page.locator("meta[name='robots']").get_attribute("content") == "noindex",
                    "enter_robots_noindex",
                )
                home_fallback = page.get_by_role("link", name="融化封蜡，进入公开页", exact=True)
                login_fallback = page.get_by_role("link", name="撕开信封，进入私人登录页", exact=True)
                check(
                    home_fallback.is_visible()
                    and home_fallback.get_attribute("href") == "/"
                    and login_fallback.is_visible()
                    and login_fallback.get_attribute("href") == "/login",
                    "enter_visible_fallback_links",
                )
                seal_box = home_fallback.bounding_box()
                check(
                    seal_box is not None
                    and seal_box["width"] >= 44
                    and seal_box["height"] >= 44,
                    "enter_semantic_seal_min_target",
                )
                narrative = page.locator(".narrative-entry")
                expect(narrative).to_have_attribute("data-quality", "reduced")
                check(
                    page.evaluate("matchMedia('(forced-colors: active)').matches"),
                    "enter_accessibility_media_fallbacks_active",
                )
                check(
                    page.locator(".narrative-entry__wax-layer--pool").evaluate(
                        "element => getComputedStyle(element).filter"
                    )
                    == "none"
                    and page.locator(".narrative-tear-debris").count() == 0,
                    "enter_reduced_motion_disables_filter_and_particles",
                )
                home_fallback.focus()
                home_fallback.press("Space")
                page.wait_for_url(args.base + "/", timeout=15_000)
                check(
                    page.evaluate("sessionStorage.getItem('star-isle:enter-completed')") == "true",
                    "enter_reduced_motion_seal_enters_public_and_marks_completion",
                )
                page.goto(args.base + "/enter", wait_until="networkidle")
                expect(page.locator(".narrative-entry")).to_have_attribute("data-quality", "reduced")
                tear_link = page.get_by_role("link", name="撕开信封，进入私人登录页", exact=True)
                tear_link.focus()
                tear_link.press("Enter")
                page.wait_for_url(args.base + "/login", timeout=15_000)
                expect(page.get_by_label("密码", exact=True)).to_be_visible()
                check(page.url == args.base + "/login", "enter_reduced_motion_tear_enters_existing_login")
            finally:
                page.emulate_media(reduced_motion="no-preference", forced_colors="none")

            fallback_context = page.context.browser.new_context(java_script_enabled=False)
            try:
                fallback_page = fallback_context.new_page()
                fallback_page.goto(args.base + "/enter", wait_until="networkidle")
                fallback_page.get_by_role("link", name="融化封蜡，进入公开页", exact=True).click()
                check(fallback_page.url == args.base + "/", "enter_without_javascript_public_link")
                fallback_page.goto(args.base + "/enter", wait_until="networkidle")
                fallback_page.get_by_role("link", name="撕开信封，进入私人登录页", exact=True).click()
                check(fallback_page.url == args.base + "/login", "enter_without_javascript_private_link")
            finally:
                fallback_context.close()

            page.evaluate("sessionStorage.setItem('star-isle:enter-completed', 'true')")
            root_response = page.goto(args.base + "/", wait_until="networkidle")
            check(
                root_response and root_response.status == 200 and "星屿" in page.content(),
                "enter_keeps_root_behavior",
            )
            public_native_interaction_checks(page, args.base)
            page.set_viewport_size({"width": 1440, "height": 900})
        finally:
            PUBLIC_STAGE_ACTIVE = False
        check(not PUBLIC_API_REQUESTS, "公开页阶段无 /api/v1 请求")

        if args.public_only:
            return public_only_result()

        print("== 1.5 默认入口行为 ==")
        page.evaluate("sessionStorage.clear()")
        page.goto(args.base + "/", wait_until="networkidle")
        check(urlparse(page.url).path.rstrip("/") == "/enter", "default_entry_redirects_to_enter")
        check(
            page.evaluate("sessionStorage.getItem('star-isle:enter-redirect-count')") == "1",
            "default_entry_redirect_count_incremented",
        )

        page.evaluate("sessionStorage.setItem('star-isle:enter-completed', 'true')")
        page.goto(args.base + "/", wait_until="networkidle")
        check(urlparse(page.url).path.rstrip("/") != "/enter", "completed_marker_retains_home")
        check("星屿" in page.content(), "completed_marker_home_content")

        page.evaluate("sessionStorage.clear()")
        page.evaluate("sessionStorage.setItem('star-isle:enter-redirect-count', '1')")
        page.goto(args.base + "/", wait_until="networkidle")
        check(urlparse(page.url).path.rstrip("/") != "/enter", "cycle_guard_degrades_to_home")
        check("星屿" in page.content(), "cycle_guard_home_content")

        page.evaluate("sessionStorage.clear()")
        page.goto(args.base + "/enter", wait_until="networkidle")
        page.wait_for_function("window.__narrativeEnterListenerReady === true", timeout=5000)
        seal_link = page.get_by_role("link", name="融化封蜡，进入公开页", exact=True)
        seal_link.focus()
        seal_link.press("Space")
        page.wait_for_url(args.base + "/", timeout=15000)
        check(
            page.evaluate("sessionStorage.getItem('star-isle:enter-completed')") == "true",
            "enter_completed_writes_marker",
        )

        page.goto(args.base + "/enter", wait_until="networkidle")
        star_link = page.get_by_role("link", name="融化封蜡，进入公开页", exact=True)
        login_link = page.get_by_role("link", name="撕开信封，进入私人登录页", exact=True)
        check(star_link.count() > 0 and star_link.first.is_visible(), "enter_fallback_star_link_visible")
        check(login_link.count() > 0 and login_link.first.is_visible(), "enter_fallback_login_link_visible")
        check(star_link.first.get_attribute("href") == "/", "enter_fallback_star_link_href")
        check(login_link.first.get_attribute("href") == "/login", "enter_fallback_login_link_href")

        print("== 2. 未知路由 404 ==")
        with allow_expected_error_responses(*UNKNOWN_ROUTE_EXPECTED_ERROR_RESPONSES):
            resp = page.goto(args.base + "/definitely-not-a-page", wait_until="networkidle")
        check(resp and resp.status == 404, "GET /definitely-not-a-page 404")

        print("== 3. 会话守卫:匿名访问 /console 跳登录 ==")
        with allow_expected_error_responses(*AUTH_GUARD_EXPECTED_ERROR_RESPONSES):
            page.goto(args.base + "/console", wait_until="networkidle")
        check(page.url.startswith(args.base + "/login"), "重定向到 /login")
        check("redirect=%2Fconsole" in page.url, "redirect 参数携带 /console")
        check(tear_module_requests["count"] == 0, "匿名会话未请求 Console tear 模块")

        print("== 4. 登录(Cookie + CSRF 链路)==")
        csrf_seen = {"ok": False}
        page.on(
            "response",
            lambda r: csrf_seen.update(ok=True) if r.url.endswith("/api/v1/auth/csrf") and r.status == 200 else None,
        )
        password_input = page.get_by_label("密码", exact=True)
        expect(password_input).to_be_enabled()
        page.get_by_role("button", name="登录", exact=True).click()
        expect(page.get_by_role("alert")).to_have_text("请输入密码。")
        check(password_input.get_attribute("aria-describedby") == "login-password-error", "login_password_error_is_associated")
        password_input.fill(args.test_password)
        password_input.press("Tab")
        expect(page.get_by_role("button", name="显示密码", exact=True)).to_be_focused()
        page.get_by_role("button", name="显示密码", exact=True).press("Enter")
        expect(password_input).to_have_attribute("type", "text")
        check(password_input.input_value() == args.test_password, "password_toggle_preserves_value")
        expect(page.get_by_role("button", name="隐藏密码", exact=True)).to_have_attribute("aria-pressed", "true")
        page.get_by_role("button", name="隐藏密码", exact=True).press("Space")
        expect(password_input).to_have_attribute("type", "password")
        check(True, "password_toggle_real_keyboard_reveals_and_hides")
        with page.expect_response(
            lambda r: r.url.endswith("/api/v1/auth/login") and r.request.method == "POST" and r.status == 200
        ):
            page.get_by_role("button", name="登录").click()
        page.wait_for_url("**/console", timeout=15000)
        check(True, "登录后回到 /console")
        check(csrf_seen["ok"], "登录前调用了 GET /auth/csrf(200)")
        cookies = {c["name"]: c for c in page.context.cookies()}
        for name, label in (
            ("star_isle_access", "access_cookie"),
            ("star_isle_refresh", "refresh_cookie"),
        ):
            c = cookies.get(name)
            check(c is not None, f"{label}_present")
            check(bool(c and c.get("httpOnly")), f"{label}_http_only")
        check(bool(cookies.get("star_isle_access", {}).get("sameSite") == "Lax"), "access Cookie SameSite=Lax")
        page.locator(".tear-portal-link").wait_for(state="visible", timeout=15_000)
        check(tear_module_requests["count"] >= 1, "授权后才请求 Console tear 模块")

        print("== 4.1 Console pointer tear ==")
        console_tear_interaction_checks(page, args.base)
        _screenshot(page, "console_overview.png")
        page.wait_for_timeout(800)

        auth_recovery_interaction_checks(page, args.base, args.test_password)

        print("== 4.5 本地邮件候选闭环 ==")
        try:
            mail_smoke = _run_local_mail_smoke(args.api_base, receipt_file)
            check(True, "本地 Worker 到 API 邮件 smoke 完成")
        except RuntimeError:
            check(False, "本地 Worker 到 API 邮件 smoke 完成")

        print("== 5. 文件上传 ==")
        page.goto(args.base + "/console/files", wait_until="networkidle")
        file_posts_before = response_count("POST", "/api/v1/files")
        file_rows = page.locator("table tbody tr")
        file_rows_before = file_rows.count()
        with page.expect_response(
            lambda r: r.url.endswith("/api/v1/files") and r.request.method == "POST" and r.status == 201
        ):
            page.locator("input[type=file]").first.set_input_files(str(sample_txt))
        page.get_by_text("e2e-sample.txt", exact=True).first.wait_for(timeout=15000)
        expect(file_rows).to_have_count(file_rows_before + 1, timeout=15000)
        page.wait_for_load_state("networkidle", timeout=5000)
        file_posts_delta = response_count("POST", "/api/v1/files") - file_posts_before
        file_rows_delta = file_rows.count() - file_rows_before
        check(file_posts_delta == 1, "文件选择仅产生一次 POST /api/v1/files")
        check(file_rows_delta == 1, "文件表仅新增一行")
        _screenshot(page, "files_after_upload.png")
        check(True, "上传文件出现在列表")

        print("== 6. 照片上传与缩略图 ==")
        page.goto(args.base + "/console/gallery", wait_until="networkidle")
        photo_posts_before = response_count("POST", "/api/v1/photos")
        photo_thumbnails = page.locator("img[alt^='照片 ']")
        photo_thumbnails_before = photo_thumbnails.count()
        with page.expect_response(
            lambda r: r.url.endswith("/api/v1/photos") and r.request.method == "POST" and r.status == 201
        ):
            page.locator("input[type=file]").first.set_input_files(str(sample_png))
        expect(photo_thumbnails).to_have_count(photo_thumbnails_before + 1, timeout=15000)
        page.wait_for_load_state("networkidle", timeout=5000)
        photo_posts_delta = response_count("POST", "/api/v1/photos") - photo_posts_before
        photo_thumbnails_delta = photo_thumbnails.count() - photo_thumbnails_before
        check(photo_posts_delta == 1, "照片选择仅产生一次 POST /api/v1/photos")
        check(photo_thumbnails_delta == 1, "照片缩略图仅新增一张")
        delete_photo = page.get_by_role("button", name="删除这张照片", exact=True).first
        expect(delete_photo).to_be_visible()
        upload_zone = page.get_by_role("button", name="拖放照片到此处,或点击选择(支持多选)", exact=True)
        upload_zone.focus()
        page.keyboard.press("Tab")
        expect(delete_photo).to_be_focused()
        page.keyboard.press("Enter")
        photo_dialog = page.get_by_role("dialog", name="删除照片", exact=True)
        expect(photo_dialog).to_be_visible()
        photo_dialog.get_by_role("button", name="取消", exact=True).press("Enter")
        expect(photo_dialog).to_be_hidden()
        check(True, "photo_delete_is_visible_and_keyboard_accessible_without_hover")
        check(page.get_by_text("e2e-sample.png", exact=True).count() > 0, "照片出现在时间线")
        if args.isolated_fixture_db:
            _seed_photo_capture_group(Path(args.isolated_fixture_db))
            page.reload(wait_until="networkidle")
            capture_day = "2026/09/05"
            same_day_photos = page.get_by_role("img", name=f"照片 {capture_day}", exact=True)
            expect(same_day_photos).to_have_count(2, timeout=15_000)
            timeline_labels = page.locator("section[aria-label]").evaluate_all(
                "sections => sections.map(section => section.getAttribute('aria-label'))"
            )
            check(timeline_labels.count(capture_day) == 1, "legacy 与 canonical EXIF 同日归为一个时间轴分组")
            check(
                page.get_by_role("img", name="照片 未知日期", exact=True).count() >= 1,
                "无 EXIF 照片归入未知日期",
            )
        _screenshot(page, "gallery_after_upload.png")

        print("== 7. 知识库:上传文档 → worker 索引 ==")
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(args.base + "/console/kb", wait_until="networkidle")
        documents_panel = page.locator("#kb-documents-panel")
        chat_panel = page.locator("#kb-chat-panel")
        documents_toggle = page.get_by_role("button", name="文档", exact=True)
        chat_toggle = page.get_by_role("button", name="问答", exact=True)
        documents_panel.wait_for(state="visible", timeout=15_000)
        check(documents_panel.is_visible(), "移动知识库默认显示文档面板")
        check(not chat_panel.is_visible(), "移动知识库默认隐藏问答面板")
        check(documents_toggle.get_attribute("aria-pressed") == "true", "文档分段 aria-pressed 默认同步")
        check(chat_toggle.get_attribute("aria-pressed") == "false", "问答分段 aria-pressed 默认同步")
        tear_panel = page.locator(".tear-portal-link")
        tear_handle = page.get_by_role("button", name="向右撕开纸页前往星", exact=True)
        tear_fallback = tear_panel.get_by_role("link", name="普通前往星", exact=True)
        tear_box = tear_panel.bounding_box()
        documents_toggle_box = documents_toggle.bounding_box()
        tear_handle_box = tear_handle.bounding_box()
        check(
            tear_panel.count() == 1
            and tear_box is not None
            and documents_toggle_box is not None
            and tear_box["y"] + tear_box["height"] <= documents_toggle_box["y"],
            "移动 Console tear 不覆盖知识库控件",
        )
        check(
            tear_handle_box is not None
            and tear_handle_box["width"] >= 44
            and tear_handle_box["height"] >= 44
            and tear_fallback.is_visible(),
            "移动 Console tear 把手与普通链接可用",
        )
        check(
            page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
            ),
            "移动 Console tear 无横向溢出",
        )

        chat_toggle.focus()
        page.keyboard.press("Enter")
        chat_panel.wait_for(state="visible", timeout=5_000)
        check(chat_toggle.get_attribute("aria-pressed") == "true", "键盘激活问答分段 aria-pressed 同步")
        check(documents_toggle.get_attribute("aria-pressed") == "false", "键盘激活后文档分段取消按下")
        check(chat_panel.is_visible(), "移动知识库问答面板可见")
        check(not documents_panel.is_visible(), "移动知识库问答时文档面板不可见")

        conversation_toggle = page.locator("button[aria-controls='kb-conversation-list']")
        check(conversation_toggle.get_attribute("aria-expanded") == "false", "会话列表初始 aria-expanded=false")
        conversation_toggle.click()
        conversation_list = page.locator("#kb-conversation-list")
        conversation_list.wait_for(state="visible", timeout=5_000)
        page.wait_for_function(
            "() => document.getElementById('kb-conversation-list')?.contains(document.activeElement)"
        )
        check(conversation_toggle.get_attribute("aria-expanded") == "true", "会话列表打开 aria-expanded=true")
        check(conversation_list.is_visible(), "移动会话列表打开")
        check(
            conversation_list.evaluate("panel => panel.contains(document.activeElement)"),
            "移动会话列表打开后焦点进入列表",
        )
        page.keyboard.press("Escape")
        page.wait_for_function(
            "() => document.activeElement === document.querySelector(\"button[aria-controls='kb-conversation-list']\")"
        )
        check(conversation_toggle.get_attribute("aria-expanded") == "false", "移动会话列表 Escape 关闭")
        check(
            conversation_toggle.evaluate("button => document.activeElement === button"),
            "移动会话列表 Escape 后恢复触发器焦点",
        )

        documents_toggle.focus()
        page.keyboard.press("Enter")
        documents_panel.wait_for(state="visible", timeout=5_000)
        check(documents_panel.is_visible(), "移动知识库切回文档面板")
        kb_posts_before = response_count("POST", "/api/v1/knowledge/documents")
        kb_file_input = page.locator("input[type=file]").first
        with page.expect_response(
            lambda r: r.url.endswith("/api/v1/knowledge/documents") and r.request.method == "POST" and r.status == 202
        ):
            kb_file_input.set_input_files(str(sample_txt))
        page.get_by_text("e2e-sample.txt", exact=True).first.wait_for(timeout=15000)
        expect(kb_file_input).to_have_value("", timeout=5_000)
        check(True, "知识库上传后 file input 已清空，可重新选择同一文件")
        page.wait_for_load_state("networkidle", timeout=5000)
        kb_posts_delta = response_count("POST", "/api/v1/knowledge/documents") - kb_posts_before
        check(kb_posts_delta == 1, "知识库选择仅产生一次 POST /api/v1/knowledge/documents")
        # 必须观察到真实 worker 写回的 ready/成功状态，不能以固定等待代替索引完成。
        kb_row = page.get_by_text("e2e-sample.txt", exact=True).first.locator("xpath=ancestor::li[1]")
        try:
            expect(kb_row).to_contain_text(re.compile(r"(?:成功|ready)"), timeout=60_000)
            check(True, "知识库文档 worker 索引成功/ready")
        except PlaywrightTimeoutError:
            check(False, "知识库文档 worker 索引成功/ready")
        _screenshot(page, "kb_docs_uploaded.png")

        print("== 8. 知识库问答(RAG 检索 + 降级流式)==")
        chat_toggle.focus()
        page.keyboard.press("Enter")
        chat_panel.wait_for(state="visible", timeout=5_000)
        check(chat_panel.is_visible(), "移动知识库上传后切回问答面板")
        question_input = page.get_by_role("textbox", name="问题输入框")
        question_input.scroll_into_view_if_needed()
        mobile_console_nav = page.locator("nav[aria-label='控制台导航']:visible")
        question_box = question_input.bounding_box()
        navigation_box = mobile_console_nav.bounding_box()
        check(
            question_box is not None
            and navigation_box is not None
            and question_box["y"] + question_box["height"] <= navigation_box["y"],
            "移动控制台底部导航不覆盖问题输入框",
        )
        question_input.fill("这个知识库里有什么?")
        with page.expect_response(
            lambda r: r.url.endswith("/api/v1/rag/query") and r.request.method == "POST" and r.status == 200
        ):
            page.get_by_role("button", name="发送").click()
        # 等待用户气泡出现,再验证助手最终气泡有非空内容。
        page.get_by_text("这个知识库里有什么?", exact=True).first.wait_for(timeout=5000)
        check(True, "问题气泡出现")
        chat_card = page.locator("textarea[aria-label='问题输入框']").locator(
            "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' card ')][1]"
        )
        assistant_bubbles = chat_card.locator("div[class~='flex'][class~='justify-start'] > div")
        try:
            expect(assistant_bubbles.last).to_contain_text(re.compile(r"\S"), timeout=15000)
            check(bool(assistant_bubbles.last.inner_text().strip()), "助手回答气泡非空")
        except PlaywrightTimeoutError:
            check(False, "助手回答气泡非空")
        citation_card = page.get_by_text(re.compile(r"e2e-sample\.txt\s+v1\s+·\s+chunk"))
        try:
            citation_card.first.wait_for(state="visible", timeout=60_000)
            check(True, "rag_citation_card_visible")
        except PlaywrightTimeoutError:
            check(False, "rag_citation_card_visible")
        page.get_by_role("button", name="发送").wait_for(timeout=15000)
        _screenshot(page, "kb_chat.png")

        print("== 9. 邮件收件箱 ==")
        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto(args.base + "/console/mail", wait_until="networkidle")
        if mail_smoke is None:
            check(False, "本地候选邮件可见、默认图片阻止与已读成功")
        else:
            subject = str(mail_smoke["subject"])
            mail_id = str(mail_smoke["id"])
            tracker_requests: list[str] = []

            def record_tracker_request(request) -> None:
                if "tracker.invalid" in request.url:
                    tracker_requests.append(safe_request_label(request.method, request.url))

            page.on("request", record_tracker_request)
            mail_item = page.get_by_test_id("mail-item").filter(has_text=subject)
            try:
                mail_item.wait_for(state="visible", timeout=60_000)
                check(True, "本地候选邮件在 60 秒内可见")
                read_path = f"/api/v1/mail/{mail_id}/read"
                with page.expect_response(
                    lambda response: (
                        response.request.method == "POST"
                        and urlparse(response.url).path == read_path
                        and response.status == 200
                    ),
                    timeout=15_000,
                ):
                    mail_item.click()
                page.get_by_text("M2 local loopback smoke: safe plain-text body.", exact=True).wait_for(timeout=15_000)
                check(
                    page.get_by_role("button", name="纯文本").get_attribute("aria-pressed") == "true",
                    "邮件打开后默认纯文本视图",
                )
                check(True, "已读 POST 成功")

                page.get_by_role("button", name="HTML 视图").click()
                html_content = page.get_by_test_id("mail-html-content")
                html_content.wait_for(state="visible", timeout=15_000)
                safety = html_content.evaluate(
                    """
                    root => ({
                      dangerousElements: root.querySelectorAll("script,style,iframe,object,embed,form,link,meta,svg,math").length,
                      eventAttributes: [...root.querySelectorAll("*")].flatMap((element) =>
                        [...element.attributes].map((attribute) => attribute.name).filter((name) => name.toLowerCase().startsWith("on"))
                      ),
                      javascriptUrls: [...root.querySelectorAll("[href],[src]")].map((element) =>
                        element.getAttribute("href") || element.getAttribute("src") || ""
                      ).filter((value) => /^javascript:/i.test(value)),
                      images: [...root.querySelectorAll("img")].map((image) => ({
                        src: image.getAttribute("src"),
                        srcset: image.getAttribute("srcset"),
                      })),
                    })
                    """
                )
                check(
                    safety["dangerousElements"] == 0
                    and not safety["eventAttributes"]
                    and not safety["javascriptUrls"],
                    "HTML 视图无危险元素、事件属性或 javascript URL",
                )
                check(
                    len(safety["images"]) >= 1
                    and all(image["src"] is None and image["srcset"] is None for image in safety["images"]),
                    "HTML 默认不保留任何图片 src 或 srcset",
                )
                check(not tracker_requests, "HTML 默认未请求 tracker 图片")
            except PlaywrightTimeoutError:
                check(False, "本地候选邮件可见、默认图片阻止与已读成功")
        _screenshot(page, "mail_inbox.png")

        print("== 9.1 邮件与相册失败恢复（仅前端 fixture） ==")
        previous_viewport = page.viewport_size
        try:
            mail_recovery_browser_checks(page, args.base)
            photo_recovery_browser_checks(page, args.base, sample_png)
        finally:
            if previous_viewport is not None:
                page.set_viewport_size(previous_viewport)

        # 真实上传、worker ready、真实 RAG citation 和本地邮件闭环均已完成后，才允许
        # 以页面内 Response/ReadableStream 覆盖精确 fixture 请求；前面所有路径保持真实。
        print("== 9.5 知识库 Job/RAG 本地候选闭环 ==")
        completed_document_id: str | None = None
        with page.expect_response(
            lambda response: (
                urlparse(response.url).path == "/api/v1/knowledge/documents"
                and response.request.method == "GET"
                and response.status == 200
            ),
            timeout=15_000,
        ) as documents_response_info:
            page.goto(args.base + "/console/kb", wait_until="networkidle")
        documents_payload = documents_response_info.value.json()
        fixture_document = next(
            (
                item
                for item in documents_payload.get("items", [])
                if item.get("title") == "e2e-sample.txt"
            ),
            None,
        )
        check(isinstance(fixture_document, dict), "fixture_uses_real_document_after_success_path")
        if isinstance(fixture_document, dict):
            fixture_document_id = str(fixture_document["id"])
            fixture_document_title = str(fixture_document["title"])
            completed_document_id = fixture_document_id
            rebuild_path = f"/api/v1/knowledge/documents/{fixture_document_id}/rebuild"
            delete_path = f"/api/v1/knowledge/documents/{fixture_document_id}"
            fixture_row = page.get_by_text(fixture_document_title, exact=True).first.locator("xpath=ancestor::li[1]")
            fixture_row.wait_for(state="visible", timeout=15_000)
            conversation_buttons = page.locator("#kb-conversation-list ul button")
            check(conversation_buttons.count() > 0, "fixture_reuses_real_conversation")
            if conversation_buttons.count() > 0:
                conversation_buttons.first.click()
                page.wait_for_timeout(150)

            rag_responses_before = response_count("POST", "/api/v1/rag/query")
            rebuild_responses_before = response_count("POST", rebuild_path)
            delete_responses_before = response_count("DELETE", delete_path)
            failed_requests_before = len(FAILED_REQUESTS)
            manual_restore_for_unmount = False
            with fixture_fetch_context(page, rebuild_path, delete_path) as fixture:
                # Job: 本地 queued → fixture running → fixture failed；同一文档在 active 期间禁重入。
                fixture_rebuild = fixture_row.get_by_role("button", name="重建", exact=True)
                fixture_delete = fixture_row.get_by_role("button", name="删除", exact=True)
                fixture_rebuild.click()
                expect(fixture_row).to_contain_text("排队中", timeout=2_000)
                check(True, "fixture_job_queued_visible")
                check(
                    fixture_rebuild.is_disabled() and fixture_delete.is_disabled(),
                    "fixture_active_document_controls_disabled",
                )
                expect(fixture_row).to_contain_text("运行中", timeout=5_000)
                check(True, "fixture_job_running_visible")
                expect(fixture_row).to_contain_text("失败", timeout=6_000)
                check(True, "fixture_job_failed_visible")
                check(
                    not fixture_rebuild.is_disabled() and not fixture_delete.is_disabled(),
                    "fixture_terminal_document_controls_reenabled",
                )

                # 首次 cancelled job 查询故意返回 503；UI 只能提供状态查询重试，不能伪造终态。
                fixture_row.get_by_role("button", name="重建", exact=True).click()
                status_retry = fixture_row.get_by_role("button", name="重试状态查询", exact=True)
                status_retry.wait_for(state="visible", timeout=4_000)
                check(status_retry.is_visible(), "fixture_job_poll_failure_has_retry")
                status_retry.click()
                expect(fixture_row).to_contain_text("已取消", timeout=4_000)
                check(fixture_row.get_by_role("button", name="取消", exact=True).count() == 0, "fixture_cancelled_is_render_only")

                # DELETE accepted 先保留 tombstone，只有 fixture succeeded 才从当前页移除。
                fixture_row.get_by_role("button", name="删除", exact=True).click()
                delete_dialog = page.get_by_role("dialog", name="删除文档")
                delete_dialog.get_by_role("button", name="确认删除", exact=True).click()
                expect(fixture_row).to_contain_text("删除任务已提交", timeout=2_000)
                check(True, "fixture_delete_tombstone_retained")
                expect(page.get_by_text(fixture_document_title, exact=True)).to_have_count(0, timeout=6_000)
                check(True, "fixture_delete_succeeded_removes_tombstone")

                question_input = page.get_by_role("textbox", name="问题输入框")
                chat_card = question_input.locator(
                    "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' card ')][1]"
                )
                assistant_bubbles = chat_card.locator("div[class~='flex'][class~='justify-start'] > div")
                user_bubbles = chat_card.locator("div[class~='flex'][class~='justify-end'] > div")
                citation_cards = chat_card.locator("details")
                assistants_before = assistant_bubbles.count()
                users_before = user_bubbles.count()
                citations_before = citation_cards.count()

                # SSE error：stage、延迟 heartbeat、partial token 后错误；错误原文绝不进入 UI，也不固化回答/引用。
                question_input.fill("fixture error scenario")
                page.get_by_role("button", name="发送", exact=True).click()
                page.get_by_text("检索中…", exact=True).wait_for(timeout=2_000)
                page.get_by_text("已收到心跳", exact=False).wait_for(timeout=2_000)
                chat_card.get_by_role("alert").wait_for(state="visible", timeout=3_000)
                fixture_text = chat_card.inner_text()
                check("FIXTURE_RAW_SSE_CANARY" not in fixture_text, "fixture_raw_sse_error_hidden")
                check(user_bubbles.count() == users_before + 1, "fixture_error_adds_one_user")
                check(assistant_bubbles.count() == assistants_before, "fixture_error_adds_no_assistant")
                check(citation_cards.count() == citations_before, "fixture_error_adds_no_citation")

                # no-done close 仍只能显示固定失败文案；retry 保持同一 conversation 且不会重复 user bubble。
                chat_card.get_by_role("button", name="重试", exact=True).click()
                chat_card.get_by_role("alert").wait_for(state="visible", timeout=3_000)
                check(user_bubbles.count() == users_before + 1, "fixture_no_done_retry_does_not_duplicate_user")
                check(assistant_bubbles.count() == assistants_before, "fixture_no_done_adds_no_assistant")
                check(citation_cards.count() == citations_before, "fixture_no_done_adds_no_citation")

                # cancel 场景必须先产生 partial token、保持 pending read，再通过 AbortSignal 安全结束。
                chat_card.get_by_role("button", name="重试", exact=True).click()
                page.get_by_text("fixture-cancel-partial", exact=True).wait_for(timeout=2_000)
                chat_card.get_by_role("button", name="取消", exact=True).click()
                expect(chat_card.get_by_role("alert")).to_contain_text("已取消", timeout=2_000)
                check(user_bubbles.count() == users_before + 1, "fixture_cancel_retry_does_not_duplicate_user")
                check(assistant_bubbles.count() == assistants_before, "fixture_cancel_adds_no_assistant")
                check(citation_cards.count() == citations_before, "fixture_cancel_adds_no_citation")

                # retry-success 才会增加一个助手消息与一个引用卡。
                chat_card.get_by_role("button", name="重试", exact=True).click()
                expect(assistant_bubbles).to_have_count(assistants_before + 1, timeout=3_000)
                expect(citation_cards).to_have_count(citations_before + 1, timeout=3_000)
                check(user_bubbles.count() == users_before + 1, "fixture_success_retry_does_not_duplicate_user")
                check(True, "fixture_retry_success_adds_one_assistant_and_citation")

                # 第五条流在 token 后切换会话；旧流的 error/token/citation/cancel 都不得落入新视图。
                question_input.fill("fixture stale switch")
                page.get_by_role("button", name="发送", exact=True).click()
                page.get_by_text("fixture-stale-token", exact=True).wait_for(timeout=2_000)
                page.get_by_role("button", name="+ 新会话", exact=True).click()
                page.wait_for_timeout(250)
                switched_text = chat_card.inner_text()
                check(
                    "fixture-stale-token" not in switched_text
                    and "FIXTURE_RAW_SSE_CANARY" not in switched_text
                    and FIXTURE_CANCELLED_UI_TEXT not in switched_text,
                    "fixture_switch_discards_stale_stream",
                )
                check(citation_cards.count() == 0, "fixture_switch_has_no_stale_citation")

                # 回到真实已有会话后起第六条 pending 流。先恢复 fetch，再导航卸载组件；
                # 已创建的 ReadableStream 仍会在 unmount AbortSignal 上结束，但无法污染目标页。
                conversation_buttons.first.click()
                page.wait_for_timeout(150)
                question_input.fill("fixture stale unmount")
                page.get_by_role("button", name="发送", exact=True).click()
                page.get_by_text("fixture-stale-token", exact=True).wait_for(timeout=2_000)
                stats = fixture.stats()
                hits = stats.get("hits", {}) if isinstance(stats, dict) else {}
                check(
                    bool(stats.get("patched"))
                    and isinstance(hits, dict)
                    and sum(int(value) for value in hits.values()) > 0,
                    "fixture_fetch_hit_count_positive",
                )
                check(
                    stats.get("firstFourSameNonEmptyConversation") is True,
                    "fixture_first_four_retries_keep_same_nonempty_conversation",
                )
                fixture.restore()
                manual_restore_for_unmount = True

            check(manual_restore_for_unmount, "fixture_fetch_identity_restored_before_unmount")
            check(response_count("POST", "/api/v1/rag/query") == rag_responses_before, "fixture_rag_has_no_real_response")
            check(response_count("POST", rebuild_path) == rebuild_responses_before, "fixture_rebuild_has_no_real_response")
            check(response_count("DELETE", delete_path) == delete_responses_before, "fixture_delete_has_no_real_response")
            check(len(FAILED_REQUESTS) == failed_requests_before, "fixture_has_no_requestfailed_pollution")

            page.goto(args.base + "/console", wait_until="networkidle")
            page.wait_for_timeout(250)
            unmounted_text = page.locator("body").inner_text()
            check(
                "fixture-stale-token" not in unmounted_text
                and "FIXTURE_RAW_SSE_CANARY" not in unmounted_text
                and FIXTURE_CANCELLED_UI_TEXT not in unmounted_text,
                "fixture_unmount_discards_stale_stream",
            )

        print("== 9.55 Console 实际统计与 job 恢复/取消 ==")
        with (
            page.expect_response(
                lambda response: (
                    response.request.method == "GET"
                    and urlparse(response.url).path == "/api/v1/stats/storage"
                    and response.status == 200
                ),
                timeout=15_000,
            ) as storage_response_info,
            page.expect_response(
                lambda response: (
                    response.request.method == "GET"
                    and urlparse(response.url).path == "/api/v1/jobs"
                    and response.status == 200
                ),
                timeout=15_000,
            ) as jobs_response_info,
        ):
            page.goto(args.base + "/console", wait_until="networkidle")
        storage_stats = storage_response_info.value.json()
        jobs_summary = jobs_response_info.value.json()
        storage_card = page.get_by_role("heading", name="存储用量", exact=True).locator("xpath=ancestor::section[1]")
        jobs_card = page.get_by_role("heading", name="作业摘要", exact=True).locator("xpath=ancestor::section[1]")
        storage_text = storage_card.inner_text() if storage_card.is_visible() else ""
        jobs_text = jobs_card.inner_text() if jobs_card.is_visible() else ""
        check(
            isinstance(storage_stats, dict)
            and storage_stats.get("usedBytes", 0) > 0
            and storage_stats.get("fileCount", 0) >= 1
            and storage_stats.get("photoCount", 0) >= 1
            and storage_card.is_visible()
            and f"文件 {storage_stats.get('fileCount')} 个 · 照片 {storage_stats.get('photoCount')} 张" in storage_text,
            "console_storage_card_uses_real_api_data",
        )
        check(
            isinstance(jobs_summary, dict)
            and isinstance(jobs_summary.get("items"), list)
            and isinstance(jobs_summary.get("counts"), dict)
            and jobs_card.is_visible()
            and (
                f"排队 {jobs_summary['counts'].get('queued')} · 运行 {jobs_summary['counts'].get('running')} "
                f"· 失败 {jobs_summary['counts'].get('failed')} · 已取消 {jobs_summary['counts'].get('cancelled')}"
            ) in jobs_text,
            "console_jobs_card_uses_real_api_data",
        )
        if args.isolated_fixture_db and completed_document_id:
            deferred_job_id = _seed_deferred_rebuild_job(Path(args.isolated_fixture_db), completed_document_id)
            with page.expect_response(
                lambda response: (
                    response.request.method == "GET"
                    and urlparse(response.url).path == "/api/v1/jobs"
                    and parse_qs(urlparse(response.url).query).get("activeOnly") == ["true"]
                    and response.status == 200
                ),
                timeout=15_000,
            ):
                page.goto(args.base + "/console/kb", wait_until="networkidle")
            restored_row = page.get_by_text("e2e-sample.txt", exact=True).first.locator("xpath=ancestor::li[1]")
            expect(restored_row).to_contain_text("排队中", timeout=15_000)
            cancel_button = restored_row.get_by_role("button", name="取消任务", exact=True)
            check(cancel_button.is_visible(), "kb_reload_restores_active_job_and_cancel_control")
            cancel_path = f"/api/v1/jobs/{deferred_job_id}/cancel"
            with page.expect_response(
                lambda response: (
                    response.request.method == "POST"
                    and urlparse(response.url).path == cancel_path
                    and response.status == 200
                ),
                timeout=15_000,
            ) as cancel_response_info:
                cancel_button.click()
            cancel_status = cancel_response_info.value.json()
            expect(restored_row).to_contain_text("已取消", timeout=15_000)
            check(
                isinstance(cancel_status, dict)
                and cancel_status.get("status") == "cancelled"
                and cancel_status.get("errorCode") == "JOB_CANCELLED",
                "kb_cancel_real_job_shows_terminal_state",
            )
        else:
            # 外部 loopback 模式不触碰调用方的数据库；该模式仍验证上面的真实
            # Console API 卡，但不能安全地人为创建待取消的 durable job。
            print("外部服务模式：跳过仅隔离栈的 active job fixture。")

        # 真实上传→worker→RAG 成功后，以 Playwright 路由提供完全确定的分页页；
        # 不写 DB、不开后门，route context 无论断言结果如何都会解除拦截。
        print("== 9.6 会话/消息分页本地候选闭环 ==")
        pagination_failed_requests_before = len(FAILED_REQUESTS)
        with conversation_pagination_fixture_context(page) as pagination_fixture:
            page.goto(args.base + "/console/kb", wait_until="networkidle")
            page.set_viewport_size({"width": 1440, "height": 900})
            fixture_list_buttons = page.locator("#kb-conversation-list ul button")
            page.get_by_role("button", name="分页会话三", exact=True).wait_for(timeout=5_000)
            first_titles = fixture_list_buttons.all_inner_texts()
            check(
                first_titles == ["分页会话三", "分页会话二"],
                "fixture_conversation_first_page_is_stable",
            )

            with page.expect_response(
                lambda response: (
                    response.request.method == "GET"
                    and urlparse(response.url).path == "/api/v1/conversations"
                    and parse_qs(urlparse(response.url).query).get("cursor") == ["fixture-list-page-2"]
                    and response.status == 200
                ),
                timeout=5_000,
            ):
                page.get_by_role("button", name="加载更多会话", exact=True).click()
            page.get_by_role("button", name="分页会话一", exact=True).wait_for(timeout=5_000)
            all_titles = fixture_list_buttons.all_inner_texts()
            check(
                all_titles == ["分页会话三", "分页会话二", "分页会话一"]
                and len(all_titles) == len(set(all_titles)),
                "fixture_conversation_second_page_has_no_duplicates",
            )

            with page.expect_response(
                lambda response: (
                    response.request.method == "GET"
                    and urlparse(response.url).path == "/api/v1/conversations/fixture-conv-3"
                    and "cursor" not in parse_qs(urlparse(response.url).query)
                    and response.status == 200
                ),
                timeout=5_000,
            ):
                page.get_by_role("button", name="分页会话三", exact=True).click()
            latest_older = str(pagination_fixture["latestOlder"])
            latest_newer = str(pagination_fixture["latestNewer"])
            oldest = str(pagination_fixture["oldest"])
            middle = str(pagination_fixture["middle"])
            page.get_by_text(latest_newer, exact=False).wait_for(timeout=5_000)
            history = page.get_by_test_id("kb-chat-history")
            latest_text = history.inner_text()
            check(
                latest_text.index(latest_older) < latest_text.index(latest_newer),
                "fixture_latest_message_page_is_ascending",
            )
            question_input = page.get_by_role("textbox", name="问题输入框", exact=True)
            question_input.fill("fixture-concurrent-question")
            scroll_before = history.evaluate(
                """
                element => {
                  const max = Math.max(0, element.scrollHeight - element.clientHeight);
                  element.scrollTop = Math.max(0, max - 96);
                  return { top: element.scrollTop, height: element.scrollHeight, client: element.clientHeight };
                }
                """
            )
            with page.expect_response(
                lambda response: (
                    response.request.method == "GET"
                    and urlparse(response.url).path == "/api/v1/conversations/fixture-conv-3"
                    and parse_qs(urlparse(response.url).query).get("cursor") == ["fixture-history-page-2"]
                    and response.status == 200
                ),
                timeout=5_000,
            ):
                # 在同一页面 JS task 内启动历史页与 form submit：前者必须先同步
                # 置位 messageLoadingRef，后者只能被入口守卫拦截。直接 click 保留
                # 人工设定的滚动位置，避免 Playwright 自动滚动污染锚点。
                page.evaluate(
                    """
                    () => {
                      const older = [...document.querySelectorAll("button")].find(
                        (button) => button.textContent?.trim() === "加载更早消息",
                      );
                      const form = document.querySelector("form");
                      if (!(older instanceof HTMLButtonElement) || !(form instanceof HTMLFormElement)) {
                        throw new Error("pagination concurrency controls unavailable");
                      }
                      older.click();
                      form.requestSubmit();
                    }
                    """
                )
            page.get_by_text(oldest, exact=False).wait_for(timeout=5_000)
            # 等待 viewport 布局稳定(scrollHeight 连续两个 rAF 不变)后再读取锚点数值,
            # 避免固定时长窗口内异步布局/effect 覆盖 useLayoutEffect 的锚点调整。
            page.wait_for_function(
                """() => {
                    const el = document.querySelector('[data-testid="kb-chat-history"]');
                    if (!el) return false;
                    const h = el.scrollHeight;
                    return new Promise((resolve) => {
                        requestAnimationFrame(() => requestAnimationFrame(() => resolve(el.scrollHeight === h)));
                    });
                }""",
                timeout=2_000,
            )
            page_text = history.inner_text()
            marker_positions = [page_text.index(marker) for marker in (oldest, middle, latest_older, latest_newer)]
            check(
                marker_positions == sorted(marker_positions),
                "fixture_older_messages_prepend_in_ascending_history",
            )
            scroll_after = history.evaluate(
                "element => ({ top: element.scrollTop, height: element.scrollHeight, client: element.clientHeight })"
            )
            before_top = float(scroll_before["top"])
            before_height = float(scroll_before["height"])
            before_max = max(0.0, before_height - float(scroll_before["client"]))
            after_top = float(scroll_after["top"])
            after_height = float(scroll_after["height"])
            after_max = max(0.0, after_height - float(scroll_after["client"]))
            height_delta = after_height - before_height


            check(
                before_max > 96
                and before_top < before_max - 20
                and height_delta > 0
                and abs(after_top - (before_top + height_delta)) <= 8
                and after_top < after_max - 20,
                "fixture_message_prepend_preserves_scroll_anchor",
            )
            calls = pagination_fixture["calls"]
            check(
                isinstance(calls, Counter)
                and calls["rag_during_history"] == 0
                and "fixture-concurrent-question" not in page_text,
                "fixture_history_loading_blocks_same_task_rag_submit",
            )
            check(
                isinstance(calls, Counter)
                and calls == Counter({
                    "list_first": 1,
                    "list_second": 1,
                    "messages_latest": 1,
                    "messages_older": 1,
                }),
                "fixture_pagination_requests_are_one_page_each",
            )
        check(
            len(FAILED_REQUESTS) == pagination_failed_requests_before,
            "fixture_pagination_has_no_requestfailed_pollution",
        )

        print("== 9.7 知识库文档分页本地候选闭环 ==")
        document_pagination_failures_before = len(FAILED_REQUESTS)
        with document_pagination_fixture_context(page) as document_pagination_calls:
            page.goto(args.base + "/console/kb", wait_until="networkidle")
            page.set_viewport_size({"width": 1440, "height": 900})
            page.get_by_text("分页文档 21", exact=True).wait_for(timeout=5_000)
            initial_rows = page.locator("#kb-documents-panel li p:first-child").all_inner_texts()
            check(
                len(initial_rows) == 20 and initial_rows[0] == "分页文档 21",
                "fixture_document_first_page_has_20_rows",
            )

            with page.expect_response(
                lambda response: (
                    response.request.method == "GET"
                    and urlparse(response.url).path == "/api/v1/knowledge/documents"
                    and parse_qs(urlparse(response.url).query).get("cursor") == ["fixture-doc-page-2"]
                    and response.status == 200
                ),
                timeout=5_000,
            ):
                page.get_by_role("button", name="加载更多", exact=True).click()
            page.get_by_text("分页文档 1", exact=True).wait_for(timeout=5_000)
            all_rows = page.locator("#kb-documents-panel li p:first-child").all_inner_texts()
            check(
                len(all_rows) == 21
                and all_rows == [f"分页文档 {index}" for index in range(21, 0, -1)]
                and len(all_rows) == len(set(all_rows)),
                "fixture_document_second_page_is_complete_without_duplicates",
            )
            check(
                page.get_by_role("button", name="加载更多", exact=True).count() == 0,
                "fixture_document_cursor_exhaustion_hides_load_more",
            )
            check(
                document_pagination_calls == Counter({"first": 1, "second": 1}),
                "fixture_document_pagination_requests_are_one_page_each",
            )
        check(
            len(FAILED_REQUESTS) == document_pagination_failures_before,
            "fixture_document_pagination_has_no_requestfailed_pollution",
        )

        print("== 9.8 /enter 私人入口仍前往登录页（已有会话）==")
        page.goto(args.base + "/enter", wait_until="networkidle")
        page.wait_for_function("window.__narrativeEnterListenerReady === true", timeout=5000)
        tear_link = page.get_by_role("link", name="撕开信封，进入私人登录页", exact=True)
        tear_link.focus()
        tear_link.press("Enter")
        page.wait_for_url(args.base + "/login", timeout=15000)
        check(page.url.endswith("/login"), "enter_private_route_does_not_infer_authenticated_session")

        print("== 10. 登出 ==")
        page.goto(args.base + "/console", wait_until="networkidle")
        page.evaluate("sessionStorage.setItem('star-isle:enter-completed', 'true')")
        with page.expect_response(
            lambda r: r.url.endswith("/api/v1/auth/logout") and r.request.method == "POST" and r.status == 204
        ):
            page.get_by_role("button", name="退出").click(timeout=5000)
        page.wait_for_url(args.base + "/", timeout=15000)
        page.wait_for_load_state("networkidle", timeout=15000)
        cookies_after = {c["name"] for c in page.context.cookies()}
        check("star_isle_access" not in cookies_after, "access Cookie 已清除")
        check("star_isle_refresh" not in cookies_after, "refresh Cookie 已清除")
        with allow_expected_error_responses(*AUTH_GUARD_EXPECTED_ERROR_RESPONSES):
            page.goto(args.base + "/console", wait_until="networkidle")
        check(page.url.startswith(args.base + "/login"), "登出后 /console 回到登录")

        print("== 11. 错误观察 ==")
        check(len(PAGE_ERRORS) == 0, "无页面 JS 错误")
        check(len(FAILED_REQUESTS) == 0, "无失败请求")

    summary = _safe_summary()
    if EVIDENCE is None:
        raise RuntimeError("browser evidence lifecycle was not initialized")
    (EVIDENCE / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== 结果 ===")
    print(f"通过检查:12 组;失败:{len(FAILURES)} 组")
    if FAILURES:
        print("\n".join(FAILURES))
    return 1 if FAILURES else 0


def main(argv: list[str] | None = None) -> int:
    global EVIDENCE
    parser = _SafeArgumentParser()
    parser.add_argument("--base", default="http://localhost:4321")
    parser.add_argument("--base-url", help="--base 的同义参数，便于静态预览回归")
    parser.add_argument("--api-base", default=os.environ.get("API_E2E_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--public-only", action="store_true", help="仅运行不依赖 API 的公开页回归")
    parser.add_argument("--external-services", action="store_true", help="使用已启动的 loopback API/Web；默认创建隔离临时栈")
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument(
        "--evidence-dir",
        help="显式 opt-in 的新 system-temp 证据目录；其中截图可能包含私有 UI。",
    )
    parser.add_argument("--owned-mail-receipt", help=argparse.SUPPRESS)
    parser.add_argument("--isolated-fixture-db", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    args.base = args.base_url or args.base
    _reset_results()

    try:
        if not args.public_only and not (is_loopback_origin(args.base) and is_loopback_origin(args.api_base)):
            print("BROWSER_E2E_LOOPBACK_REQUIRED", file=sys.stderr)
            return 1
        if not args.public_only:
            if args.external_services:
                try:
                    args.test_password, _mail_hmac_key = _required_external_credentials()
                except RuntimeError:
                    print("BROWSER_E2E_TEST_CREDENTIALS_REQUIRED", file=sys.stderr)
                    return 1
                args.isolated_fixture_db = _validated_isolated_fixture_db(args.isolated_fixture_db)
            else:
                if args.isolated_fixture_db:
                    raise HarnessSafetyError("isolated fixture DB is child-only")
                return _run_isolated_local_e2e(args)
        with _evidence_directory(args) as evidence, _mail_receipt_file(args) as receipt_file:
            EVIDENCE = evidence
            sample_txt = evidence / "e2e-sample.txt"
            sample_png = evidence / "e2e-sample.png"
            sample_txt.write_text("browser harness sample document", encoding="utf-8")
            sample_png.write_bytes(PNG_1PX)
            return _browser_flow(args, sample_txt, sample_png, receipt_file)
    except (HarnessSafetyError, OSError, RuntimeError, subprocess.SubprocessError):
        print("BROWSER_E2E_FAILED", file=sys.stderr)
        return 1
    except Exception:
        print("BROWSER_E2E_FAILED", file=sys.stderr)
        return 1
    finally:
        EVIDENCE = None


if __name__ == "__main__":
    raise SystemExit(main())
