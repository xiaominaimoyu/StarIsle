"""本机私人书桌的路径与绑定规则。

启动入口只接受 127.0.0.1。数据目录和备份目录必须是仓库外的两个
互不包含的绝对路径，避免把正在使用的数据库放进 OneDrive。
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_BIND_PORT = 8787
RESERVED_PORTS = frozenset({8000, 8001})
WINDOWS_DATA_ROOT = Path(r"D:\StarIsleData")
WINDOWS_BACKUP_ROOT = Path(r"D:\ASUS\OneDrive\StarIsleBackup")
SNAPSHOT_KEEP = 7
LOOPBACK_CLIENTS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})


class PrivateRuntimeError(Exception):
    """可打印的 ASCII 失败码。调用方不要附加路径或密钥。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def resolve_bind_host(value: str | None) -> str:
    """缺省和空白都落在回环地址；其它地址一律拒绝。"""

    if value is None or value.strip() == "":
        return DEFAULT_BIND_HOST
    if value.strip() != DEFAULT_BIND_HOST:
        raise PrivateRuntimeError("BIND_HOST_REFUSED")
    return DEFAULT_BIND_HOST


def resolve_port(value: object | None) -> int:
    """8787 是书桌端口。8000/8001 留给本机 llama.cpp。"""

    if value is None or (isinstance(value, str) and value.strip() == ""):
        return DEFAULT_BIND_PORT
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise PrivateRuntimeError("PORT_INVALID") from exc
    if port < 1 or port > 65535:
        raise PrivateRuntimeError("PORT_INVALID")
    if port in RESERVED_PORTS:
        raise PrivateRuntimeError("PORT_RESERVED")
    return port


def default_data_root() -> Path | None:
    if os.name == "nt":
        return WINDOWS_DATA_ROOT
    return None


def default_backup_root() -> Path | None:
    if os.name == "nt":
        return WINDOWS_BACKUP_ROOT
    return None


def client_is_loopback(host: str | None) -> bool:
    return host in LOOPBACK_CLIENTS


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise PrivateRuntimeError("PATH_UNSAFE") from exc
    if stat.S_ISLNK(metadata.st_mode):
        return True
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(getattr(metadata, "st_file_attributes", 0) & reparse)


def assert_absolute_safe(path: Path) -> Path:
    """拒绝相对路径、磁盘根目录，以及路径上已经存在的链接。"""

    if not path.is_absolute():
        raise PrivateRuntimeError("PATH_NOT_ABSOLUTE")
    current = path
    while True:
        if _is_link_or_reparse(current):
            raise PrivateRuntimeError("PATH_UNSAFE")
        parent = current.parent
        if parent == current:
            break
        current = parent
    resolved = path.resolve()
    if resolved.parent == resolved:
        raise PrivateRuntimeError("PATH_UNSAFE")
    return resolved


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def assert_separated(data_root: Path, backup_root: Path) -> None:
    data = assert_absolute_safe(data_root)
    backup = assert_absolute_safe(backup_root)
    if data == backup or _is_within(data, backup) or _is_within(backup, data):
        raise PrivateRuntimeError("ROOTS_OVERLAP")


def assert_outside_repo(path: Path, repo: Path | None = None) -> None:
    resolved = assert_absolute_safe(path)
    root = (repo or repository_root()).resolve()
    if resolved == root or _is_within(resolved, root):
        raise PrivateRuntimeError("ROOT_INSIDE_REPO")
