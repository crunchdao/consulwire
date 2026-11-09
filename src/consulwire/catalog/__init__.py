"""The catalog section: every service registered in the cluster, healthy or not.

Reached through :attr:`consulwire.Consul.catalog` and :attr:`consulwire.AsyncConsul.catalog`. To
call another service, :mod:`consulwire.health` lists only its healthy instances.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from consulwire.catalog import _requests
from consulwire.catalog._instance import ServiceInstance

if TYPE_CHECKING:
    from consulwire._transport import AsyncTransport, Transport

__all__ = ["AsyncCatalog", "Catalog", "ServiceInstance"]


class Catalog:
    """Catalog API of :class:`consulwire.Consul`."""

    def __init__(self, transport: Transport) -> None:
        self._transport: Transport = transport

    def services(self) -> dict[str, list[str]]:
        """Return the name of every service, with the tags of its instances, sorted by name.

        Raises:
            TransportError: The agent could not be reached.
            HTTPStatusError: The agent refused the request, like 403 for a missing ACL permission.
            ResponseError: The agent answered something else than services.
        """
        return _requests.parse_services(self._transport.send(_requests.services()))

    def service(self, name: str, *, tag: str | None = None) -> list[ServiceInstance]:
        """Return every registered instance of a service, including the failing ones.

        Raises like :meth:`services`.

        Args:
            name: Service name. An unknown one has no instance.
            tag: Only the instances with this tag.
        """
        request = _requests.service(name, tag=tag)

        return _requests.parse_instances(self._transport.send(request))


class AsyncCatalog:
    """Catalog API of :class:`consulwire.AsyncConsul`, the same as :class:`Catalog`."""

    def __init__(self, transport: AsyncTransport) -> None:
        self._transport: AsyncTransport = transport

    async def services(self) -> dict[str, list[str]]:
        """See :meth:`Catalog.services`."""
        return _requests.parse_services(await self._transport.send(_requests.services()))

    async def service(self, name: str, *, tag: str | None = None) -> list[ServiceInstance]:
        """See :meth:`Catalog.service`."""
        request = _requests.service(name, tag=tag)

        return _requests.parse_instances(await self._transport.send(request))
