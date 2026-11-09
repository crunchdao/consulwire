"""The clients: one connection to the agent, shared by every section."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from consulwire._environment import DEFAULT_URL, find_token, find_url
from consulwire._transport import AsyncTransport, Transport
from consulwire.agent import Agent, AsyncAgent
from consulwire.catalog import AsyncCatalog, Catalog
from consulwire.health import AsyncHealth, Health
from consulwire.kv import KV, AsyncKV

if TYPE_CHECKING:
    from types import TracebackType

    import httpx
    from typing_extensions import Self

DEFAULT_TIMEOUT: Final = 10.0
"""Seconds before a request to the agent fails."""


class Consul:
    """Synchronous client of the local Consul agent.

    Example:
        ```python
        with Consul() as consul:
            consul.agent.register_service("my-service", port=8000)
        ```
    """

    url: str
    """Agent URL, without trailing slash."""

    agent: Agent
    """Services and health checks of this machine."""

    catalog: Catalog
    """Every service of the cluster, healthy or not."""

    health: Health
    """The healthy instances of the services, to call them."""

    kv: KV
    """Key/value store."""

    def __init__(
        self,
        url: str | None = None,
        *,
        token: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Create a client. Nothing is sent until a section is used.

        Args:
            url: Agent URL. Read from the environment when not given (``CONSUL_HTTP_ADDR``, then
                ``CONSUL_HOST`` and ``CONSUL_PORT``), ``http://localhost:8500`` otherwise.
            token: ACL token, sent as ``X-Consul-Token``. Read from ``CONSUL_HTTP_TOKEN`` when not
                given. Only needed when the agent enforces ACLs.
            timeout: Seconds before a request fails.
            transport: HTTP transport, for tests.

        Raises:
            ConfigurationError: The URL is not given and the environment holds an invalid value.
        """
        self.url = _resolve_url(url)
        self._transport: Transport = Transport(
            self.url,
            token=token or find_token(),
            timeout=timeout,
            transport=transport,
        )

        self.agent = Agent(self._transport)
        self.catalog = Catalog(self._transport)
        self.health = Health(self._transport)
        self.kv = KV(self._transport)

    def close(self) -> None:
        """Close the connections to the agent."""
        self._transport.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class AsyncConsul:
    """Asynchronous client of the local Consul agent, the same as :class:`Consul`.

    Example:
        ```python
        async with AsyncConsul() as consul:
            await consul.agent.register_service("my-service", port=8000)
        ```
    """

    url: str
    """Agent URL, without trailing slash."""

    agent: AsyncAgent
    """Services and health checks of this machine."""

    catalog: AsyncCatalog
    """Every service of the cluster, healthy or not."""

    health: AsyncHealth
    """The healthy instances of the services, to call them."""

    kv: AsyncKV
    """Key/value store."""

    def __init__(
        self,
        url: str | None = None,
        *,
        token: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Create a client. See :class:`Consul` for the arguments.

        Raises:
            ConfigurationError: The URL is not given and the environment holds an invalid value.
        """
        self.url = _resolve_url(url)
        self._transport: AsyncTransport = AsyncTransport(
            self.url,
            token=token or find_token(),
            timeout=timeout,
            transport=transport,
        )

        self.agent = AsyncAgent(self._transport)
        self.catalog = AsyncCatalog(self._transport)
        self.health = AsyncHealth(self._transport)
        self.kv = AsyncKV(self._transport)

    async def close(self) -> None:
        """Close the connections to the agent."""
        await self._transport.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()


def _resolve_url(url: str | None) -> str:
    return (url or find_url() or DEFAULT_URL).rstrip("/")
