"""The clients: one connection to the agent, shared by every section."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from consulwire._environment import DEFAULT_URL, find_token, find_url
from consulwire._transport import AsyncTransport, Transport
from consulwire.agent import Agent, AsyncAgent
from consulwire.catalog import AsyncCatalog, Catalog
from consulwire.health import AsyncHealth, Health
from consulwire.kv import KV, AsyncKV
from consulwire.locks import DEFAULT_TTL, AsyncLock, Lock
from consulwire.session import AsyncSessions, Sessions

if TYPE_CHECKING:
    from datetime import timedelta
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

    session: Sessions
    """Sessions, which hold locks on keys of the store."""

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
        self.session = Sessions(self._transport)

    def lock(
        self,
        key: str,
        *,
        value: bytes | str = b"",
        ttl: timedelta = DEFAULT_TTL,
        lock_delay: timedelta | None = None,
        timeout: timedelta | None = None,
    ) -> Lock:
        """Return a lock on a key, held by one process of the cluster at a time while entered.

        Example:
            ```python
            with consul.lock("jobs/nightly-report"):
                send_report()
            ```

        Args:
            key: Key of the lock, only used for it.
            value: Value of the key while held, like the name of the holder.
            ttl: Time after the last renewal before Consul ends the session of a process that
                died, freeing the lock. Between 10 seconds and a day.
            lock_delay: Time the key stays unlockable after a holder's session ended, 15 seconds
                by default. A process that lost the lock notices it before another takes it.
            timeout: Longest wait for the key: entering raises ``TimeoutError`` after it.
                ``timedelta(0)`` only tries once. As long as needed by default.
        """
        return Lock(
            self.kv,
            self.session,
            self._transport,
            key,
            value=value,
            ttl=ttl,
            lock_delay=lock_delay,
            timeout=timeout,
        )

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

    session: AsyncSessions
    """Sessions, which hold locks on keys of the store."""

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
        self.session = AsyncSessions(self._transport)

    def lock(
        self,
        key: str,
        *,
        value: bytes | str = b"",
        ttl: timedelta = DEFAULT_TTL,
        lock_delay: timedelta | None = None,
        timeout: timedelta | None = None,
    ) -> AsyncLock:
        """See :meth:`Consul.lock`, entered with ``async with``."""
        return AsyncLock(
            self.kv,
            self.session,
            self._transport,
            key,
            value=value,
            ttl=ttl,
            lock_delay=lock_delay,
            timeout=timeout,
        )

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
