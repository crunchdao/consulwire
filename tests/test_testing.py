from __future__ import annotations

import threading
import time
from datetime import timedelta
from typing import TYPE_CHECKING

import anyio
import httpx
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from consulwire import HTTPStatusError
from consulwire.integrations.fastapi import ConsulLifespan
from consulwire.testing import AGENT_URL, InMemoryAgent

if TYPE_CHECKING:
    from consulwire.kv import Entry


@pytest.fixture
def agent() -> InMemoryAgent:
    return InMemoryAgent()


def test_tests_an_integration_without_consul(agent: InMemoryAgent) -> None:
    lifespan = ConsulLifespan(name="my-service", port=8000, consul=agent.async_consul())

    with TestClient(Starlette(lifespan=lifespan)):
        assert agent.services["my-service"]["Port"] == 8000

    assert agent.services == {}


def test_services_are_stored_by_instance_id(agent: InMemoryAgent) -> None:
    with agent.consul() as consul:
        consul.agent.register_service("my-service", port=8000)
        consul.agent.register_service("my-service", service_id="my-service-2", port=8001)

    assert agent.services == {
        "my-service": {"Name": "my-service", "Port": 8000},
        "my-service-2": {"Name": "my-service", "ID": "my-service-2", "Port": 8001},
    }
    assert [request.url.path for request in agent.requests] == [
        "/v1/agent/service/register",
        "/v1/agent/service/register",
    ]


def test_deregistering_an_unknown_service_is_refused(agent: InMemoryAgent) -> None:
    with pytest.raises(HTTPStatusError, match=r'404 .* Unknown service ID "missing"'):
        agent.consul().agent.deregister_service("missing")


def test_the_store_is_readable(agent: InMemoryAgent) -> None:
    with agent.consul() as consul:
        _ = consul.kv.put("b", "2")
        _ = consul.kv.put("a", b"1")

    assert agent.kv == {"a": b"1", "b": b"2"}


def raw_client(agent: InMemoryAgent) -> httpx.Client:
    return httpx.Client(base_url=AGENT_URL, transport=agent.transport())


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/v1/session/list"),
        ("POST", "/v1/catalog/services"),
        ("PUT", "/v1/agent/check/update/consumer-alive"),
    ],
    ids=["another endpoint", "another method", "another check report"],
)
def test_other_endpoints_answer_not_found(agent: InMemoryAgent, method: str, path: str) -> None:
    with raw_client(agent) as client:
        response = client.request(method, path)

    assert response.status_code == 404
    assert response.text == f"the in-memory agent does not support {method} {path}"


def test_other_methods_on_keys_are_not_allowed(agent: InMemoryAgent) -> None:
    with raw_client(agent) as client:
        assert client.patch("/v1/kv/a").status_code == 405


def test_a_registration_must_be_an_object(agent: InMemoryAgent) -> None:
    with raw_client(agent) as client, pytest.raises(TypeError, match="expected a JSON object"):
        _ = client.put("/v1/agent/service/register", json=["my-service"])


def test_a_blocking_query_answers_unchanged_once_its_wait_runs_out(agent: InMemoryAgent) -> None:
    with raw_client(agent) as client:
        started = time.monotonic()
        response = client.get("/v1/kv/a", params={"index": "1", "wait": "50ms"})

    assert time.monotonic() - started >= 0.05
    assert response.status_code == 404
    assert response.headers["X-Consul-Index"] == "1"


@pytest.mark.anyio
async def test_an_async_blocking_query_answers_unchanged_once_its_wait_runs_out(
    agent: InMemoryAgent,
) -> None:
    transport = agent.async_transport()

    async with httpx.AsyncClient(base_url=AGENT_URL, transport=transport) as client:
        started = time.monotonic()
        response = await client.get("/v1/kv/a", params={"index": "1", "wait": "50ms"})

    assert time.monotonic() - started >= 0.05
    assert response.status_code == 404


def test_a_watch_skips_the_unchanged_answers(agent: InMemoryAgent) -> None:
    watch = agent.consul().kv.watch("a", wait=timedelta(milliseconds=10))
    writer = threading.Timer(0.1, lambda: agent.consul().kv.put("a", "1"))

    assert next(watch) is None

    writer.start()
    entry = next(watch)
    writer.join()

    assert entry is not None
    assert entry.value == b"1"
    assert len(agent.requests) > 3  # unchanged answers came in between


@pytest.mark.anyio
async def test_an_async_watch_skips_the_unchanged_answers(agent: InMemoryAgent) -> None:
    async with agent.async_consul() as consul:
        watch = consul.kv.watch("a", wait=timedelta(milliseconds=10))

        assert await anext(watch) is None

        async def write() -> None:
            await anyio.sleep(0.1)
            _ = await consul.kv.put("a", "1")

        entry: Entry | None = None

        async with anyio.create_task_group() as group:
            _ = group.start_soon(write)
            entry = await anext(watch)

    assert entry is not None
    assert entry.value == b"1"
    assert len(agent.requests) > 3  # unchanged answers came in between
