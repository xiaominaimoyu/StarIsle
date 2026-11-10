"""私人书桌的唯一启动入口。只监听 127.0.0.1。"""

from __future__ import annotations

import atexit
import os
import sys
from pathlib import Path

from app.private_runtime import PrivateRuntimeError, resolve_bind_host, resolve_port


def configure_desk_log(log_path: Path):
    """把进程输出追加到 UTF-8 日志。调用方不要再用外壳重定向 stderr。"""

    log_path.parent.mkdir(parents=True, exist_ok=True)
    # 进程退出前一直持有，不能用 with 提前关掉。
    handle = open(log_path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    sys.stdout = handle
    sys.stderr = handle
    return handle


def write_pid_file(pid_path: Path) -> None:
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    payload = f"{os.getpid()}\n".encode("ascii")
    pid_path.write_bytes(payload)

    def _remove_if_ours() -> None:
        try:
            current = pid_path.read_text(encoding="ascii").strip()
        except OSError:
            return
        if current == str(os.getpid()):
            try:
                pid_path.unlink()
            except OSError:
                return

    atexit.register(_remove_if_ours)


def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        host = resolve_bind_host(os.environ.get("STAR_ISLE_BIND_HOST"))
        port = resolve_port(os.environ.get("STAR_ISLE_BIND_PORT"))
    except PrivateRuntimeError as exc:
        print(exc.code)
        return 2
    os.environ["STAR_ISLE_BIND_HOST"] = host
    os.environ["STAR_ISLE_BIND_PORT"] = str(port)
    os.environ["STAR_ISLE_PRIVATE_DESK_LOOPBACK_ONLY"] = "1"
    log_value = os.environ.get("STAR_ISLE_DESK_LOG", "").strip()
    if log_value:
        try:
            configure_desk_log(Path(log_value))
            write_pid_file(Path(log_value).with_name("private-desk.pid"))
        except OSError:
            print("LOG_UNAVAILABLE")
            return 2
    print("PRIVATE_DESK_LISTENING", flush=True)
    import uvicorn

    from app.main import app

    uvicorn.run(app, host=host, port=port, access_log=False, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
