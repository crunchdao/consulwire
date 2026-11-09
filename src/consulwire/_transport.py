"""Send requests to the agent, synchronously or not.

The sections only describe their requests as :class:`Request` values, so the same description is
sent by both transports, and the sync and async clients cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, TypeAlias

import httpx

from consulwire.errors import HTTPStatusError, TransportError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import timedelta

Payload: TypeAlias = "dict[str, object]"
"""A JSON object of the agent API, with Consul's own field names."""

NOT_FOUND: Final = 404

BLOCKING_JITTER_DIVISOR: Final = 16
"""Consul adds up to ``wait / 16`` to a blocking query, so clients do not all wake up at once."""


@dataclass(frozen=True, slots=True)
class Request:
    """A request to the agent.

    Attributes:
        payload: JSON body.
        content: Raw body, for the values of the key/value store.
        wait: Longest time the agent holds this blocking query: the transport waits that long on
            top of its timeout.
        allow_not_found: Return a ``404`` answer instead of raising, for lookups of missing keys.
    """

    method: str
    path: str
    parameters: Mapping[str, str] = field(default_factory=dict[str, str])
    payload: Payload | None = None
    content: bytes | None = None
    wait: timedelta | None = None
    allow_not_found: bool = False


class Transport:
    def __init__(
        self,
        url: str,
        *,
        token: str | None,
        timeout: float,
        transport: httpx.BaseTransport | None,
    ) -> None:
        self.url: str = url
        self._timeout: float = timeout
        self._http_client: httpx.Client = httpx.Client(
            base_url=url,
            headers=_headers(token),
            timeout=timeout,
            transport=transport,
        )

    def send(self, request: Request) -> httpx.Response:
        try:
            response = self._http_client.request(
                request.method,
                request.path,
                params=request.parameters,
                json=request.payload,
                content=request.content,
                timeout=_timeout(request, self._timeout),
            )
        except httpx.TransportError as error:
            raise _unreachable(self.url, error) from error

        return _checked(request, response)

    def close(self) -> None:
        self._http_client.close()


class AsyncTransport:
    def __init__(
        self,
        url: str,
        *,
        token: str | None,
        timeout: float,
        transport: httpx.AsyncBaseTransport | None,
    ) -> None:
        self.url: str = url
        self._timeout: float = timeout
        self._http_client: httpx.AsyncClient = httpx.AsyncClient(
            base_url=url,
            headers=_headers(token),
            timeout=timeout,
            transport=transport,
        )

    async def send(self, request: Request) -> httpx.Response:
        try:
            response = await self._http_client.request(
                request.method,
                request.path,
                params=request.parameters,
                json=request.payload,
                content=request.content,
                timeout=_timeout(request, self._timeout),
            )
        except httpx.TransportError as error:
            raise _unreachable(self.url, error) from error

        return _checked(request, response)

    async def close(self) -> None:
        await self._http_client.aclose()


def _headers(token: str | None) -> dict[str, str]:
    return {"X-Consul-Token": token} if token is not None else {}


def _timeout(request: Request, timeout: float) -> float:
    if request.wait is None:
        return timeout

    wait = request.wait.total_seconds()

    return timeout + wait + wait / BLOCKING_JITTER_DIVISOR


def _unreachable(url: str, error: httpx.TransportError) -> TransportError:
    return TransportError(f"consul agent at {url} could not be reached: {error}")


def _checked(request: Request, response: httpx.Response) -> httpx.Response:
    if request.allow_not_found and response.status_code == NOT_FOUND:
        return response

    if response.is_error:
        raise HTTPStatusError(request.method, request.path, response.status_code, response.text)

    return response
