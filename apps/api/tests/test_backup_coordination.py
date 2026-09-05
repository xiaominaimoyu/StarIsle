"""备份协调门禁：所有 fixture 都在 pytest 临时 DATA_ROOT 中。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import sqlite3
import threading
from pathlib import Path

import pytest

from tests.test_backup_restore import _add_v5_lifecycle_asset, _fixture_data_root, _fixture_legacy_v3_data_root
from tests.test_backup_restore import backup_restore
from app.db import MIGRATION_V2, MIGRATION_V3, MIGRATION_V4, SCHEMA_V1, SCHEMA_V4


def _manifest_paths(backup: Path) -> set[str]:
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    return {str(entry["path"]) for entry in manifest["files"]}


def _add_legacy_asset(root: Path, object_key: str, content: bytes, subdirs: tuple[str, ...]) -> None:
    connection = sqlite3.connect(root / "db" / "star-isle.db")
    try:
        connection.execute(
            "INSERT INTO asset (id, original_name, mime_type, size, object_key, sha256, status, created_at) "
            "VALUES ('legacy-asset', 'legacy.bin', 'application/octet-stream', ?, ?, ?, 'active', "
            "'2026-08-27T00:00:00+00:00')",
            (len(content), object_key, hashlib.sha256(content).hexdigest()),
        )
        connection.commit()
    finally:
        connection.close()
    for subdir in subdirs:
        target = root / "objects" / subdir / object_key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _fixture_schema_v1_to_v4(root: Path, version: int) -> None:
    if version == 3:
        _fixture_legacy_v3_data_root(root)
        return

    database = root / "db" / "star-isle.db"
    database.parent.mkdir(parents=True)
    connection = sqlite3.connect(database)
    try:
        if version == 1:
            connection.executescript(SCHEMA_V1)
        elif version == 2:
            connection.executescript(SCHEMA_V1)
            for statement in MIGRATION_V2:
                connection.execute(statement)
        elif version == 4:
            connection.executescript(SCHEMA_V4)
            for statement in MIGRATION_V2 + MIGRATION_V3[1:] + MIGRATION_V4[1:]:
                connection.execute(statement)
        else:  # pragma: no cover - parametrization is the contract.
            raise AssertionError("unsupported fixture schema")
        connection.execute(
            "CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_version VALUES (1, ?, '2026-08-27T00:00:00+00:00')", (version,))
        connection.commit()
    finally:
        connection.close()


def test_lock_a_allows_independent_online_backup_b_and_blocks_other_writer(tmp_path: Path):
    source = tmp_path / "source"
    destination = tmp_path / "destination" / "db" / "star-isle.db"
    _fixture_data_root(source)
    database = source / "db" / "star-isle.db"

    lock_a = backup_restore._acquire_source_lock(database, 1.0)
    writer = sqlite3.connect(database, timeout=0.05)
    try:
        writer.execute("PRAGMA busy_timeout=50")
        # B is a separate read-only connection inside _sqlite_snapshot; it must not self-block on A.
        backup_restore._sqlite_snapshot(database, destination)
        assert destination.is_file()
        with pytest.raises(sqlite3.OperationalError):
            writer.execute("BEGIN IMMEDIATE")
    finally:
        writer.close()
        lock_a.rollback()
        lock_a.close()

    writer = sqlite3.connect(database, timeout=0.5)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.rollback()
    finally:
        writer.close()


def test_writer_held_lock_times_out_before_creating_output_then_recovers(tmp_path: Path):
    source = tmp_path / "source"
    output = tmp_path / "backup"
    _fixture_data_root(source)
    database = source / "db" / "star-isle.db"
    writer = sqlite3.connect(database, timeout=1.0)
    writer.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(backup_restore.BackupError, match="协调锁"):
            backup_restore.backup(source, output, verify_after=False, lock_timeout_seconds=0.1)
        assert not output.exists()
    finally:
        writer.rollback()
        writer.close()

    backup_restore.backup(source, output, verify_after=True, lock_timeout_seconds=1.0)
    assert (output / "manifest.json").is_file()


def test_snapshot_manifest_only_copies_referenced_objects_and_not_late_orphans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "source"
    backup = tmp_path / "backup"
    restored = tmp_path / "restored"
    _fixture_data_root(source)
    (source / "objects" / "originals" / "ordinary-orphan.bin").write_bytes(b"orphan")
    (source / "objects" / "health").mkdir(parents=True)
    (source / "objects" / "health" / "probe.probe").write_bytes(b"health")

    original_copy = backup_restore._copy_logical_objects

    copy_entered = threading.Event()
    unreferenced_created = threading.Event()
    creator_errors: list[BaseException] = []

    def create_unreferenced_object() -> None:
        try:
            assert copy_entered.wait(timeout=5)
            (source / "objects" / "originals" / "late-unreferenced.bin").write_bytes(b"late")
            unreferenced_created.set()
        except BaseException as exc:  # pragma: no cover - assertion is re-raised below.
            creator_errors.append(exc)

    def copy_after_unreferenced_create(source_root: Path, backup_root: Path, records):
        copy_entered.set()
        assert unreferenced_created.wait(timeout=5)
        return original_copy(source_root, backup_root, records)

    monkeypatch.setattr(backup_restore, "_copy_logical_objects", copy_after_unreferenced_create)
    creator = threading.Thread(target=create_unreferenced_object)
    creator.start()
    backup_restore.backup(source, backup, verify_after=True)
    creator.join(timeout=5)
    assert not creator.is_alive()
    assert not creator_errors
    paths = _manifest_paths(backup)
    assert "objects/originals/photo.bin" in paths
    assert "objects/originals/ordinary-orphan.bin" not in paths
    assert "objects/originals/late-unreferenced.bin" not in paths
    assert "objects/health/probe.probe" not in paths
    assert not (backup / "objects" / "health").exists()
    assert json.loads((backup / "manifest.json").read_text(encoding="utf-8"))["objectCount"] == 5

    backup_restore.restore(backup, restored)
    assert not (restored / "objects" / "originals" / "ordinary-orphan.bin").exists()
    assert not (restored / "objects" / "originals" / "late-unreferenced.bin").exists()
    assert (restored / "objects" / "originals" / "photo.bin").read_bytes() == b"photo"


@pytest.mark.parametrize("schema_version", [1, 2, 3, 4])
@pytest.mark.parametrize("layout", [("originals",), (), ("originals", "thumbnails")])
def test_v1_to_v4_asset_requires_exactly_one_safe_candidate(
    tmp_path: Path,
    schema_version: int,
    layout: tuple[str, ...],
):
    source = tmp_path / f"legacy-source-v{schema_version}"
    backup = tmp_path / f"legacy-backup-v{schema_version}"
    _fixture_schema_v1_to_v4(source, schema_version)
    _add_legacy_asset(source, "legacy.bin", b"legacy", layout)

    if layout == ("originals",):
        backup_restore.backup(source, backup, verify_after=False)
        assert "objects/originals/legacy.bin" in _manifest_paths(backup)
    else:
        with pytest.raises(backup_restore.BackupError, match="历史 asset"):
            backup_restore.backup(source, backup, verify_after=False)
        assert (backup / backup_restore.INCOMPLETE_MARKER_NAME).is_file()


@pytest.mark.parametrize("layout", ["missing", "multiple"])
def test_v5_unknown_asset_requires_exactly_one_candidate(tmp_path: Path, layout: str):
    source = tmp_path / f"source-{layout}"
    backup = tmp_path / f"backup-{layout}"
    _fixture_data_root(source)
    _add_v5_lifecycle_asset(
        source,
        asset_id="unknown-candidate",
        storage_class="unknown",
        content=b"unknown",
        object_key="unknown-candidate.bin",
        subdir="originals",
    )
    candidate = source / "objects" / "originals" / "unknown-candidate.bin"
    if layout == "missing":
        candidate.unlink()
    else:
        second = source / "objects" / "thumbnails" / candidate.name
        second.parent.mkdir(parents=True, exist_ok=True)
        second.write_bytes(b"unknown")

    with pytest.raises(backup_restore.BackupError, match="未知 asset"):
        backup_restore.backup(source, backup, verify_after=False)
    assert (backup / backup_restore.INCOMPLETE_MARKER_NAME).is_file()


def test_future_schema_version_is_rejected_for_backup_and_public_verify(tmp_path: Path):
    source = tmp_path / "source"
    rejected_output = tmp_path / "rejected-output"
    verified_output = tmp_path / "verified-output"
    _fixture_data_root(source)

    connection = sqlite3.connect(source / "db" / "star-isle.db")
    try:
        connection.execute("UPDATE schema_version SET version=6 WHERE id=1")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(backup_restore.BackupError, match="schema"):
        backup_restore.backup(source, rejected_output, verify_after=False)
    assert (rejected_output / backup_restore.INCOMPLETE_MARKER_NAME).is_file()

    _fixture_data_root(verified_output)
    generated_backup = tmp_path / "generated-backup"
    backup_restore.backup(verified_output, generated_backup, verify_after=True)
    manifest_path = generated_backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schemaVersion"] = 6
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(backup_restore.BackupError, match="schema"):
        backup_restore.verify(generated_backup)


def test_referenced_missing_or_symlink_object_is_rejected(tmp_path: Path):
    source = tmp_path / "source"
    missing_backup = tmp_path / "missing-backup"
    _fixture_data_root(source)
    target = source / "objects" / "originals" / "photo.bin"
    target.unlink()
    with pytest.raises(backup_restore.BackupError, match="对象缺失"):
        backup_restore.backup(source, missing_backup, verify_after=False)
    assert (missing_backup / backup_restore.INCOMPLETE_MARKER_NAME).is_file()

    symlink_source = tmp_path / "symlink-source"
    symlink_backup = tmp_path / "symlink-backup"
    _fixture_data_root(symlink_source)
    target = symlink_source / "objects" / "originals" / "photo.bin"
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"photo")
    target.unlink()
    try:
        os.symlink(outside, target)
    except OSError:
        pytest.skip("当前 Windows 测试账户不能创建符号链接")
    with pytest.raises(backup_restore.BackupError, match="符号链接"):
        backup_restore.backup(symlink_source, symlink_backup, verify_after=False)
    assert (symlink_backup / backup_restore.INCOMPLETE_MARKER_NAME).is_file()


def test_same_logical_path_with_conflicting_metadata_is_rejected(tmp_path: Path):
    source = tmp_path / "source"
    backup = tmp_path / "backup"
    _fixture_data_root(source)
    connection = sqlite3.connect(source / "db" / "star-isle.db")
    try:
        connection.execute(
            "INSERT INTO asset (id, original_name, mime_type, size, object_key, sha256, storage_class, status, created_at) "
            "VALUES ('duplicate-path', 'copy.bin', 'application/octet-stream', 1, 'photo.bin', ?, 'file', "
            "'active', '2026-08-27T00:00:00+00:00')",
            ("0" * 64,),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(backup_restore.BackupError, match="物理状态|元数据"):
        backup_restore.backup(source, backup, verify_after=False)


def test_marker_blocks_public_verify_restore_after_copy_or_manifest_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "source"
    copy_output = tmp_path / "copy-failure"
    _fixture_data_root(source)
    original_copy = backup_restore._copy_logical_objects

    def fail_copy(*_args, **_kwargs):
        raise backup_restore.BackupError("injected copy failure")

    monkeypatch.setattr(backup_restore, "_copy_logical_objects", fail_copy)
    with pytest.raises(backup_restore.BackupError):
        backup_restore.backup(source, copy_output, verify_after=False)
    assert (copy_output / backup_restore.INCOMPLETE_MARKER_NAME).is_file()
    with pytest.raises(backup_restore.BackupError, match="尚未完成"):
        backup_restore.verify(copy_output)
    with pytest.raises(backup_restore.BackupError, match="尚未完成"):
        backup_restore.restore(copy_output, tmp_path / "copy-restore")

    manifest_output = tmp_path / "manifest-failure"
    original_replace = backup_restore.os.replace

    def fail_manifest_replace(source_path, destination_path):
        if Path(source_path).name == backup_restore.MANIFEST_TEMP_NAME:
            raise OSError("injected manifest failure")
        return original_replace(source_path, destination_path)

    monkeypatch.setattr(backup_restore, "_copy_logical_objects", original_copy)
    monkeypatch.setattr(backup_restore.os, "replace", fail_manifest_replace)
    with pytest.raises(backup_restore.BackupError, match="manifest"):
        backup_restore.backup(source, manifest_output, verify_after=False)
    assert (manifest_output / backup_restore.INCOMPLETE_MARKER_NAME).is_file()
    assert (manifest_output / backup_restore.MANIFEST_TEMP_NAME).is_file()
    assert not (manifest_output / "manifest.json").exists()

    verify_output = tmp_path / "verify-failure"
    monkeypatch.setattr(backup_restore.os, "replace", original_replace)

    def fail_verify(*_args, **_kwargs):
        raise backup_restore.BackupError("injected verify failure")

    original_verify = backup_restore._verify_backup
    monkeypatch.setattr(backup_restore, "_verify_backup", fail_verify)
    with pytest.raises(backup_restore.BackupError, match="verify"):
        backup_restore.backup(source, verify_output, verify_after=True)
    assert (verify_output / "manifest.json").is_file()
    assert (verify_output / backup_restore.INCOMPLETE_MARKER_NAME).is_file()
    monkeypatch.setattr(backup_restore, "_verify_backup", original_verify)
    with pytest.raises(backup_restore.BackupError, match="尚未完成"):
        backup_restore.verify(verify_output)


def test_manifest_is_atomically_published_and_success_removes_build_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "source"
    output = tmp_path / "backup"
    _fixture_data_root(source)
    observed_atomic_publish = []
    original_replace = backup_restore.os.replace

    def observe_replace(source_path, destination_path):
        if Path(source_path).name == backup_restore.MANIFEST_TEMP_NAME:
            assert Path(source_path).is_file()
            assert not Path(destination_path).exists()
            observed_atomic_publish.append(True)
        return original_replace(source_path, destination_path)

    monkeypatch.setattr(backup_restore.os, "replace", observe_replace)
    backup_restore.backup(source, output, verify_after=True)
    assert observed_atomic_publish == [True]
    assert not (output / backup_restore.INCOMPLETE_MARKER_NAME).exists()
    assert not (output / backup_restore.MANIFEST_TEMP_NAME).exists()
    backup_restore.verify(output)


def test_source_gate_serializes_different_outputs_and_marker_rejects_same_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "source"
    other_source = tmp_path / "other-source"
    first_output = tmp_path / "first-output"
    second_output = tmp_path / "second-output"
    shared_output = tmp_path / "shared-output"
    _fixture_data_root(source)
    _fixture_data_root(other_source)
    original_copy = backup_restore._copy_logical_objects
    entered = threading.Event()
    release = threading.Event()

    def blocking_copy(source_root: Path, backup_root: Path, records):
        if backup_root == first_output or backup_root == shared_output:
            entered.set()
            assert release.wait(timeout=5)
        return original_copy(source_root, backup_root, records)

    monkeypatch.setattr(backup_restore, "_copy_logical_objects", blocking_copy)
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(backup_restore.backup, source, first_output, False, 2.0)
        assert entered.wait(timeout=5)
        with pytest.raises(backup_restore.BackupError, match="协调锁"):
            backup_restore.backup(source, second_output, verify_after=False, lock_timeout_seconds=0.1)
        assert not second_output.exists()
        release.set()
        first.result(timeout=10)

    backup_restore.backup(source, second_output, verify_after=True)
    assert (second_output / "manifest.json").is_file()

    entered.clear()
    release.clear()
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(backup_restore.backup, source, shared_output, False, 2.0)
        assert entered.wait(timeout=5)
        with pytest.raises(backup_restore.BackupError, match="构建|拒绝覆盖"):
            backup_restore.backup(other_source, shared_output, verify_after=False, lock_timeout_seconds=1.0)
        release.set()
        first.result(timeout=10)
    manifest_hash = hashlib.sha256((shared_output / "manifest.json").read_bytes()).hexdigest()
    with pytest.raises(backup_restore.BackupError, match="拒绝覆盖"):
        backup_restore.backup(other_source, shared_output, verify_after=False)
    assert hashlib.sha256((shared_output / "manifest.json").read_bytes()).hexdigest() == manifest_hash


def test_gc_style_writer_cannot_remove_snapshot_referenced_object_until_gate_releases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "source"
    backup = tmp_path / "backup"
    _fixture_data_root(source)
    _add_v5_lifecycle_asset(
        source,
        asset_id="gc-coordinated",
        storage_class="file",
        content=b"pending GC object",
        object_key="gc-coordinated.bin",
        status="deleted",
    )
    database = source / "db" / "star-isle.db"
    referenced_object = source / "objects" / "originals" / "gc-coordinated.bin"
    original_copy = backup_restore._copy_logical_objects
    copy_entered = threading.Event()
    release_copy = threading.Event()
    writer_started = threading.Event()
    writer_acquired = threading.Event()
    writer_errors: list[BaseException] = []

    def blocking_copy(source_root: Path, backup_root: Path, records):
        copy_entered.set()
        assert release_copy.wait(timeout=5)
        return original_copy(source_root, backup_root, records)

    def gc_style_writer() -> None:
        connection = sqlite3.connect(database, timeout=5.0)
        try:
            writer_started.set()
            # This is deliberately the first SQL statement: a retention/GC writer
            # cannot delete a referenced file until it owns the same write gate.
            connection.execute("BEGIN IMMEDIATE")
            writer_acquired.set()
            referenced_object.unlink()
            connection.execute(
                "UPDATE asset SET object_purged_at='2026-08-31T00:00:00+00:00', "
                "gc_next_run_at=NULL WHERE id='gc-coordinated'"
            )
            connection.commit()
        except BaseException as exc:  # pragma: no cover - assertion is re-raised below.
            writer_errors.append(exc)
        finally:
            connection.close()

    monkeypatch.setattr(backup_restore, "_copy_logical_objects", blocking_copy)
    with ThreadPoolExecutor(max_workers=1) as executor:
        backup_future = executor.submit(backup_restore.backup, source, backup, False, 2.0)
        assert copy_entered.wait(timeout=5)
        writer = threading.Thread(target=gc_style_writer)
        writer.start()
        assert writer_started.wait(timeout=5)
        assert not writer_acquired.wait(timeout=0.25)
        assert referenced_object.is_file()
        release_copy.set()
        backup_future.result(timeout=10)

    writer.join(timeout=5)
    assert not writer.is_alive()
    assert not writer_errors
    assert writer_acquired.is_set()
    assert not referenced_object.exists()
    assert "objects/originals/gc-coordinated.bin" in _manifest_paths(backup)


def test_wal_source_is_restored_without_wal_shm_or_journal_in_backup(tmp_path: Path):
    source = tmp_path / "source"
    backup = tmp_path / "backup"
    restored = tmp_path / "restored"
    _fixture_data_root(source)
    database = source / "db" / "star-isle.db"
    connection = sqlite3.connect(database)
    try:
        assert str(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower() == "wal"
        connection.execute("UPDATE mail_message SET subject='wal snapshot' WHERE id='mail-1'")
        connection.commit()
        assert database.with_name(database.name + "-wal").exists()
        backup_restore.backup(source, backup, verify_after=True)
    finally:
        connection.close()

    paths = _manifest_paths(backup)
    assert not any(path.endswith(("-wal", "-shm", "-journal")) for path in paths)
    assert not any((backup / "db" / f"star-isle.db{suffix}").exists() for suffix in ("-wal", "-shm", "-journal"))
    backup_restore.restore(backup, restored)
    backup_restore.verify(backup, restored)
    restored_connection = sqlite3.connect(restored / "db" / "star-isle.db")
    try:
        assert restored_connection.execute("SELECT subject FROM mail_message WHERE id='mail-1'").fetchone()[0] == "wal snapshot"
    finally:
        restored_connection.close()
