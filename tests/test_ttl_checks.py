from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, cast

import httpx
import pytest

from consulwire import HTTPStatusError
from consulwire.testing import AGENT_URL, InMemoryAgent
from tests.support import FakeAgent

if TYPE_CHECKING:
    from consulwire import Consul
    from consulwire.agent import Check

# --------------------------------------------------------------------------- the requests


def test_a_ttl_check_is_registered_without_interval() -> None:
    agent = FakeAgent()

    agent.consul().agent.register_service(
        "queue-consumer",
        checks=[
            {
                "ttl": timedelta(seconds=30),
                "deregister_critical_after": timedelta(minutes=5),
                "id": "consumer-alive",
                "name": "Consumer loop",
            },
        ],
    )

    assert agent.sent_json() == {
        "Name": "queue-consumer",
        "Checks": [
            {
                "TTL": "30s",
                "DeregisterCriticalServiceAfter": "300s",
                "CheckID": "consumer-alive",
                "Name": "Consumer loop",
            },
        ],
    }


@pytest.mark.parametrize("report", ["pass", "warn", "fail"])
def test_reports_name_the_check(report: str) -> None:
    agent = FakeAgent()
    consul = agent.consul()
    method = {
        "pass": consul.agent.pass_check,
        "warn": consul.agent.warn_check,
        "fail": consul.agent.fail_check,
    }[report]

    method("service:queue consumer", note="42 messages consumed")
    method("consumer-alive")

    first, second = agent.requests
    assert first.method == "PUT"
    assert first.url.raw_path == (
        f"/v1/agent/check/{report}/service%3Aqueue%20consumer?note=42+messages+consumed".encode()
    )
    assert second.url.raw_path == f"/v1/agent/check/{report}/consumer-alive".encode()


@pytest.mark.anyio
async def test_the_async_client_reports_the_same_way() -> None:
    agent = FakeAgent()

    async with agent.async_consul() as consul:
        await consul.agent.pass_check("consumer-alive", note="ok")
        await consul.agent.warn_check("consumer-alive")
        await consul.agent.fail_check("consumer-alive")

    assert agent.calls() == [
        ("PUT", "/v1/agent/check/pass/consumer-alive"),
        ("PUT", "/v1/agent/check/warn/consumer-alive"),
        ("PUT", "/v1/agent/check/fail/consumer-alive"),
    ]


def test_an_unknown_check_raises() -> None:
    agent = FakeAgent(status_code=404, body='Unknown check ID "missing"')

    with pytest.raises(HTTPStatusError, match="404") as raised:
        agent.consul().agent.pass_check("missing")

    assert raised.value.status_code == 404


# --------------------------------------------------------------------------- the in-memory agent


@pytest.fixture
def consul() -> Consul:
    return InMemoryAgent().consul()


def healthy(consul: Consul) -> list[str]:
    return [instance.id for instance in consul.health.service("queue-consumer")]


def test_a_ttl_check_starts_critical_until_reported(consul: Consul) -> None:
    consul.agent.register_service(
        "queue-consumer", checks=[{"ttl": timedelta(seconds=30), "id": "consumer-alive"}]
    )

    assert healthy(consul) == []

    consul.agent.pass_check("consumer-alive")
    assert healthy(consul) == ["queue-consumer"]

    consul.agent.warn_check("consumer-alive")
    assert healthy(consul) == []

    consul.agent.pass_check("consumer-alive")
    consul.agent.fail_check("consumer-alive")
    assert healthy(consul) == []


def test_checks_get_consul_ids(consul: Consul) -> None:
    consul.agent.register_service("queue-consumer", checks=[{"ttl": timedelta(seconds=30)}])
    consul.agent.register_service(
        "queue-consumer",
        service_id="queue-consumer-2",
        checks=[
            {"ttl": timedelta(seconds=30)},
            {"tcp": "10.0.0.5:8000", "interval": timedelta(seconds=10)},
        ],
    )

    consul.agent.pass_check("service:queue-consumer")
    consul.agent.pass_check("service:queue-consumer-2:1")

    assert healthy(consul) == ["queue-consumer", "queue-consumer-2"]

    with pytest.raises(HTTPStatusError, match='Unknown check ID "service:queue-consumer-2"'):
        consul.agent.pass_check("service:queue-consumer-2")


def test_deregistering_forgets_the_checks(consul: Consul) -> None:
    consul.agent.register_service(
        "queue-consumer", checks=[{"ttl": timedelta(seconds=30), "id": "consumer-alive"}]
    )
    consul.agent.deregister_service("queue-consumer")

    with pytest.raises(HTTPStatusError, match="404"):
        consul.agent.pass_check("consumer-alive")


def test_registering_again_starts_critical_again(consul: Consul) -> None:
    checks: list[Check] = [{"ttl": timedelta(seconds=30), "id": "consumer-alive"}]
    consul.agent.register_service("queue-consumer", checks=checks)
    consul.agent.pass_check("consumer-alive")

    consul.agent.register_service("queue-consumer", checks=checks)

    assert healthy(consul) == []


def test_the_health_answer_shows_each_check_status() -> None:
    agent = InMemoryAgent()
    agent.consul().agent.register_service(
        "queue-consumer",
        checks=[
            {"ttl": timedelta(seconds=30), "id": "consumer-alive"},
            {"tcp": "10.0.0.5:8000", "interval": timedelta(seconds=10)},
        ],
    )
    agent.consul().agent.warn_check("consumer-alive", note="queue is late")

    with httpx.Client(base_url=AGENT_URL, transport=agent.transport()) as client:
        body = cast(
            "list[dict[str, object]]", client.get("/v1/health/service/queue-consumer").json()
        )

    checks = cast("list[dict[str, str]]", body[0]["Checks"])

    assert [(check["CheckID"], check["Status"]) for check in checks] == [
        ("consumer-alive", "warning"),
        ("service:queue-consumer:2", "warning"),
    ]
