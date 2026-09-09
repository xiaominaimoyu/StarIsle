"""StorageAdapter:不透明对象 ID 存储;每次读写验证目标仍在 DATA_ROOT 内,拒绝符号链接越界。"""

from __future__ import annotations

from contextlib import contextmanager
import errno
import os
import stat
import time
from pathlib import Path
from typing import Iterator

from .config import settings
from .security import random_id


class StorageQuotaExceeded(RuntimeError):
    """写入会超出已配置对象存储配额。"""


class StorageUnavailable(RuntimeError):
    """对象存储当前无法在有限时间内安全完成操作。"""


class StorageKeyAllocationError(RuntimeError):
    """随机对象键连续冲突后无法分配新的独占文件。"""


SAVE_KEY_ATTEMPTS = 8
QUOTA_LOCK_NAME = ".star-isle-storage-quota.lock"
QUOTA_LOCK_TIMEOUT_SECONDS = 10.0
QUOTA_LOCK_RETRY_SECONDS = 0.05


class StorageAdapter:
    """原件 / 缩略图 / 邮件附件与私有原文分离；对象 ID 始终随机且不可预测。"""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or settings.objects_root).resolve()
        self._quota_lock_path = self.root / QUOTA_LOCK_NAME
        settings.ensure_dirs()

    @staticmethod
    def _acquire_quota_lock(descriptor: int, *, timeout_seconds: float) -> None:
        if timeout_seconds < 0:
            raise ValueError("对象存储配额锁超时必须为非负数")

        deadline = time.monotonic() + timeout_seconds
        retryable = {errno.EACCES, errno.EAGAIN, errno.EDEADLK, errno.EINTR}
        if os.name == "nt":
            import msvcrt
        else:
            import fcntl

        while True:
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                if os.name == "nt":
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except OSError as exc:
                if exc.errno not in retryable:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise StorageUnavailable(
                        "对象存储配额锁等待超时，存储暂不可用"
                    ) from exc
                time.sleep(min(QUOTA_LOCK_RETRY_SECONDS, remaining))

    @staticmethod
    def _release_quota_lock(descriptor: int) -> None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)

    @contextmanager
    def _quota_lock(self) -> Iterator[None]:
        """串行化同一主机上的配额统计与对象写删；锁文件不得在释放时删除。"""

        self.root.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self._quota_lock_path, flags, 0o600)
        acquired = False
        try:
            opened = os.fstat(descriptor)
            linked = os.lstat(self._quota_lock_path)
            if (
                not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(linked.st_mode)
                or opened.st_dev != linked.st_dev
                or opened.st_ino != linked.st_ino
            ):
                raise ValueError("对象存储配额锁非法")
            if opened.st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)

            self._acquire_quota_lock(
                descriptor,
                timeout_seconds=QUOTA_LOCK_TIMEOUT_SECONDS,
            )
            acquired = True
            # 等锁期间路径若被替换，继续使用旧 inode 会形成两把互不相干的锁。
            linked = os.lstat(self._quota_lock_path)
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(linked.st_mode)
                or opened.st_dev != linked.st_dev
                or opened.st_ino != linked.st_ino
            ):
                raise ValueError("对象存储配额锁已被替换")
            yield
        finally:
            if acquired:
                self._release_quota_lock(descriptor)
            os.close(descriptor)

    def _resolve(self, subdir: str, object_key: str) -> Path:
        if subdir not in {
            "originals",
            "thumbnails",
            "mail-attachments",
            "mail-raw",
            "health",
        }:
            raise ValueError("未知对象目录")
        if not object_key or Path(object_key).name != object_key:
            raise ValueError("object key 非法")
        base = (self.root / subdir).resolve()
        candidate = self.root / subdir / object_key
        # 任何目录层的链接都可能把对象读写带出 DATA_ROOT，直接拒绝。
        current = candidate.parent
        while current != self.root.parent:
            if current.exists() and current.is_symlink():
                raise ValueError("对象路径包含符号链接")
            if current == self.root:
                break
            current = current.parent
        if candidate.exists() and candidate.is_symlink():
            raise ValueError("对象文件不能是符号链接")
        target = candidate.resolve()
        if base not in target.parents or not os.path.realpath(target).startswith(
            str(base)
        ):
            raise ValueError("object key 越界")
        return target

    def _allocated_bytes(self) -> int:
        if not self.root.exists():
            return 0
        total = 0
        for path in self.root.rglob("*"):
            if path.is_symlink():
                raise ValueError("对象存储包含符号链接")
            if path == self._quota_lock_path:
                continue
            if path.is_file():
                total += path.stat().st_size
        return total

    def _ensure_quota_unlocked(self, additional_bytes: int) -> None:
        if additional_bytes < 0:
            raise ValueError("对象大小非法")
        if self._allocated_bytes() + additional_bytes > settings.storage_quota_bytes:
            raise StorageQuotaExceeded("对象存储配额不足")

    def ensure_quota(self, additional_bytes: int) -> None:
        """检查调用时刻的可用配额；真正写入仍须由 ``save`` 原子保留。"""

        with self._quota_lock():
            self._ensure_quota_unlocked(additional_bytes)

    @staticmethod
    def _remove_created_partial(target: Path, created_stat: os.stat_result) -> None:
        """仅删除仍是本次 O_EXCL 创建的普通文件，绝不触碰碰撞对象。"""
        try:
            current = os.lstat(target)
        except FileNotFoundError:
            return
        except OSError:
            return
        if (
            stat.S_ISREG(current.st_mode)
            and current.st_dev == created_stat.st_dev
            and current.st_ino == created_stat.st_ino
        ):
            try:
                target.unlink()
            except OSError:
                pass

    def save(self, data: bytes, subdir: str, suffix: str = "") -> str:
        with self._quota_lock():
            self._ensure_quota_unlocked(len(data))
            for _ in range(SAVE_KEY_ATTEMPTS):
                object_key = f"{random_id()}{suffix}"
                target = self._resolve(subdir, object_key)
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    descriptor = os.open(
                        target,
                        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                        0o600,
                    )
                except FileExistsError:
                    # 既有对象无论内容或时间戳都不接触，换一个随机键重试。
                    continue

                created_stat: os.stat_result | None = None
                try:
                    created_stat = os.fstat(descriptor)
                    with os.fdopen(descriptor, "wb") as handle:
                        descriptor = -1  # fd 的关闭责任已经移交给 file object。
                        handle.write(data)
                        handle.flush()
                        os.fsync(handle.fileno())
                except Exception:
                    if descriptor >= 0:
                        os.close(descriptor)
                    if created_stat is not None:
                        self._remove_created_partial(target, created_stat)
                    raise
                return object_key
            raise StorageKeyAllocationError("无法分配新的对象存储键")

    def open(self, subdir: str, object_key: str) -> bytes:
        return self._resolve(subdir, object_key).read_bytes()

    def delete(self, subdir: str, object_key: str) -> None:
        with self._quota_lock():
            target = self._resolve(subdir, object_key)
            if target.exists():
                target.unlink()

    def exists(self, subdir: str, object_key: str) -> bool:
        return self._resolve(subdir, object_key).exists()

    def file_size(self, subdir: str, object_key: str) -> int:
        return self._resolve(subdir, object_key).stat().st_size

    def healthcheck(self) -> bool:
        """真实验证对象存储可写且可清理；不留下健康探针对象。"""
        key = self.save(b"", "health", ".probe")
        try:
            return self.exists("health", key)
        finally:
            self.delete("health", key)


storage = StorageAdapter()
