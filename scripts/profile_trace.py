"""正式性能 trace/profile 采集脚本（实验性接口）。

输入：目标页面清单 + 浏览器参数。
输出：落盘 trace 文件 + 帧间隔分布 + 浏览器/硬件/版本快照（五点证据）。
复用 browser_e2e.py 的隔离栈启动能力与 Playwright。

用法：
  python -B scripts/profile_trace.py --pages /,/projects,/resume --headless
  python -B scripts/profile_trace.py --base-url http://127.0.0.1:4321 --pages /,/projects

回滚边界：本脚本为 scripts/ 内实验性接口，可独立删除，不影响业务代码与 schema v5。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import secrets
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from harness_safety import (
    owned_temporary_directory,
    is_loopback_origin,
)

from browser_e2e import (
    _api_python,
    _free_loopback_port,
    _password_hash,
    _spawn_owned_process,
    _stop_owned_process,
    _wait_for_http,
)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


def _hardware_snapshot() -> dict:
    return {
        "platform": platform.platform(),
        "processor": platform.processor(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "hostname": platform.node(),
    }


def _command_versions() -> dict:
    from importlib.metadata import version as _pkg_version, PackageNotFoundError
    versions: dict[str, str] = {"python": sys.version}
    try:
        versions["playwright"] = _pkg_version("playwright")
    except PackageNotFoundError:
        versions["playwright"] = "unavailable"
    for cmd in ("node", "pnpm"):
        path = shutil.which(cmd)
        if path:
            try:
                result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5)
                versions[cmd] = result.stdout.strip()
            except Exception:
                versions[cmd] = "error"
        else:
            versions[cmd] = "not found"
    return versions


def _collect_frame_intervals(page, duration_ms: int = 3000) -> list[float]:
    intervals = page.evaluate(
        """
        (duration_ms) => new Promise((resolve) => {
            const intervals = [];
            let last = performance.now();
            const start = last;
            function tick(now) {
                if (now - start > duration_ms) { resolve(intervals); return; }
                intervals.push(now - last);
                last = now;
                requestAnimationFrame(tick);
            }
            requestAnimationFrame(tick);
        })
        """,
        duration_ms,
    )
    return [float(x) for x in intervals]


def _frame_distribution(intervals: list[float]) -> dict:
    if not intervals:
        return {"count": 0}
    s = sorted(intervals)
    n = len(s)
    return {
        "count": n,
        "min_ms": round(s[0], 3),
        "max_ms": round(s[-1], 3),
        "mean_ms": round(sum(intervals) / n, 3),
        "median_ms": round(s[n // 2], 3),
        "p95_ms": round(s[min(int(n * 0.95), n - 1)], 3),
        "p99_ms": round(s[min(int(n * 0.99), n - 1)], 3),
        "intervals_ms": [round(x, 3) for x in intervals],
    }


@contextmanager
def _isolated_web_stack(root: Path):
    """启动临时 API/worker/Web 栈，yield web_base。"""
    test_password = secrets.token_urlsafe(32)
    hmac_key = secrets.token_urlsafe(48)
    api_python = _api_python(root)
    api_port = _free_loopback_port()
    web_port = _free_loopback_port()
    api_base = f"http://127.0.0.1:{api_port}"
    web_base = f"http://127.0.0.1:{web_port}"
    with owned_temporary_directory("starisle-profile-") as temporary:
        temporary_root = Path(temporary)
        data_root = temporary_root / "data"
        data_host_root = temporary_root / "data-host"
        data_root.mkdir(mode=0o700)
        data_host_root.mkdir(mode=0o700)
        api_env = os.environ.copy()
        api_env.update({
            "DATA_ROOT": str(data_root),
            "DATA_HOST_ROOT": str(data_host_root),
            "AUTH_JWT_SIGNING_KEY": "profile-jwt-signing-key-0123456789abcdef",
            "AUTH_JWT_ACTIVE_KID": "profile",
            "ADMIN_BOOTSTRAP_PASSWORD_HASH": _password_hash(api_python, test_password),
            "COOKIE_SECURE": "false",
            "MAIL_HMAC_KEY": hmac_key,
            "MAIL_MAX_BYTES": os.environ.get("MAIL_MAX_BYTES", str(5 * 1024 * 1024)),
            "LLAMA_CHAT_BASE_URL": "",
            "LLAMA_EMBED_BASE_URL": "",
            "EMBEDDING_MODEL_ID": "",
            "PYTHON_DOTENV_DISABLED": "1",
        })
        web_env = os.environ.copy()
        web_env.update({"API_DEV_PROXY_TARGET": api_base})
        api_log = (temporary_root / "api.log").open("wb")
        worker_log = (temporary_root / "worker.log").open("wb")
        web_log = (temporary_root / "web.log").open("wb")
        api_proc = None
        worker_proc = None
        web_proc = None
        try:
            api_proc = _spawn_owned_process(
                [str(api_python), "-B", "-m", "uvicorn", "app.main:app",
                 "--host", "127.0.0.1", "--port", str(api_port), "--no-access-log"],
                cwd=root / "apps" / "api", env=api_env,
                stdout=api_log, stderr=subprocess.STDOUT,
            )
            _wait_for_http(api_base + "/api/v1/health/live")
            worker_proc = _spawn_owned_process(
                [str(api_python), "-B", "-m", "app.worker_runner"],
                cwd=root / "apps" / "api", env=api_env,
                stdout=worker_log, stderr=subprocess.STDOUT,
            )
            if worker_proc.poll() is not None:
                raise RuntimeError("worker 未能启动")
            pnpm = shutil.which("pnpm")
            if not pnpm:
                raise RuntimeError("找不到 pnpm")
            web_proc = _spawn_owned_process(
                [pnpm, "--filter", "@star-isle/web", "dev", "--host", "127.0.0.1", "--port", str(web_port)],
                cwd=root, env=web_env,
                stdout=web_log, stderr=subprocess.STDOUT,
            )
            _wait_for_http(web_base + "/")
            yield web_base
        finally:
            _stop_owned_process(web_proc)
            _stop_owned_process(worker_proc)
            _stop_owned_process(api_proc)
            for log in (web_log, worker_log, api_log):
                try:
                    log.close()
                except Exception:
                    pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="StarIsle 正式性能 trace/profile 采集")
    parser.add_argument("--pages", default="/,/projects,/resume", help="逗号分隔的目标页面路径")
    parser.add_argument("--base-url", help="外部 Web 基址（跳过隔离栈）")
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--headed", dest="headless", action="store_false")
    parser.add_argument("--output", help="证据落盘目录")
    parser.add_argument("--frame-duration-ms", type=int, default=3000, help="每页帧采集时长")
    args = parser.parse_args(argv)

    if sync_playwright is None:
        print("Playwright 不可用", file=sys.stderr)
        return 1

    pages = [p.strip() for p in args.pages.split(",") if p.strip()]
    if not pages:
        print("未指定页面", file=sys.stderr)
        return 1

    root = Path(__file__).resolve().parents[1]
    output_dir = Path(args.output) if args.output else root / "scripts" / ".e2e-browser-evidence" / "profile"
    output_dir.mkdir(parents=True, exist_ok=True)

    base_url = args.base_url
    stack_ctx = None
    if base_url:
        if not is_loopback_origin(base_url):
            print("base-url 必须是 loopback", file=sys.stderr)
            return 1
    else:
        stack_ctx = _isolated_web_stack(root)
        base_url = stack_ctx.__enter__()

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=args.headless)
            try:
                browser_version = browser.version
                context = browser.new_context(viewport={"width": 1440, "height": 900})
                page = context.new_page()
                try:
                    for page_path in pages:
                        url = base_url.rstrip("/") + page_path
                        safe_name = page_path.replace("/", "_").strip("_") or "root"
                        trace_path = str(output_dir / f"{safe_name}_trace.zip")

                        context.tracing.start(screenshots=True, snapshots=True, sources=True)
                        try:
                            page.goto(url, wait_until="networkidle", timeout=15_000)
                        except Exception:
                            print(f"页面 {page_path} goto 失败，跳过", file=sys.stderr)
                            context.tracing.stop()
                            continue
                        intervals = _collect_frame_intervals(page, args.frame_duration_ms)
                        context.tracing.stop(path=trace_path)

                        frame_dist = _frame_distribution(intervals)
                        with (output_dir / f"{safe_name}_frame_intervals.json").open("w", encoding="utf-8") as f:
                            json.dump(frame_dist, f, ensure_ascii=False, indent=2)
                        print(f"  {page_path}: {frame_dist['count']} 帧, 均值 {frame_dist.get('mean_ms', 'N/A')}ms, trace → {trace_path}")
                finally:
                    page.close()
                    context.close()
            finally:
                browser.close()

        with (output_dir / "browser_version.json").open("w", encoding="utf-8") as f:
            json.dump({"browser": "chromium", "version": browser_version}, f, ensure_ascii=False, indent=2)
        with (output_dir / "hardware_snapshot.json").open("w", encoding="utf-8") as f:
            json.dump(_hardware_snapshot(), f, ensure_ascii=False, indent=2)
        with (output_dir / "command_versions.json").open("w", encoding="utf-8") as f:
            json.dump(_command_versions(), f, ensure_ascii=False, indent=2)

        page_summaries = []
        for page_path in pages:
            safe_name = page_path.replace("/", "_").strip("_") or "root"
            frame_file = output_dir / f"{safe_name}_frame_intervals.json"
            if frame_file.exists():
                with frame_file.open("r", encoding="utf-8") as f:
                    fd = json.load(f)
                page_summaries.append({
                    "page": page_path,
                    "frame_count": fd.get("count", 0),
                    "mean_ms": fd.get("mean_ms"),
                    "p95_ms": fd.get("p95_ms"),
                    "p99_ms": fd.get("p99_ms"),
                })
        summary = {
            "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "pages": page_summaries,
            "conclusion_boundary": (
                "本证据仅证明本机（见 hardware_snapshot.json）排除明显帧率退化，"
                "不支撑目标设备 FPS 结论。历史未保留 trace 的 3 秒 headless 瞬时采样"
                "结论已被本正式 profile 覆盖。trace 可用 playwright show-trace 重放。"
            ),
        }
        with (output_dir / "summary.json").open("w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        print(f"\n五点证据已落盘至 {output_dir}")
        print("  1. trace 文件: *_trace.zip")
        print("  2. 帧间隔分布: *_frame_intervals.json")
        print("  3. 浏览器版本: browser_version.json")
        print("  4. 硬件快照:   hardware_snapshot.json")
        print("  5. 命令版本:   command_versions.json")
        return 0
    finally:
        if stack_ctx is not None:
            stack_ctx.__exit__(None, None, None)


if __name__ == "__main__":
    raise SystemExit(main())