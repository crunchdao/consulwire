from __future__ import annotations

import queue
import threading
from datetime import timedelta
from typing import TYPE_CHECKING

import anyio
import pytest

from consulwire import ResponseError
from consulwire.catalog import ServiceInstance
from consulwire.testing import NODE, NODE_ADDRESS, InMemoryAgent
from tests.support import WATCH_WAIT, answering

if TYPE_CHECKING:
    from consulwire import Consul

SCORING = ServiceInstance(
    id="scoring-service-1",
    name="scoring-service",
    address="10.0.0.5",
    port=8005,
    tags=("internal",),
    meta={"version": "1.2.0"},
    node="node-1",
)


def catalog_entry(**overrides: object) -> dict[str, object]:
    return {
        "Node": "node-1",
        "Address": "10.0.0.2",
        "ServiceID": "scoring-service-1",
        "ServiceName": "scoring-service",
        "ServiceAddress": "10.0.0.5",
        "ServicePort": 8005,
        "ServiceTags": ["internal"],
        "ServiceMeta": {"version": "1.2.0"},
        **overrides,
    }


def health_entry(**service_overrides: object) -> dict[str, object]:
    return {
        "Node": {"Node": "node-1", "Address": "10.0.0.2"},
        "Service": {
            "ID": "scoring-service-1",
            "Service": "scoring-service",
            "Address": "10.0.0.5",
            "Port": 8005,
            "Tags": ["internal"],
            "Meta": {"version": "1.2.0"},
            **service_overrides,
        },
        "Checks": [{"CheckID": "serfHealth", "Status": "passing"}],
    }


# --------------------------------------------------------------------------- the model


@pytest.mark.parametrize(
    ("address", "url"),
    [("10.0.0.5", "http://10.0.0.5:8005/score"), ("fd00::5", "http://[fd00::5]:8005/score")],
    ids=["IPv4", "IPv6"],
)
def test_the_url_of_an_instance(address: str, url: str) -> None:
    instance = ServiceInstance(
        id="a", name="a", address=address, port=8005, tags=(), meta={}, node="n"
    )

    assert instance.url("/score") == url
    assert instance.url(scheme="https").startswith("https://")


def test_instances_are_hashable() -> None:
    assert len({SCORING, SCORING}) == 1


# --------------------------------------------------------------------------- the catalog


def test_catalog_services_are_sorted_with_their_tags() -> None:
    agent = answering({"web": ["v1"], "consul": []})

    assert agent.consul().catalog.services() == {"consul": [], "web": ["v1"]}
    assert agent.calls() == [("GET", "/v1/catalog/services")]


def test_catalog_service_reads_every_field() -> None:
    agent = answering([catalog_entry()])

    instances = agent.consul().catalog.service("scoring service", tag="internal")

    assert instances == [SCORING]
    assert agent.requests[0].url.raw_path == b"/v1/catalog/service/scoring%20service?tag=internal"


def test_catalog_service_falls_back_to_the_node_address() -> None:
    agent = answering([catalog_entry(ServiceAddress="", ServiceTags=None, ServiceMeta=None)])

    [instance] = agent.consul().catalog.service("scoring-service")

    assert (instance.address, instance.tags, instance.meta) == ("10.0.0.2", (), {})
    assert agent.requests[0].url.query == b""


# --------------------------------------------------------------------------- the health


def test_health_service_asks_for_the_passing_instances() -> None:
    agent = answering([health_entry()])

    instances = agent.consul().health.service("scoring-service", tag="internal")

    assert instances == [SCORING]
    assert agent.calls() == [("GET", "/v1/health/service/scoring-service")]
    assert dict(agent.requests[0].url.params) == {"passing": "true", "tag": "internal"}


def test_health_service_can_list_every_instance() -> None:
    agent = answering([health_entry(Address="")])

    [instance] = agent.consul().health.service("scoring-service", passing=False)

    assert instance.address == "10.0.0.2"
    assert agent.requests[0].url.query == b""


def test_a_watch_is_a_blocking_query() -> None:
    agent = answering([health_entry()])

    instances = next(agent.consul().health.watch_service("scoring-service", wait=WATCH_WAIT))

    assert instances == [SCORING]
    assert dict(agent.requests[0].url.params) == {"passing": "true", "index": "0", "wait": "5s"}


# --------------------------------------------------------------------------- malformed answers


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        (["not an object"], "an instance that is not an object"),
        ([catalog_entry(ServiceID=1)], "`ServiceID` that is not text"),
        ([catalog_entry(ServicePort="8005")], "`ServicePort` that is not a number"),
        ([catalog_entry(ServicePort=True)], "`ServicePort` that is not a number"),
        ([catalog_entry(ServiceTags="internal")], "`ServiceTags` that is not a list of texts"),
        ([catalog_entry(ServiceTags=[1])], "`ServiceTags` that is not a list of texts"),
        ([catalog_entry(ServiceMeta=["version"])], "`ServiceMeta` that is not an object of texts"),
        ([catalog_entry(ServiceMeta={"v": 1})], "`ServiceMeta` that is not an object of texts"),
        ({"not": "a list"}, "a body that is not a list"),
    ],
    ids=[
        "an entry",
        "an ID",
        "a port",
        "a boolean port",
        "tags",
        "a tag",
        "meta",
        "a meta value",
        "a list",
    ],
)
def test_malformed_catalog_instances_are_response_errors(body: object, problem: str) -> None:
    with pytest.raises(ResponseError) as raised:
        _ = answering(body).consul().catalog.service("scoring-service")

    assert (
        str(raised.value)
        == f"consul answered GET /v1/catalog/service/scoring-service with {problem}"
    )


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        (["consul"], "a body that is not an object"),
        ({"consul": "no tags"}, "`consul` that is not a list of texts"),
    ],
    ids=["a list", "tags"],
)
def test_malformed_catalog_services_are_response_errors(body: object, problem: str) -> None:
    with pytest.raises(ResponseError, match=problem):
        _ = answering(body).consul().catalog.services()


def test_nested_fields_are_named_with_their_parent() -> None:
    body = [health_entry(Port="8005")]

    with pytest.raises(ResponseError, match=r"with `Service\.Port` that is not a number"):
        _ = answering(body).consul().health.service("scoring-service")


def test_a_health_entry_without_its_service_is_a_response_error() -> None:
    entry = health_entry()
    del entry["Service"]

    with pytest.raises(ResponseError, match="with `Service` that is not an object"):
        _ = answering([entry]).consul().health.service("scoring-service")


# --------------------------------------------------------------------------- the in-memory agent


@pytest.fixture
def agent() -> InMemoryAgent:
    return InMemoryAgent()


@pytest.fixture
def consul(agent: InMemoryAgent) -> Consul:
    consul = agent.consul()
    consul.agent.register_service("scoring-service", service_id="scoring-1", port=8005, tags=["a"])
    consul.agent.register_service(
        "scoring-service", service_id="scoring-2", address="10.0.0.6", port=8005, tags=["b"]
    )
    consul.agent.register_service("notebook-processor-service", port=8004)

    return consul


def test_the_in_memory_catalog(consul: Consul) -> None:
    assert consul.catalog.services() == {
        "notebook-processor-service": [],
        "scoring-service": ["a", "b"],
    }
    assert [instance.address for instance in consul.catalog.service("scoring-service")] == [
        NODE_ADDRESS,
        "10.0.0.6",
    ]
    assert [instance.id for instance in consul.catalog.service("scoring-service", tag="b")] == [
        "scoring-2",
    ]
    assert consul.catalog.service("missing") == []


def test_the_in_memory_health(agent: InMemoryAgent, consul: Consul) -> None:
    agent.set_passing("scoring-1", passing=False)

    assert [instance.id for instance in consul.health.service("scoring-service")] == ["scoring-2"]
    assert len(consul.health.service("scoring-service", passing=False)) == 2
    assert len(consul.catalog.service("scoring-service")) == 2
    assert consul.health.service("scoring-service")[0].node == NODE

    agent.set_passing("scoring-1", passing=True)

    assert len(consul.health.service("scoring-service")) == 2


def test_registering_again_passes_again(agent: InMemoryAgent, consul: Consul) -> None:
    agent.set_passing("scoring-1", passing=False)

    consul.agent.register_service("scoring-service", service_id="scoring-1", port=8005)

    assert len(consul.health.service("scoring-service")) == 2


def test_an_unknown_instance_cannot_fail(agent: InMemoryAgent) -> None:
    with pytest.raises(LookupError, match="no instance has the ID 'missing'"):
        agent.set_passing("missing", passing=False)


def test_a_watch_follows_the_instances(agent: InMemoryAgent, consul: Consul) -> None:
    seen: queue.Queue[list[str]] = queue.Queue()

    def watch() -> None:
        for instances in agent.consul().health.watch_service("scoring-service", wait=WATCH_WAIT):
            seen.put([instance.id for instance in instances])

            if not instances:
                return

    watcher = threading.Thread(target=watch)
    watcher.start()

    assert seen.get(timeout=5) == ["scoring-1", "scoring-2"]

    agent.set_passing("scoring-1", passing=False)
    assert seen.get(timeout=5) == ["scoring-2"]

    consul.agent.register_service("notebook-processor-service", port=8004)  # another service
    consul.agent.deregister_service("scoring-2")
    assert seen.get(timeout=5) == []

    watcher.join(5)
    assert seen.empty()


@pytest.mark.anyio
async def test_the_async_sections(agent: InMemoryAgent) -> None:
    async with agent.async_consul() as consul:
        await consul.agent.register_service("scoring-service", port=8005)

        assert await consul.catalog.services() == {"scoring-service": []}
        assert [instance.port for instance in await consul.catalog.service("scoring-service")] == [
            8005
        ]
        assert len(await consul.health.service("scoring-service")) == 1

        seen: list[int] = []
        watching = anyio.Event()

        async def watch() -> None:
            async for instances in consul.health.watch_service("scoring-service", wait=WATCH_WAIT):
                seen.append(len(instances))
                watching.set()

                if not instances:
                    return

        with anyio.fail_after(5):
            async with anyio.create_task_group() as group:
                _ = group.start_soon(watch)

                await watching.wait()
                agent.set_passing("scoring-service", passing=False)

        assert seen == [1, 0]


def test_a_watch_skips_the_unchanged_answers(agent: InMemoryAgent, consul: Consul) -> None:
    watch = consul.health.watch_service("scoring-service", wait=timedelta(milliseconds=10))
    failing = threading.Timer(0.1, lambda: agent.set_passing("scoring-1", passing=False))

    assert len(next(watch)) == 2

    failing.start()
    instances = next(watch)
    failing.join()

    assert [instance.id for instance in instances] == ["scoring-2"]
    assert len(agent.requests) > 6  # unchanged answers came in between


@pytest.mark.anyio
async def test_an_async_watch_skips_the_unchanged_answers(agent: InMemoryAgent) -> None:
    async with agent.async_consul() as consul:
        await consul.agent.register_service("scoring-service", port=8005)
        watch = consul.health.watch_service("scoring-service", wait=timedelta(milliseconds=10))

        assert len(await anext(watch)) == 1

        async def fail() -> None:
            await anyio.sleep(0.1)
            agent.set_passing("scoring-service", passing=False)

        instances: list[ServiceInstance] | None = None

        async with anyio.create_task_group() as group:
            _ = group.start_soon(fail)
            instances = await anext(watch)

    assert instances == []
    assert len(agent.requests) > 3  # unchanged answers came in between
