from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import httpx
import pytest
from fastapi import FastAPI
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from consulwire import AsyncConsul, ConsulError
from consulwire.integrations import fastapi as consul_fastapi
from consulwire.integrations._common import HealthCheckLogFilter
from consulwire.integrations.fastapi import ConsulLifespan
from tests.support import FakeAgent

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from starlette.requests import Request

    from tests.conftest import LocalAddressLookups

pytestmark = pytest.mark.anyio


@pytest.fixture
def agent() -> FakeAgent:
    return FakeAgent()


async def get(application: Starlette, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=application)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


async def database_status(_request: Request) -> PlainTextResponse:
    return PlainTextResponse("database is fine")


async def fastapi_database_status() -> PlainTextResponse:
    return PlainTextResponse("database is fine")


async def test_registers_at_startup_and_deregisters_at_shutdown(
    agent: FakeAgent,
    local_address: LocalAddressLookups,
) -> None:
    application = FastAPI(
        lifespan=ConsulLifespan(
            name="my-service",
            port=8000,
            consul=agent.async_consul(),
            tags=["internal"],
            meta={"v": "1"},
        ),
    )

    async with application.router.lifespan_context(application):
        assert agent.calls() == [("PUT", "/v1/agent/service/register")]
        assert local_address.agent_urls == ["http://consul:8500"]
        assert local_address.on_the_event_loop == [False]
        assert agent.sent_json() == {
            "Name": "my-service",
            "ID": "my-service",
            "Address": "10.0.0.5",
            "Port": 8000,
            "Tags": ["internal"],
            "Meta": {"v": "1"},
            "Checks": [
                {
                    "HTTP": "http://10.0.0.5:8000/health",
                    "Interval": "10s",
                    "DeregisterCriticalServiceAfter": "60s",
                },
            ],
        }

    assert agent.calls() == [
        ("PUT", "/v1/agent/service/register"),
        ("PUT", "/v1/agent/service/deregister/my-service"),
    ]


async def test_each_reload_replaces_the_instance(agent: FakeAgent) -> None:
    lifespan = ConsulLifespan(
        name="my-service", port=8000, consul=agent.async_consul(), address="a"
    )
    application = Starlette(lifespan=lifespan)

    for _ in range(2):
        async with application.router.lifespan_context(application):
            pass

    assert agent.calls() == [
        ("PUT", "/v1/agent/service/register"),
        ("PUT", "/v1/agent/service/deregister/my-service"),
        ("PUT", "/v1/agent/service/register"),
        ("PUT", "/v1/agent/service/deregister/my-service"),
    ]


async def test_a_given_address_is_registered(
    agent: FakeAgent,
    local_address: LocalAddressLookups,
) -> None:
    lifespan = ConsulLifespan(
        name="my-service",
        port=8000,
        consul=agent.async_consul(),
        address="fd00::2",
        deregister_critical_after=None,
    )
    application = Starlette(lifespan=lifespan)

    async with application.router.lifespan_context(application):
        pass

    assert local_address.agent_urls == []
    assert agent.requests[0].content == (
        b'{"Name":"my-service","ID":"my-service","Address":"fd00::2","Port":8000,'
        b'"Checks":[{"HTTP":"http://[fd00::2]:8000/health","Interval":"10s"}]}'
    )


async def test_the_agent_comes_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
    agent: FakeAgent,
) -> None:
    monkeypatch.setenv("CONSUL_HOST", "consul")
    monkeypatch.setenv("CONSUL_PORT", "8600")
    created: list[AsyncConsul] = []

    def async_consul(url: str) -> AsyncConsul:
        created.append(agent.async_consul(url))

        return created[-1]

    monkeypatch.setattr(consul_fastapi, "AsyncConsul", async_consul)
    application = Starlette(lifespan=ConsulLifespan(name="my-service", port=8000, address="a"))

    async with application.router.lifespan_context(application):
        pass

    assert str(agent.requests[0].url) == "http://consul:8600/v1/agent/service/register"

    with pytest.raises(RuntimeError, match="client has been closed"):
        await created[0].agent.deregister_service("my-service")


async def test_a_given_client_stays_open(agent: FakeAgent) -> None:
    consul = agent.async_consul()
    application = Starlette(
        lifespan=ConsulLifespan(name="my-service", port=8000, consul=consul, address="a"),
    )

    async with application.router.lifespan_context(application):
        pass

    await consul.agent.deregister_service("still-open")

    assert agent.calls()[-1] == ("PUT", "/v1/agent/service/deregister/still-open")


async def test_no_agent_configured_skips_the_registration(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    application = Starlette(lifespan=ConsulLifespan(name="my-service", port=8000))

    async with application.router.lifespan_context(application):
        response = await get(application, "/health")

    assert response.status_code == 200
    assert "consul registration skipped, no agent configured - name=my-service" in caplog.text


async def test_registration_failure_stops_the_start() -> None:
    agent = FakeAgent(status_code=400, body="Invalid check")
    application = Starlette(
        lifespan=ConsulLifespan(name="my-service", port=8000, consul=agent.async_consul()),
    )

    with pytest.raises(ConsulError, match="Invalid check"):
        async with application.router.lifespan_context(application):
            pass


async def test_deregistration_failure_does_not_fail_the_shutdown(
    agent: FakeAgent,
    caplog: pytest.LogCaptureFixture,
) -> None:
    application = Starlette(
        lifespan=ConsulLifespan(name="my-service", port=8000, consul=agent.async_consul()),
    )

    async with application.router.lifespan_context(application):
        agent.status_code = 500
        agent.body = "agent is shutting down"

    assert "consul deregistration failed - name=my-service" in caplog.text


async def test_a_missing_health_route_is_added_out_of_the_schema(agent: FakeAgent) -> None:
    application = FastAPI(
        lifespan=ConsulLifespan(name="my-service", port=8000, consul=agent.async_consul()),
    )

    async with application.router.lifespan_context(application):
        response = await get(application, "/health")

    assert response.status_code == 200
    assert "/health" not in application.openapi()["paths"]


async def test_the_application_health_route_is_kept(agent: FakeAgent) -> None:
    application = FastAPI(
        lifespan=ConsulLifespan(name="my-service", port=8000, consul=agent.async_consul()),
    )
    application.add_api_route("/health", fastapi_database_status, methods=["GET"])

    async with application.router.lifespan_context(application):
        response = await get(application, "/health")

    assert response.text == "database is fine"


async def test_a_given_health_path_is_added_when_missing(agent: FakeAgent) -> None:
    application = Starlette(
        routes=[Route("/health", database_status)],
        lifespan=ConsulLifespan(
            name="my-service",
            port=8000,
            consul=agent.async_consul(),
            address="a",
            health_path="/status",
        ),
    )

    async with application.router.lifespan_context(application):
        status = await get(application, "/status")
        health = await get(application, "/health")

    assert status.status_code == 200
    assert health.text == "database is fine"


async def test_works_inside_the_lifespan_of_the_application(agent: FakeAgent) -> None:
    consul = ConsulLifespan(name="my-service", port=8000, consul=agent.async_consul(), address="a")
    events: list[str] = []

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
        events.append("startup")

        async with consul(application):
            yield

        events.append("shutdown")

    application = FastAPI(lifespan=lifespan)

    async with application.router.lifespan_context(application):
        assert agent.calls() == [("PUT", "/v1/agent/service/register")]

    assert events == ["startup", "shutdown"]
    assert len(agent.calls()) == 2


@pytest.mark.parametrize("logger_name", consul_fastapi.ACCESS_LOGGERS)
async def test_health_checks_are_hidden_from_the_access_logs(
    agent: FakeAgent,
    logger_name: str,
) -> None:
    application = Starlette(
        lifespan=ConsulLifespan(name="my-service", port=8000, consul=agent.async_consul()),
    )

    async with application.router.lifespan_context(application):
        pass

    filters = [
        item
        for item in logging.getLogger(logger_name).filters
        if isinstance(item, HealthCheckLogFilter)
    ]

    assert [item.health_path for item in filters] == ["/health"]

    # The access log line of uvicorn.
    record = logging.LogRecord(
        logger_name,
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:52000", "GET", "/health", "1.1", 200),
        None,
    )
    assert filters[0].filter(record) is False


async def test_health_checks_can_stay_in_the_access_logs(agent: FakeAgent) -> None:
    lifespan = ConsulLifespan(
        name="my-service",
        port=8000,
        consul=agent.async_consul(),
        log_health_checks=True,
    )
    application = Starlette(lifespan=lifespan)

    async with application.router.lifespan_context(application):
        pass

    assert not any(
        isinstance(item, HealthCheckLogFilter)
        for item in logging.getLogger("uvicorn.access").filters
    )


async def test_the_registration_is_left_to_gunicorn(
    monkeypatch: pytest.MonkeyPatch,
    agent: FakeAgent,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("CONSULWIRE_REGISTERED_BY", "gunicorn")
    application = Starlette(
        lifespan=ConsulLifespan(name="my-service", port=8000, consul=agent.async_consul()),
    )

    async with application.router.lifespan_context(application):
        response = await get(application, "/health")

    assert response.status_code == 200
    assert agent.requests == []
    assert "consul registration left to gunicorn - name=my-service" in caplog.text
    assert any(
        isinstance(item, HealthCheckLogFilter)
        for item in logging.getLogger("uvicorn.access").filters
    )
