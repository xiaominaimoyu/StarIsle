"""开机后的每日备份。

数据库只用 SQLite 在线 backup API 写成独立快照，保留最近 7 份。
照片按快照里仍有效的对象增量复制。绝不复制正在使用的数据库文件，
也拒绝把数据目录和备份目录嵌在一起。
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.private_runtime import (
    SNAPSHOT_KEEP,
    PrivateRuntimeError,
    assert_separated,
)

LIVE_DB = Path("db") / "star-isle.db"
SNAPSHOT_DIR = "db-snapshots"
PHOTO_BACKUP_DIR = "photos"
PHOTO_CLASSES = {
    "photo-original": "originals",
    "photo-thumbnail": "thumbnails",
}
_OBJECT_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class DailyBackupError(PrivateRuntimeError):
    pass


@dataclass(frozen=True)
class BackupReport:
    snapshot_count: int
    photos_copied: int
    photos_skipped: int
    photos_missing: int
    photos_refused: int
    skipped_no_db: bool = False


def _utc_stamp(now: datetime) -> str:
    moment = now.astimezone(UTC)
    return moment.strftime("%Y%m%dT%H%M%SZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _same_file(left: Path, right: Path) -> bool:
    try:
        left_stat = os.lstat(left)
        right_stat = os.lstat(right)
    except OSError as exc:
        raise DailyBackupError("BACKUP_IO") from exc
    return left_stat.st_dev == right_stat.st_dev and left_stat.st_ino == right_stat.st_ino


def snapshot_database(live_db: Path, backup_root: Path, *, now: datetime | None = None) -> Path:
    """用 Connection.backup 写快照。不复制 live db 文件本身。"""

    live = live_db.resolve()
    root = backup_root.resolve()
    try:
        live.relative_to(root)
    except ValueError:
        inside = False
    else:
        inside = True
    if inside or live == root:
        raise DailyBackupError("BACKUP_DB_INSIDE_BACKUP_ROOT")
    if not live.is_file():
        raise DailyBackupError("BACKUP_DB_MISSING")

    moment = now or datetime.now(UTC)
    folder = root / SNAPSHOT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    stamp = _utc_stamp(moment)
    prefix = f"star-isle-{stamp}-"
    sequences = []
    for path in folder.glob(prefix + "*.db"):
        tail = path.name[len(prefix) : -len(".db")]
        if tail.isdigit():
            sequences.append(int(tail))
    destination = folder / f"{prefix}{max(sequences, default=0) + 1:02d}.db"
    partial = destination.with_name(destination.name + ".partial")
    if partial.exists():
        partial.unlink()

    with closing(sqlite3.connect(live, timeout=30)) as source, closing(sqlite3.connect(partial)) as target:
        try:
            source.backup(target)
            check = target.execute("PRAGMA integrity_check").fetchone()
            if check is None or check[0] != "ok":
                raise DailyBackupError("BACKUP_INTEGRITY_FAILED")
        except DailyBackupError:
            partial.unlink(missing_ok=True)
            raise
        except Exception as exc:
            partial.unlink(missing_ok=True)
            raise DailyBackupError("BACKUP_FAILED") from exc
    os.replace(partial, destination)
    if _same_file(live, destination):
        destination.unlink(missing_ok=True)
        raise DailyBackupError("BACKUP_DB_INSIDE_BACKUP_ROOT")
    return destination


def prune_snapshots(backup_root: Path, *, keep: int = SNAPSHOT_KEEP) -> list[Path]:
    if keep < 1:
        raise DailyBackupError("BACKUP_RETENTION_INVALID")
    folder = backup_root / SNAPSHOT_DIR
    if not folder.is_dir():
        return []
    snapshots = sorted(
        path
        for path in folder.iterdir()
        if path.is_file() and path.name.startswith("star-isle-") and path.suffix == ".db"
    )
    extra = snapshots[: max(0, len(snapshots) - keep)]
    for path in extra:
        path.unlink()
    return extra


def _child(parent: Path, name: str) -> Path:
    if not _OBJECT_KEY.fullmatch(name):
        raise DailyBackupError("BACKUP_OBJECT_KEY")
    path = parent / name
    if path.parent != parent:
        raise DailyBackupError("BACKUP_OBJECT_KEY")
    return path


def _is_symlink(path: Path) -> bool:
    try:
        return path.is_symlink()
    except OSError as exc:
        raise DailyBackupError("BACKUP_IO") from exc


def copy_photos_from_snapshot(data_root: Path, backup_root: Path, snapshot: Path) -> tuple[int, int, int, int]:
    """按快照中的有效照片对象增量复制。同内容跳过，不跟随符号链接。"""

    copied = skipped = missing = refused = 0
    uri = snapshot.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        try:
            rows = connection.execute(
                "SELECT storage_class, object_key FROM asset "
                "WHERE status='active' AND storage_class IN ('photo-original', 'photo-thumbnail')"
            ).fetchall()
        except sqlite3.OperationalError:
            return 0, 0, 0, 0
    finally:
        connection.close()

    for storage_class, object_key in rows:
        subdir = PHOTO_CLASSES.get(str(storage_class))
        if subdir is None or not isinstance(object_key, str):
            refused += 1
            continue
        try:
            source = _child(data_root / "objects" / subdir, object_key)
            target = _child(backup_root / PHOTO_BACKUP_DIR / subdir, object_key)
        except DailyBackupError:
            refused += 1
            continue
        if _is_symlink(source) or not source.is_file():
            if _is_symlink(source):
                refused += 1
            else:
                missing += 1
            continue
        if target.exists():
            if _is_symlink(target):
                refused += 1
                continue
            try:
                same = source.stat().st_size == target.stat().st_size and _sha256(source) == _sha256(target)
            except OSError as exc:
                raise DailyBackupError("BACKUP_IO") from exc
            if same:
                skipped += 1
                continue
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".partial")
        partial.write_bytes(source.read_bytes())
        os.replace(partial, target)
        copied += 1
    return copied, skipped, missing, refused


def run_daily_backup(data_root: Path, backup_root: Path, *, now: datetime | None = None) -> BackupReport:
    assert_separated(data_root, backup_root)
    live = data_root / LIVE_DB
    if not live.is_file():
        return BackupReport(0, 0, 0, 0, 0, skipped_no_db=True)
    backup_root.mkdir(parents=True, exist_ok=True)
    snapshot = snapshot_database(live, backup_root, now=now)
    prune_snapshots(backup_root)
    copied, skipped, missing, refused = copy_photos_from_snapshot(data_root, backup_root, snapshot)
    kept = len(list((backup_root / SNAPSHOT_DIR).glob("star-isle-*.db")))
    return BackupReport(kept, copied, skipped, missing, refused)


def _line(report: BackupReport) -> str:
    if report.skipped_no_db:
        return "BACKUP_SKIPPED_NO_DB"
    return (
        "BACKUP_OK"
        f" snapshots={report.snapshot_count}"
        f" photos_copied={report.photos_copied}"
        f" photos_skipped={report.photos_skipped}"
        f" photos_missing={report.photos_missing}"
        f" photos_refused={report.photos_refused}"
    )


def main(argv: list[str] | None = None) -> int:
    import os

    del argv
    data_value = os.environ.get("DATA_ROOT", "").strip()
    backup_value = os.environ.get("STAR_ISLE_BACKUP_ROOT", "").strip()
    if not data_value:
        print("DATA_ROOT_REQUIRED")
        return 2
    if not backup_value:
        print("BACKUP_ROOT_REQUIRED")
        return 2
    try:
        report = run_daily_backup(Path(data_value), Path(backup_value))
    except PrivateRuntimeError as exc:
        print(exc.code)
        return 2
    print(_line(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
