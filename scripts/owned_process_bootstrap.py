"""Internal Windows-only gate for :mod:`harness_safety` owned processes.

The helper deliberately emits no diagnostics.  It blocks before launching the
target until its parent has placed this bootstrap in a private Job Object.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess


_COMMAND_ENV = "STAR_ISLE_OWNED_PROCESS_COMMAND"
_GATE_HANDLE_ENV = "STAR_ISLE_OWNED_PROCESS_GATE_HANDLE"
_READY_HANDLE_ENV = "STAR_ISLE_OWNED_PROCESS_READY_HANDLE"


def _close(fd: int | None) -> None:
    if fd is None:
        return
    try:
        os.close(fd)
    except OSError:
        pass


def _status(fd: int | None, value: bytes) -> bool:
    if fd is None:
        return False
    try:
        return os.write(fd, value) == len(value)
    except OSError:
        return False


def _read_bootstrap_values() -> tuple[str | list[str], int, int]:
    encoded = os.environ.pop(_COMMAND_ENV, None)
    gate_handle = os.environ.pop(_GATE_HANDLE_ENV, None)
    ready_handle = os.environ.pop(_READY_HANDLE_ENV, None)
    if not isinstance(encoded, str) or not isinstance(gate_handle, str) or not isinstance(ready_handle, str):
        raise ValueError
    decoded = base64.b64decode(encoded.encode("ascii"), validate=True)
    command = json.loads(decoded.decode("utf-8"))
    if isinstance(command, str):
        if not command or "\0" in command:
            raise ValueError
    elif isinstance(command, list):
        if not command or any(not isinstance(item, str) or "\0" in item for item in command):
            raise ValueError
    else:
        raise ValueError
    gate = int(gate_handle)
    ready = int(ready_handle)
    if gate <= 0 or ready <= 0:
        raise ValueError
    return command, gate, ready


def main() -> int:
    gate_fd: int | None = None
    ready_fd: int | None = None
    try:
        if os.name != "nt":
            return 1
        import msvcrt

        command, gate_handle, ready_handle = _read_bootstrap_values()
        gate_fd = msvcrt.open_osfhandle(gate_handle, os.O_RDONLY)
        ready_fd = msvcrt.open_osfhandle(ready_handle, os.O_WRONLY)
        if not _status(ready_fd, b"R"):
            return 1
        _close(ready_fd)
        ready_fd = None
        if os.read(gate_fd, 1) != b"G":
            return 1
        _close(gate_fd)
        gate_fd = None
        child = subprocess.Popen(command)
        return int(child.wait())
    except Exception:
        _status(ready_fd, b"F")
        return 1
    finally:
        _close(gate_fd)
        _close(ready_fd)


if __name__ == "__main__":
    raise SystemExit(main())
