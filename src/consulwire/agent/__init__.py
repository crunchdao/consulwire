"""The agent section: register the services of this machine, and their health checks.

Reached through :attr:`consulwire.Consul.agent` and :attr:`consulwire.AsyncConsul.agent`. The check
types only exist for annotations: a check is a plain dictionary.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from consulwire.agent import _requests
from consulwire.agent._checks import Check, HTTPCheck, TCPCheck

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from consulwire._transport import AsyncTransport, Transport

__all__ = ["Agent", "AsyncAgent", "Check", "HTTPCheck", "TCPCheck"]


class Agent:
    """Agent API of :class:`consulwire.Consul`."""

    def __init__(self, transport: Transport) -> None:
        self._transport: Transport = transport

    def register_service(  # noqa: PLR0913  # the fields of a Consul service
        self,
        name: str,
        *,
        port: int | None = None,
        address: str | None = None,
        service_id: str | None = None,
        tags: Sequence[str] = (),
        meta: Mapping[str, str] | None = None,
        checks: Sequence[Check] = (),
        replace_existing_checks: bool = False,
    ) -> None:
        """Register a service instance, or update it when its ID is already registered.

        Args:
            name: Name other services look this one up by.
            port: Port other services connect to.
            address: Address other services connect to. Consul uses the agent's address when not
                given, which is wrong for a service in another container.
            service_id: Instance ID, unique on the agent. Consul uses the name when not given, so
                registering the same name again replaces the instance instead of adding one.
            tags: Tags, used to filter instances.
            meta: Key and value pairs.
            checks: Health checks, such as
                ``{"http": "http://my-service:8000/health", "interval": timedelta(seconds=10)}``.
                A service without any is always healthy.
            replace_existing_checks: Remove the checks of a previous registration that are not part
                of this one. Consul keeps them by default.

        Raises:
            TransportError: The agent could not be reached.
            HTTPStatusError: The agent refused the registration, for example an invalid check.
        """
        _ = self._transport.send(
            _requests.register_service(
                name,
                service_id=service_id,
                address=address,
                port=port,
                tags=tags,
                meta=meta,
                checks=checks,
                replace_existing_checks=replace_existing_checks,
            )
        )

    def deregister_service(self, service_id: str) -> None:
        """Remove a service instance and its checks.

        Args:
            service_id: Instance ID, which is the service name unless another ID was registered.

        Raises:
            TransportError: The agent could not be reached.
            HTTPStatusError: The agent refused the request: 404 when no instance has this ID, 403
                for a missing ACL permission.
        """
        _ = self._transport.send(_requests.deregister_service(service_id))


class AsyncAgent:
    """Agent API of :class:`consulwire.AsyncConsul`, the same as :class:`Agent`."""

    def __init__(self, transport: AsyncTransport) -> None:
        self._transport: AsyncTransport = transport

    async def register_service(  # noqa: PLR0913  # the fields of a Consul service
        self,
        name: str,
        *,
        port: int | None = None,
        address: str | None = None,
        service_id: str | None = None,
        tags: Sequence[str] = (),
        meta: Mapping[str, str] | None = None,
        checks: Sequence[Check] = (),
        replace_existing_checks: bool = False,
    ) -> None:
        """See :meth:`Agent.register_service`."""
        _ = await self._transport.send(
            _requests.register_service(
                name,
                service_id=service_id,
                address=address,
                port=port,
                tags=tags,
                meta=meta,
                checks=checks,
                replace_existing_checks=replace_existing_checks,
            )
        )

    async def deregister_service(self, service_id: str) -> None:
        """See :meth:`Agent.deregister_service`."""
        _ = await self._transport.send(_requests.deregister_service(service_id))
