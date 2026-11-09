"""What every integration shares: the registration, the address, and the quiet access log."""

from __future__ import annotations

import ipaddress
import logging
import os
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Final, TypeVar

from typing_extensions import override

from consulwire._environment import find_url
from consulwire._network import find_local_address, url_host
from consulwire.errors import ConsulError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from consulwire._client import AsyncConsul, Consul
    from consulwire.agent import HTTPCheck

_Client = TypeVar("_Client")

DEFAULT_HEALTH_PATH: Final = "/health"

DEFAULT_CHECK_INTERVAL: Final = timedelta(seconds=10)

DEFAULT_DEREGISTER_CRITICAL_AFTER: Final = timedelta(minutes=1)

REGISTERED_BY_VARIABLE: Final = "CONSULWIRE_REGISTERED_BY"
"""Set by a server that registers the application from its main process, like the gunicorn hooks,
before it starts the workers: the integrations running in the workers leave the registration to it.
"""


def registering_server() -> str | None:
    """Return the server registering the application from its main process, if any."""
    return os.environ.get(REGISTERED_BY_VARIABLE) or None


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


@dataclass(frozen=True)
class Registration:
    """What an integration registers, but the address and port, known once the server listens.

    The instance ID is the service name, so registering again replaces the instance. Logs go to the
    logger of the integration.
    """

    name: str
    health_path: str
    check_interval: timedelta
    deregister_critical_after: timedelta | None
    tags: Sequence[str]
    meta: Mapping[str, str]
    logger: logging.Logger

    def agent_client(
        self,
        given: _Client | None,
        create: Callable[[str], _Client],
    ) -> tuple[_Client | None, bool]:
        """Return the client to register with, and whether the integration owns it.

        The given client first, otherwise one created from the environment. ``None``, with a log
        line, when no agent is configured.
        """
        if given is not None:
            return given, False

        url = find_url()
        if url is None:
            self.logger.info(
                "consul registration skipped, no agent configured - name=%s", self.name
            )
            return None, False

        return create(url), True

    def register(self, consul: Consul, address: str, port: int) -> None:
        """Register the instance. Errors are raised: the application fails to start."""
        consul.agent.register_service(
            self.name,
            service_id=self.name,
            address=address,
            port=port,
            tags=self.tags,
            meta=self.meta,
            checks=[self._health_check(address, port)],
        )

        self._registered(address, port, consul.url)

    async def register_async(self, consul: AsyncConsul, address: str, port: int) -> None:
        """See :meth:`register`."""
        await consul.agent.register_service(
            self.name,
            service_id=self.name,
            address=address,
            port=port,
            tags=self.tags,
            meta=self.meta,
            checks=[self._health_check(address, port)],
        )

        self._registered(address, port, consul.url)

    def deregister(self, consul: Consul) -> None:
        """Deregister the instance. Errors are logged: Consul removes the instance later anyway."""
        try:
            consul.agent.deregister_service(self.name)
        except ConsulError as error:
            self._deregistration_failed(error)
        else:
            self.logger.info("deregistered from consul - name=%s", self.name)

    async def deregister_async(self, consul: AsyncConsul) -> None:
        """See :meth:`deregister`."""
        try:
            await consul.agent.deregister_service(self.name)
        except ConsulError as error:
            self._deregistration_failed(error)
        else:
            self.logger.info("deregistered from consul - name=%s", self.name)

    def _health_check(self, address: str, port: int) -> HTTPCheck:
        check: HTTPCheck = {
            "http": f"http://{url_host(address)}:{port}{self.health_path}",
            "interval": self.check_interval,
        }

        if self.deregister_critical_after is not None:
            check["deregister_critical_after"] = self.deregister_critical_after

        return check

    def _registered(self, address: str, port: int, agent_url: str) -> None:
        self.logger.info(
            "registered in consul - name=%s address=%s port=%d agent=%s",
            self.name,
            address,
            port,
            agent_url,
        )

    def _deregistration_failed(self, error: ConsulError) -> None:
        self.logger.warning("consul deregistration failed - name=%s error=`%s`", self.name, error)


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
