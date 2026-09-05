"""Regression coverage for local harness privacy and owned-resource boundaries."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import _isolation, e2e_smoke


SCRIPT_ROOT = Path(__file__).resolve().parents[3] / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from harness_safety import (  # noqa: E402
    HarnessSafetyError,
    OwnedProcessError,
    is_loopback_origin,
    new_output_file_path,
    new_temporary_directory_path,
    new_temporary_file_path,
    safe_pathname,
    safe_request_label,
    spawn_owned_process,
    stop_owned_process,
)
import harness_safety  # noqa: E402


def _browser_module():
    module_name = "starisle_browser_e2e_harness_test"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_ROOT / "browser_e2e.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _public_budget_module():
    module_name = "starisle_public_js_budget_harness_test"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_ROOT / "public_js_budget.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _kb_debug_module():
    module_name = "starisle_kb_debug_harness_test"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_ROOT / "debug_kb_403.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_safe_request_labels_drop_origin_query_fragment_and_canaries():
    canary = "PRIVATE_CANARY_DOCUMENT PRIVATE_CANARY_ANSWER PRIVATE_CANARY_CSRF C:/PRIVATE_CANARY_PATH"
    url = f"https://private.example.test/console/kb?secret={canary}#PRIVATE_CANARY_FRAGMENT"
    assert safe_pathname(url) == "/console/kb"
    assert safe_request_label("post", url) == "POST /console/kb"
    assert canary not in safe_request_label("POST", url)


def test_safe_pathname_templates_dynamic_segments_and_rejects_unknown_routes():
    canary = "PRIVATE_CANARY_MESSAGE_ID"
    assert safe_pathname(f"https://private.example.test/api/v1/mail/{canary}/read?csrf=PRIVATE_CANARY_CSRF") == (
        "/api/v1/mail/:id/read"
    )
    assert safe_pathname(f"https://private.example.test/_astro/{canary}.js") == "/_astro/:asset"
    assert safe_pathname(f"https://private.example.test/src/{canary}.ts") == "/src/:asset"
    assert safe_pathname(f"https://private.example.test/private/{canary}") == "/other"
    assert canary not in safe_request_label("POST", f"/api/v1/mail/{canary}/read")


def test_loopback_origin_rejects_remote_credentials_paths_queries_and_bad_ports():
    assert is_loopback_origin("http://127.0.0.1:8000")
    assert is_loopback_origin("https://[::1]")
    for value in (
        "https://outside.invalid",
        "http://user@127.0.0.1",
        "http://127.0.0.1/private",
        "http://127.0.0.1/?private=1",
        "http://127.0.0.1:bad",
    ):
        assert not is_loopback_origin(value)


def test_pytest_test_only_environment_is_system_temp_and_external_root_is_not_owned(tmp_path: Path):
    active_root = _isolation.validate_external_temp_root(os.environ["DATA_ROOT"])
    assert active_root.is_relative_to(Path(tempfile.gettempdir()).resolve())
    assert active_root != Path(tempfile.gettempdir()).resolve()
    assert os.environ["AUTH_JWT_ACTIVE_KID"] == "test"
    assert os.environ["PYTHON_DOTENV_DISABLED"] == "1"

    external = tmp_path / "external-harness-root"
    external.mkdir()
    assert _isolation.validate_external_temp_root(external) == external.resolve()
    assert external.exists()
    assert _isolation.owned_data_root() != external


def test_pytest_rejects_system_temp_root_and_retries_owned_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    class FlakyOwnedTemporaryDirectory:
        name = str(Path(tempfile.gettempdir()) / "starisle-test-owned-placeholder")

        def __init__(self) -> None:
            self.cleanup_calls = 0

        def cleanup(self) -> None:
            self.cleanup_calls += 1
            if self.cleanup_calls == 1:
                raise OSError("PRIVATE_CANARY_PATH")

    with pytest.raises(_isolation.TestIsolationError):
        _isolation.validate_external_temp_root(Path(tempfile.gettempdir()))

    owned = FlakyOwnedTemporaryDirectory()
    monkeypatch.setattr(_isolation, "_owned_data_root", owned)
    assert not _isolation.cleanup_owned_data_root()
    assert _isolation._owned_data_root is owned
    assert _isolation.cleanup_owned_data_root()
    assert _isolation._owned_data_root is None
    captured = capsys.readouterr()
    assert captured.err == "PYTEST_OWNED_TEMP_CLEANUP_FAILED\n"
    assert "PRIVATE_CANARY_PATH" not in captured.err


def test_pytest_sessionfinish_marks_cleanup_failure_as_failed(monkeypatch: pytest.MonkeyPatch):
    test_conftest = importlib.import_module("tests.conftest")
    session = SimpleNamespace(exitstatus=0)
    monkeypatch.setattr(test_conftest, "cleanup_owned_data_root", lambda: False)
    test_conftest.pytest_sessionfinish(session, 0)
    assert session.exitstatus == 1


def test_owned_temp_cleanup_and_synthetic_repository_path_rejection():
    owned = _isolation.owned_temporary_directory("starisle-harness-safety-")
    root = Path(owned.name)
    root.joinpath("marker").write_text("owned", encoding="utf-8")
    owned.cleanup()
    assert not root.exists()

    synthetic_repository_path = Path.cwd() / "synthetic-repository-data-root"
    with pytest.raises(_isolation.TestIsolationError):
        _isolation.validate_external_temp_root(synthetic_repository_path)


def test_exclusive_output_and_evidence_paths_reject_overwrite_and_synthetic_data_roots(tmp_path: Path):
    repository = tmp_path / "synthetic-repository"
    data_root = repository / "data"
    data_root.mkdir(parents=True)
    candidate = tmp_path / "report.json"
    validated = new_output_file_path(candidate, repository_root=repository)
    with validated.open("x", encoding="utf-8"):
        pass
    with pytest.raises(HarnessSafetyError):
        new_output_file_path(candidate, repository_root=repository)
    with pytest.raises(HarnessSafetyError):
        new_output_file_path(data_root / "report.json", repository_root=repository)

    evidence = tmp_path / "private-evidence"
    assert new_temporary_directory_path(evidence) == evidence.resolve()
    evidence.mkdir()
    with pytest.raises(HarnessSafetyError):
        new_temporary_directory_path(evidence)
    with pytest.raises(HarnessSafetyError):
        new_temporary_directory_path(data_root / "evidence", protected_roots=(data_root,))

    receipt = tmp_path / "receipt.json"
    assert new_temporary_file_path(receipt, protected_roots=(data_root,)) == receipt.resolve()
    with pytest.raises(HarnessSafetyError):
        new_temporary_file_path(data_root / "receipt.json", protected_roots=(data_root,))


def test_e2e_smoke_failure_output_drops_canary(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    canary = "PRIVATE_CANARY_DOCUMENT PRIVATE_CANARY_ANSWER PRIVATE_CANARY_CSRF C:/PRIVATE_CANARY_PATH"

    def fail() -> int:
        raise RuntimeError(canary)

    monkeypatch.setattr(e2e_smoke, "_run", fail)
    assert e2e_smoke.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "E2E smoke failed\n"
    assert canary not in captured.out + captured.err


def test_public_budget_rejects_existing_canary_output_without_reflecting_its_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    public_budget = _public_budget_module()
    output = tmp_path / "PRIVATE_CANARY_PATH-report.json"
    output.write_text("existing", encoding="utf-8")
    assert public_budget.main(["--base-url", "http://127.0.0.1:4321", "--output", str(output)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "PUBLIC_JAVASCRIPT_BUDGET_OUTPUT_REJECTED\n"
    assert "PRIVATE_CANARY_PATH" not in captured.out + captured.err


def test_kb_debug_requires_test_only_password_and_never_reflects_an_unsafe_base(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    kb_debug = _kb_debug_module()
    canary = "PRIVATE_CANARY_CSRF C:/PRIVATE_CANARY_PATH"
    monkeypatch.setenv(kb_debug.PASSWORD_ENV, canary)
    monkeypatch.setenv(kb_debug.BASE_ENV, f"https://outside.invalid/?token={canary}")
    assert kb_debug.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "KB_DEBUG_LOOPBACK_BASE_REQUIRED\n"
    assert canary not in captured.out + captured.err


def test_browser_records_summary_and_cleanup_drop_canaries(capsys: pytest.CaptureFixture[str]):
    browser = _browser_module()
    browser._reset_results()
    canary = (
        "PRIVATE_CANARY_DOCUMENT PRIVATE_CANARY_ANSWER PRIVATE_CANARY_SUBJECT "
        "PRIVATE_CANARY_MESSAGE_ID PRIVATE_CANARY_CSRF C:/PRIVATE_CANARY_PATH"
    )
    request = SimpleNamespace(
        method="POST",
        url=f"https://private.example.test/api/v1/mail/PRIVATE_CANARY_MESSAGE_ID/read?token={canary}",
        failure=canary,
    )
    response = SimpleNamespace(request=request, url=request.url, status=500)
    browser.record_console_error(SimpleNamespace(type="error", text=canary))
    browser.PUBLIC_STAGE_ACTIVE = True
    browser.record_public_api_request(SimpleNamespace(method="POST", url=f"https://private.example.test/api/v1/knowledge/documents?{canary}"))
    browser.record_response(response)
    browser.record_failed_request(request)
    browser.check(False, "fixed_check_name", canary)
    summary = json.dumps(browser._safe_summary(), ensure_ascii=False)
    captured = capsys.readouterr()

    assert canary not in captured.out + captured.err + summary
    assert browser.PAGE_ERRORS == ["console_error"]
    assert browser.PUBLIC_API_REQUESTS == ["POST /api/v1/knowledge/documents"]
    assert all("?" not in value and "https://" not in value for value in browser.FAILED_REQUESTS)
    assert browser.FAILED_REQUESTS == [
        "POST /api/v1/mail/:id/read HTTP 500",
        "POST /api/v1/mail/:id/read request_failed",
    ]
    assert browser.RESPONSE_COUNTS == {("POST", "/api/v1/mail/:id/read"): 1}


def test_fixture_fetch_context_restores_page_fetch_on_normal_and_exception(
    capsys: pytest.CaptureFixture[str],
):
    browser = _browser_module()
    canary = "FIXTURE_RAW_SSE_CANARY PRIVATE_CANARY_CSRF C:/PRIVATE_CANARY_PATH"

    class FakeFixturePage:
        def __init__(self) -> None:
            self.fetch_identity = "native"
            self.installed_paths: dict[str, str] | None = None

        def evaluate(self, script: str, argument=None):
            if script == browser.FIXTURE_FETCH_INSTALL:
                self.installed_paths = argument
                self.fetch_identity = "fixture"
                return True
            if script == browser.FIXTURE_FETCH_STATS:
                return {
                    "hits": {"rag": 1},
                    "patched": self.fetch_identity == "fixture",
                    "firstFourSameNonEmptyConversation": True,
                }
            if script == browser.FIXTURE_FETCH_RESTORE:
                was_fixture = self.fetch_identity == "fixture"
                self.fetch_identity = "native"
                return was_fixture
            raise AssertionError("unexpected fixture operation")

    page = FakeFixturePage()
    with browser.fixture_fetch_context(page, "/api/v1/knowledge/documents/fixed/rebuild", "/api/v1/knowledge/documents/fixed") as scope:
        assert page.fetch_identity == "fixture"
        stats = scope.stats()
        assert stats["patched"] is True
        assert stats["firstFourSameNonEmptyConversation"] is True
        assert canary not in json.dumps(stats)
    assert page.fetch_identity == "native"
    assert page.installed_paths == {
        "rebuildPath": "/api/v1/knowledge/documents/fixed/rebuild",
        "deletePath": "/api/v1/knowledge/documents/fixed",
    }

    with pytest.raises(RuntimeError):
        with browser.fixture_fetch_context(page, "/api/v1/knowledge/documents/fixed/rebuild", "/api/v1/knowledge/documents/fixed"):
            raise RuntimeError(canary)
    assert page.fetch_identity == "native"
    captured = capsys.readouterr()
    assert canary not in captured.out + captured.err


def test_browser_default_owned_evidence_cleanup_and_child_timeout_gate_is_nonzero():
    browser = _browser_module()
    args = argparse.Namespace(evidence_dir=None, owned_mail_receipt=None)
    with browser._evidence_directory(args) as evidence:
        assert evidence.is_dir()
        owned_evidence = evidence
    assert not owned_evidence.exists()

    process = browser._spawn_owned_process([sys.executable, "-B", "-c", "import time; time.sleep(30)"])
    result, stopped = browser._wait_for_owned_child(process, timeout_seconds=0.01)
    assert result == 1
    assert stopped
    assert process.poll() is not None
    assert browser._isolated_exit_code(0, False, True, True, True, True, True, True) == 1
    assert browser._isolated_exit_code(0, True, False, True, True, True, True, True) == 1
    assert browser._isolated_exit_code(0, True, True, True, True, True, True, False) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows Job gate regression")
def test_windows_assignment_failure_never_releases_target_and_closes_bootstrap_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    marker = tmp_path / "target-ran-marker"
    hook_marker = tmp_path / "preassignment-startup-hook-marker"
    hook_directory = tmp_path / "system-temp-hooks"
    hook_directory.mkdir()
    (hook_directory / "sitecustomize.py").write_text(
        f"from pathlib import Path\nPath({str(hook_marker)!r}).write_text('hook', encoding='utf-8')\n",
        encoding="utf-8",
    )
    caller_environment = os.environ.copy()
    caller_environment["PYTHONPATH"] = str(hook_directory)
    target = [
        sys.executable,
        "-B",
        "-c",
        f"from pathlib import Path; Path({str(marker)!r}).write_text('ran', encoding='utf-8')",
    ]
    captured_processes: list[subprocess.Popen[bytes]] = []
    captured_jobs = []
    original_popen = harness_safety.subprocess.Popen
    original_create = harness_safety._WindowsJob.create

    def capture_popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        captured_processes.append(process)
        return process

    def capture_create():
        job = original_create()
        captured_jobs.append(job)
        return job

    def fail_assign(_job, _process) -> None:
        time.sleep(1)
        raise OwnedProcessError("assignment failure")

    monkeypatch.setattr(harness_safety.subprocess, "Popen", capture_popen)
    monkeypatch.setattr(harness_safety._WindowsJob, "create", classmethod(lambda _cls: capture_create()))
    monkeypatch.setattr(harness_safety._WindowsJob, "assign", fail_assign)
    with pytest.raises(OwnedProcessError) as raised:
        spawn_owned_process(
            target,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=caller_environment,
        )

    assert str(raised.value) == "owned process setup failed"
    assert not marker.exists()
    assert not hook_marker.exists()
    assert len(captured_processes) == 1
    bootstrap = captured_processes[0]
    assert bootstrap.poll() is not None
    assert all(pipe is None or pipe.closed for pipe in (bootstrap.stdin, bootstrap.stdout, bootstrap.stderr))
    assert len(captured_jobs) == 1
    assert captured_jobs[0].handle == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows Job gate regression")
def test_windows_target_inherits_pythonpath_only_after_assignment_and_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    hook_marker = tmp_path / "target-startup-hook-marker"
    hook_directory = tmp_path / "system-temp-target-hooks"
    hook_directory.mkdir()
    (hook_directory / "sitecustomize.py").write_text(
        f"from pathlib import Path\nPath({str(hook_marker)!r}).write_text('target', encoding='utf-8')\n",
        encoding="utf-8",
    )
    caller_environment = os.environ.copy()
    caller_environment["PYTHONPATH"] = str(hook_directory)
    original_assign = harness_safety._WindowsJob.assign
    assignment_observed = False

    def observe_assign(job, process) -> None:
        nonlocal assignment_observed
        assert not hook_marker.exists()
        original_assign(job, process)
        assignment_observed = True

    monkeypatch.setattr(harness_safety._WindowsJob, "assign", observe_assign)
    process = spawn_owned_process(
        [sys.executable, "-B", "-c", "print('TARGET_GATE_OK', flush=True)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=caller_environment,
    )
    try:
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0
        assert stdout.strip() == b"TARGET_GATE_OK"
        assert stderr == b""
        assert assignment_observed
        assert hook_marker.read_text(encoding="utf-8") == "target"
    finally:
        assert stop_owned_process(process)


@pytest.mark.parametrize(
    "reserved_name",
    (
        "star_isle_owned_process_command",
        "StAr_IsLe_OwNeD_PrOcEsS_GaTe_HaNdLe",
        "STAR_ISLE_owned_process_ready_handle",
    ),
)
def test_bootstrap_reserved_environment_names_reject_case_variants_without_reflection(reserved_name: str):
    with pytest.raises(OwnedProcessError) as raised:
        harness_safety._bootstrap_environment(
            {reserved_name: "PRIVATE_CANARY_ENV"}, ["target"], 1, 2
        )
    assert str(raised.value) == "owned process setup failed"
    assert "PRIVATE_CANARY_ENV" not in str(raised.value)


def test_bootstrap_helper_reparse_or_symlink_path_is_rejected(tmp_path: Path):
    helper = tmp_path / "synthetic-helper.py"
    helper.write_text("pass\n", encoding="utf-8")
    helper_link = tmp_path / "synthetic-helper-link.py"
    try:
        helper_link.symlink_to(helper)
    except OSError:
        pytest.skip("当前 Windows 测试账户不允许创建符号链接")
    with pytest.raises(OwnedProcessError) as raised:
        harness_safety._validated_bootstrap_helper_path(helper_link)
    assert str(raised.value) == "owned process setup failed"


def test_browser_rejects_existing_evidence_without_overwriting_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    browser = _browser_module()
    evidence = tmp_path / "existing-evidence"
    evidence.mkdir()
    marker = evidence / "PRIVATE_CANARY_DOCUMENT-marker.txt"
    marker.write_text("PRIVATE_CANARY_ANSWER", encoding="utf-8")
    called = False

    def unexpected_flow(*_args):
        nonlocal called
        called = True
        return 0

    monkeypatch.setenv("MAIL_HMAC_KEY", "test-only-mail-hmac")
    monkeypatch.setenv(browser.BROWSER_TEST_PASSWORD_ENV, "test-only-browser-password")
    monkeypatch.setattr(browser, "_browser_flow", unexpected_flow)
    assert browser.main(
        [
            "--external-services",
            "--base",
            "http://127.0.0.1:4321",
            "--api-base",
            "http://127.0.0.1:8000",
            "--evidence-dir",
            str(evidence),
        ]
    ) == 1
    captured = capsys.readouterr()
    assert not called
    assert marker.read_text(encoding="utf-8") == "PRIVATE_CANARY_ANSWER"
    assert captured.out == ""
    assert captured.err == "BROWSER_E2E_FAILED\n"
    assert "PRIVATE_CANARY" not in captured.out + captured.err


def test_browser_rejects_remote_private_services_before_playwright_or_flow(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    browser = _browser_module()
    called = False

    def unexpected(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("PRIVATE_CANARY_ANSWER")

    monkeypatch.setattr(browser, "sync_playwright", unexpected)
    monkeypatch.setattr(browser, "_browser_flow", unexpected)
    canary = "PRIVATE_CANARY_CSRF PRIVATE_CANARY_PATH"
    assert browser.main(
        [
            "--external-services",
            "--base",
            f"https://outside.invalid/?{canary}",
            "--api-base",
            "http://127.0.0.1:8000",
        ]
    ) == 1
    captured = capsys.readouterr()
    assert not called
    assert captured.out == ""
    assert captured.err == "BROWSER_E2E_LOOPBACK_REQUIRED\n"
    assert canary not in captured.out + captured.err


def test_browser_does_not_accept_legacy_owned_evidence_argument(
    capsys: pytest.CaptureFixture[str],
):
    browser = _browser_module()
    with pytest.raises(SystemExit) as raised:
        browser.main(["--owned-evidence-dir", "PRIVATE_CANARY_PATH"])
    captured = capsys.readouterr()
    assert raised.value.code == 2
    assert captured.out == ""
    assert captured.err == "BROWSER_E2E_ARGUMENTS_INVALID\n"
    assert "PRIVATE_CANARY_PATH" not in captured.err


def test_browser_rejects_legacy_password_argument_without_reflecting_secret(
    capsys: pytest.CaptureFixture[str],
):
    browser = _browser_module()
    with pytest.raises(SystemExit) as raised:
        browser.main(["--password", "PRIVATE_CANARY_PASSWORD"])
    captured = capsys.readouterr()
    assert raised.value.code == 2
    assert captured.out == ""
    assert captured.err == "BROWSER_E2E_ARGUMENTS_INVALID\n"
    assert "PRIVATE_CANARY_PASSWORD" not in captured.err


def test_browser_external_credentials_and_argon_helper_keep_password_out_of_argv(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    browser = _browser_module()
    canary = "PRIVATE_CANARY_PASSWORD"
    entered_flow = False

    def unexpected_flow(*_args, **_kwargs):
        nonlocal entered_flow
        entered_flow = True
        raise AssertionError("PRIVATE_CANARY_NETWORK")

    monkeypatch.setattr(browser, "_browser_flow", unexpected_flow)
    monkeypatch.delenv(browser.BROWSER_TEST_PASSWORD_ENV, raising=False)
    monkeypatch.delenv("MAIL_HMAC_KEY", raising=False)
    assert browser.main(
        [
            "--external-services",
            "--base",
            "http://127.0.0.1:4321",
            "--api-base",
            "http://127.0.0.1:8000",
        ]
    ) == 1
    captured = capsys.readouterr()
    assert not entered_flow
    assert captured.out == ""
    assert captured.err == "BROWSER_E2E_TEST_CREDENTIALS_REQUIRED\n"

    argv_seen: list[list[str]] = []

    def fake_run(command, **kwargs):
        argv_seen.append([str(item) for item in command])
        assert kwargs["input"] == canary
        return SimpleNamespace(stdout="$argon2id$synthetic", returncode=0)

    monkeypatch.setattr(browser.subprocess, "run", fake_run)
    assert browser._password_hash(Path("python"), canary) == "$argon2id$synthetic"
    assert all(canary not in "\0".join(command) for command in argv_seen)


def test_browser_receipt_rejects_live_roots_and_cleanup_failure_is_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    browser = _browser_module()
    data_root = tmp_path / "live-data"
    host_root = tmp_path / "live-host"
    data_root.mkdir()
    host_root.mkdir()
    marker = host_root / "PRIVATE_CANARY_MARKER"
    marker.write_text("PRIVATE_CANARY_DOCUMENT", encoding="utf-8")
    monkeypatch.setenv("DATA_ROOT", str(data_root))
    monkeypatch.setenv("DATA_HOST_ROOT", str(host_root))
    with pytest.raises(HarnessSafetyError):
        with browser._mail_receipt_file(argparse.Namespace(owned_mail_receipt=host_root / "receipt.json")):
            pass
    with pytest.raises(HarnessSafetyError):
        with browser._mail_receipt_file(
            argparse.Namespace(owned_mail_receipt=Path.cwd() / "PRIVATE_CANARY_RECEIPT.json")
        ):
            pass
    assert marker.read_text(encoding="utf-8") == "PRIVATE_CANARY_DOCUMENT"

    receipt = tmp_path / "safe-receipt.json"

    def write_receipt(_args, _sample_txt, _sample_png, receipt_file):
        receipt_file.write_text("PRIVATE_CANARY_RECEIPT", encoding="utf-8")
        return 0

    original_unlink = Path.unlink

    def fail_receipt_unlink(path: Path, *args, **kwargs):
        if path == receipt:
            raise OSError("PRIVATE_CANARY_UNLINK")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setenv(browser.BROWSER_TEST_PASSWORD_ENV, "test-only-browser-password")
    monkeypatch.setenv("MAIL_HMAC_KEY", "test-only-mail-hmac")
    monkeypatch.setattr(browser, "_browser_flow", write_receipt)
    monkeypatch.setattr(Path, "unlink", fail_receipt_unlink)
    assert browser.main(
        [
            "--external-services",
            "--base",
            "http://127.0.0.1:4321",
            "--api-base",
            "http://127.0.0.1:8000",
            "--owned-mail-receipt",
            str(receipt),
        ]
    ) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "BROWSER_E2E_FAILED\n"
    assert "PRIVATE_CANARY" not in captured.out + captured.err
    monkeypatch.setattr(Path, "unlink", original_unlink)
    receipt.unlink()


def test_public_budget_rejects_remote_without_opt_in_before_playwright(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    public_budget = _public_budget_module()
    called = False

    def unexpected_playwright():
        nonlocal called
        called = True
        raise AssertionError("PRIVATE_CANARY_NETWORK")

    monkeypatch.setattr(public_budget, "sync_playwright", unexpected_playwright)
    assert public_budget.main(["--base-url", "https://outside.invalid/PRIVATE_CANARY_PATH"]) == 1
    captured = capsys.readouterr()
    assert not called
    assert captured.out == ""
    assert captured.err == "PUBLIC_JAVASCRIPT_BUDGET_LOOPBACK_REQUIRED\n"
    assert "PRIVATE_CANARY_PATH" not in captured.out + captured.err


def test_public_budget_remote_opt_in_is_required_but_does_not_need_a_real_network(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    public_budget = _public_budget_module()
    called = False

    def no_network_playwright():
        nonlocal called
        called = True
        raise RuntimeError("PRIVATE_CANARY_NETWORK")

    monkeypatch.setattr(public_budget, "sync_playwright", no_network_playwright)
    assert public_budget.main(
        ["--base-url", "https://public.example.test", "--allow-remote-public"]
    ) == 1
    captured = capsys.readouterr()
    assert called
    assert captured.out == ""
    assert captured.err == "PUBLIC_JAVASCRIPT_BUDGET_FAILED\n"
    assert "PRIVATE_CANARY_NETWORK" not in captured.out + captured.err


def test_browser_close_failure_is_a_stable_main_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    browser = _browser_module()
    created: list[tuple[object, object, object]] = []

    class Page:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True
            raise RuntimeError("PRIVATE_CANARY_DOCUMENT")

    class Context:
        def __init__(self, page: Page) -> None:
            self.page = page
            self.closed = False

        def new_page(self) -> Page:
            return self.page

        def close(self) -> None:
            self.closed = True

    class Browser:
        def __init__(self, context: Context) -> None:
            self.context = context
            self.closed = False

        def new_context(self, **_kwargs) -> Context:
            return self.context

        def close(self) -> None:
            self.closed = True

    class Manager:
        def __init__(self) -> None:
            page = Page()
            context = Context(page)
            owned_browser = Browser(context)
            self.playwright = SimpleNamespace(chromium=SimpleNamespace(launch=lambda **_kwargs: owned_browser))
            created.append((page, context, owned_browser))

        def __enter__(self):
            return self.playwright

        def __exit__(self, *_args) -> bool:
            return False

    monkeypatch.setattr(browser, "sync_playwright", Manager)
    with pytest.raises(browser.BrowserCleanupError, match="browser cleanup failed"):
        with browser._browser_session(True):
            pass
    page, context, owned_browser = created[-1]
    assert page.closed and context.closed and owned_browser.closed

    def failing_cleanup_flow(args, *_paths) -> int:
        with browser._browser_session(args.headless):
            return 0

    monkeypatch.setattr(browser, "_browser_flow", failing_cleanup_flow)
    assert browser.main(["--public-only", "--base", "https://public.example.test"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "BROWSER_E2E_FAILED\n"
    assert "PRIVATE_CANARY_DOCUMENT" not in captured.out + captured.err


def test_debug_and_public_budget_dynamic_path_labels_and_cleanup_failure_are_safe(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    kb_debug = _kb_debug_module()
    public_budget = _public_budget_module()
    canary = "PRIVATE_CANARY_MESSAGE_ID"
    url = f"https://private.example.test/api/v1/mail/{canary}/read?x=PRIVATE_CANARY_CSRF"
    assert kb_debug._safe_http_status_line("POST", url, 403) == "HTTP 403 POST /api/v1/mail/:id/read"
    assert public_budget._safe_request_error("failed_request", "POST", url) == (
        "failed_request:POST /api/v1/mail/:id/read"
    )

    class Closable:
        def __init__(self, fail: bool = False) -> None:
            self.fail = fail
            self.closed = False

        def close(self) -> None:
            self.closed = True
            if self.fail:
                raise RuntimeError("PRIVATE_CANARY_ANSWER")

    page = Closable(fail=True)
    context = Closable()
    owned_browser = Closable()
    assert not kb_debug._close_owned_browser(page, context, owned_browser)
    assert page.closed and context.closed and owned_browser.closed
    assert kb_debug._finish(0, cleanup_ok=False) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "KB_DEBUG_CLEANUP_FAILED\n"
    assert "PRIVATE_CANARY" not in captured.out + captured.err

    class Locator:
        first = None

        def __init__(self) -> None:
            self.first = self

        def click(self) -> None:
            return None

        def fill(self, _value: str) -> None:
            return None

        def set_input_files(self, _value: str) -> None:
            return None

    class DebugPage(Closable):
        def on(self, *_args) -> None:
            return None

        def goto(self, *_args, **_kwargs) -> None:
            return None

        def get_by_role(self, *_args, **_kwargs) -> Locator:
            return Locator()

        def wait_for_url(self, *_args, **_kwargs) -> None:
            return None

        def locator(self, *_args, **_kwargs) -> Locator:
            return Locator()

        def wait_for_timeout(self, *_args, **_kwargs) -> None:
            return None

        def screenshot(self, *_args, **_kwargs) -> None:
            return None

    class DebugContext(Closable):
        def __init__(self, debug_page: DebugPage) -> None:
            super().__init__()
            self.debug_page = debug_page

        def new_page(self) -> DebugPage:
            return self.debug_page

    class DebugBrowser(Closable):
        def __init__(self, debug_context: DebugContext) -> None:
            super().__init__()
            self.debug_context = debug_context

        def new_context(self, **_kwargs) -> DebugContext:
            return self.debug_context

    instances: list[tuple[DebugPage, DebugContext, DebugBrowser]] = []

    class DebugPlaywrightManager:
        def __init__(self) -> None:
            debug_page = DebugPage(fail=True)
            debug_context = DebugContext(debug_page)
            debug_browser = DebugBrowser(debug_context)
            self.playwright = SimpleNamespace(
                chromium=SimpleNamespace(launch=lambda **_kwargs: debug_browser)
            )
            instances.append((debug_page, debug_context, debug_browser))

        def __enter__(self):
            return self.playwright

        def __exit__(self, *_args) -> bool:
            return False

    monkeypatch.setenv(kb_debug.PASSWORD_ENV, "PRIVATE_CANARY_PASSWORD")
    monkeypatch.setenv(kb_debug.BASE_ENV, "http://127.0.0.1:4321")
    monkeypatch.setattr(kb_debug, "sync_playwright", DebugPlaywrightManager)
    assert kb_debug.main() == 1
    debug_page, debug_context, debug_browser = instances[-1]
    assert debug_page.closed and debug_context.closed and debug_browser.closed
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "KB_DEBUG_CLEANUP_FAILED\n"
    assert "PRIVATE_CANARY" not in captured.out + captured.err
