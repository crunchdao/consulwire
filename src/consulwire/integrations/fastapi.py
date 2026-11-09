"""FastAPI integration: a lifespan registering the application, deregistering it at shutdown.

Works with any Starlette application, FastAPI included.

Example:
    ```python
    application = FastAPI(lifespan=ConsulLifespan(name="my-service", port=8000))

    uvicorn.run(application, host="0.0.0.0", port=8000)
    ```
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Final

import anyio.to_thread
from starlette.responses import Response
from starlette.routing import Route

from consulwire._client import AsyncConsul
from consulwire.integrations._common import (
    DEFAULT_CHECK_INTERVAL,
    DEFAULT_DEREGISTER_CRITICAL_AFTER,
    DEFAULT_HEALTH_PATH,
    Registration,
    hide_health_checks,
    registering_server,
    resolve_address,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping, Sequence
    from contextlib import AbstractAsyncContextManager
    from datetime import timedelta

    from starlette.applications import Starlette
    from starlette.requests import Request

__all__ = ["ConsulLifespan"]

ACCESS_LOGGERS: Final = ("uvicorn.access", "gunicorn.access")

logger = logging.getLogger(__name__)


class ConsulLifespan:
    """Lifespan registering a Starlette or FastAPI application in Consul.

    At startup, before the first request:

    - gives Consul a health route: the application's own ``GET`` route at ``health_path`` when it
      has one, otherwise one answering 200, left out of the OpenAPI schema;
    - hides the health check requests from the uvicorn and gunicorn access logs, unless
      ``log_health_checks`` is set;
    - registers the application, with the address found automatically when none is given.

    At shutdown, the application is deregistered. Under ``uvicorn --reload``, each reload is a
    shutdown then a startup: the instance is replaced, its ID being the service name.

    Registration is skipped, with a log line, when no agent is configured: no ``consul`` given, and
    neither ``CONSUL_HTTP_ADDR`` nor ``CONSUL_HOST`` set. It is also left to gunicorn when
    :class:`consulwire.integrations.gunicorn.ConsulHooks` registered the application from the main
    process: the workers restarting then do not touch Consul.

    Example:
        With a lifespan of the application's own:

        ```python
        consul = ConsulLifespan(name="my-service", port=8000)

        @asynccontextmanager
        async def lifespan(application: FastAPI) -> AsyncIterator[None]:
            async with consul(application):
                yield

        application = FastAPI(lifespan=lifespan)
        ```
    """

    name: str
    """Service name, also the instance ID: registering again replaces the instance."""

    port: int

    health_path: str

    def __init__(  # noqa: PLR0913  # keyword-only options
        self,
        *,
        name: str,
        port: int,
        health_path: str = DEFAULT_HEALTH_PATH,
        address: str | None = None,
        consul: AsyncConsul | None = None,
        check_interval: timedelta = DEFAULT_CHECK_INTERVAL,
        deregister_critical_after: timedelta | None = DEFAULT_DEREGISTER_CRITICAL_AFTER,
        tags: Sequence[str] = (),
        meta: Mapping[str, str] | None = None,
        log_health_checks: bool = False,
    ) -> None:
        """Create the lifespan, given to the application as ``lifespan=``.

        Args:
            name: Name other services look this one up by.
            port: Port the application listens on. The server does not tell the application, so
                it must match the one given to uvicorn.
            health_path: Route Consul checks. The application's own ``GET`` route is used when it
                has one, otherwise one answering 200 is added.
            address: Address other services connect to. By default, the local IP address the
                system uses to reach the agent.
            consul: Client of the agent, left open at shutdown. By default, one configured from the
                environment, closed at shutdown.
            check_interval: Time between two health checks.
            deregister_critical_after: Time after which Consul removes an instance whose health
                check keeps failing, for instances that stopped without a shutdown. ``None`` keeps
                them. Consul ignores values under a minute.
            tags: Service tags.
            meta: Service key and value pairs.
            log_health_checks: Keep the health check requests in the access logs.
        """
        self.name = name
        self.port = port
        self.health_path = health_path

        self._address: str | None = address
        self._consul: AsyncConsul | None = consul
        self._log_health_checks: bool = log_health_checks
        self._registration: Registration = Registration(
            name=name,
            health_path=health_path,
            check_interval=check_interval,
            deregister_critical_after=deregister_critical_after,
            tags=tags,
            meta=meta or {},
            logger=logger,
        )

    def __call__(self, application: Starlette) -> AbstractAsyncContextManager[None]:
        """Called by Starlette at each startup, ``--reload`` included: one registration per run."""
        return self._lifespan(application)

    @asynccontextmanager
    async def _lifespan(self, application: Starlette) -> AsyncGenerator[None, None]:
        _ensure_health_route(application, self.health_path)

        if not self._log_health_checks:
            hide_health_checks(ACCESS_LOGGERS, self.health_path)

        server = registering_server()
        if server is not None:
            logger.info("consul registration left to %s - name=%s", server, self.name)
            yield
            return

        consul, owned = self._registration.agent_client(self._consul, AsyncConsul)
        if consul is None:
            yield
            return

        try:
            address = self._address

            if address is None:
                address = await anyio.to_thread.run_sync(resolve_address, None, None, consul.url)

            await self._registration.register_async(consul, address, self.port)

            try:
                yield
            finally:
                await self._registration.deregister_async(consul)
        finally:
            if owned:
                await consul.close()


def _ensure_health_route(application: Starlette, health_path: str) -> None:
    for route in application.router.routes:
        if (
            isinstance(route, Route)
            and route.path == health_path
            and "GET" in (route.methods or ())
        ):
            return

    application.router.add_route(
        health_path,
        _healthy,
        methods=["GET"],
        name="consulwire_health",
        include_in_schema=False,
    )


async def _healthy(_request: Request) -> Response:
    return Response(status_code=200)
