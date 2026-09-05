"""Pytest-only environment isolation for the StarIsle API test harness.

This module intentionally imports only the standard library.  It is loaded by
``conftest.py`` before pytest imports application modules, so production
configuration and real data roots cannot leak into a test collection.
"""

from __future__ import annotations

import atexit
import os
import stat
import sys
import tempfile
from pathlib import Path


TEST_PASSWORD = "test-password-12345"
TEST_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$HVWAthrotBYyUyMdgYIK9Q$"
    "BHM39lGOmFZkENTS3fKFvfw6exwBvM48JSQyTTmyVyU"
)
TEST_ENVIRONMENT = {
    "AUTH_JWT_SIGNING_KEY": "test-only-secret-key-0123456789abcdef",
    "AUTH_JWT_ACTIVE_KID": "test",
    "MAIL_HMAC_KEY": "test-mail-hmac-key",
    "COOKIE_SECURE": "false",
    "LLAMA_CHAT_BASE_URL": "",
    "LLAMA_EMBED_BASE_URL": "",
    "EMBEDDING_MODEL_ID": "",
    "PYTHON_DOTENV_DISABLED": "1",
    "ADMIN_BOOTSTRAP_PASSWORD_HASH": TEST_PASSWORD_HASH,
}


class TestIsolationError(RuntimeError):
    """Raised before application import when a test root is unsafe."""


_owned_data_root: tempfile.TemporaryDirectory[str] | None = None
_configured = False
_CLEANUP_FAILURE_CODE = "PYTEST_OWNED_TEMP_CLEANUP_FAILED"


def _absolute(path: Path) -> Path:
    return path if path.is_absolute() else Path.cwd() / path


def _is_link_or_reparse(path: Path) -> bool:
    """Return true for symlink/reparse components without following them."""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise TestIsolationError("test data root cannot be inspected safely") from exc
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(reparse_point and attributes & reparse_point)


def _has_link_or_reparse_component(path: Path) -> bool:
    current = _absolute(path)
    while True:
        if _is_link_or_reparse(current):
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def _is_within(path: Path, parent: Path) -> bool:
    try:
        common = os.path.commonpath((os.path.normcase(str(path)), os.path.normcase(str(parent))))
    except ValueError:
        return False
    return os.path.normcase(common) == os.path.normcase(str(parent))


def _validated_system_temp_root() -> Path:
    """Return the configured temp directory only if its path has no link escape."""

    supplied = _absolute(Path(tempfile.gettempdir()))
    if _has_link_or_reparse_component(supplied):
        raise TestIsolationError("system temporary directory must not contain a symlink or reparse point")
    try:
        resolved = supplied.resolve(strict=False)
    except OSError as exc:
        raise TestIsolationError("system temporary directory cannot be resolved safely") from exc
    if _has_link_or_reparse_component(resolved):
        raise TestIsolationError("system temporary directory must not contain a symlink or reparse point")
    return resolved


def validate_external_temp_root(value: str | Path) -> Path:
    """Accept only a non-reparse path contained by the active system temp root."""

    supplied = _absolute(Path(value).expanduser())
    if _has_link_or_reparse_component(supplied):
        raise TestIsolationError("DATA_ROOT must not contain a symlink or reparse point")
    try:
        resolved = supplied.resolve(strict=False)
        system_temp = _validated_system_temp_root()
    except OSError as exc:
        raise TestIsolationError("DATA_ROOT cannot be resolved safely") from exc
    if (
        _has_link_or_reparse_component(resolved)
        or resolved == system_temp
        or not _is_within(resolved, system_temp)
    ):
        raise TestIsolationError("DATA_ROOT must be a strict child of the system temporary directory")
    return resolved


def owned_temporary_directory(prefix: str = "starisle-pytest-") -> tempfile.TemporaryDirectory[str]:
    """Create a caller-owned temporary directory under the current system temp root."""

    system_temp = _validated_system_temp_root()
    owned = tempfile.TemporaryDirectory(prefix=prefix, dir=str(system_temp))
    owned_path = Path(owned.name)
    if _has_link_or_reparse_component(owned_path) or owned_path == system_temp or not _is_within(owned_path, system_temp):
        try:
            owned.cleanup()
        except Exception:
            print(_CLEANUP_FAILURE_CODE, file=sys.stderr)
            raise TestIsolationError("owned pytest temporary directory cleanup failed") from None
        raise TestIsolationError("owned pytest temporary directory is unsafe")
    return owned


def owned_data_root() -> Path | None:
    """Return the harness-owned root, never an explicitly supplied external root."""

    return Path(_owned_data_root.name) if _owned_data_root is not None else None


def cleanup_owned_data_root() -> bool:
    """Clean only our TemporaryDirectory and retain ownership if cleanup fails."""

    global _owned_data_root
    owned = _owned_data_root
    if owned is None:
        return True
    try:
        owned.cleanup()
    except Exception:
        print(_CLEANUP_FAILURE_CODE, file=sys.stderr)
        return False
    _owned_data_root = None
    return True


def configure_pytest_environment() -> Path:
    """Install deterministic test-only settings before any application import."""

    global _configured, _owned_data_root
    if _configured:
        current = os.environ.get("DATA_ROOT")
        if not current:
            raise TestIsolationError("pytest DATA_ROOT unexpectedly disappeared")
        return validate_external_temp_root(current)

    raw_data_root = os.environ.get("DATA_ROOT")
    if raw_data_root:
        data_root = validate_external_temp_root(raw_data_root)
    else:
        _owned_data_root = owned_temporary_directory()
        data_root = Path(_owned_data_root.name)

    raw_data_host_root = os.environ.get("DATA_HOST_ROOT")
    data_host_root = validate_external_temp_root(raw_data_host_root) if raw_data_host_root else data_root

    os.environ["DATA_ROOT"] = str(data_root)
    os.environ["DATA_HOST_ROOT"] = str(data_host_root)
    os.environ.update(TEST_ENVIRONMENT)
    _configured = True
    return data_root


atexit.register(cleanup_owned_data_root)
