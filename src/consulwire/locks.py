"""Locks held by one process of the cluster at a time, on a key of the store.

Created by :meth:`consulwire.Consul.lock` and :meth:`consulwire.AsyncConsul.lock`. A lock is a
session holding the key: the session is renewed while the lock is held, and ends with it.

Example:
    ```python
    with consul.lock("jobs/nightly-report"):
        send_report()
    ```
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import TYPE_CHECKING, Final

import anyio

from consulwire._blocking import DEFAULT_WAIT, BlockingQuery
from consulwire.errors import ConsulError
from consulwire.kv import _requests as kv_requests

if TYPE_CHECKING:
    from types import TracebackType

    from typing_extensions import Self

    from consulwire._transport import AsyncTransport, Transport
    from consulwire.kv import KV, AsyncKV, Entry
    from consulwire.session import AsyncSessions, Sessions

__all__ = ["DEFAULT_TTL", "AsyncLock", "Lock"]

DEFAULT_TTL: Final = timedelta(seconds=15)
"""Time after the last renewal before Consul ends the session of a process that died."""

RETRY_INTERVAL: Final = timedelta(seconds=5)
"""Time between two tries on a free key Consul refuses during the lock delay of its last holder,
like Consul's own Go client."""

_SHORTEST_WAIT: Final = timedelta(milliseconds=1)

logger = logging.getLogger(__name__)


class Lock:
    """A lock on a key, held by one process of the cluster at a time.

    Entering waits until the key is free, and takes it. Leaving releases it, then ends its
    session. While held, the session is renewed from a thread, every half TTL.

    If the session ends while the lock is held, after the agent or the network failed for longer
    than the TTL, the lock is lost: another process may take it. The code holding it keeps running,
    so it checks :attr:`lost` before what must not happen twice, or passes :attr:`lock_index` to
    the systems it writes to, which then refuse a write with an older index.

    Attributes:
        key: Key of the lock.
        lock_index: Number of times the key was locked, the last time by this lock: increases
            with every holder, so it orders them. ``None`` while not held.
        lost: Set when the session ended while the lock was held. It stays set after leaving,
            until the lock is entered again.
    """

    key: str
    lock_index: int | None
    lost: threading.Event

    def __init__(  # noqa: PLR0913  # the options of the lock, given by Consul.lock
        self,
        kv: KV,
        sessions: Sessions,
        transport: Transport,
        key: str,
        *,
        value: bytes | str,
        ttl: timedelta,
        lock_delay: timedelta | None,
        timeout: timedelta | None,
    ) -> None:
        self.key = key
        self.lock_index = None
        self.lost = threading.Event()

        self._kv: KV = kv
        self._sessions: Sessions = sessions
        self._transport: Transport = transport
        self._value: bytes | str = value
        self._ttl: timedelta = ttl
        self._lock_delay: timedelta | None = lock_delay
        self._timeout: timedelta | None = timeout
        self._session: _Session | None = None

    @property
    def session_id(self) -> str | None:
        """Session holding the lock, ``None`` while not held."""
        return None if self._session is None else self._session.id

    def __enter__(self) -> Self:
        """Wait for the key, and take it.

        Raises:
            RuntimeError: The lock is already held.
            TimeoutError: The ``timeout`` ran out while another session held the key.
            ConsulError: The agent could not be reached, or refused the session.
        """
        if self._session is not None:
            message = f"the lock on {self.key} is already held"
            raise RuntimeError(message)

        session = _Session.open(
            self._sessions, self.key, ttl=self._ttl, lock_delay=self._lock_delay
        )

        try:
            self.lock_index = self._acquire(session.id, _Deadline(self._timeout))
        except BaseException:
            session.close()
            raise

        self._session = session
        self.lost = session.lost

        logger.info("consul lock acquired - key=%s lock_index=%s", self.key, self.lock_index)

        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        session, self._session, self.lock_index = self._session, None, None

        if session is None:
            return

        try:
            _ = self._kv.put(self.key, self._value, release=session.id)
        except ConsulError as error:
            _release_failed(self.key, error)

        session.close()

        logger.info("consul lock released - key=%s", self.key)

    def _acquire(self, session_id: str, deadline: _Deadline) -> int:
        """Wait until the key is free, take it, and return its lock index."""
        query = _key_query(self.key, deadline)
        entry = _first(query.receive(self._transport.send(query.request())))

        while True:
            if entry is None or entry.session is None:
                if self._kv.put(self.key, self._value, acquire=session_id):
                    return _lock_index(self._kv.get_entry(self.key))

                deadline.check(self.key)
                time.sleep(deadline.bound(RETRY_INTERVAL).total_seconds())
                entry = self._kv.get_entry(self.key)
            else:
                deadline.check(self.key)
                changed = query.receive(self._transport.send(query.request()))

                if changed is not None:
                    entry = changed[0]


class AsyncLock:
    """A lock on a key, like :class:`Lock`, renewed from a task.

    Attributes:
        key: Key of the lock.
        lock_index: See :attr:`Lock.lock_index`.
        lost: See :attr:`Lock.lost`.
    """

    key: str
    lock_index: int | None
    lost: anyio.Event

    def __init__(  # noqa: PLR0913  # the options of the lock, given by AsyncConsul.lock
        self,
        kv: AsyncKV,
        sessions: AsyncSessions,
        transport: AsyncTransport,
        key: str,
        *,
        value: bytes | str,
        ttl: timedelta,
        lock_delay: timedelta | None,
        timeout: timedelta | None,
    ) -> None:
        self.key = key
        self.lock_index = None
        self.lost = anyio.Event()

        self._kv: AsyncKV = kv
        self._sessions: AsyncSessions = sessions
        self._transport: AsyncTransport = transport
        self._value: bytes | str = value
        self._ttl: timedelta = ttl
        self._lock_delay: timedelta | None = lock_delay
        self._timeout: timedelta | None = timeout
        self._session: _AsyncSession | None = None

    @property
    def session_id(self) -> str | None:
        """Session holding the lock, ``None`` while not held."""
        return None if self._session is None else self._session.id

    async def __aenter__(self) -> Self:
        """See :meth:`Lock.__enter__`."""
        if self._session is not None:
            message = f"the lock on {self.key} is already held"
            raise RuntimeError(message)

        session = await _AsyncSession.open(
            self._sessions, self.key, ttl=self._ttl, lock_delay=self._lock_delay
        )

        try:
            self.lock_index = await self._acquire(session.id, _Deadline(self._timeout))
        except BaseException:
            await session.close()
            raise

        self._session = session
        self.lost = session.lost

        logger.info("consul lock acquired - key=%s lock_index=%s", self.key, self.lock_index)

        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        session, self._session, self.lock_index = self._session, None, None

        if session is None:
            return

        try:
            _ = await self._kv.put(self.key, self._value, release=session.id)
        except ConsulError as error:
            _release_failed(self.key, error)

        await session.close()

        logger.info("consul lock released - key=%s", self.key)

    async def _acquire(self, session_id: str, deadline: _Deadline) -> int:
        """See :meth:`Lock._acquire`."""
        query = _key_query(self.key, deadline)
        entry = _first(query.receive(await self._transport.send(query.request())))

        while True:
            if entry is None or entry.session is None:
                if await self._kv.put(self.key, self._value, acquire=session_id):
                    return _lock_index(await self._kv.get_entry(self.key))

                deadline.check(self.key)
                await anyio.sleep(deadline.bound(RETRY_INTERVAL).total_seconds())
                entry = await self._kv.get_entry(self.key)
            else:
                deadline.check(self.key)
                changed = query.receive(await self._transport.send(query.request()))

                if changed is not None:
                    entry = changed[0]


class _Session:
    """The session of a :class:`Lock`: created, renewed from a thread, then ended."""

    def __init__(self, sessions: Sessions, key: str, session_id: str, ttl: timedelta) -> None:
        self.id: str = session_id
        self.lost: threading.Event = threading.Event()

        self._sessions: Sessions = sessions
        self._key: str = key
        self._clock: _RenewalClock = _RenewalClock(ttl)
        self._stopped: threading.Event = threading.Event()
        self._thread: threading.Thread = threading.Thread(
            target=self._renew, name=f"consul session {session_id}", daemon=True
        )

    @classmethod
    def open(
        cls,
        sessions: Sessions,
        key: str,
        *,
        ttl: timedelta,
        lock_delay: timedelta | None,
    ) -> _Session:
        """Create the session, and start renewing it."""
        session = cls(sessions, key, sessions.create(name=key, ttl=ttl, lock_delay=lock_delay), ttl)
        session._thread.start()

        return session

    def close(self) -> None:
        """Stop renewing the session, and end it."""
        self._stopped.set()
        self._thread.join()

        try:
            self._sessions.destroy(self.id)
        except ConsulError as error:
            _end_failed(self._key, error)

    def _renew(self) -> None:
        while not self._stopped.wait(self._clock.interval):
            try:
                alive = self._sessions.renew(self.id)
            except ConsulError as error:
                alive = self._clock.failed(self._key, error)
            else:
                self._clock.renewed()

            if not alive:
                _lost(self._key)
                self.lost.set()
                return


class _AsyncSession:
    """The session of an :class:`AsyncLock`, like :class:`_Session`, renewed from a task."""

    def __init__(self, sessions: AsyncSessions, key: str, session_id: str, ttl: timedelta) -> None:
        self.id: str = session_id
        self.lost: anyio.Event = anyio.Event()

        self._sessions: AsyncSessions = sessions
        self._key: str = key
        self._clock: _RenewalClock = _RenewalClock(ttl)
        self._renewal: AsyncExitStack = AsyncExitStack()

    @classmethod
    async def open(
        cls,
        sessions: AsyncSessions,
        key: str,
        *,
        ttl: timedelta,
        lock_delay: timedelta | None,
    ) -> _AsyncSession:
        """See :meth:`_Session.open`."""
        session_id = await sessions.create(name=key, ttl=ttl, lock_delay=lock_delay)
        session = cls(sessions, key, session_id, ttl)

        # close() leaves the task group in the same task: the one entering and leaving the lock.
        task_group = await session._renewal.enter_async_context(anyio.create_task_group())
        _ = session._renewal.callback(task_group.cancel_scope.cancel)
        _ = task_group.start_soon(session._renew)

        return session

    async def close(self) -> None:
        """See :meth:`_Session.close`."""
        await self._renewal.aclose()

        try:
            await self._sessions.destroy(self.id)
        except ConsulError as error:
            _end_failed(self._key, error)

    async def _renew(self) -> None:
        while True:
            await anyio.sleep(self._clock.interval)

            try:
                alive = await self._sessions.renew(self.id)
            except ConsulError as error:
                alive = self._clock.failed(self._key, error)
            else:
                self._clock.renewed()

            if not alive:
                _lost(self._key)
                self.lost.set()
                return


class _RenewalClock:
    """When to renew a session, and when failed renewals mean it ended."""

    def __init__(self, ttl: timedelta) -> None:
        self.interval: float = ttl.total_seconds() / 2
        self._ttl: float = ttl.total_seconds()
        self._renewed: float = time.monotonic()

    def renewed(self) -> None:
        self._renewed = time.monotonic()

    def failed(self, key: str, error: ConsulError) -> bool:
        """Return whether the session may still exist: not after a whole TTL without renewal."""
        logger.warning("consul lock renewal failed - key=%s error=`%s`", key, error)

        return time.monotonic() - self._renewed < self._ttl


class _Deadline:
    """The end of the ``timeout`` of a lock, ``None`` to wait as long as needed."""

    def __init__(self, timeout: timedelta | None) -> None:
        self._end: float | None = (
            None if timeout is None else time.monotonic() + timeout.total_seconds()
        )

    def check(self, key: str) -> None:
        """Raise when the timeout ran out."""
        if self._end is not None and time.monotonic() >= self._end:
            message = f"the lock on {key} is held by another session"
            raise TimeoutError(message)

    def bound(self, longest: timedelta) -> timedelta:
        """Return ``longest``, or less to stop at the deadline."""
        if self._end is None:
            return longest

        remaining = timedelta(seconds=self._end - time.monotonic())

        return max(min(longest, remaining), _SHORTEST_WAIT)


def _key_query(key: str, deadline: _Deadline) -> BlockingQuery[Entry | None]:
    return BlockingQuery(
        lambda index: kv_requests.get(key, index=index, wait=deadline.bound(DEFAULT_WAIT)),
        kv_requests.parse_entry,
    )


def _first(answer: tuple[Entry | None] | None) -> Entry | None:
    """Return the first answer of a query: always a change."""
    return None if answer is None else answer[0]


def _lock_index(entry: Entry | None) -> int:
    return 0 if entry is None else entry.lock_index


def _lost(key: str) -> None:
    logger.warning("consul lock lost, its session ended - key=%s", key)


def _release_failed(key: str, error: ConsulError) -> None:
    logger.warning(
        "consul lock release failed, it ends with its session - key=%s error=`%s`", key, error
    )


def _end_failed(key: str, error: ConsulError) -> None:
    logger.warning(
        "consul session end failed, it expires after its TTL - key=%s error=`%s`", key, error
    )
