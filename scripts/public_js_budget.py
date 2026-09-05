"""Check public-page request graph and JavaScript gzip budgets.

Normal output and optional reports retain only fixed check codes, routes,
method/pathname labels, and byte counts. ``--output`` is an exclusive write.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from harness_safety import (
    HarnessSafetyError,
    is_http_origin,
    is_loopback_origin,
    new_output_file_path,
    safe_pathname,
    safe_request_label,
)
try:
    from playwright.sync_api import sync_playwright
except ImportError:  # Pure output-safety helpers remain testable without Playwright installed.
    sync_playwright = None


ROUTES = ("/", "/projects/", "/resume/", "/404.html")
MAX_GZIP_BYTES = 60_000


class _SafeArgumentParser(argparse.ArgumentParser):
    """Do not reflect a supplied base URL in parser diagnostics."""

    def error(self, _message: str) -> None:
        self.exit(2, "PUBLIC_JAVASCRIPT_BUDGET_ARGUMENTS_INVALID\n")


@dataclass
class RouteReport:
    route: str
    js: list[tuple[str, int]] = field(default_factory=list)
    gzip_bytes: int = 0
    errors: list[str] = field(default_factory=list)


def same_origin(url: str, base_url: str) -> bool:
    candidate = urlparse(url)
    origin = urlparse(base_url)
    return (candidate.scheme, candidate.netloc) == (origin.scheme, origin.netloc)


def route_url(base_url: str, route: str) -> str:
    return f"{base_url.rstrip('/')}{route}"


def _safe_request_error(category: str, method: str, url: str) -> str:
    """Return a report-safe request classification without dynamic path segments."""

    return f"{category}:{safe_request_label(method, url)}"


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


def inspect_route(browser, base_url: str, route: str) -> RouteReport:
    report = RouteReport(route=route)
    context = browser.new_context()
    page = context.new_page()
    responses: list[tuple[str, object]] = []
    origin = urlparse(base_url)

    def record_request(request) -> None:
        parsed = urlparse(request.url)
        if parsed.path.startswith("/api/"):
            report.errors.append(_safe_request_error("api_request", request.method, request.url))
        if parsed.scheme in {"http", "https"} and (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc):
            report.errors.append(_safe_request_error("external_request", request.method, request.url))

    def record_response(response) -> None:
        request = response.request
        if response.status < 400 and request.resource_type == "script" and same_origin(response.url, base_url):
            responses.append((safe_pathname(response.url), response))

    def record_console(message) -> None:
        if message.type == "error":
            report.errors.append("console_error")
        if "[object Object]" in message.text:
            report.errors.append("console_object")

    page.on("request", record_request)
    page.on("response", record_response)
    page.on("pageerror", lambda _error: report.errors.append("page_error"))
    page.on("console", record_console)
    page.on("requestfailed", lambda request: report.errors.append(_safe_request_error("failed_request", request.method, request.url)))

    try:
        response = page.goto(route_url(base_url, route), wait_until="networkidle")
        if response is None:
            report.errors.append("navigation_no_response")
        elif response.status >= 400:
            report.errors.append(f"navigation_status:{response.status}")

        content = page.content()
        if "[object Object]" in content:
            report.errors.append("page_content_object")
        broken_labels = missing_aria_labelledby_ids(page)
        if broken_labels:
            report.errors.append(f"broken_aria_labelledby:{len(broken_labels)}")

        for pathname, response in responses:
            try:
                body = response.body()
            except Exception:
                report.errors.append(f"js_body_unavailable:{pathname}")
                continue
            compressed = len(gzip.compress(body, compresslevel=9, mtime=0))
            report.js.append((pathname, compressed))
            report.gzip_bytes += compressed
        if report.gzip_bytes > MAX_GZIP_BYTES:
            report.errors.append("js_gzip_budget_exceeded")
    finally:
        context.close()

    return report


def _output_path(value: Path) -> Path:
    repository_root = Path(__file__).resolve().parent.parent
    roots = tuple(
        Path(raw)
        for raw in (os.environ.get("DATA_ROOT"), os.environ.get("DATA_HOST_ROOT"))
        if raw and raw.strip()
    )
    return new_output_file_path(value, repository_root=repository_root, data_roots=roots)


def main(argv: list[str] | None = None) -> int:
    parser = _SafeArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="static preview root, for example http://127.0.0.1:4321")
    parser.add_argument(
        "--allow-remote-public",
        action="store_true",
        help="明确允许只读公开页面预算检查；不会加载私有 fixture 或凭据。",
    )
    parser.add_argument("--output", type=Path, help="optional exclusive JSON report path")
    args = parser.parse_args(argv)

    if not is_loopback_origin(args.base_url) and not (
        args.allow_remote_public and is_http_origin(args.base_url)
    ):
        print("PUBLIC_JAVASCRIPT_BUDGET_LOOPBACK_REQUIRED", file=sys.stderr)
        return 1

    output: Path | None = None
    if args.output is not None:
        try:
            output = _output_path(args.output)
        except HarnessSafetyError:
            print("PUBLIC_JAVASCRIPT_BUDGET_OUTPUT_REJECTED", file=sys.stderr)
            return 1

    reports: list[RouteReport] = []
    try:
        if sync_playwright is None:
            raise RuntimeError("Playwright is unavailable")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                for route in ROUTES:
                    reports.append(inspect_route(browser, args.base_url, route))
            finally:
                browser.close()
    except Exception:
        print("PUBLIC_JAVASCRIPT_BUDGET_FAILED", file=sys.stderr)
        return 1

    failed = False
    for report in reports:
        print(f"ROUTE {report.route} gzipBytes={report.gzip_bytes} limit={MAX_GZIP_BYTES}")
        for pathname, size in report.js:
            print(f"JAVASCRIPT {pathname} gzipBytes={size}")
        if not report.js:
            print("JAVASCRIPT none gzipBytes=0")
        for error in report.errors:
            failed = True
            print(f"CHECK_FAILED {error}")

    if output is not None:
        try:
            with output.open("x", encoding="utf-8") as handle:
                json.dump([asdict(report) for report in reports], handle, ensure_ascii=False, indent=2)
                handle.write("\n")
        except (FileExistsError, OSError):
            print("PUBLIC_JAVASCRIPT_BUDGET_OUTPUT_FAILED", file=sys.stderr)
            return 1

    if failed:
        print("PUBLIC_JAVASCRIPT_BUDGET_FAILED")
        return 1
    print("PUBLIC_JAVASCRIPT_BUDGET_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
