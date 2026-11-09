"""The health section: the instances of a service that pass their checks, to call them.

Reached through :attr:`consulwire.Consul.health` and :attr:`consulwire.AsyncConsul.health`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from consulwire._blocking import DEFAULT_WAIT, BlockingQuery, follow, follow_async
from consulwire.catalog._instance import ServiceInstance
from consulwire.health import _requests

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
    from datetime import timedelta

    from consulwire._transport import AsyncTransport, Transport

__all__ = ["AsyncHealth", "Health", "ServiceInstance"]


class Health:
    """Health API of :class:`consulwire.Consul`."""

    def __init__(self, transport: Transport) -> None:
        self._transport: Transport = transport

    def service(
        self,
        name: str,
        *,
        tag: str | None = None,
        healthy_only: bool = True,
    ) -> list[ServiceInstance]:
        """Return the instances of a service that pass every check, the ones to call.

        Args:
            name: Service name. An unknown one has no instance.
            tag: Only the instances with this tag.
            healthy_only: Only the instances whose checks all pass. ``False`` returns every
                instance, like the catalog. A check in warning does not pass.

        Raises:
            TransportError: The agent could not be reached.
            HTTPStatusError: The agent refused the request, like 403 for a missing ACL permission.
            ResponseError: The agent answered something else than instances.
        """
        request = _requests.service(name, tag=tag, healthy_only=healthy_only)

        return _requests.parse_instances(self._transport.send(request))

    def watch_service(
        self,
        name: str,
        *,
        tag: str | None = None,
        healthy_only: bool = True,
        wait: timedelta = DEFAULT_WAIT,
    ) -> Iterator[list[ServiceInstance]]:
        """Yield the instances now, then every time one starts, stops, or fails its checks.

        Works like :meth:`consulwire.kv.KV.watch`: a blocking query, so a change arrives within
        milliseconds without polling. The loop never ends by itself, and stops at the first error.

        Example:
            ```python
            for instances in consul.health.watch_service("web"):
                targets = instances
            ```

        Args:
            name: Service name.
            tag: Only the instances with this tag.
            healthy_only: Only the instances whose checks all pass.
            wait: Longest time the agent holds a request, at most 10 minutes.

        Raises:
            TransportError: The agent could not be reached.
            HTTPStatusError: The agent refused the request.
            ResponseError: The agent answered something else than instances.
        """
        return follow(
            self._transport,
            BlockingQuery(
                lambda index: _requests.service(
                    name, tag=tag, healthy_only=healthy_only, index=index, wait=wait
                ),
                _requests.parse_instances,
            ),
        )


class AsyncHealth:
    """Health API of :class:`consulwire.AsyncConsul`, the same as :class:`Health`."""

    def __init__(self, transport: AsyncTransport) -> None:
        self._transport: AsyncTransport = transport

    async def service(
        self,
        name: str,
        *,
        tag: str | None = None,
        healthy_only: bool = True,
    ) -> list[ServiceInstance]:
        """See :meth:`Health.service`."""
        request = _requests.service(name, tag=tag, healthy_only=healthy_only)

        return _requests.parse_instances(await self._transport.send(request))

    def watch_service(
        self,
        name: str,
        *,
        tag: str | None = None,
        healthy_only: bool = True,
        wait: timedelta = DEFAULT_WAIT,
    ) -> AsyncIterator[list[ServiceInstance]]:
        """See :meth:`Health.watch_service`, with ``async for``."""
        return follow_async(
            self._transport,
            BlockingQuery(
                lambda index: _requests.service(
                    name, tag=tag, healthy_only=healthy_only, index=index, wait=wait
                ),
                _requests.parse_instances,
            ),
        )
