"""The session section: sessions, which hold locks on keys of the store until they end.

Reached through :attr:`consulwire.Consul.session` and :attr:`consulwire.AsyncConsul.session`. To
hold a lock, :meth:`consulwire.Consul.lock` creates, renews and ends the session itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from consulwire.session import _requests
from consulwire.session._session import Session, SessionBehavior

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import timedelta

    from consulwire._transport import AsyncTransport, Transport

__all__ = ["AsyncSessions", "Session", "SessionBehavior", "Sessions"]


class Sessions:
    """Session API of :class:`consulwire.Consul`."""

    def __init__(self, transport: Transport) -> None:
        self._transport: Transport = transport

    def create(  # the fields of a Consul session
        self,
        *,
        name: str | None = None,
        ttl: timedelta | None = None,
        lock_delay: timedelta | None = None,
        behavior: SessionBehavior = "release",
        service_checks: Sequence[str] = (),
    ) -> str:
        """Create a session on the agent's node, and return its ID.

        The session ends when it is destroyed, when its node fails, when one of its service checks
        fails, or when it is not renewed within its TTL.

        Args:
            name: Name shown in the UI.
            ttl: Time after the last renewal before the session ends, between 10 seconds and a
                day. Consul may wait up to twice as long. Without one, it lasts until destroyed.
            lock_delay: Time a key the session held stays unlockable after it ends, so a process
                that lost the lock can notice before another takes it. 15 seconds by default.
            behavior: What happens to the keys the session holds when it ends.
            service_checks: IDs of service checks: the session ends when one of them fails.

        Raises:
            TransportError: The agent could not be reached.
            HTTPStatusError: The agent refused the session, like a TTL out of bounds.
            ResponseError: The agent answered without a session ID.
        """
        request = _requests.create(
            name=name,
            ttl=ttl,
            lock_delay=lock_delay,
            behavior=behavior,
            service_checks=service_checks,
        )

        return _requests.parse_created(self._transport.send(request))

    def renew(self, session_id: str) -> bool:
        """Renew a session for another TTL.

        Returns:
            Whether the session still exists: ``False`` once it ended.

        Raises:
            TransportError: The agent could not be reached.
            HTTPStatusError: The agent refused the request.
        """
        return _requests.parse_renewed(self._transport.send(_requests.renew(session_id)))

    def destroy(self, session_id: str) -> None:
        """End a session, ended or not: the keys it holds are released or deleted.

        Raises:
            TransportError: The agent could not be reached.
            HTTPStatusError: The agent refused the request.
        """
        _ = self._transport.send(_requests.destroy(session_id))

    def info(self, session_id: str) -> Session | None:
        """Return a session, ``None`` once it ended.

        Raises:
            TransportError: The agent could not be reached.
            HTTPStatusError: The agent refused the request.
            ResponseError: The agent answered something else than a session.
        """
        return _requests.parse_session(self._transport.send(_requests.info(session_id)))


class AsyncSessions:
    """Session API of :class:`consulwire.AsyncConsul`, the same as :class:`Sessions`."""

    def __init__(self, transport: AsyncTransport) -> None:
        self._transport: AsyncTransport = transport

    async def create(  # the fields of a Consul session
        self,
        *,
        name: str | None = None,
        ttl: timedelta | None = None,
        lock_delay: timedelta | None = None,
        behavior: SessionBehavior = "release",
        service_checks: Sequence[str] = (),
    ) -> str:
        """See :meth:`Sessions.create`."""
        request = _requests.create(
            name=name,
            ttl=ttl,
            lock_delay=lock_delay,
            behavior=behavior,
            service_checks=service_checks,
        )

        return _requests.parse_created(await self._transport.send(request))

    async def renew(self, session_id: str) -> bool:
        """See :meth:`Sessions.renew`."""
        return _requests.parse_renewed(await self._transport.send(_requests.renew(session_id)))

    async def destroy(self, session_id: str) -> None:
        """See :meth:`Sessions.destroy`."""
        _ = await self._transport.send(_requests.destroy(session_id))

    async def info(self, session_id: str) -> Session | None:
        """See :meth:`Sessions.info`."""
        return _requests.parse_session(await self._transport.send(_requests.info(session_id)))
