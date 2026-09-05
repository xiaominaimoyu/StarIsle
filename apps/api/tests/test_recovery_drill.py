"""隔离 recovery drill 的 CLI、报告和失败边界回归。"""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.test_backup_restore import _fixture_data_root, backup_restore


RUNNER = Path(__file__).resolve().parents[3] / "scripts" / "recovery_drill.py"
SCRIPT_ROOT = RUNNER.parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from harness_safety import OwnedProcess, spawn_owned_process, stop_owned_process  # noqa: E402


SPEC = importlib.util.spec_from_file_location("starisle_recovery_drill", RUNNER)
assert SPEC and SPEC.loader
recovery_drill = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = recovery_drill
SPEC.loader.exec_module(recovery_drill)


def _runner_environment(data_root: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "DATA_ROOT": str(data_root),
            "DATA_HOST_ROOT": str(data_root),
            "PYTHON_DOTENV_DISABLED": "1",
            "LLAMA_CHAT_BASE_URL": "",
            "LLAMA_EMBED_BASE_URL": "",
            "EMBEDDING_MODEL_ID": "",
        }
    )
    return environment


def _completed_backup(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    backup = tmp_path / "backup"
    _fixture_data_root(source)
    backup_restore.backup(source, backup, verify_after=True)
    return source, backup


def _run_cli(
    backup: Path,
    target: Path,
    report: Path,
    query: str,
    data_root: Path,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [
            sys.executable,
            "-B",
            str(RUNNER),
            "--backup",
            str(backup),
            "--target",
            str(target),
            "--report",
            str(report),
            "--knowledge-query-stdin",
        ],
        input=query.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_runner_environment(data_root),
        check=False,
    )


def _cli_report(completed: subprocess.CompletedProcess[bytes]) -> dict[str, object]:
    try:
        if completed.stderr:
            raise AssertionError
        lines = completed.stdout.decode("utf-8").splitlines()
        if len(lines) != 1:
            raise AssertionError
        parsed = json.loads(lines[0])
        if not isinstance(parsed, dict):
            raise AssertionError
        return parsed
    except (AssertionError, AttributeError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        raise AssertionError("recovery CLI output invalid") from None


def test_recovery_drill_cli_restores_target_only_and_keeps_report_private(tmp_path: Path):
    source, backup = _completed_backup(tmp_path)
    target = tmp_path / "target"
    report = tmp_path / "report.json"
    configured_data_root = tmp_path / "configured-data-root"
    query = "private-query-canary-knowledge"
    source_canary = "source-private-canary"
    (source / "objects" / "originals" / "photo.bin").write_bytes(source_canary.encode("utf-8"))
    connection = sqlite3.connect(source / "db" / "star-isle.db")
    try:
        connection.execute("UPDATE mail_message SET subject=? WHERE id='mail-1'", (source_canary,))
        connection.commit()
    finally:
        connection.close()

    completed = _run_cli(backup, target, report, query, configured_data_root)
    stdout_report = _cli_report(completed)
    assert completed.returncode == 0
    assert stdout_report["status"] == "passed"
    persisted = json.loads(report.read_text(encoding="utf-8"))
    assert persisted == stdout_report
    assert persisted["scope"] == "isolated-local-candidate"
    assert persisted["productionAccepted"] is False
    assert persisted["provenance"] == "restored-target"
    assert persisted["checks"]["knowledge"]["searchHits"] >= 1
    assert (target / "objects" / "originals" / "photo.bin").read_bytes() == b"photo"
    assert not report.with_name(report.name + recovery_drill.REPORT_MARKER_SUFFIX).exists()
    assert not report.with_name(report.name + recovery_drill.REPORT_TEMP_SUFFIX).exists()

    emitted = completed.stdout + completed.stderr + report.read_bytes()
    for forbidden in (
        query.encode("utf-8"),
        source_canary.encode("utf-8"),
        str(source).encode("utf-8"),
        b"photo.bin",
        b"mail-raw.eml",
        b"backup-mail@example.test",
    ):
        assert forbidden not in emitted


def _manifest(backup: Path) -> dict[str, object]:
    return json.loads((backup / "manifest.json").read_text(encoding="utf-8"))


def _write_manifest(backup: Path, manifest: dict[str, object]) -> None:
    (backup / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _failed_cli(
    backup: Path,
    target: Path,
    report: Path,
    configured_data_root: Path,
    *,
    query: str = "private-query",
) -> dict[str, object]:
    completed = _run_cli(backup, target, report, query, configured_data_root)
    parsed = _cli_report(completed)
    assert completed.returncode == 1
    assert parsed["status"] == "failed"
    return parsed


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("incomplete", "BACKUP_INCOMPLETE"),
        ("temporary", "BACKUP_INCOMPLETE"),
        ("health", "BACKUP_CANDIDATE_INVALID"),
        ("database-sidecar", "BACKUP_INVALID"),
        ("bad-json", "BACKUP_INVALID"),
        ("json-list", "BACKUP_INVALID"),
        ("legacy-format", "BACKUP_CANDIDATE_INVALID"),
        ("schema-four", "BACKUP_CANDIDATE_INVALID"),
        ("duplicate", "BACKUP_INVALID"),
        ("bad-size", "BACKUP_INVALID"),
        ("bad-object-count", "BACKUP_INVALID"),
        ("unallowed-path", "BACKUP_INVALID"),
        ("extra-object", "BACKUP_INVALID"),
        ("missing-object", "BACKUP_INVALID"),
        ("nested-object", "BACKUP_INVALID"),
        ("extra-root", "BACKUP_INVALID"),
    ],
)
def test_recovery_drill_rejects_incomplete_and_malformed_candidates(
    tmp_path: Path,
    mutation: str,
    expected_code: str,
):
    _, backup = _completed_backup(tmp_path)
    manifest_path = backup / "manifest.json"
    if mutation == "incomplete":
        (backup / backup_restore.INCOMPLETE_MARKER_NAME).write_text("incomplete\n", encoding="utf-8")
    elif mutation == "temporary":
        (backup / backup_restore.MANIFEST_TEMP_NAME).write_text("partial\n", encoding="utf-8")
    elif mutation == "health":
        (backup / "objects" / "health").mkdir()
    elif mutation == "database-sidecar":
        (backup / "db" / "star-isle.db-wal").write_bytes(b"sidecar")
    elif mutation == "bad-json":
        manifest_path.write_text("{", encoding="utf-8")
    elif mutation == "json-list":
        manifest_path.write_text("[]", encoding="utf-8")
    elif mutation == "legacy-format":
        manifest = _manifest(backup)
        manifest["formatVersion"] = backup_restore.LEGACY_FORMAT_VERSION
        manifest["schemaVersion"] = 3
        _write_manifest(backup, manifest)
    elif mutation == "schema-four":
        manifest = _manifest(backup)
        manifest["schemaVersion"] = 4
        _write_manifest(backup, manifest)
    elif mutation == "duplicate":
        manifest = _manifest(backup)
        files = manifest["files"]
        assert isinstance(files, list)
        files.append(dict(files[0]))
        _write_manifest(backup, manifest)
    elif mutation == "bad-size":
        manifest = _manifest(backup)
        files = manifest["files"]
        assert isinstance(files, list)
        files[0]["size"] = True
        _write_manifest(backup, manifest)
    elif mutation == "bad-object-count":
        manifest = _manifest(backup)
        manifest["objectCount"] = int(manifest["objectCount"]) + 1
        _write_manifest(backup, manifest)
    elif mutation == "unallowed-path":
        manifest = _manifest(backup)
        files = manifest["files"]
        assert isinstance(files, list)
        for entry in files:
            if entry["path"] == "objects/originals/photo.bin":
                entry["path"] = "objects/originals/nested/photo.bin"
                break
        _write_manifest(backup, manifest)
    elif mutation == "extra-object":
        (backup / "objects" / "originals" / "unreferenced.bin").write_bytes(b"orphan")
    elif mutation == "missing-object":
        (backup / "objects" / "originals" / "photo.bin").unlink()
    elif mutation == "nested-object":
        nested = backup / "objects" / "originals" / "nested"
        nested.mkdir()
        (nested / "object.bin").write_bytes(b"nested")
    elif mutation == "extra-root":
        (backup / "unexpected").mkdir()
    else:
        raise AssertionError(mutation)

    target = tmp_path / f"target-{mutation}"
    report = tmp_path / f"report-{mutation}.json"
    parsed = _failed_cli(backup, target, report, tmp_path / "configured-data-root")
    assert parsed["failureCode"] == expected_code
    assert report.is_file()
    assert report.with_name(report.name + recovery_drill.REPORT_MARKER_SUFFIX).is_file()
    assert not target.exists()


def test_recovery_drill_rejects_object_symlink_candidate(tmp_path: Path):
    _, backup = _completed_backup(tmp_path)
    object_file = backup / "objects" / "originals" / "photo.bin"
    replacement = tmp_path / "replacement.bin"
    replacement.write_bytes(b"replacement")
    object_file.unlink()
    try:
        object_file.symlink_to(replacement)
    except OSError:
        pytest.skip("当前 Windows 测试账户不允许创建符号链接")

    report = tmp_path / "symlink-report.json"
    parsed = _failed_cli(backup, tmp_path / "symlink-target", report, tmp_path / "configured-data-root")
    assert parsed["failureCode"] == "BACKUP_INVALID"
    assert report.with_name(report.name + recovery_drill.REPORT_MARKER_SUFFIX).is_file()


def _backup_with_source_mutation(tmp_path: Path, mutation) -> tuple[Path, Path]:
    source = tmp_path / "source"
    backup = tmp_path / "backup"
    _fixture_data_root(source)
    mutation(source)
    backup_restore.backup(source, backup, verify_after=False)
    return source, backup


@pytest.mark.parametrize(
    ("name", "mutation", "expected_code"),
    [
        (
            "no-photo",
            lambda source: _execute_source_sql(source, "DELETE FROM photo"),
            "PHOTO_REQUIRED",
        ),
        (
            "broken-photo",
            lambda source: _execute_source_sql(
                source,
                "UPDATE photo SET thumbnail_asset_id='missing-thumbnail' WHERE id='photo-1'",
            ),
            "BACKUP_INVALID",
        ),
        (
            "queued-document",
            lambda source: _execute_source_sql(
                source,
                "UPDATE knowledge_document SET index_state='queued' WHERE id='doc-1'",
            ),
            "KNOWLEDGE_REQUIRED",
        ),
        (
            "broken-vector",
            lambda source: _execute_source_sql(source, "UPDATE vector_ref SET vector_id=''"),
            "BACKUP_INVALID",
        ),
        (
            "unsupported-profile",
            lambda source: _execute_source_sql(source, "UPDATE vector_ref SET model_id='different-profile'"),
            "KNOWLEDGE_PROFILE_UNAVAILABLE",
        ),
    ],
)
def test_recovery_drill_requires_recoverable_photo_and_knowledge(
    tmp_path: Path,
    name: str,
    mutation,
    expected_code: str,
):
    _, backup = _backup_with_source_mutation(tmp_path, mutation)
    report = tmp_path / f"{name}.json"
    parsed = _failed_cli(backup, tmp_path / f"{name}-target", report, tmp_path / "configured-data-root")
    assert parsed["failureCode"] == expected_code
    assert report.with_name(report.name + recovery_drill.REPORT_MARKER_SUFFIX).is_file()


def _execute_source_sql(source: Path, statement: str) -> None:
    connection = sqlite3.connect(source / "db" / "star-isle.db")
    try:
        connection.execute(statement)
        connection.commit()
    finally:
        connection.close()


def test_recovery_drill_excludes_live_orphans_from_restored_closure(tmp_path: Path):
    source = tmp_path / "source"
    backup = tmp_path / "backup"
    _fixture_data_root(source)
    (source / "objects" / "originals" / "ordinary-orphan.bin").write_bytes(b"orphan")
    (source / "objects" / "mail-raw" / "raw-orphan.eml").write_bytes(b"raw-orphan")
    backup_restore.backup(source, backup, verify_after=True)

    target = tmp_path / "target"
    report = tmp_path / "report.json"
    completed = _run_cli(backup, target, report, "closed-object-query", tmp_path / "configured-data-root")
    assert completed.returncode == 0
    assert _cli_report(completed)["status"] == "passed"
    assert not (target / "objects" / "originals" / "ordinary-orphan.bin").exists()
    assert not (target / "objects" / "mail-raw" / "raw-orphan.eml").exists()


@pytest.mark.parametrize("query", ["", "   ", "x" * (recovery_drill.MAX_QUERY_BYTES + 1)])
def test_recovery_drill_cli_rejects_invalid_stdin_query_without_report(tmp_path: Path, query: str):
    _, backup = _completed_backup(tmp_path)
    target = tmp_path / "target"
    report = tmp_path / "report.json"
    completed = _run_cli(backup, target, report, query, tmp_path / "configured-data-root")
    parsed = _cli_report(completed)
    assert completed.returncode == 1
    assert parsed["failureCode"] == "QUERY_INVALID"
    assert not report.exists()
    assert not target.exists()


@pytest.mark.parametrize("query", ["", " \t", "x" * (recovery_drill.MAX_QUERY_BYTES + 1), "\ud800"])
def test_recovery_drill_direct_run_rejects_invalid_query_before_path_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
):
    _, backup = _completed_backup(tmp_path)
    runtime_root = tmp_path / "runtime-root"
    monkeypatch.setenv("DATA_ROOT", str(runtime_root))
    monkeypatch.setenv("DATA_HOST_ROOT", str(runtime_root))
    target = tmp_path / "target"
    report = tmp_path / "report.json"

    result = recovery_drill.run(str(backup), str(target), str(report), query)

    assert result["failureCode"] == "QUERY_INVALID"
    assert not target.exists()
    assert not report.exists()
    assert not report.with_name(report.name + recovery_drill.REPORT_MARKER_SUFFIX).exists()
    assert not report.with_name(report.name + recovery_drill.REPORT_TEMP_SUFFIX).exists()


@pytest.mark.parametrize("kind", ["empty-target", "nonempty-target", "report", "marker", "temporary"])
def test_recovery_drill_refuses_existing_target_or_report_claim(tmp_path: Path, kind: str):
    _, backup = _completed_backup(tmp_path)
    target = tmp_path / "target"
    report = tmp_path / "report.json"
    protected: Path | None = None
    if kind == "empty-target":
        target.mkdir()
    elif kind == "nonempty-target":
        target.mkdir()
        (target / "keep.txt").write_text("keep", encoding="utf-8")
    elif kind == "report":
        report.write_text("do-not-overwrite", encoding="utf-8")
        protected = report
    elif kind == "marker":
        protected = report.with_name(report.name + recovery_drill.REPORT_MARKER_SUFFIX)
        protected.write_text("do-not-overwrite", encoding="utf-8")
    elif kind == "temporary":
        protected = report.with_name(report.name + recovery_drill.REPORT_TEMP_SUFFIX)
        protected.write_text("do-not-overwrite", encoding="utf-8")
    else:
        raise AssertionError(kind)

    parsed = _failed_cli(backup, target, report, tmp_path / "configured-data-root")
    assert parsed["failureCode"] in {"TARGET_NOT_NEW", "REPORT_CONFLICT"}
    if protected is not None:
        assert protected.read_text(encoding="utf-8") == "do-not-overwrite"
    if kind == "nonempty-target":
        assert (target / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_recovery_drill_rejects_data_root_and_overlapping_paths(tmp_path: Path):
    _, backup = _completed_backup(tmp_path)
    configured_data_root = tmp_path / "configured-data-root"
    configured_data_root.mkdir()

    in_data_root = _failed_cli(
        backup,
        tmp_path / "target-one",
        configured_data_root / "report.json",
        configured_data_root,
    )
    assert in_data_root["failureCode"] == "REPORT_IN_DATA_ROOT"

    overlap = _failed_cli(
        backup,
        backup / "target-overlap",
        tmp_path / "report-two.json",
        configured_data_root,
    )
    assert overlap["failureCode"] == "PATH_OVERLAP"


@pytest.mark.parametrize("relationship", ["inside", "equal", "contains"])
def test_recovery_drill_rejects_backup_data_root_overlap_before_claim(tmp_path: Path, relationship: str):
    source = tmp_path / "source"
    target = tmp_path / "target"
    report = tmp_path / "report.json"
    if relationship == "inside":
        runtime_root = tmp_path / "runtime-root"
        backup = runtime_root / "backup"
        runtime_root.mkdir()
    else:
        backup = tmp_path / "backup"
        runtime_root = backup
    _fixture_data_root(source)
    backup_restore.backup(source, backup, verify_after=True)
    if relationship == "contains":
        runtime_root = backup / "runtime-root"
        runtime_root.mkdir()

    completed = _run_cli(backup, target, report, "safe-query", runtime_root)
    parsed = _cli_report(completed)

    assert completed.returncode == 1
    assert parsed["failureCode"] == "BACKUP_IN_DATA_ROOT"
    assert not target.exists()
    assert not report.exists()
    assert not report.with_name(report.name + recovery_drill.REPORT_MARKER_SUFFIX).exists()


@pytest.mark.parametrize(
    ("kind", "failure_code"),
    (("backup", "BACKUP_IN_DATA_ROOT"), ("target", "TARGET_IN_DATA_ROOT"), ("report", "REPORT_IN_DATA_ROOT")),
)
def test_recovery_drill_rejects_distinct_data_host_root_before_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, failure_code: str
):
    source = tmp_path / "source"
    _fixture_data_root(source)
    data_root = tmp_path / "live-data-root"
    host_root = tmp_path / "live-data-host-root"
    data_root.mkdir()
    host_root.mkdir()
    backup = host_root / "backup" if kind == "backup" else tmp_path / "backup"
    backup_restore.backup(source, backup, verify_after=True)
    target = host_root / "target" if kind == "target" else tmp_path / "target"
    report = host_root / "report.json" if kind == "report" else tmp_path / "report.json"
    monkeypatch.setenv("DATA_ROOT", str(data_root))
    monkeypatch.setenv("DATA_HOST_ROOT", str(host_root))

    result = recovery_drill.run(str(backup), str(target), str(report), "safe-query")

    assert result["failureCode"] == failure_code
    assert not target.exists()
    assert not report.exists()
    assert not report.with_name(report.name + recovery_drill.REPORT_MARKER_SUFFIX).exists()
    assert not report.with_name(report.name + recovery_drill.REPORT_TEMP_SUFFIX).exists()


def test_recovery_drill_fails_closed_for_unresolvable_explicit_data_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _, backup = _completed_backup(tmp_path)
    runtime_root = tmp_path / "runtime-root"
    target = tmp_path / "target"
    report = tmp_path / "report.json"
    monkeypatch.setenv("DATA_ROOT", str(runtime_root))
    monkeypatch.setenv("DATA_HOST_ROOT", str(runtime_root))
    original_safe_path_chain = recovery_drill._safe_path_chain

    def fail_runtime_root(path: Path, code: str) -> Path:
        if Path(path) == runtime_root:
            raise recovery_drill.DrillFailure(code)
        return original_safe_path_chain(path, code)

    monkeypatch.setattr(recovery_drill, "_safe_path_chain", fail_runtime_root)

    result = recovery_drill.run(str(backup), str(target), str(report), "safe-query")

    assert result["failureCode"] == "DATA_ROOT_UNSAFE"
    assert not target.exists()
    assert not report.exists()
    assert not report.with_name(report.name + recovery_drill.REPORT_MARKER_SUFFIX).exists()


def test_recovery_drill_strict_verify_precedes_backup_database_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _, backup = _completed_backup(tmp_path)
    runtime_root = tmp_path / "runtime-root"
    monkeypatch.setenv("DATA_ROOT", str(runtime_root))
    monkeypatch.setenv("DATA_HOST_ROOT", str(runtime_root))
    events: list[str] = []
    original_quiet_call = recovery_drill._quiet_call
    original_photo_preflight = recovery_drill._photo_preflight
    original_knowledge_preflight = recovery_drill._knowledge_preflight

    def tracked_quiet_call(function, *args, **kwargs):
        if function is recovery_drill.backup_restore.verify:
            events.append("verify")
        return original_quiet_call(function, *args, **kwargs)

    def tracked_photo_preflight(data_root: Path) -> int:
        events.append("photo")
        return original_photo_preflight(data_root)

    def tracked_knowledge_preflight(data_root: Path) -> tuple[int, int]:
        events.append("knowledge")
        return original_knowledge_preflight(data_root)

    monkeypatch.setattr(recovery_drill, "_quiet_call", tracked_quiet_call)
    monkeypatch.setattr(recovery_drill, "_photo_preflight", tracked_photo_preflight)
    monkeypatch.setattr(recovery_drill, "_knowledge_preflight", tracked_knowledge_preflight)

    result = recovery_drill.run(
        str(backup),
        str(tmp_path / "target"),
        str(tmp_path / "report.json"),
        "safe-query",
    )

    assert result["status"] == "passed"
    assert events.index("verify") < events.index("photo")
    assert events.index("verify") < events.index("knowledge")


def test_recovery_drill_rejects_backup_db_reparse_before_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _, backup = _completed_backup(tmp_path)
    runtime_root = tmp_path / "runtime-root"
    monkeypatch.setenv("DATA_ROOT", str(runtime_root))
    monkeypatch.setenv("DATA_HOST_ROOT", str(runtime_root))
    original_is_link_or_reparse = recovery_drill.backup_restore._is_link_or_reparse

    def simulated_reparse(path: Path) -> bool:
        if Path(path) == backup / "db":
            return True
        return original_is_link_or_reparse(Path(path))

    def preflight_must_not_run(_data_root: Path):
        raise AssertionError("backup preflight ran before strict verify")

    monkeypatch.setattr(recovery_drill.backup_restore, "_is_link_or_reparse", simulated_reparse)
    monkeypatch.setattr(recovery_drill, "_photo_preflight", preflight_must_not_run)

    target = tmp_path / "target"
    report = tmp_path / "report.json"
    result = recovery_drill.run(str(backup), str(target), str(report), "safe-query")

    assert result["failureCode"] == "BACKUP_INVALID"
    assert not target.exists()
    assert report.with_name(report.name + recovery_drill.REPORT_MARKER_SUFFIX).is_file()


@pytest.mark.parametrize("reparse_relative", ["db", "db/star-isle.db"])
def test_open_readonly_database_rejects_simulated_reparse_without_connecting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reparse_relative: str,
):
    data_root = tmp_path / "candidate"
    _fixture_data_root(data_root)
    flagged = data_root.joinpath(*reparse_relative.split("/"))
    original_is_link_or_reparse = recovery_drill._is_link_or_reparse
    connected = False

    def simulated_reparse(path: Path) -> bool:
        if Path(path) == flagged:
            return True
        return original_is_link_or_reparse(Path(path))

    def unexpected_connect(*_args, **_kwargs):
        nonlocal connected
        connected = True
        raise AssertionError("SQLite was opened after a reparse path was detected")

    monkeypatch.setattr(recovery_drill, "_is_link_or_reparse", simulated_reparse)
    monkeypatch.setattr(recovery_drill.sqlite3, "connect", unexpected_connect)

    with pytest.raises(recovery_drill.DrillFailure) as failure:
        recovery_drill._open_readonly_database(data_root)

    assert failure.value.code == "DATABASE_UNREADABLE"
    assert not connected


def test_recovery_drill_rejects_target_and_parent_symlinks(tmp_path: Path):
    _, backup = _completed_backup(tmp_path)
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    parent_link = tmp_path / "parent-link"
    target_link = tmp_path / "target-link"
    try:
        parent_link.symlink_to(real_parent, target_is_directory=True)
        target_link.symlink_to(real_parent, target_is_directory=True)
    except OSError:
        pytest.skip("当前 Windows 测试账户不允许创建符号链接")

    parent_result = _failed_cli(
        backup,
        parent_link / "child",
        tmp_path / "parent-report.json",
        tmp_path / "configured-data-root",
    )
    assert parent_result["failureCode"] == "TARGET_PATH_UNSAFE"
    link_result = _failed_cli(
        backup,
        target_link,
        tmp_path / "target-report.json",
        tmp_path / "configured-data-root",
    )
    assert link_result["failureCode"] in {"TARGET_PATH_UNSAFE", "TARGET_NOT_NEW"}
    assert not (real_parent / "child").exists()


def _popen_runner(
    backup: Path,
    target: Path,
    report: Path,
    configured_data_root: Path,
) -> OwnedProcess:
    return spawn_owned_process(
        [
            sys.executable,
            "-B",
            str(RUNNER),
            "--backup",
            str(backup),
            "--target",
            str(target),
            "--report",
            str(report),
            "--knowledge-query-stdin",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_runner_environment(configured_data_root),
    )


def _stop_popen_runner(process: OwnedProcess) -> bool:
    """Stop only this test-owned runner, reap it, then close all pipes."""

    return stop_owned_process(process, timeout_seconds=5)


def _popen_report(
    process: OwnedProcess, *, timeout_seconds: float = 45
) -> tuple[int, dict[str, object]]:
    try:
        try:
            stdout, stderr = process.communicate(b"parallel isolated knowledge query", timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            raise AssertionError("recovery runner timed out") from None
        except OSError:
            raise AssertionError("recovery runner execution failed") from None
        try:
            if stderr:
                raise AssertionError
            lines = stdout.decode("utf-8").splitlines()
            if len(lines) != 1:
                raise AssertionError
            parsed = json.loads(lines[0])
            if not isinstance(parsed, dict) or process.returncode is None:
                raise AssertionError
            return process.returncode, parsed
        except (AssertionError, AttributeError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
            raise AssertionError("recovery runner output invalid") from None
    finally:
        if not _stop_popen_runner(process):
            raise RuntimeError("test-owned recovery runner cleanup failed") from None


def _collect_popen_reports(
    *processes: OwnedProcess, timeout_seconds: float = 45
) -> list[tuple[int, dict[str, object]]]:
    try:
        return [_popen_report(process, timeout_seconds=timeout_seconds) for process in processes]
    finally:
        # If parsing/collection of one runner fails, no sibling is allowed to survive.
        cleanup_ok = True
        for process in processes:
            cleanup_ok = _stop_popen_runner(process) and cleanup_ok
        if not cleanup_ok:
            raise RuntimeError("test-owned recovery runner cleanup failed") from None


def test_recovery_drill_same_claim_allows_only_one_runner(tmp_path: Path):
    _, backup = _completed_backup(tmp_path)
    target = tmp_path / "target"
    report = tmp_path / "report.json"
    configured_data_root = tmp_path / "configured-data-root"
    first = _popen_runner(backup, target, report, configured_data_root)
    second = _popen_runner(backup, target, report, configured_data_root)
    outcomes = _collect_popen_reports(first, second)
    assert sorted(code for code, _ in outcomes) == [0, 1]
    failed = next(payload for code, payload in outcomes if code == 1)
    passed = next(payload for code, payload in outcomes if code == 0)
    assert failed["failureCode"] in {"REPORT_CONFLICT", "TARGET_NOT_NEW"}
    assert passed["status"] == "passed"
    assert not report.with_name(report.name + recovery_drill.REPORT_MARKER_SUFFIX).exists()


def test_recovery_drill_separate_safe_claims_can_complete(tmp_path: Path):
    _, backup = _completed_backup(tmp_path)
    configured_data_root = tmp_path / "configured-data-root"
    first = _popen_runner(backup, tmp_path / "target-one", tmp_path / "one.json", configured_data_root)
    second = _popen_runner(backup, tmp_path / "target-two", tmp_path / "two.json", configured_data_root)
    outcomes = _collect_popen_reports(first, second)
    assert [code for code, _ in outcomes] == [0, 0]
    assert all(payload["status"] == "passed" for _, payload in outcomes)


def test_popen_report_timeout_reaps_owned_runner_and_closes_pipes():
    process = spawn_owned_process(
        [sys.executable, "-B", "-c", "import time; time.sleep(30)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    with pytest.raises(AssertionError, match="recovery runner timed out"):
        _popen_report(process, timeout_seconds=0.01)
    assert process.poll() is not None
    assert all(pipe is None or pipe.closed for pipe in (process.stdin, process.stdout, process.stderr))


def test_popen_parse_failure_reaps_sibling_runner_without_waiting_for_default_timeout():
    malformed = spawn_owned_process(
        [sys.executable, "-B", "-c", "import sys; sys.stdout.write('not-json\\n'); sys.stdout.flush()"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    sibling = spawn_owned_process(
        [sys.executable, "-B", "-c", "import time; time.sleep(30)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Windows launches a gate-blocked bootstrap and then the requested Python command.
    # This bounded window covers both cold starts without masking a 45-second collection wait.
    started = time.monotonic()
    with pytest.raises(AssertionError, match="recovery runner output invalid") as raised:
        _collect_popen_reports(malformed, sibling, timeout_seconds=5)
    elapsed = time.monotonic() - started
    assert "not-json" not in str(raised.value)
    assert elapsed < 10
    assert malformed.poll() is not None
    assert sibling.poll() is not None
    assert all(pipe is None or pipe.closed for pipe in (sibling.stdin, sibling.stdout, sibling.stderr))


def test_popen_raw_stdout_or_stderr_is_replaced_with_a_fixed_assertion():
    process = spawn_owned_process(
        [
            sys.executable,
            "-B",
            "-c",
            "import sys; sys.stdout.buffer.write(b'PRIVATE_CANARY_DOCUMENT\\n'); "
            "sys.stderr.buffer.write(b'PRIVATE_CANARY_ANSWER\\n')",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    with pytest.raises(AssertionError, match="recovery runner output invalid") as raised:
        _popen_report(process, timeout_seconds=2)
    assert "PRIVATE_CANARY" not in str(raised.value)
    assert process.poll() is not None
    assert all(pipe is None or pipe.closed for pipe in (process.stdin, process.stdout, process.stderr))


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _port_is_held(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True


def _wait_for_port_state(port: int, *, held: bool) -> bool:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if _port_is_held(port) is held:
            return True
        time.sleep(0.05)
    return _port_is_held(port) is held


def _terminate_exact_pid_for_test(pid: int) -> None:
    """Last-resort test cleanup: address only the recorded child PID."""

    if pid <= 0:
        return
    try:
        os.kill(pid, signal.SIGTERM if os.name == "nt" else signal.SIGKILL)
    except (OSError, ProcessLookupError):
        return


def test_owned_process_tree_cleanup_reaps_grandchild_after_leader_exit():
    for _ in range(3):
        port = _free_loopback_port()
        grandchild = (
            "import socket, time; "
            "sock=socket.socket(socket.AF_INET, socket.SOCK_STREAM); "
            "sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); "
            f"sock.bind(('127.0.0.1', {port})); sock.listen(1); time.sleep(30)"
        )
        leader = (
            "import subprocess, sys; "
            f"child=subprocess.Popen([sys.executable, '-B', '-c', {grandchild!r}]); "
            "print(child.pid, flush=True)"
        )
        process = spawn_owned_process(
            [sys.executable, "-B", "-c", leader],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        child_pid = -1
        try:
            process.wait(timeout=3)
            assert process.stdout is not None
            stdout = process.stdout.readline()
            child_pid = int(stdout.decode("ascii").strip())
            assert process.poll() is not None
            assert _wait_for_port_state(port, held=True)
            assert _stop_popen_runner(process)
            assert _wait_for_port_state(port, held=False)
        finally:
            _stop_popen_runner(process)
            _terminate_exact_pid_for_test(child_pid)
            _wait_for_port_state(port, held=False)


def test_recovery_drill_keeps_marker_and_partial_target_on_restore_failure(tmp_path: Path, monkeypatch):
    _, backup = _completed_backup(tmp_path)
    target = tmp_path / "target"
    report = tmp_path / "report.json"

    def fail_restore(*_args, **_kwargs):
        raise recovery_drill.backup_restore.BackupError("injected")

    monkeypatch.setattr(recovery_drill.backup_restore, "restore", fail_restore)
    result = recovery_drill.run(str(backup), str(target), str(report), "failure query")
    assert result["failureCode"] == "RESTORE_FAILED"
    assert target.is_dir()
    assert report.with_name(report.name + recovery_drill.REPORT_MARKER_SUFFIX).is_file()
    assert json.loads(report.read_text(encoding="utf-8"))["status"] == "failed"


def test_recovery_drill_preserves_actual_restore_partial_for_operator(tmp_path: Path, monkeypatch):
    _, backup = _completed_backup(tmp_path)
    target = tmp_path / "target"
    report = tmp_path / "report.json"

    def fail_copy(*_args, **_kwargs):
        raise OSError("injected")

    monkeypatch.setattr(recovery_drill.backup_restore.shutil, "copy2", fail_copy)
    result = recovery_drill.run(str(backup), str(target), str(report), "partial restore query")
    assert result["failureCode"] == "RESTORE_FAILED"
    assert (target / "db").is_dir()
    assert report.with_name(report.name + recovery_drill.REPORT_MARKER_SUFFIX).is_file()


def test_recovery_drill_keeps_marker_when_post_restore_verify_fails(tmp_path: Path, monkeypatch):
    _, backup = _completed_backup(tmp_path)
    target = tmp_path / "target"
    report = tmp_path / "report.json"
    original_quiet = recovery_drill._quiet_call
    verify_calls = 0

    def fail_second_verify(function, *args, **kwargs):
        nonlocal verify_calls
        if function is recovery_drill.backup_restore.verify:
            verify_calls += 1
            if verify_calls == 2:
                raise recovery_drill.backup_restore.BackupError("injected")
        return original_quiet(function, *args, **kwargs)

    monkeypatch.setattr(recovery_drill, "_quiet_call", fail_second_verify)
    result = recovery_drill.run(str(backup), str(target), str(report), "failure query")
    assert result["failureCode"] == "VERIFY_FAILED"
    assert target.is_dir()
    assert report.with_name(report.name + recovery_drill.REPORT_MARKER_SUFFIX).is_file()


def test_recovery_drill_maps_zero_hits_and_child_failure_to_knowledge_codes(tmp_path: Path, monkeypatch):
    _, backup = _completed_backup(tmp_path)

    class ZeroHitResult:
        returncode = 0
        stdout = b'{"hits":0,"ok":true}\n'
        stderr = b""

    monkeypatch.setattr(recovery_drill.subprocess, "run", lambda *_args, **_kwargs: ZeroHitResult())
    zero_target = tmp_path / "zero-target"
    zero_report = tmp_path / "zero.json"
    zero = recovery_drill.run(str(backup), str(zero_target), str(zero_report), "zero-hit query")
    assert zero["failureCode"] == "KNOWLEDGE_NO_HITS"
    assert zero_report.with_name(zero_report.name + recovery_drill.REPORT_MARKER_SUFFIX).is_file()

    child_target = tmp_path / "child-target"
    child_report = tmp_path / "child.json"

    def child_failed(*_args):
        raise recovery_drill.DrillFailure("KNOWLEDGE_CHILD_FAILED")

    monkeypatch.setattr(recovery_drill, "_run_target_search", child_failed)
    child = recovery_drill.run(str(backup), str(child_target), str(child_report), "child-failure query")
    assert child["failureCode"] == "KNOWLEDGE_CHILD_FAILED"
    assert child_report.with_name(child_report.name + recovery_drill.REPORT_MARKER_SUFFIX).is_file()


def test_recovery_drill_marker_vetoes_a_previously_published_passed_report(tmp_path: Path, monkeypatch):
    _, backup = _completed_backup(tmp_path)
    target = tmp_path / "target"
    report = tmp_path / "report.json"
    original_update = recovery_drill._publish_report_update
    update_calls = 0

    def fail_failed_update(claim, payload):
        nonlocal update_calls
        update_calls += 1
        if update_calls == 2:
            raise recovery_drill.DrillFailure("REPORT_PUBLISH_FAILED")
        return original_update(claim, payload)

    def fail_marker_release(_claim):
        raise recovery_drill.DrillFailure("MARKER_RELEASE_FAILED")

    monkeypatch.setattr(recovery_drill, "_publish_report_update", fail_failed_update)
    monkeypatch.setattr(recovery_drill, "_release_marker", fail_marker_release)
    result = recovery_drill.run(str(backup), str(target), str(report), "marker veto query")
    assert result["status"] == "failed"
    assert result["failureCode"] == "MARKER_RELEASE_FAILED"
    assert json.loads(report.read_text(encoding="utf-8"))["status"] == "passed"
    assert report.with_name(report.name + recovery_drill.REPORT_MARKER_SUFFIX).is_file()


def test_recovery_drill_report_replace_failure_keeps_running_report_and_marker(tmp_path: Path, monkeypatch):
    _, backup = _completed_backup(tmp_path)
    target = tmp_path / "target"
    report = tmp_path / "report.json"

    def fail_update(*_args):
        raise recovery_drill.DrillFailure("REPORT_PUBLISH_FAILED")

    monkeypatch.setattr(recovery_drill, "_publish_report_update", fail_update)
    result = recovery_drill.run(str(backup), str(target), str(report), "report failure query")
    assert result["status"] == "failed"
    assert result["failureCode"] == "REPORT_PUBLISH_FAILED"
    assert target.is_dir()
    assert json.loads(report.read_text(encoding="utf-8"))["status"] == "running"
    assert report.with_name(report.name + recovery_drill.REPORT_MARKER_SUFFIX).is_file()
