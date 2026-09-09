"""对象存储配额的跨进程互斥回归。"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event
import time

import pytest
from starlette.requests import Request

import app.storage as storage_module
from app.config import settings
from app.main import storage_unavailable_exception_handler
from app.storage import StorageAdapter, StorageQuotaExceeded, StorageUnavailable


def _wait_for_path(
    path: Path, process: subprocess.Popen[str], timeout: float = 10.0
) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                f"storage child exited before signalling readiness: {stdout!r} {stderr!r}"
            )
        if time.monotonic() >= deadline:
            process.kill()
            stdout, stderr = process.communicate()
            raise AssertionError(
                f"storage child did not become ready: {stdout!r} {stderr!r}"
            )
        time.sleep(0.02)


def test_quota_lock_rechecks_usage_after_another_process_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = tmp_path / "objects"
    ready = tmp_path / "child-ready"
    adapter = StorageAdapter(root)
    monkeypatch.setattr(settings, "storage_quota_bytes", 10)

    child_code = """
import sys
from pathlib import Path
from app.config import settings
from app.storage import StorageAdapter, StorageQuotaExceeded

root = Path(sys.argv[1])
ready = Path(sys.argv[2])
settings.storage_quota_bytes = 10
adapter = StorageAdapter(root)
ready.write_text("ready", encoding="utf-8")
try:
    adapter.save(b"bbbbbb", "originals", ".bin")
except StorageQuotaExceeded:
    print("quota", flush=True)
else:
    print("saved", flush=True)
"""
    child_env = os.environ.copy()
    child_env["PYTHON_DOTENV_DISABLED"] = "1"
    api_root = Path(__file__).resolve().parents[1]

    with adapter._quota_lock():
        process = subprocess.Popen(
            [sys.executable, "-c", child_code, str(root), str(ready)],
            cwd=api_root,
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        _wait_for_path(ready, process)
        time.sleep(0.2)
        assert process.poll() is None, "a second process must wait for the quota lock"
        first = root / "originals" / "first.bin"
        first.parent.mkdir(parents=True, exist_ok=True)
        first.write_bytes(b"aaaaaa")

    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, stderr
    assert stdout.strip() == "quota"
    assert adapter._allocated_bytes() == 6


def test_quota_lock_serializes_threads_in_the_api_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = tmp_path / "objects"
    adapter = StorageAdapter(root)
    monkeypatch.setattr(settings, "storage_quota_bytes", 10)
    started = Event()

    def save_second() -> str:
        started.set()
        return adapter.save(b"bbbbbb", "originals", ".bin")

    with ThreadPoolExecutor(max_workers=1) as executor:
        with adapter._quota_lock():
            future = executor.submit(save_second)
            assert started.wait(timeout=2)
            time.sleep(0.1)
            assert not future.done(), "another API thread must wait for the quota lock"
            first = root / "originals" / "first.bin"
            first.parent.mkdir(parents=True, exist_ok=True)
            first.write_bytes(b"aaaaaa")

        with pytest.raises(StorageQuotaExceeded, match="配额不足"):
            future.result(timeout=5)

    assert adapter._allocated_bytes() == 6


def test_quota_lock_file_is_not_counted_as_object_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    adapter = StorageAdapter(tmp_path / "objects")
    monkeypatch.setattr(settings, "storage_quota_bytes", 6)

    key = adapter.save(b"123456", "originals", ".bin")

    assert adapter.open("originals", key) == b"123456"
    assert adapter._allocated_bytes() == 6


def test_quota_lock_times_out_and_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = tmp_path / "objects"
    holder = StorageAdapter(root)
    contender = StorageAdapter(root)
    monkeypatch.setattr(settings, "storage_quota_bytes", 10)
    monkeypatch.setattr(storage_module, "QUOTA_LOCK_TIMEOUT_SECONDS", 0.1)

    with holder._quota_lock():
        started = time.monotonic()
        with pytest.raises(StorageUnavailable, match="存储暂不可用"):
            contender.save(b"blocked", "originals", ".bin")
        assert time.monotonic() - started < 2

    key = contender.save(b"ok", "originals", ".bin")
    assert contender.open("originals", key) == b"ok"


def test_storage_lock_timeout_maps_to_retryable_http_response():
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/files",
            "headers": [],
        }
    )
    request.state.request_id = "request-storage-timeout"

    response = asyncio.run(
        storage_unavailable_exception_handler(request, StorageUnavailable())
    )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert json.loads(response.body) == {
        "error": {
            "code": "INTERNAL",
            "message": "对象存储暂不可用,请稍后重试。",
            "requestId": "request-storage-timeout",
        }
    }
