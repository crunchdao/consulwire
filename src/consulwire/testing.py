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

    Instances pass their checks until told otherwise, to test how a caller handles a failing one:

    ```python
    agent.set_passing("scoring-service-1", passing=False)
    ```

    An instance with a TTL check starts critical, like in Consul, until it reports with
    ``consul.agent.pass_check()``. The time to live itself never runs out: a test reports the
    status it needs.
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

__all__ = ["AGENT_URL", "NODE", "NODE_ADDRESS", "InMemoryAgent"]

AGENT_URL: Final = "http://127.0.0.1:8500"
"""URL of the clients the agent creates. Nothing is sent to it: the requests stay in memory."""

NODE: Final = "in-memory"
"""Name of the node every service is registered on."""

NODE_ADDRESS: Final = "127.0.0.1"
"""Address of the node, used for the instances registered without an address."""

_KV_PATH: Final = "/v1/kv/"

_DEREGISTER_PATH: Final = "/v1/agent/service/deregister/"

_CHECK_PATH: Final = "/v1/agent/check/"

_REPORTED_STATUSES: Final = {"pass": "passing", "warn": "warning", "fail": "critical"}

_CATALOG_SERVICE_PATH: Final = "/v1/catalog/service/"

_HEALTH_SERVICE_PATH: Final = "/v1/health/service/"

_POLL_INTERVAL: Final = 0.005
"""Seconds between two looks at the store while an asynchronous blocking query waits."""


class InMemoryAgent:
    """Answer the requests of consulwire's clients like a Consul agent, from memory.

    Supports registering and deregistering services, reporting their TTL checks, listing them
    through the catalog and health endpoints, and the key/value store with check-and-set. Reads
    block like Consul's blocking queries. Each instance has one status, the one of its checks.
    Every request is recorded in :attr:`requests`. Other endpoints answer 404. Thread-safe: a
    watch can wait in one thread while another writes.
    """

    requests: list[httpx.Request]
    """Every request received, in order."""

    def __init__(self) -> None:
        self.requests = []

        self._condition: threading.Condition = threading.Condition()
        self._index: int = 1
        self._services: dict[str, dict[str, object]] = {}
        self._statuses: dict[str, str] = {}
        self._checks: dict[str, str] = {}
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

    def set_passing(self, service_id: str, *, passing: bool) -> None:
        """Make an instance pass or fail its checks, which wakes the watches of its service.

        Raises:
            LookupError: No instance has this ID.
        """
        with self._condition:
            if service_id not in self._services:
                message = f"no instance has the ID {service_id!r}"
                raise LookupError(message)

            self._statuses[service_id] = "passing" if passing else "critical"
            self._changed()

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
                _ = self._condition.wait_for(lambda: self._changed_since(index), timeout=wait)

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

    def _changed(self) -> None:
        self._index += 1
        self._condition.notify_all()

    def _respond(self, request: httpx.Request) -> httpx.Response:
        parameters = dict(request.url.params)

        for respond in (
            self._respond_kv,
            self._respond_agent,
            self._respond_check,
            self._respond_discovery,
        ):
            response = respond(request, parameters)

            if response is not None:
                return response

        return httpx.Response(
            404, text=f"the in-memory agent does not support {request.method} {request.url.path}"
        )

    def _read(self, body: object) -> httpx.Response:
        """Answer a read with the index of the agent, ``404`` when there is nothing to answer."""
        headers = {"X-Consul-Index": str(self._index)}

        if body is None:
            return httpx.Response(404, headers=headers)

        return httpx.Response(200, headers=headers, json=body)

    def _respond_agent(
        self,
        request: httpx.Request,
        _parameters: Mapping[str, str],
    ) -> httpx.Response | None:
        path = request.url.path

        if request.method != "PUT":
            return None

        if path == "/v1/agent/service/register":
            service = _json_object(request.content)
            service_id = str(service.get("ID") or service.get("Name"))
            checks = _checks(service)

            self._deregister(service_id)
            self._services[service_id] = service
            self._statuses[service_id] = (
                "critical" if any("TTL" in check for check in checks) else "passing"
            )
            self._checks.update(
                (_check_id(service_id, check, number, len(checks)), service_id)
                for number, check in enumerate(checks, start=1)
            )
            self._changed()

            return httpx.Response(200)

        if path.startswith(_DEREGISTER_PATH):
            service_id = unquote(path.removeprefix(_DEREGISTER_PATH))

            if service_id not in self._services:
                return httpx.Response(404, text=f'Unknown service ID "{service_id}"')

            self._deregister(service_id)
            self._changed()

            return httpx.Response(200)

        return None

    def _respond_check(
        self,
        request: httpx.Request,
        _parameters: Mapping[str, str],
    ) -> httpx.Response | None:
        path = request.url.path

        if request.method != "PUT" or not path.startswith(_CHECK_PATH):
            return None

        report, _, quoted_check_id = path.removeprefix(_CHECK_PATH).partition("/")
        status = _REPORTED_STATUSES.get(report)

        if status is None:
            return None

        check_id = unquote(quoted_check_id)
        owner = self._checks.get(check_id)

        if owner is None:
            return httpx.Response(404, text=f'Unknown check ID "{check_id}"')

        self._statuses[owner] = status
        self._changed()

        return httpx.Response(200)

    def _deregister(self, service_id: str) -> None:
        """Forget an instance and its checks, if registered."""
        _ = self._services.pop(service_id, None)
        _ = self._statuses.pop(service_id, None)

        for check_id in [check for check, owner in self._checks.items() if owner == service_id]:
            del self._checks[check_id]

    def _respond_discovery(
        self,
        request: httpx.Request,
        parameters: Mapping[str, str],
    ) -> httpx.Response | None:
        path = request.url.path

        if request.method != "GET":
            return None

        if path == "/v1/catalog/services":
            tags: dict[str, set[str]] = {}

            for service in self._services.values():
                tags.setdefault(str(service.get("Name")), set()).update(_tags(service))

            return self._read({name: sorted(found) for name, found in sorted(tags.items())})

        if path.startswith(_CATALOG_SERVICE_PATH):
            name = unquote(path.removeprefix(_CATALOG_SERVICE_PATH))
            instances = self._instances(name, parameters.get("tag"))

            return self._read(
                [_catalog_payload(service_id, service) for service_id, service in instances]
            )

        if path.startswith(_HEALTH_SERVICE_PATH):
            name = unquote(path.removeprefix(_HEALTH_SERVICE_PATH))
            instances = [
                (service_id, service)
                for service_id, service in self._instances(name, parameters.get("tag"))
                if "passing" not in parameters or self._statuses[service_id] == "passing"
            ]

            return self._read(
                [
                    _health_payload(service_id, service, self._statuses[service_id])
                    for service_id, service in instances
                ]
            )

        return None

    def _instances(self, name: str, tag: str | None) -> list[tuple[str, dict[str, object]]]:
        return [
            (service_id, service)
            for service_id, service in sorted(self._services.items())
            if service.get("Name") == name and (tag is None or tag in _tags(service))
        ]

    def _respond_kv(
        self,
        request: httpx.Request,
        parameters: Mapping[str, str],
    ) -> httpx.Response | None:
        path = request.url.path

        if not path.startswith(_KV_PATH):
            return None

        key = unquote(path.removeprefix(_KV_PATH))

        if request.method == "GET":
            return self._read(self._kv_body(key, parameters) or None)

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

    def _kv_body(self, key: str, parameters: Mapping[str, str]) -> list[object]:
        if "keys" in parameters:
            return list(_key_names(key, sorted(self._entries), parameters.get("separator")))

        if "recurse" in parameters:
            found = [entry for name, entry in sorted(self._entries.items()) if name.startswith(key)]
        else:
            found = [self._entries[key]] if key in self._entries else []

        return [_entry_payload(entry) for entry in found]

    def _write(self, key: str, value: bytes, flags: int, current: Entry | None) -> None:
        self._changed()

        self._entries[key] = Entry(
            key=key,
            value=value,
            flags=flags,
            modify_index=self._index,
            create_index=self._index if current is None else current.create_index,
            lock_index=0,
            session=None,
        )

    def _delete(self, key: str, *, recurse: bool) -> None:
        self._changed()

        for name in list(self._entries):
            if name == key or (recurse and name.startswith(key)):
                del self._entries[name]


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


def _tags(service: Mapping[str, object]) -> list[str]:
    return cast("list[str]", service.get("Tags") or [])


def _service_payload(service_id: str, service: Mapping[str, object]) -> dict[str, object]:
    """Return the instance as the health endpoint describes it."""
    return {
        "ID": service_id,
        "Service": service.get("Name"),
        "Address": service.get("Address", ""),
        "Port": service.get("Port", 0),
        "Tags": _tags(service),
        "Meta": service.get("Meta") or {},
    }


def _catalog_payload(service_id: str, service: Mapping[str, object]) -> dict[str, object]:
    """Return the instance as the catalog describes it: the same fields, prefixed by Service."""
    fields = _service_payload(service_id, service)

    return {
        "Node": NODE,
        "Address": NODE_ADDRESS,
        "ServiceID": fields.pop("ID"),
        "ServiceName": fields.pop("Service"),
        **{f"Service{name}": value for name, value in fields.items()},
    }


def _health_payload(
    service_id: str,
    service: Mapping[str, object],
    status: str,
) -> dict[str, object]:
    checks = _checks(service) or [{}]

    return {
        "Node": {"Node": NODE, "Address": NODE_ADDRESS},
        "Service": _service_payload(service_id, service),
        "Checks": [
            {
                "CheckID": _check_id(service_id, check, number, len(checks)),
                "ServiceID": service_id,
                "Status": status,
            }
            for number, check in enumerate(checks, start=1)
        ],
    }


def _checks(service: Mapping[str, object]) -> list[dict[str, object]]:
    return cast("list[dict[str, object]]", service.get("Checks") or [])


def _check_id(service_id: str, check: Mapping[str, object], number: int, count: int) -> str:
    """Return the ID Consul gives a check: its own, or one after its service and position."""
    given = check.get("CheckID")
    if isinstance(given, str):
        return given

    return f"service:{service_id}" if count == 1 else f"service:{service_id}:{number}"


def _json_object(content: bytes) -> dict[str, object]:
    body = cast("object", json.loads(content))

    if not isinstance(body, dict):
        message = f"expected a JSON object, not {content!r}"
        raise TypeError(message)

    return cast("dict[str, object]", body)
