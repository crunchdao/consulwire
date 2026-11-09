from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

import pytest

from consulwire.integrations import _common

if TYPE_CHECKING:
    from collections.abc import Iterator

CONSUL_VARIABLES = (
    "CONSUL_HTTP_ADDR",
    "CONSUL_HTTP_SSL",
    "CONSUL_HTTP_TOKEN",
    "CONSUL_HOST",
    "CONSUL_PORT",
    "CONSULWIRE_REGISTERED_BY",
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def no_consul_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in CONSUL_VARIABLES:
        # Set first so the variable is recorded: whatever the code under test sets is undone.
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)


ACCESS_LOGGERS = ("werkzeug", "uvicorn.access", "gunicorn.access")


@pytest.fixture(autouse=True)
def restore_access_log_filters() -> Iterator[None]:
    loggers = [logging.getLogger(name) for name in ACCESS_LOGGERS]
    filters = [list(access_logger.filters) for access_logger in loggers]

    yield

    for access_logger, previous in zip(loggers, filters, strict=True):
        access_logger.filters = previous


class LocalAddressLookups:
    """Agent URLs the local address was asked for, and whether each lookup ran on the loop."""

    def __init__(self) -> None:
        self.agent_urls: list[str] = []
        self.on_the_event_loop: list[bool] = []


@pytest.fixture(autouse=True)
def local_address(monkeypatch: pytest.MonkeyPatch) -> LocalAddressLookups:
    """Make the local address of the integrations 10.0.0.5, and record how it was looked up."""
    lookups = LocalAddressLookups()

    def find_local_address(agent_url: str) -> str:
        lookups.agent_urls.append(agent_url)
        lookups.on_the_event_loop.append(threading.current_thread() is threading.main_thread())

        return "10.0.0.5"

    monkeypatch.setattr(_common, "find_local_address", find_local_address)

    return lookups
