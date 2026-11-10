"""私人书桌的唯一启动入口。只监听 127.0.0.1。"""

from __future__ import annotations

import os

from app.private_runtime import PrivateRuntimeError, resolve_bind_host, resolve_port


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
    import uvicorn

    from app.main import app

    uvicorn.run(app, host=host, port=port, access_log=False, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
