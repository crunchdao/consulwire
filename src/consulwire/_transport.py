"""Send requests to the agent, synchronously or not.

The sections (``agent``, later ``kv``) only describe their requests as :class:`Request` values, so
the same description is sent by both transports, and the sync and async clients cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypeAlias

import httpx

from consulwire.errors import HTTPStatusError, TransportError

if TYPE_CHECKING:
    from collections.abc import Mapping

Payload: TypeAlias = "dict[str, object]"
"""A JSON object of the agent API, with Consul's own field names."""


@dataclass(frozen=True, slots=True)
class Request:
    method: str
    path: str
    parameters: Mapping[str, str] = field(default_factory=dict[str, str])
    payload: Payload | None = None


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
            )
        except httpx.TransportError as error:
            raise _unreachable(self.url, error) from error

        return _checked(request, response)

    async def close(self) -> None:
        await self._http_client.aclose()


def _headers(token: str | None) -> dict[str, str]:
    return {"X-Consul-Token": token} if token is not None else {}


def _unreachable(url: str, error: httpx.TransportError) -> TransportError:
    return TransportError(f"consul agent at {url} could not be reached: {error}")


def _checked(request: Request, response: httpx.Response) -> httpx.Response:
    if response.is_error:
        raise HTTPStatusError(request.method, request.path, response.status_code, response.text)

    return response
