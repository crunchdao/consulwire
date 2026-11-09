"""What every integration shares: the health check, the address, and the quiet access log."""

from __future__ import annotations

import ipaddress
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Final

from typing_extensions import override

from consulwire._network import find_local_address

if TYPE_CHECKING:
    from collections.abc import Iterable

    from consulwire.agent import HTTPCheck

DEFAULT_HEALTH_PATH: Final = "/health"

DEFAULT_CHECK_INTERVAL: Final = timedelta(seconds=10)

DEFAULT_DEREGISTER_CRITICAL_AFTER: Final = timedelta(minutes=1)


def resolve_address(given: str | None, listening_host: str | None, agent_url: str) -> str:
    """Return the address other services connect to.

    The given one first, then the listening host when it names this machine for others, then the
    local address towards the agent.
    """
    return given or specific_address(listening_host) or find_local_address(agent_url)


def specific_address(host: str | None) -> str | None:
    """Return the listening host when other machines can reach it at that address.

    Wildcards (``0.0.0.0``, ``::``) and loopbacks are not: the address is found automatically then.
    """
    if not host or host == "localhost":
        return None

    try:
        ip_address = ipaddress.ip_address(host)
    except ValueError:
        return host

    if ip_address.is_unspecified or ip_address.is_loopback:
        return None

    return host


def health_check(
    address: str,
    port: int,
    health_path: str,
    *,
    interval: timedelta,
    deregister_critical_after: timedelta | None,
) -> HTTPCheck:
    host = f"[{address}]" if ":" in address else address
    check: HTTPCheck = {"http": f"http://{host}:{port}{health_path}", "interval": interval}

    if deregister_critical_after is not None:
        check["deregister_critical_after"] = deregister_critical_after

    return check


class HealthCheckLogFilter(logging.Filter):
    """Hide the health check requests of one route from an access log.

    Werkzeug, uvicorn and gunicorn all write the request line in quotes, like
    ``"GET /health HTTP/1.1"``.
    """

    def __init__(self, health_path: str) -> None:
        super().__init__()

        self.health_path: str = health_path
        self._request_line: str = f'"GET {health_path} HTTP/'

    @override
    def filter(self, record: logging.LogRecord) -> bool:
        """Keep every record that is not a health check request."""
        return self._request_line not in record.getMessage()


def hide_health_checks(logger_names: Iterable[str], health_path: str) -> None:
    """Add the filter to the access loggers, once per route: applications sharing it add nothing."""
    for logger_name in logger_names:
        access_logger = logging.getLogger(logger_name)

        already_hidden = any(
            isinstance(existing, HealthCheckLogFilter) and existing.health_path == health_path
            for existing in access_logger.filters
        )

        if not already_hidden:
            access_logger.addFilter(HealthCheckLogFilter(health_path))
