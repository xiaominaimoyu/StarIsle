"""创建唯一的主人账号。没有注册接口。

密码只从交互输入或 --password-stdin 读取，不接受命令行参数。
runtime.env 写在数据目录里，不进 Git，也不进备份目录。
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import sqlite3
import sys
from pathlib import Path

from app.private_runtime import (
    PrivateRuntimeError,
    assert_outside_repo,
    assert_separated,
    default_backup_root,
    default_data_root,
    resolve_bind_host,
    resolve_port,
)

MIN_PASSWORD_LENGTH = 12
_ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")


class _Parser(argparse.ArgumentParser):
    """失败时只打印固定代码，避免把参数值回显到 GBK 控制台。"""

    def error(self, message: str) -> None:
        print("ARGUMENT_ERROR")
        self.exit(2)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description="Create the single StarIsle owner account.", allow_abbrev=False)
    parser.add_argument("--data-root", default=None)
    parser.add_argument("--backup-root", default=None)
    parser.add_argument("--bind-host", default=None)
    parser.add_argument("--port", default=None)
    parser.add_argument("--password-stdin", action="store_true")
    return parser


def _read_secret(from_stdin: bool) -> tuple[str, str]:
    """交互终端才询问。管道用 --password-stdin，否则只接受环境变量。"""

    if not from_stdin:
        env_password = os.environ.get("STAR_ISLE_OWNER_PASSWORD")
        if env_password is not None:
            os.environ.pop("STAR_ISLE_OWNER_PASSWORD", None)
            return env_password, env_password
        if not sys.stdin.isatty():
            raise PrivateRuntimeError("PASSWORD_TTY_REQUIRED")
        import getpass

        return getpass.getpass("Owner password: "), getpass.getpass("Repeat owner password: ")
    first = sys.stdin.readline()
    second = sys.stdin.readline()
    first = first.removesuffix("\n").removesuffix("\r")
    second = second.removesuffix("\n").removesuffix("\r")
    return first, second


def _accept_password(first: str, second: str) -> str:
    if first != second:
        raise PrivateRuntimeError("PASSWORD_MISMATCH")
    if first == "" or first.strip() == "":
        raise PrivateRuntimeError("PASSWORD_EMPTY")
    if "\x00" in first or "\r" in first or "\n" in first:
        raise PrivateRuntimeError("PASSWORD_EMPTY")
    if len(first) < MIN_PASSWORD_LENGTH:
        raise PrivateRuntimeError("PASSWORD_TOO_SHORT")
    return first


def _write_runtime_env(path: Path, values: dict[str, str]) -> None:
    if path.exists() or path.is_symlink():
        raise PrivateRuntimeError("RUNTIME_ENV_EXISTS")
    lines: list[str] = []
    for key, value in values.items():
        if not _ENV_KEY.fullmatch(key) or "\n" in value or "\r" in value or "\x00" in value:
            raise PrivateRuntimeError("RUNTIME_ENV_INVALID")
        lines.append(f"{key}={value}")
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    partial = path.with_name("runtime.env.partial")
    if partial.exists():
        partial.unlink()
    descriptor = os.open(partial, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if path.exists():
        partial.unlink(missing_ok=True)
        raise PrivateRuntimeError("RUNTIME_ENV_EXISTS")
    os.replace(partial, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _fail(code: str) -> int:
    print(code)
    return 2


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    data_value = args.data_root or (str(default_data_root()) if default_data_root() else "")
    backup_value = args.backup_root or (str(default_backup_root()) if default_backup_root() else "")
    if not data_value:
        return _fail("DATA_ROOT_REQUIRED")
    if not backup_value:
        return _fail("BACKUP_ROOT_REQUIRED")
    try:
        data_root = Path(data_value)
        backup_root = Path(backup_value)
        assert_outside_repo(data_root)
        assert_outside_repo(backup_root)
        assert_separated(data_root, backup_root)
        bind_host = resolve_bind_host(args.bind_host)
        port = resolve_port(args.port)
    except PrivateRuntimeError as exc:
        return _fail(exc.code)

    runtime_env = data_root / "runtime.env"
    if runtime_env.exists() or runtime_env.is_symlink():
        return _fail("RUNTIME_ENV_EXISTS")

    try:
        password = _accept_password(*_read_secret(args.password_stdin))
    except PrivateRuntimeError as exc:
        return _fail(exc.code)

    signing_key = secrets.token_urlsafe(48)
    os.environ["DATA_ROOT"] = str(data_root)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["STAR_ISLE_ENV"] = "development"
    os.environ["COOKIE_SECURE"] = "false"
    os.environ["AUTH_JWT_SIGNING_KEY"] = signing_key
    os.environ["AUTH_JWT_ACTIVE_KID"] = "v1"
    os.environ["AUTH_JWT_PREVIOUS_KEYS_JSON"] = "{}"

    try:
        from app.db import get_conn, init_db
        from app.security import hash_password, iso, now_utc

        password_hash = hash_password(password)
        os.environ["ADMIN_BOOTSTRAP_PASSWORD_HASH"] = password_hash
        init_db()
        with get_conn() as conn:
            if conn.execute("SELECT id FROM admin LIMIT 1").fetchone() is not None:
                return _fail("OWNER_EXISTS")
        values = {
            "STAR_ISLE_ENV": "development",
            "PYTHON_DOTENV_DISABLED": "1",
            "AUTH_JWT_SIGNING_KEY": signing_key,
            "AUTH_JWT_ACTIVE_KID": "v1",
            "AUTH_JWT_PREVIOUS_KEYS_JSON": "{}",
            "ADMIN_BOOTSTRAP_PASSWORD_HASH": password_hash,
            "COOKIE_SECURE": "false",
            "DATA_ROOT": str(data_root),
            "STAR_ISLE_BIND_HOST": bind_host,
            "STAR_ISLE_BIND_PORT": str(port),
            "STAR_ISLE_BACKUP_ROOT": str(backup_root),
            "VISITOR_PULL_BASE_URL": "https://starisle-6bt.pages.dev",
            "VISITOR_PULL_KEY": "",
        }
        _write_runtime_env(runtime_env, values)
        with get_conn(immediate=True) as conn:
            if conn.execute("SELECT id FROM admin LIMIT 1").fetchone() is not None:
                return _fail("OWNER_EXISTS")
            moment = iso(now_utc())
            conn.execute(
                "INSERT INTO admin (id, password_hash, role, created_at, updated_at) VALUES (?, ?, 'admin', ?, ?)",
                ("admin", password_hash, moment, moment),
            )
    except PrivateRuntimeError as exc:
        return _fail(exc.code)
    except (OSError, RuntimeError, ValueError, sqlite3.Error):
        print("OWNER_SETUP_FAILED")
        return 1
    print("OWNER_CREATED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
