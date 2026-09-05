"""StorageAdapter:不透明对象 ID 存储;每次读写验证目标仍在 DATA_ROOT 内,拒绝符号链接越界。"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from .config import settings
from .security import random_id


class StorageQuotaExceeded(RuntimeError):
    """写入会超出已配置对象存储配额。"""


class StorageKeyAllocationError(RuntimeError):
    """随机对象键连续冲突后无法分配新的独占文件。"""


SAVE_KEY_ATTEMPTS = 8


class StorageAdapter:
    """原件 / 缩略图 / 邮件附件与私有原文分离；对象 ID 始终随机且不可预测。"""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or settings.objects_root).resolve()
        settings.ensure_dirs()

    def _resolve(self, subdir: str, object_key: str) -> Path:
        if subdir not in {"originals", "thumbnails", "mail-attachments", "mail-raw", "health"}:
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
        if base not in target.parents or not os.path.realpath(target).startswith(str(base)):
            raise ValueError("object key 越界")
        return target

    def _allocated_bytes(self) -> int:
        if not self.root.exists():
            return 0
        total = 0
        for path in self.root.rglob("*"):
            if path.is_symlink():
                raise ValueError("对象存储包含符号链接")
            if path.is_file():
                total += path.stat().st_size
        return total

    def ensure_quota(self, additional_bytes: int) -> None:
        if additional_bytes < 0:
            raise ValueError("对象大小非法")
        if self._allocated_bytes() + additional_bytes > settings.storage_quota_bytes:
            raise StorageQuotaExceeded("对象存储配额不足")

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
        self.ensure_quota(len(data))
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
