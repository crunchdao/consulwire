"""Fake Consul agent shared by the tests."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import cast

import httpx

from consulwire import AsyncConsul, Consul

AGENT_URL = "http://consul:8500"

WATCH_WAIT = timedelta(seconds=5)
"""Wait of the watches in tests: long enough to see they block."""


class FakeAgent:
    """In-memory Consul agent: answers every request with the same response, records them all."""

    def __init__(
        self,
        status_code: int = 200,
        body: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.requests: list[httpx.Request] = []
        self.status_code: int = status_code
        self.body: str = body
        self.headers: dict[str, str] = headers or {}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def consul(self, url: str = AGENT_URL, *, token: str | None = None) -> Consul:
        return Consul(url, token=token, transport=self.transport())

    def async_consul(self, url: str = AGENT_URL, *, token: str | None = None) -> AsyncConsul:
        return AsyncConsul(url, token=token, transport=self.transport())

    def sent_json(self) -> object:
        return cast("object", json.loads(self.requests[-1].content))

    def calls(self) -> list[tuple[str, str]]:
        return [(request.method, request.url.path) for request in self.requests]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)

        return httpx.Response(self.status_code, text=self.body, headers=self.headers)


def answering(body: object, *, index: int = 12) -> FakeAgent:
    """Return an agent answering every read with this JSON body, at this index."""
    return FakeAgent(body=json.dumps(body), headers={"X-Consul-Index": str(index)})
