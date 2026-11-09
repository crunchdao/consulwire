"""An in-memory Consul agent, to test an application and its integration without Consul.

Example:
    ```python
    from consulwire.testing import InMemoryAgent

    agent = InMemoryAgent()
    lifespan = ConsulLifespan(name="my-service", port=8000, consul=agent.async_consul())

    with TestClient(FastAPI(lifespan=lifespan)):
        assert agent.services["my-service"]["Port"] == 8000

    assert agent.services == {}
    ```
"""

from __future__ import annotations

import base64
import json
import threading
import time
from typing import TYPE_CHECKING, Final, cast
from urllib.parse import unquote

import anyio
import httpx

from consulwire._client import AsyncConsul, Consul
from consulwire._duration import parse_duration
from consulwire.kv._entry import Entry

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["AGENT_URL", "InMemoryAgent"]

AGENT_URL: Final = "http://127.0.0.1:8500"
"""URL of the clients the agent creates. Nothing is sent to it: the requests stay in memory."""

_KV_PATH: Final = "/v1/kv/"

_DEREGISTER_PATH: Final = "/v1/agent/service/deregister/"

_POLL_INTERVAL: Final = 0.005
"""Seconds between two looks at the store while an asynchronous blocking query waits."""


class InMemoryAgent:
    """Answer the requests of consulwire's clients like a Consul agent, from memory.

    Supports registering and deregistering services, and the key/value store with check-and-set and
    blocking queries. Every request is recorded in :attr:`requests`. Other endpoints answer 404.
    Thread-safe: a watch can wait in one thread while another writes.
    """

    requests: list[httpx.Request]
    """Every request received, in order."""

    def __init__(self) -> None:
        self.requests = []

        self._condition: threading.Condition = threading.Condition()
        self._index: int = 1
        self._services: dict[str, dict[str, object]] = {}
        self._entries: dict[str, Entry] = {}

    @property
    def services(self) -> dict[str, dict[str, object]]:
        """Registered services by instance ID, with Consul's field names: ``Name``, ``Port``."""
        with self._condition:
            return {service_id: dict(service) for service_id, service in self._services.items()}

    @property
    def kv(self) -> dict[str, bytes]:
        """A copy of the key/value store."""
        with self._condition:
            return {key: entry.value for key, entry in sorted(self._entries.items())}

    def consul(self, *, timeout: float = 10.0) -> Consul:
        """Return a client of this agent."""
        return Consul(AGENT_URL, timeout=timeout, transport=self.transport())

    def async_consul(self, *, timeout: float = 10.0) -> AsyncConsul:
        """Return an asynchronous client of this agent."""
        return AsyncConsul(AGENT_URL, timeout=timeout, transport=self.async_transport())

    def transport(self) -> httpx.MockTransport:
        """Return an HTTP transport for :class:`consulwire.Consul`, or any ``httpx.Client``."""
        return httpx.MockTransport(self._handle)

    def async_transport(self) -> httpx.MockTransport:
        """Return an HTTP transport for :class:`consulwire.AsyncConsul` or ``httpx.AsyncClient``.

        Blocking queries wait without blocking the event loop.
        """
        return httpx.MockTransport(self._handle_async)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        blocking = self._record(request)

        with self._condition:
            if blocking is not None:
                index, wait = blocking
                _ = self._condition.wait_for(lambda: self._index > index, timeout=wait)

            return self._respond(request)

    async def _handle_async(self, request: httpx.Request) -> httpx.Response:
        blocking = self._record(request)

        if blocking is not None:
            index, wait = blocking
            deadline = time.monotonic() + wait

            # Writes come from other threads too, which an anyio.Event does not support.
            while not self._changed_since(index) and time.monotonic() < deadline:  # noqa: ASYNC110
                await anyio.sleep(_POLL_INTERVAL)

        with self._condition:
            return self._respond(request)

    def _record(self, request: httpx.Request) -> tuple[int, float] | None:
        """Record the request, and return the index and seconds a blocking query waits for."""
        with self._condition:
            self.requests.append(request)

        parameters = dict(request.url.params)
        index = parameters.get("index")
        wait = parameters.get("wait")

        if request.method != "GET" or index is None or wait is None:
            return None

        return int(index), parse_duration(wait).total_seconds()

    def _changed_since(self, index: int) -> bool:
        with self._condition:
            return self._index > index

    def _respond(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path

        if path.startswith(_KV_PATH):
            return self._respond_kv(request, unquote(path.removeprefix(_KV_PATH)))

        if request.method == "PUT" and path == "/v1/agent/service/register":
            service = _json_object(request.content)
            name = service.get("Name")

            self._services[str(service.get("ID") or name)] = service

            return httpx.Response(200)

        if request.method == "PUT" and path.startswith(_DEREGISTER_PATH):
            service_id = unquote(path.removeprefix(_DEREGISTER_PATH))

            if self._services.pop(service_id, None) is None:
                return httpx.Response(404, text=f'Unknown service ID "{service_id}"')

            return httpx.Response(200)

        return httpx.Response(
            404, text=f"the in-memory agent does not support {request.method} {path}"
        )

    def _respond_kv(self, request: httpx.Request, key: str) -> httpx.Response:
        parameters = dict(request.url.params)

        if request.method == "GET":
            return self._read(key, parameters)

        cas = parameters.get("cas")
        current = self._entries.get(key)

        if cas is not None and int(cas) != (0 if current is None else current.modify_index):
            return httpx.Response(200, json=False)

        if request.method == "PUT":
            self._write(key, request.content, int(parameters.get("flags", "0")), current)
        elif request.method == "DELETE":
            self._delete(key, recurse="recurse" in parameters)
        else:
            return httpx.Response(405)

        return httpx.Response(200, json=True)

    def _read(self, key: str, parameters: Mapping[str, str]) -> httpx.Response:
        headers = {"X-Consul-Index": str(self._index)}

        if "keys" in parameters:
            names = _key_names(key, sorted(self._entries), parameters.get("separator"))

            if not names:
                return httpx.Response(404, headers=headers)

            return httpx.Response(200, headers=headers, json=names)

        if "recurse" in parameters:
            found = [entry for name, entry in sorted(self._entries.items()) if name.startswith(key)]
        else:
            found = [self._entries[key]] if key in self._entries else []

        if not found:
            return httpx.Response(404, headers=headers)

        return httpx.Response(200, headers=headers, json=[_entry_payload(entry) for entry in found])

    def _write(self, key: str, value: bytes, flags: int, current: Entry | None) -> None:
        self._index += 1

        self._entries[key] = Entry(
            key=key,
            value=value,
            flags=flags,
            modify_index=self._index,
            create_index=self._index if current is None else current.create_index,
            lock_index=0,
            session=None,
        )

        self._condition.notify_all()

    def _delete(self, key: str, *, recurse: bool) -> None:
        self._index += 1

        for name in list(self._entries):
            if name == key or (recurse and name.startswith(key)):
                del self._entries[name]

        self._condition.notify_all()


def _key_names(prefix: str, names: list[str], separator: str | None) -> list[str]:
    listed: list[str] = []

    for name in names:
        if not name.startswith(prefix):
            continue

        if separator:
            end = name.find(separator, len(prefix))
            if end != -1:
                name = name[: end + len(separator)]  # noqa: PLW2901  # the folder of the key

        if name not in listed:
            listed.append(name)

    return listed


def _entry_payload(entry: Entry) -> dict[str, object]:
    return {
        "Key": entry.key,
        "Value": base64.b64encode(entry.value).decode() if entry.value else None,
        "Flags": entry.flags,
        "CreateIndex": entry.create_index,
        "ModifyIndex": entry.modify_index,
        "LockIndex": entry.lock_index,
    }


def _json_object(content: bytes) -> dict[str, object]:
    body = cast("object", json.loads(content))

    if not isinstance(body, dict):
        message = f"expected a JSON object, not {content!r}"
        raise TypeError(message)

    return cast("dict[str, object]", body)
