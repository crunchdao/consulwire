from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import pytest
from typing_extensions import override

from consulwire import Consul, ConsulError
from consulwire.integrations import gunicorn as consul_gunicorn
from consulwire.integrations._common import HealthCheckLogFilter
from consulwire.integrations.gunicorn import ConsulHooks
from consulwire.testing import InMemoryAgent
from tests.support import FakeAgent

if TYPE_CHECKING:
    from tests.conftest import LocalAddressLookups


class FakeListener:
    def __init__(self, socket_name: object) -> None:
        self.socket_name: object = socket_name

    def getsockname(self) -> object:
        return self.socket_name


class FakeArbiter:
    """The main process of gunicorn, once its sockets are bound."""

    def __init__(self, *socket_names: object, reexec_pid: int = 0) -> None:
        self.LISTENERS: list[FakeListener] = [FakeListener(name) for name in socket_names]
        self.reexec_pid: int = reexec_pid


@pytest.fixture
def agent() -> InMemoryAgent:
    return InMemoryAgent()


def test_registers_once_ready_and_deregisters_at_exit(
    agent: InMemoryAgent,
    local_address: LocalAddressLookups,
) -> None:
    hooks = ConsulHooks(name="my-service", consul=agent.consul(), tags=["internal"])
    server = FakeArbiter(("0.0.0.0", 8000))

    hooks.when_ready(server)

    assert agent.services == {
        "my-service": {
            "Name": "my-service",
            "ID": "my-service",
            "Address": "10.0.0.5",
            "Port": 8000,
            "Tags": ["internal"],
            "Checks": [
                {
                    "HTTP": "http://10.0.0.5:8000/health",
                    "Interval": "10s",
                    "DeregisterCriticalServiceAfter": "60s",
                },
            ],
        },
    }
    assert local_address.agent_urls == ["http://127.0.0.1:8500"]
    assert os.environ["CONSULWIRE_REGISTERED_BY"] == "gunicorn"

    hooks.on_exit(server)

    assert agent.services == {}


@pytest.mark.parametrize(
    ("socket_names", "address", "port"),
    [
        ([("10.1.2.3", 9000)], "10.1.2.3", 9000),
        ([("::", 9000, 0, 0)], "10.0.0.5", 9000),
        (["/run/gunicorn.sock", ("0.0.0.0", 9001)], "10.0.0.5", 9001),
        ([(17, b"\x00\x01"), ("0.0.0.0", 9002)], "10.0.0.5", 9002),
    ],
    ids=["a specific host", "IPv6", "after a UNIX socket", "after a raw socket"],
)
def test_the_first_tcp_socket_is_registered(
    agent: InMemoryAgent,
    socket_names: list[object],
    address: str,
    port: int,
) -> None:
    hooks = ConsulHooks(name="my-service", consul=agent.consul())

    hooks.when_ready(FakeArbiter(*socket_names))

    service = agent.services["my-service"]
    assert (service["Address"], service["Port"]) == (address, port)


def test_a_given_port_and_address_win(agent: InMemoryAgent) -> None:
    hooks = ConsulHooks(name="my-service", consul=agent.consul(), port=443, address="my-service")

    hooks.when_ready(FakeArbiter("/run/gunicorn.sock"))

    service = agent.services["my-service"]
    assert (service["Address"], service["Port"]) == ("my-service", 443)


def test_no_tcp_socket_skips_the_registration(
    agent: InMemoryAgent,
    caplog: pytest.LogCaptureFixture,
) -> None:
    hooks = ConsulHooks(name="my-service", consul=agent.consul())

    hooks.when_ready(FakeArbiter("/run/gunicorn.sock"))
    hooks.on_exit(FakeArbiter("/run/gunicorn.sock"))

    assert agent.requests == []
    assert "CONSULWIRE_REGISTERED_BY" not in os.environ
    assert "consul registration skipped, gunicorn listens on no TCP port" in caplog.text


def test_no_agent_configured_skips_the_registration(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    hooks = ConsulHooks(name="my-service")

    hooks.when_ready(FakeArbiter(("0.0.0.0", 8000)))
    hooks.on_exit(FakeArbiter(("0.0.0.0", 8000)))

    assert "CONSULWIRE_REGISTERED_BY" not in os.environ
    assert "consul registration skipped, no agent configured - name=my-service" in caplog.text


class ClosedTracking(Consul):
    def __init__(self, url: str, agent: InMemoryAgent | FakeAgent) -> None:
        super().__init__(url, transport=agent.transport())

        self.closed: bool = False

    @override
    def close(self) -> None:
        self.closed = True

        super().close()


@pytest.fixture
def created(monkeypatch: pytest.MonkeyPatch, agent: InMemoryAgent) -> list[ClosedTracking]:
    """Clients created from the environment, on the in-memory agent."""
    monkeypatch.setenv("CONSUL_HOST", "consul")
    clients: list[ClosedTracking] = []

    def consul(url: str) -> Consul:
        clients.append(ClosedTracking(url, agent))

        return clients[-1]

    monkeypatch.setattr(consul_gunicorn, "Consul", consul)

    return clients


def test_the_agent_comes_from_the_environment(
    agent: InMemoryAgent,
    created: list[ClosedTracking],
) -> None:
    hooks = ConsulHooks(name="my-service", address="a")

    hooks.when_ready(FakeArbiter(("0.0.0.0", 8000)))

    assert [client.url for client in created] == ["http://consul:8500"]
    assert "my-service" in agent.services
    assert not created[0].closed

    hooks.on_exit(FakeArbiter(("0.0.0.0", 8000)))

    assert agent.services == {}
    assert created[0].closed


def test_a_skipped_registration_closes_its_client(created: list[ClosedTracking]) -> None:
    hooks = ConsulHooks(name="my-service")

    hooks.when_ready(FakeArbiter("/run/gunicorn.sock"))

    assert created[0].closed


def test_registration_failure_stops_gunicorn(
    monkeypatch: pytest.MonkeyPatch,
    created: list[ClosedTracking],
) -> None:
    failing = FakeAgent(status_code=400, body="Invalid check")

    def consul(url: str) -> Consul:
        created.append(ClosedTracking(url, failing))

        return created[-1]

    monkeypatch.setattr(consul_gunicorn, "Consul", consul)
    hooks = ConsulHooks(name="my-service", address="a")

    with pytest.raises(ConsulError, match="Invalid check"):
        hooks.when_ready(FakeArbiter(("0.0.0.0", 8000)))

    assert created[0].closed
    assert "CONSULWIRE_REGISTERED_BY" not in os.environ


def test_a_given_client_stays_open(agent: InMemoryAgent) -> None:
    consul = agent.consul()
    hooks = ConsulHooks(name="my-service", consul=consul, address="a")

    hooks.when_ready(FakeArbiter(("0.0.0.0", 8000)))
    hooks.on_exit(FakeArbiter(("0.0.0.0", 8000)))

    assert consul.kv.put("still-open", "yes") is True


def test_the_old_main_process_of_an_upgrade_keeps_the_instance(
    agent: InMemoryAgent,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    hooks = ConsulHooks(name="my-service", consul=agent.consul(), address="a")

    hooks.when_ready(FakeArbiter(("0.0.0.0", 8000)))
    hooks.on_exit(FakeArbiter(("0.0.0.0", 8000), reexec_pid=4242))

    assert "my-service" in agent.services
    assert "consul deregistration left to the new main process" in caplog.text


def test_deregistration_failure_does_not_fail_the_exit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent = FakeAgent()
    hooks = ConsulHooks(name="my-service", consul=agent.consul(), address="a")

    hooks.when_ready(FakeArbiter(("0.0.0.0", 8000)))
    agent.status_code = 500
    hooks.on_exit(FakeArbiter(("0.0.0.0", 8000)))

    assert "consul deregistration failed - name=my-service" in caplog.text


@pytest.mark.parametrize("logger_name", consul_gunicorn.ACCESS_LOGGERS)
def test_health_checks_are_hidden_from_the_access_logs(
    agent: InMemoryAgent,
    logger_name: str,
) -> None:
    hooks = ConsulHooks(name="my-service", consul=agent.consul(), health_path="/status")

    hooks.when_ready(FakeArbiter(("0.0.0.0", 8000)))

    assert [
        item.health_path
        for item in logging.getLogger(logger_name).filters
        if isinstance(item, HealthCheckLogFilter)
    ] == ["/status"]


def test_health_checks_can_stay_in_the_access_logs(agent: InMemoryAgent) -> None:
    hooks = ConsulHooks(name="my-service", consul=agent.consul(), log_health_checks=True)

    hooks.when_ready(FakeArbiter(("0.0.0.0", 8000)))

    assert not any(
        isinstance(item, HealthCheckLogFilter)
        for item in logging.getLogger("gunicorn.access").filters
    )
