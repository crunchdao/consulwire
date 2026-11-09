"""gunicorn integration: server hooks registering the application once, from the main process.

Example:
    In ``gunicorn.conf.py``:

    ```python
    from consulwire.integrations.gunicorn import ConsulHooks

    consul_hooks = ConsulHooks(name="my-service")

    when_ready = consul_hooks.when_ready
    on_exit = consul_hooks.on_exit
    ```
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Final, Protocol, cast

from consulwire._client import Consul
from consulwire._environment import find_url
from consulwire.errors import ConsulError
from consulwire.integrations._common import (
    DEFAULT_CHECK_INTERVAL,
    DEFAULT_DEREGISTER_CRITICAL_AFTER,
    DEFAULT_HEALTH_PATH,
    REGISTERED_BY_VARIABLE,
    health_check,
    hide_health_checks,
    resolve_address,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import timedelta

__all__ = ["Arbiter", "ConsulHooks"]

ACCESS_LOGGERS: Final = ("gunicorn.access", "uvicorn.access")

logger = logging.getLogger(__name__)


class Listener(Protocol):
    """A socket gunicorn listens on."""

    def getsockname(self) -> object: ...


class Arbiter(Protocol):
    """What the hooks read of ``gunicorn.arbiter.Arbiter``, the main process."""

    @property
    def LISTENERS(self) -> Sequence[Listener]: ...  # noqa: D102, N802  # gunicorn's own name

    @property
    def reexec_pid(self) -> int: ...  # noqa: D102


class ConsulHooks:
    """gunicorn server hooks registering the application in Consul, like Spring Cloud Consul does.

    - ``when_ready`` runs in the main process once the sockets are bound, before the workers start:
      it hides the health check requests from the gunicorn and uvicorn access logs, unless
      ``log_health_checks`` is set, and registers the application with the port gunicorn listens
      on;
    - ``on_exit`` deregisters it when gunicorn stops.

    Workers restarting, ``--reload`` and ``--max-requests`` included, do not touch Consul. The
    integrations running in the workers, like
    :class:`consulwire.integrations.fastapi.ConsulLifespan`, leave the registration to the hooks,
    and still add the health route.

    Registration is skipped, with a log line, when no agent is configured: no ``consul`` given, and
    neither ``CONSUL_HTTP_ADDR`` nor ``CONSUL_HOST`` set.
    """

    name: str
    """Service name, also the instance ID: registering again replaces the instance."""

    health_path: str

    def __init__(  # noqa: PLR0913  # keyword-only options
        self,
        *,
        name: str,
        port: int | None = None,
        health_path: str = DEFAULT_HEALTH_PATH,
        address: str | None = None,
        consul: Consul | None = None,
        check_interval: timedelta = DEFAULT_CHECK_INTERVAL,
        deregister_critical_after: timedelta | None = DEFAULT_DEREGISTER_CRITICAL_AFTER,
        tags: Sequence[str] = (),
        meta: Mapping[str, str] | None = None,
        log_health_checks: bool = False,
    ) -> None:
        """Create the hooks, given to gunicorn in its configuration file.

        Args:
            name: Name other services look this one up by.
            port: Port other services connect to. By default, the first TCP port gunicorn
                listens on.
            health_path: Route Consul checks. The application must answer it: the Flask and
                FastAPI integrations add it when missing.
            address: Address other services connect to. By default, the host gunicorn listens on
                when it is a specific one, otherwise the local IP address the system uses to reach
                the agent.
            consul: Client of the agent, left open at exit. By default, one configured from the
                environment, closed at exit.
            check_interval: Time between two health checks.
            deregister_critical_after: Time after which Consul removes an instance whose health
                check keeps failing, for instances that stopped without exiting. ``None`` keeps
                them. Consul ignores values under a minute.
            tags: Service tags.
            meta: Service key and value pairs.
            log_health_checks: Keep the health check requests in the access logs.
        """
        self.name = name
        self.health_path = health_path

        self._port: int | None = port
        self._address: str | None = address
        self._given_consul: Consul | None = consul
        self._check_interval: timedelta = check_interval
        self._deregister_critical_after: timedelta | None = deregister_critical_after
        self._tags: Sequence[str] = tags
        self._meta: Mapping[str, str] = meta or {}
        self._log_health_checks: bool = log_health_checks

        self._consul: Consul | None = None

    def when_ready(self, server: Arbiter) -> None:
        """Register the application. gunicorn calls it once the sockets are bound.

        Raises:
            ConsulError: The agent could not be reached, or refused the registration: gunicorn
                stops, like an application failing to start.
        """
        if not self._log_health_checks:
            hide_health_checks(ACCESS_LOGGERS, self.health_path)

        consul = self._agent_client()
        if consul is None:
            logger.info("consul registration skipped, no agent configured - name=%s", self.name)
            return

        registered = False

        try:
            registered = self._register(consul, server)
        finally:
            if registered:
                self._consul = consul
            elif consul is not self._given_consul:
                consul.close()

    def _register(self, consul: Consul, server: Arbiter) -> bool:
        listening_host, listening_port = _tcp_listener(server)
        port = self._port or listening_port

        if port is None:
            logger.warning(
                "consul registration skipped, gunicorn listens on no TCP port - name=%s", self.name
            )
            return False

        address = resolve_address(self._address, listening_host, consul.url)

        consul.agent.register_service(
            self.name,
            service_id=self.name,
            address=address,
            port=port,
            tags=self._tags,
            meta=self._meta,
            checks=[
                health_check(
                    address,
                    port,
                    self.health_path,
                    interval=self._check_interval,
                    deregister_critical_after=self._deregister_critical_after,
                ),
            ],
        )

        os.environ[REGISTERED_BY_VARIABLE] = "gunicorn"

        logger.info(
            "registered in consul - name=%s address=%s port=%d agent=%s",
            self.name,
            address,
            port,
            consul.url,
        )

        return True

    def on_exit(self, server: Arbiter) -> None:
        """Deregister the application, without failing the exit. gunicorn calls it last."""
        consul = self._consul
        if consul is None:
            return

        self._consul = None

        try:
            # During a binary upgrade (USR2), the new main process registered the same instance:
            # the old one exiting leaves it.
            if server.reexec_pid:
                logger.info(
                    "consul deregistration left to the new main process - name=%s", self.name
                )
                return

            consul.agent.deregister_service(self.name)
        except ConsulError as error:
            logger.warning("consul deregistration failed - name=%s error=`%s`", self.name, error)
        else:
            logger.info("deregistered from consul - name=%s", self.name)
        finally:
            if consul is not self._given_consul:
                consul.close()

    def _agent_client(self) -> Consul | None:
        if self._given_consul is not None:
            return self._given_consul

        url = find_url()
        if url is None:
            return None

        return Consul(url)


def _tcp_listener(server: Arbiter) -> tuple[str | None, int | None]:
    """Return the host and port of the first TCP socket gunicorn listens on."""
    for listener in server.LISTENERS:
        socket_name = listener.getsockname()

        # A TCP socket is named (host, port), plus flow and scope for IPv6. A UNIX one is a path.
        if not isinstance(socket_name, tuple):
            continue

        host, port, *_ = cast("tuple[object, ...]", socket_name)

        if isinstance(host, str) and isinstance(port, int):
            return host, port

    return None, None
