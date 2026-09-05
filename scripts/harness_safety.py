"""Small standard-library safety helpers for local verification harnesses.

The helpers deliberately retain only HTTP method/pathname labels and enforce
exclusive, non-reparse output targets. They do not inspect file contents.
"""

from __future__ import annotations

import base64
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class HarnessSafetyError(RuntimeError):
    """Stable, caller-safe error for rejected harness paths."""


class OwnedProcessError(RuntimeError):
    """Stable error raised when a harness cannot own a process safely."""


_METHOD = re.compile(r"^[A-Z]+$")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_EXACT_SAFE_PATHS = frozenset(
    {
        "/",
        "/login",
        "/console",
        "/console/files",
        "/console/gallery",
        "/console/kb",
        "/console/mail",
        "/projects",
        "/projects/",
        "/resume",
        "/resume/",
        "/404.html",
        "/favicon.svg",
        "/api/v1/auth/csrf",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/auth/me",
        "/api/v1/auth/refresh",
        "/api/v1/files",
        "/api/v1/photos",
        "/api/v1/knowledge/documents",
        "/api/v1/rag/query",
        "/api/v1/health/live",
        "/api/v1/health/ready",
        "/api/v1/mail",
    }
)


def safe_pathname(url: str) -> str:
    """Return a fixed route template, never caller-controlled path segments.

    Harness summaries are diagnostic output.  Keeping an origin-less pathname
    is insufficient because object keys, identifiers, and filenames often live
    in path segments.  This function therefore only returns fixed route names
    or fixed parameter templates; every unknown path collapses to ``/other``.
    """

    try:
        path = urlsplit(str(url)).path
    except (TypeError, ValueError):
        return "/other"
    if not path.startswith("/"):
        return "/other"
    if path in _EXACT_SAFE_PATHS:
        return path
    if path.startswith("/_astro/"):
        return "/_astro/:asset"
    if path.startswith("/src/"):
        return "/src/:asset"
    if path.startswith("/assets/"):
        return "/assets/:asset"
    if path.startswith("/presets/"):
        return "/presets/:asset"

    segments = [segment for segment in path.split("/") if segment]
    if segments[:2] == ["api", "v1"]:
        if len(segments) >= 3:
            resource = segments[2]
            if resource == "mail":
                if len(segments) >= 5 and segments[4] == "read":
                    return "/api/v1/mail/:id/read"
                return "/api/v1/mail"
            if resource in {"files", "photos", "knowledge", "rag", "auth", "health"}:
                return f"/api/v1/{resource}"
        return "/other"
    if path.startswith("/console/"):
        return "/console"
    return "/other"


def safe_request_label(method: str, url: str) -> str:
    """Return a fixed-format method/pathname label suitable for normal output."""

    normalized_method = str(method).upper()
    if not _METHOD.fullmatch(normalized_method):
        normalized_method = "OTHER"
    return f"{normalized_method} {safe_pathname(url)}"


def is_loopback_origin(value: str) -> bool:
    """Accept only an HTTP(S) origin on an explicit loopback host."""

    try:
        parsed = urlsplit(str(value))
        # Accessing ``port`` performs the parser's range/format validation.
        # A malformed port must not be treated as a harmless loopback origin.
        _ = parsed.port
    except (TypeError, ValueError):
        return False
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.hostname in _LOOPBACK_HOSTS
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
        and parsed.username is None
        and parsed.password is None
    )


def is_loopback_http_url(value: str) -> bool:
    """Accept an HTTP(S) URL on loopback, including an endpoint path."""

    try:
        parsed = urlsplit(str(value))
        _ = parsed.port
    except (TypeError, ValueError):
        return False
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.hostname in _LOOPBACK_HOSTS
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


def is_http_origin(value: str) -> bool:
    """Accept a strict HTTP(S) origin without credentials or URL components."""

    try:
        parsed = urlsplit(str(value))
        _ = parsed.port
    except (TypeError, ValueError):
        return False
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.hostname
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
        and parsed.username is None
        and parsed.password is None
    )


def _absolute(path: Path) -> Path:
    return path if path.is_absolute() else Path.cwd() / path


def is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise HarnessSafetyError("harness path cannot be inspected safely") from exc
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(reparse_point and attributes & reparse_point)


def has_link_or_reparse_component(path: Path) -> bool:
    current = _absolute(path)
    while True:
        if is_link_or_reparse(current):
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def is_within(path: Path, parent: Path) -> bool:
    try:
        common = os.path.commonpath((os.path.normcase(str(path)), os.path.normcase(str(parent))))
    except ValueError:
        return False
    return os.path.normcase(common) == os.path.normcase(str(parent))


def _resolved_without_links(path: Path) -> Path:
    supplied = _absolute(path.expanduser())
    if has_link_or_reparse_component(supplied):
        raise HarnessSafetyError("harness path must not contain a symlink or reparse point")
    try:
        return supplied.resolve(strict=False)
    except OSError as exc:
        raise HarnessSafetyError("harness path cannot be resolved safely") from exc


def validated_system_temp_root() -> Path:
    """Return the configured system temp root only when it has no link escape."""

    supplied = Path(tempfile.gettempdir())
    if has_link_or_reparse_component(supplied):
        raise HarnessSafetyError("system temporary directory must not contain a symlink or reparse point")
    return _resolved_without_links(supplied)


def owned_temporary_directory(prefix: str) -> tempfile.TemporaryDirectory[str]:
    """Create a harness-owned child of the validated system temporary root."""

    return tempfile.TemporaryDirectory(prefix=prefix, dir=str(validated_system_temp_root()))


def new_temporary_directory_path(
    value: str | Path, *, protected_roots: tuple[Path, ...] = ()
) -> Path:
    """Validate a new evidence directory under system temp and outside protected roots."""

    supplied = Path(value)
    if supplied.exists() or supplied.is_symlink():
        raise HarnessSafetyError("evidence directory already exists")
    if not supplied.parent.is_dir():
        raise HarnessSafetyError("evidence parent directory must already exist")
    resolved = _resolved_without_links(supplied)
    temp_root = validated_system_temp_root()
    if not is_within(resolved, temp_root):
        raise HarnessSafetyError("evidence directory must be inside the system temporary directory")
    for protected_root in protected_roots:
        try:
            resolved_protected = _resolved_without_links(protected_root)
        except HarnessSafetyError:
            raise HarnessSafetyError("evidence protected root is unsafe") from None
        if is_within(resolved, resolved_protected):
            raise HarnessSafetyError("evidence directory must not be inside a protected root")
    return resolved


def new_temporary_file_path(
    value: str | Path, *, protected_roots: tuple[Path, ...] = ()
) -> Path:
    """Validate a not-yet-created private exchange file under system temp."""

    supplied = Path(value)
    if supplied.exists() or supplied.is_symlink():
        raise HarnessSafetyError("temporary exchange file already exists")
    if not supplied.parent.is_dir():
        raise HarnessSafetyError("temporary exchange parent must already exist")
    resolved = _resolved_without_links(supplied)
    temp_root = validated_system_temp_root()
    if not is_within(resolved, temp_root):
        raise HarnessSafetyError("temporary exchange file must be inside the system temporary directory")
    for protected_root in protected_roots:
        try:
            resolved_protected = _resolved_without_links(protected_root)
        except HarnessSafetyError:
            raise HarnessSafetyError("temporary exchange protected root is unsafe") from None
        if is_within(resolved, resolved_protected):
            raise HarnessSafetyError("temporary exchange file must not be inside a protected root")
    return resolved


def new_output_file_path(
    value: str | Path,
    *,
    repository_root: Path,
    data_roots: tuple[Path, ...] = (),
) -> Path:
    """Validate a new report path without creating parents or overwriting files."""

    supplied = Path(value)
    if supplied.exists() or supplied.is_symlink():
        raise HarnessSafetyError("output already exists")
    if not supplied.parent.is_dir():
        raise HarnessSafetyError("output parent directory must already exist")
    resolved = _resolved_without_links(supplied)
    repo_root = _resolved_without_links(repository_root)
    protected_roots = [repo_root / "data"]
    protected_roots.extend(data_roots)
    for protected in protected_roots:
        try:
            resolved_protected = _resolved_without_links(protected)
        except HarnessSafetyError:
            # A malformed optional DATA_ROOT is not a safe place to write either.
            raise HarnessSafetyError("output data root is unsafe") from None
        if is_within(resolved, resolved_protected):
            raise HarnessSafetyError("output must not be inside a data root")
    return resolved


class OwnedProcess:
    """A subprocess and the private OS ownership boundary created for it.

    The wrapper intentionally exposes the ``Popen`` attributes used by local
    harnesses, but callers must stop it through :func:`stop_owned_process` so
    the complete process tree is handled rather than only its leader.
    """

    def __init__(self, process: subprocess.Popen[Any], *, process_group: int | None = None, job: Any = None) -> None:
        self.process = process
        self.process_group = process_group
        self.job = job
        self.stopped = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self.process, name)


if os.name == "nt":  # pragma: no cover - exercised on Windows hosts.
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _JobBasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JobExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JobBasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _JobBasicAccountingInformation(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_int64),
            ("TotalKernelTime", ctypes.c_int64),
            ("ThisPeriodTotalUserTime", ctypes.c_int64),
            ("ThisPeriodTotalKernelTime", ctypes.c_int64),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, wintypes.INT, wintypes.LPVOID, wintypes.DWORD)
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.QueryInformationJobObject.argtypes = (
        wintypes.HANDLE,
        wintypes.INT,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
    )
    _kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.PeekNamedPipe.argtypes = (
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    )
    _kernel32.PeekNamedPipe.restype = wintypes.BOOL

    class _WindowsJob:
        """Private kill-on-close job object for exactly one owned process tree."""

        def __init__(self, handle: int) -> None:
            self.handle = handle

        @classmethod
        def create(cls) -> "_WindowsJob":
            handle = _kernel32.CreateJobObjectW(None, None)
            if not handle:
                raise OwnedProcessError("owned process setup failed")
            job = cls(handle)
            info = _JobExtendedLimitInformation()
            info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not _kernel32.SetInformationJobObject(
                handle,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info),
                ctypes.sizeof(info),
            ):
                job.close()
                raise OwnedProcessError("owned process setup failed")
            return job

        def assign(self, process: subprocess.Popen[Any]) -> None:
            handle = getattr(process, "_handle", None)
            if not handle or not _kernel32.AssignProcessToJobObject(self.handle, handle):
                raise OwnedProcessError("owned process setup failed")

        def active_processes(self) -> int | None:
            info = _JobBasicAccountingInformation()
            if not _kernel32.QueryInformationJobObject(
                self.handle,
                _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
                ctypes.byref(info),
                ctypes.sizeof(info),
                None,
            ):
                return None
            return int(info.ActiveProcesses)

        def terminate_tree(self, timeout_seconds: float) -> bool:
            if not _kernel32.TerminateJobObject(self.handle, 1):
                return False
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                active = self.active_processes()
                if active == 0:
                    return True
                if active is None:
                    return False
                time.sleep(0.05)
            return self.active_processes() == 0

        def close(self) -> bool:
            if not self.handle:
                return True
            handle, self.handle = self.handle, 0
            return bool(_kernel32.CloseHandle(handle))


_BOOTSTRAP_COMMAND_ENV = "STAR_ISLE_OWNED_PROCESS_COMMAND"
_BOOTSTRAP_GATE_HANDLE_ENV = "STAR_ISLE_OWNED_PROCESS_GATE_HANDLE"
_BOOTSTRAP_READY_HANDLE_ENV = "STAR_ISLE_OWNED_PROCESS_READY_HANDLE"
_BOOTSTRAP_ENV_NAMES = frozenset(
    {_BOOTSTRAP_COMMAND_ENV, _BOOTSTRAP_GATE_HANDLE_ENV, _BOOTSTRAP_READY_HANDLE_ENV}
)
_BOOTSTRAP_ENV_NAME_CASEFOLDS = frozenset(name.casefold() for name in _BOOTSTRAP_ENV_NAMES)
_BOOTSTRAP_READY_TIMEOUT_SECONDS = 5


def _close_fd(fd: int | None) -> bool:
    if fd is None:
        return True
    try:
        os.close(fd)
    except OSError:
        return False
    return True


def _bootstrap_target_command(
    popen_args: tuple[Any, ...], popen_kwargs: dict[str, Any]
) -> tuple[str | list[str], dict[str, Any]]:
    """Return a JSON-safe target command and bootstrap-only Popen kwargs.

    Repository callers use argument sequences, cwd/env, and standard streams.
    Options whose target semantics cannot be preserved through a separate
    bootstrap are rejected instead of being silently changed.
    """

    bootstrap_kwargs = dict(popen_kwargs)
    if popen_args:
        if len(popen_args) != 1 or "args" in bootstrap_kwargs:
            raise OwnedProcessError("owned process setup failed")
        raw_command = popen_args[0]
    else:
        try:
            raw_command = bootstrap_kwargs.pop("args")
        except KeyError:
            raise OwnedProcessError("owned process setup failed") from None

    unsupported = {
        "close_fds",
        "creationflags",
        "pass_fds",
        "preexec_fn",
        "process_group",
        "start_new_session",
        "startupinfo",
    }
    if unsupported.intersection(bootstrap_kwargs):
        raise OwnedProcessError("owned process setup failed")
    if bootstrap_kwargs.get("shell") or bootstrap_kwargs.get("executable") is not None:
        raise OwnedProcessError("owned process setup failed")
    bootstrap_kwargs.pop("shell", None)
    bootstrap_kwargs.pop("executable", None)

    if isinstance(raw_command, (str, os.PathLike)):
        command_value = os.fspath(raw_command)
        if not isinstance(command_value, str) or "\0" in command_value:
            raise OwnedProcessError("owned process setup failed")
        return command_value, bootstrap_kwargs
    if not isinstance(raw_command, (list, tuple)) or not raw_command:
        raise OwnedProcessError("owned process setup failed")
    command: list[str] = []
    for item in raw_command:
        if not isinstance(item, (str, os.PathLike)):
            raise OwnedProcessError("owned process setup failed")
        value = os.fspath(item)
        if not isinstance(value, str) or "\0" in value:
            raise OwnedProcessError("owned process setup failed")
        command.append(value)
    return command, bootstrap_kwargs


def _bootstrap_environment(
    supplied_environment: Any, command: str | list[str], gate_handle: int, ready_handle: int
) -> dict[str, str]:
    if supplied_environment is None:
        environment = dict(os.environ)
    elif isinstance(supplied_environment, dict):
        environment = dict(supplied_environment)
    else:
        raise OwnedProcessError("owned process setup failed")
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in environment.items()):
        raise OwnedProcessError("owned process setup failed")
    if any(key.casefold() in _BOOTSTRAP_ENV_NAME_CASEFOLDS for key in environment):
        raise OwnedProcessError("owned process setup failed")
    try:
        encoded_command = base64.b64encode(
            json.dumps(command, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise OwnedProcessError("owned process setup failed") from None
    environment.update(
        {
            _BOOTSTRAP_COMMAND_ENV: encoded_command,
            _BOOTSTRAP_GATE_HANDLE_ENV: str(gate_handle),
            _BOOTSTRAP_READY_HANDLE_ENV: str(ready_handle),
        }
    )
    return environment


def _validated_bootstrap_helper_path(value: Path) -> Path:
    """Accept only a regular bootstrap file on a non-reparse path chain."""

    candidate = _absolute(value)
    try:
        if has_link_or_reparse_component(candidate):
            raise OwnedProcessError("owned process setup failed")
        metadata = candidate.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise OwnedProcessError("owned process setup failed")
        resolved = candidate.resolve(strict=True)
    except OwnedProcessError:
        raise
    except OSError:
        raise OwnedProcessError("owned process setup failed") from None
    if has_link_or_reparse_component(resolved):
        raise OwnedProcessError("owned process setup failed")
    return resolved


def _wait_for_windows_bootstrap_ready(process: subprocess.Popen[Any], ready_fd: int) -> bytes | None:
    """Read a bounded fixed bootstrap readiness byte without blocking forever."""

    if os.name != "nt":  # pragma: no cover - Windows-only helper.
        return None
    try:
        ready_handle = msvcrt.get_osfhandle(ready_fd)
    except OSError:
        return None
    deadline = time.monotonic() + _BOOTSTRAP_READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        available = wintypes.DWORD()
        if not _kernel32.PeekNamedPipe(
            ready_handle, None, 0, None, ctypes.byref(available), None
        ):
            return None
        if available.value:
            try:
                return os.read(ready_fd, 1)
            except OSError:
                return None
        if process.poll() is not None:
            return None
        time.sleep(0.01)
    return None


def _abort_windows_bootstrap(
    job: Any | None,
    process: subprocess.Popen[Any] | None,
    *fds: int | None,
) -> bool:
    """Close only this setup's fds and recover its gate-blocked bootstrap."""

    complete = True
    for fd in fds:
        complete = _close_fd(fd) and complete
    if process is not None:
        if job is not None:
            try:
                complete = job.terminate_tree(5) and complete
            except Exception:
                complete = False
        complete = _reap_exact_process(process, 5) and complete
    if job is not None:
        try:
            complete = job.close() and complete
        except Exception:
            complete = False
    return complete


def _close_popen_pipes(process: subprocess.Popen[Any]) -> bool:
    """Close only pipes held by this owned process, returning every failure."""

    closed = True
    for pipe in (process.stdin, process.stdout, process.stderr):
        if pipe is not None and not pipe.closed:
            try:
                pipe.close()
            except OSError:
                closed = False
    return closed


def _reap_exact_process(process: subprocess.Popen[Any], timeout_seconds: float) -> bool:
    """Best-effort recovery for the just-created leader when ownership setup fails."""

    try:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=timeout_seconds)
        return process.poll() is not None
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        _close_popen_pipes(process)


def spawn_owned_process(*popen_args: Any, **popen_kwargs: Any) -> OwnedProcess:
    """Start a process with a private, complete-tree ownership boundary.

    POSIX starts a new session/process group.  Windows starts only an internal
    gate-blocked bootstrap, assigns that bootstrap to a fresh Job Object with
    ``KILL_ON_JOB_CLOSE``, then releases the real target.  Target code cannot
    run before Job ownership exists.
    """

    if os.name == "nt":  # pragma: no cover - covered by Windows regression runs.
        gate_read_fd: int | None = None
        gate_write_fd: int | None = None
        ready_read_fd: int | None = None
        ready_write_fd: int | None = None
        job: _WindowsJob | None = None
        process: subprocess.Popen[Any] | None = None
        try:
            command, bootstrap_kwargs = _bootstrap_target_command(popen_args, popen_kwargs)
            helper = _validated_bootstrap_helper_path(Path(__file__).with_name("owned_process_bootstrap.py"))
            gate_read_fd, gate_write_fd = os.pipe()
            ready_read_fd, ready_write_fd = os.pipe()
            os.set_inheritable(gate_read_fd, True)
            os.set_inheritable(gate_write_fd, False)
            os.set_inheritable(ready_read_fd, False)
            os.set_inheritable(ready_write_fd, True)
            gate_handle = msvcrt.get_osfhandle(gate_read_fd)
            ready_handle = msvcrt.get_osfhandle(ready_write_fd)
            bootstrap_kwargs["env"] = _bootstrap_environment(
                bootstrap_kwargs.get("env"), command, gate_handle, ready_handle
            )
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.lpAttributeList = {"handle_list": [gate_handle, ready_handle]}
            bootstrap_kwargs["startupinfo"] = startupinfo
            bootstrap_kwargs["close_fds"] = True
            job = _WindowsJob.create()
            process = subprocess.Popen([sys.executable, "-I", "-S", "-B", str(helper)], **bootstrap_kwargs)
            if not _close_fd(gate_read_fd):
                raise OwnedProcessError("owned process setup failed")
            gate_read_fd = None
            if not _close_fd(ready_write_fd):
                raise OwnedProcessError("owned process setup failed")
            ready_write_fd = None
            job.assign(process)
            if _wait_for_windows_bootstrap_ready(process, ready_read_fd) != b"R":
                raise OwnedProcessError("owned process setup failed")
            if not _close_fd(ready_read_fd):
                raise OwnedProcessError("owned process setup failed")
            ready_read_fd = None
            if os.write(gate_write_fd, b"G") != 1:
                raise OwnedProcessError("owned process setup failed")
            if not _close_fd(gate_write_fd):
                raise OwnedProcessError("owned process setup failed")
            gate_write_fd = None
            return OwnedProcess(process, job=job)
        except Exception:
            _abort_windows_bootstrap(
                job,
                process,
                gate_read_fd,
                gate_write_fd,
                ready_read_fd,
                ready_write_fd,
            )
            raise OwnedProcessError("owned process setup failed") from None

    requested_session = popen_kwargs.get("start_new_session")
    if requested_session is False:
        raise OwnedProcessError("owned process setup failed")
    popen_kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(*popen_args, **popen_kwargs)
    except (OSError, subprocess.SubprocessError):
        raise OwnedProcessError("owned process setup failed") from None
    return OwnedProcess(process, process_group=process.pid)


def _posix_group_alive(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True


def _stop_posix_process_group(process_group: int, timeout_seconds: float) -> bool:
    """Terminate the exact owned process group even after the leader exits."""

    if not _posix_group_alive(process_group):
        return True
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _posix_group_alive(process_group):
            return True
        time.sleep(0.05)
    try:
        os.killpg(process_group, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _posix_group_alive(process_group):
            return True
        time.sleep(0.05)
    return not _posix_group_alive(process_group)


def stop_owned_process(owned: OwnedProcess | None, *, timeout_seconds: float = 10) -> bool:
    """Stop/reap the owned complete process tree and close all owned pipes.

    A completed leader is not treated as a completed tree: POSIX still checks
    its original process group and Windows still terminates and verifies its
    private Job Object.  Any tree, handle, wait, or pipe-close failure returns
    ``False`` so callers must surface a non-zero harness result.
    """

    if owned is None:
        return True
    if not isinstance(owned, OwnedProcess):
        return False
    if owned.stopped:
        return True

    tree_stopped = False
    leader_stopped = False
    handles_closed = True
    try:
        if os.name == "nt":  # pragma: no cover - covered by Windows regression runs.
            if owned.job is None:
                tree_stopped = False
            else:
                job = owned.job
                try:
                    tree_stopped = job.terminate_tree(timeout_seconds)
                except Exception:
                    tree_stopped = False
                finally:
                    try:
                        handles_closed = job.close()
                    except Exception:
                        handles_closed = False
                    owned.job = None
        elif owned.process_group is not None:
            tree_stopped = _stop_posix_process_group(owned.process_group, timeout_seconds)
        else:
            tree_stopped = False

        try:
            owned.process.wait(timeout=timeout_seconds)
            leader_stopped = owned.process.poll() is not None
        except (OSError, subprocess.SubprocessError):
            leader_stopped = False
    finally:
        pipes_closed = _close_popen_pipes(owned.process)

    owned.stopped = tree_stopped and leader_stopped and handles_closed and pipes_closed
    return owned.stopped


def wait_for_owned_process(
    owned: OwnedProcess, *, timeout_seconds: float = 240
) -> tuple[int, bool]:
    """Wait a bounded interval, then unconditionally stop the complete tree."""

    result = 1
    try:
        result = owned.process.wait(timeout=timeout_seconds)
    except (OSError, subprocess.SubprocessError):
        result = 1
    stopped = stop_owned_process(owned)
    return result, stopped
