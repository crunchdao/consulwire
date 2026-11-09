"""The key/value section: read, write and watch the keys of Consul's store.

Reached through :attr:`consulwire.Consul.kv` and :attr:`consulwire.AsyncConsul.kv`. Values are raw
bytes: the application decides their format.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from consulwire._blocking import DEFAULT_WAIT, BlockingQuery, follow, follow_async
from consulwire.kv import _requests
from consulwire.kv._entry import Entry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
    from datetime import timedelta

    from consulwire._transport import AsyncTransport, Transport

__all__ = ["KV", "AsyncKV", "Entry"]


class KV:
    """Key/value API of :class:`consulwire.Consul`."""

    def __init__(self, transport: Transport) -> None:
        self._transport: Transport = transport

    def get(self, key: str) -> bytes | None:
        """Return the value of a key, or ``None`` when the key does not exist.

        Raises:
            TransportError: The agent could not be reached.
            HTTPStatusError: The agent refused the request, like 403 for a missing ACL permission.
            ResponseError: The agent answered something else than an entry.
        """
        entry = self.get_entry(key)

        return None if entry is None else entry.value

    def get_entry(self, key: str) -> Entry | None:
        """Return a key with its indexes, ``None`` when missing. Raises like :meth:`get`."""
        return _requests.parse_entry(self._transport.send(_requests.get(key)))

    def entries(self, prefix: str = "") -> list[Entry]:
        """Return every key starting with the prefix, sorted. Raises like :meth:`get`.

        Args:
            prefix: Start of the keys, like ``my-service/``. Every key of the store by default.
        """
        return _requests.parse_entries(self._transport.send(_requests.entries(prefix)))

    def keys(self, prefix: str = "", *, separator: str | None = None) -> list[str]:
        """Return the names of the keys starting with the prefix, sorted. Raises like :meth:`get`.

        Args:
            prefix: Start of the keys. Every key of the store by default.
            separator: Stop each name after this separator, like a folder listing: with ``/``,
                ``my-service/database/url`` is listed as ``my-service/database/`` under the prefix
                ``my-service/``.
        """
        request = _requests.keys(prefix, separator=separator)

        return _requests.parse_keys(self._transport.send(request))

    def put(  # noqa: PLR0913  # the options of a Consul write
        self,
        key: str,
        value: bytes | str,
        *,
        flags: int | None = None,
        cas: int | None = None,
        acquire: str | None = None,
        release: str | None = None,
    ) -> bool:
        """Write a key, creating it when it does not exist.

        Args:
            key: Full key.
            value: Value, stored as given. Text is stored in UTF-8.
            flags: Number to attach to the key, for the application's own use.
            cas: Check-and-set: write only when the key's ``modify_index`` is still this one. ``0``
                writes only when the key does not exist.
            acquire: Session ID: write and lock the key for this session, only when no other
                session holds it. :meth:`consulwire.Consul.lock` does it for you.
            release: Session ID: write and unlock the key, only when this session holds it.

        Returns:
            Whether the key was written: ``False`` when ``cas`` did not match, or the lock is held
            by another session.

        Raises:
            ValueError: Both ``acquire`` and ``release`` are given.
            TransportError: The agent could not be reached.
            HTTPStatusError: The agent refused the request, like 413 for a value over 512 KB, or
                500 for a session that does not exist.
            ResponseError: The agent answered something else than ``true`` or ``false``.
        """
        request = _requests.put(key, value, flags=flags, cas=cas, acquire=acquire, release=release)

        return _requests.parse_boolean(self._transport.send(request))

    def delete(self, key: str, *, cas: int | None = None) -> bool:
        """Delete a key. Deleting a missing key succeeds. Raises like :meth:`put`.

        Args:
            key: Full key.
            cas: Check-and-set: delete only when the key's ``modify_index`` is still this one.

        Returns:
            Whether the key was deleted: ``False`` only when ``cas`` did not match.
        """
        request = _requests.delete(key, cas=cas)

        return _requests.parse_boolean(self._transport.send(request))

    def delete_prefix(self, prefix: str) -> None:
        """Delete every key starting with the prefix.

        Raises:
            ValueError: The prefix is empty, which would delete every key of the store.
            TransportError: The agent could not be reached.
            HTTPStatusError: The agent refused the request.
        """
        _ = self._transport.send(_requests.delete_prefix(prefix))

    def watch(self, key: str, *, wait: timedelta = DEFAULT_WAIT) -> Iterator[Entry | None]:
        """Yield a key now, then every time it changes, ``None`` while it does not exist.

        Uses Consul's blocking queries: a request stays open until the key changes or ``wait`` runs
        out, so a change arrives within milliseconds without polling. The loop never ends by
        itself, and stops at the first error: retrying is the application's choice.

        Example:
            ```python
            for entry in consul.kv.watch("my-service/feature-flag"):
                enabled = entry is not None and entry.value == b"on"
            ```

        Args:
            key: Full key.
            wait: Longest time the agent holds a request, at most 10 minutes.

        Raises:
            TransportError: The agent could not be reached.
            HTTPStatusError: The agent refused the request.
            ResponseError: The agent answered something else than an entry.
        """
        return follow(
            self._transport,
            BlockingQuery(
                lambda index: _requests.get(key, index=index, wait=wait),
                _requests.parse_entry,
            ),
        )

    def watch_entries(
        self,
        prefix: str = "",
        *,
        wait: timedelta = DEFAULT_WAIT,
    ) -> Iterator[list[Entry]]:
        """Yield the keys starting with the prefix now, then every time one of them changes.

        Works like :meth:`watch`: a key created, written or deleted under the prefix yields the
        whole list again.
        """
        return follow(
            self._transport,
            BlockingQuery(
                lambda index: _requests.entries(prefix, index=index, wait=wait),
                _requests.parse_entries,
            ),
        )


class AsyncKV:
    """Key/value API of :class:`consulwire.AsyncConsul`, the same as :class:`KV`."""

    def __init__(self, transport: AsyncTransport) -> None:
        self._transport: AsyncTransport = transport

    async def get(self, key: str) -> bytes | None:
        """See :meth:`KV.get`."""
        entry = await self.get_entry(key)

        return None if entry is None else entry.value

    async def get_entry(self, key: str) -> Entry | None:
        """See :meth:`KV.get_entry`."""
        return _requests.parse_entry(await self._transport.send(_requests.get(key)))

    async def entries(self, prefix: str = "") -> list[Entry]:
        """See :meth:`KV.entries`."""
        return _requests.parse_entries(await self._transport.send(_requests.entries(prefix)))

    async def keys(self, prefix: str = "", *, separator: str | None = None) -> list[str]:
        """See :meth:`KV.keys`."""
        request = _requests.keys(prefix, separator=separator)

        return _requests.parse_keys(await self._transport.send(request))

    async def put(  # noqa: PLR0913  # the options of a Consul write
        self,
        key: str,
        value: bytes | str,
        *,
        flags: int | None = None,
        cas: int | None = None,
        acquire: str | None = None,
        release: str | None = None,
    ) -> bool:
        """See :meth:`KV.put`."""
        request = _requests.put(key, value, flags=flags, cas=cas, acquire=acquire, release=release)

        return _requests.parse_boolean(await self._transport.send(request))

    async def delete(self, key: str, *, cas: int | None = None) -> bool:
        """See :meth:`KV.delete`."""
        request = _requests.delete(key, cas=cas)

        return _requests.parse_boolean(await self._transport.send(request))

    async def delete_prefix(self, prefix: str) -> None:
        """See :meth:`KV.delete_prefix`."""
        _ = await self._transport.send(_requests.delete_prefix(prefix))

    def watch(self, key: str, *, wait: timedelta = DEFAULT_WAIT) -> AsyncIterator[Entry | None]:
        """See :meth:`KV.watch`, with ``async for``."""
        return follow_async(
            self._transport,
            BlockingQuery(
                lambda index: _requests.get(key, index=index, wait=wait),
                _requests.parse_entry,
            ),
        )

    def watch_entries(
        self,
        prefix: str = "",
        *,
        wait: timedelta = DEFAULT_WAIT,
    ) -> AsyncIterator[list[Entry]]:
        """See :meth:`KV.watch_entries`, with ``async for``."""
        return follow_async(
            self._transport,
            BlockingQuery(
                lambda index: _requests.entries(prefix, index=index, wait=wait),
                _requests.parse_entries,
            ),
        )
