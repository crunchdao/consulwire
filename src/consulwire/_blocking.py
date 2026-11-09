"""Blocking queries: requests the agent holds until the data changes, shared by every section."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Final, Generic, TypeVar, cast

from consulwire._duration import format_duration
from consulwire._responses import malformed

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator

    import httpx

    from consulwire._transport import AsyncTransport, Request, Transport

DEFAULT_WAIT: Final = timedelta(minutes=5)
"""Longest time the agent holds a watch query before answering unchanged data."""

_T = TypeVar("_T")


def blocking_parameters(index: int, wait: timedelta | None) -> dict[str, str]:
    """Return the parameters making a read wait for a change after ``index``, if ``wait`` is set."""
    if wait is None:
        return {}

    return {"index": str(index), "wait": format_duration(wait)}


def _parse_index(response: httpx.Response) -> int:
    index = cast("str | None", response.headers.get("X-Consul-Index"))

    if index is None:
        raise malformed(response, "no X-Consul-Index header")

    if not index.isdigit():
        raise malformed(response, "an X-Consul-Index header that is not a number")

    return int(index)


class BlockingQuery(Generic[_T]):
    """Index bookkeeping of a watch, shared by both clients.

    Consul holds a blocking query until the data changes or ``wait`` runs out, then answers the
    current data either way. Only changes are reported: the first answer, then each different one.
    """

    def __init__(
        self,
        request: Callable[[int], Request],
        parse: Callable[[httpx.Response], _T],
    ) -> None:
        self._request: Callable[[int], Request] = request
        self._parse: Callable[[httpx.Response], _T] = parse
        self._index: int = 0
        self._last: tuple[_T] | None = None

    def request(self) -> Request:
        return self._request(self._index)

    def receive(self, response: httpx.Response) -> tuple[_T] | None:
        """Return the data in a tuple when it changed, ``None`` otherwise."""
        index = _parse_index(response)

        # Following Consul's guidance: an index going backwards means the agent's state was reset,
        # so start over, and an index of 0 would make the next query return at once.
        self._index = 0 if index < self._index else max(index, 1)

        data = self._parse(response)

        if self._last is not None and self._last[0] == data:
            return None

        self._last = (data,)

        return self._last


def follow(transport: Transport, query: BlockingQuery[_T]) -> Iterator[_T]:
    """Send the query again and again, yielding the data each time it changed."""
    while True:
        changed = query.receive(transport.send(query.request()))

        if changed is not None:
            yield changed[0]


async def follow_async(transport: AsyncTransport, query: BlockingQuery[_T]) -> AsyncIterator[_T]:
    """See :func:`follow`."""
    while True:
        changed = query.receive(await transport.send(query.request()))

        if changed is not None:
            yield changed[0]
