"""Collection-time API test isolation.

The module-level call is deliberate: pytest loads this file before importing
test modules and, therefore, before any ``app`` settings are instantiated.
"""

from __future__ import annotations

from tests._isolation import cleanup_owned_data_root, configure_pytest_environment


configure_pytest_environment()


def pytest_sessionfinish(session, exitstatus: int) -> None:  # noqa: ARG001
    if not cleanup_owned_data_root():
        session.exitstatus = 1


def pytest_unconfigure(config) -> None:  # noqa: ARG001
    cleanup_owned_data_root()
