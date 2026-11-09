from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from consulwire import AsyncConsul, ConfigurationError, Consul, HTTPStatusError, TransportError
from consulwire._duration import format_duration
from consulwire._environment import find_token, find_url
from tests.support import FakeAgent


def test_register_service_sends_the_service_with_consul_names() -> None:
    agent = FakeAgent()

    with agent.consul() as consul:
        consul.agent.register_service(
            "aml-review-service",
            service_id="aml-review-service-1",
            address="aml-review-service",
            port=8000,
            tags=["internal"],
            meta={"version": "1"},
            checks=[
                {
                    "http": "http://aml-review-service:8000/health",
                    "interval": timedelta(seconds=10),
                    "timeout": timedelta(milliseconds=1500),
                    "method": "HEAD",
                    "headers": {"Accept": "text/plain"},
                    "tls_skip_verify": True,
                    "deregister_critical_after": timedelta(minutes=30),
                    "id": "health",
                    "name": "HTTP health",
                },
                {"tcp": "aml-review-service:8000", "interval": timedelta(seconds=30)},
            ],
        )

    request = agent.requests[0]
    assert request.method == "PUT"
    assert str(request.url) == "http://consul:8500/v1/agent/service/register"
    assert agent.sent_json() == {
        "Name": "aml-review-service",
        "ID": "aml-review-service-1",
        "Address": "aml-review-service",
        "Port": 8000,
        "Tags": ["internal"],
        "Meta": {"version": "1"},
        "Checks": [
            {
                "HTTP": "http://aml-review-service:8000/health",
                "Method": "HEAD",
                "Header": {"Accept": ["text/plain"]},
                "TLSSkipVerify": True,
                "Interval": "10s",
                "Timeout": "1500ms",
                "DeregisterCriticalServiceAfter": "1800s",
                "CheckID": "health",
                "Name": "HTTP health",
            },
            {"TCP": "aml-review-service:8000", "Interval": "30s"},
        ],
    }


def test_register_service_leaves_out_what_is_not_set() -> None:
    agent = FakeAgent()

    agent.consul().agent.register_service("minimal")

    assert agent.sent_json() == {"Name": "minimal"}
    assert agent.requests[0].url.query == b""


def test_register_service_can_replace_existing_checks() -> None:
    agent = FakeAgent()

    agent.consul().agent.register_service("minimal", replace_existing_checks=True)

    assert agent.requests[0].url.params["replace-existing-checks"] == "true"


def test_deregister_service_escapes_the_service_id() -> None:
    agent = FakeAgent()

    agent.consul().agent.deregister_service("aml review/1")

    request = agent.requests[0]
    assert request.method == "PUT"
    assert request.url.raw_path == b"/v1/agent/service/deregister/aml%20review%2F1"
    assert request.content == b""


@pytest.mark.anyio
async def test_the_async_client_sends_the_same_requests() -> None:
    agent = FakeAgent()

    async with agent.async_consul() as consul:
        await consul.agent.register_service(
            "my-service",
            port=8000,
            checks=[{"tcp": "my-service:8000", "interval": timedelta(seconds=5)}],
        )
        await consul.agent.deregister_service("my-service")

    assert agent.calls() == [
        ("PUT", "/v1/agent/service/register"),
        ("PUT", "/v1/agent/service/deregister/my-service"),
    ]
    assert agent.requests[0].content == (
        b'{"Name":"my-service","Port":8000,"Checks":[{"TCP":"my-service:8000","Interval":"5s"}]}'
    )


def test_refusal_raises_with_the_consul_explanation() -> None:
    agent = FakeAgent(status_code=400, body="Invalid check: Interval must be > 0\n")

    with pytest.raises(HTTPStatusError) as raised:
        agent.consul().agent.register_service("broken")

    assert raised.value.status_code == 400
    assert raised.value.body == "Invalid check: Interval must be > 0\n"
    assert str(raised.value) == (
        "consul answered 400 to PUT /v1/agent/service/register: Invalid check: Interval must be > 0"
    )


def test_deregistering_an_unknown_id_raises() -> None:
    # Body of a real Consul 1.12 agent.
    agent = FakeAgent(
        status_code=404,
        body=(
            'Unknown service ID "gone". Ensure that the service ID is passed, not the service name.'
        ),
    )

    with pytest.raises(HTTPStatusError) as raised:
        agent.consul().agent.deregister_service("gone")

    assert raised.value.status_code == 404


def refuse(request: httpx.Request) -> httpx.Response:
    message = "connection refused"
    raise httpx.ConnectError(message, request=request)


def test_unreachable_agent_raises_transport_error() -> None:
    consul = Consul("http://consul:8500", transport=httpx.MockTransport(refuse))

    with pytest.raises(TransportError, match=r"agent at http://consul:8500 could not be reached"):
        consul.agent.deregister_service("anything")


@pytest.mark.anyio
async def test_unreachable_agent_raises_transport_error_asynchronously() -> None:
    consul = AsyncConsul("http://consul:8500", transport=httpx.MockTransport(refuse))

    with pytest.raises(TransportError, match="connection refused"):
        await consul.agent.deregister_service("anything")


def test_token_is_sent_as_header() -> None:
    agent = FakeAgent()

    agent.consul(token="a0b1c2d3e4f5").agent.deregister_service("anything")

    assert agent.requests[0].headers["X-Consul-Token"] == "a0b1c2d3e4f5"


def test_no_token_header_by_default() -> None:
    agent = FakeAgent()

    agent.consul().agent.deregister_service("anything")

    assert "X-Consul-Token" not in agent.requests[0].headers


def test_url_and_token_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONSUL_HTTP_ADDR", "consul:8501")
    monkeypatch.setenv("CONSUL_HTTP_SSL", "true")
    monkeypatch.setenv("CONSUL_HTTP_TOKEN", "a0b1c2d3e4f5")
    agent = FakeAgent()

    consul = Consul(transport=agent.transport())
    consul.agent.deregister_service("anything")

    assert consul.url == "https://consul:8501"
    assert str(agent.requests[0].url) == "https://consul:8501/v1/agent/service/deregister/anything"
    assert agent.requests[0].headers["X-Consul-Token"] == "a0b1c2d3e4f5"


def test_default_url() -> None:
    assert Consul().url == "http://localhost:8500"
    assert AsyncConsul().url == "http://localhost:8500"


def test_trailing_slash_is_removed() -> None:
    assert Consul("http://consul:8500/").url == "http://consul:8500"


@pytest.mark.parametrize(
    ("environment", "url"),
    [
        ({}, None),
        ({"CONSUL_HTTP_ADDR": ""}, None),
        ({"CONSUL_HTTP_ADDR": "consul:8500"}, "http://consul:8500"),
        ({"CONSUL_HTTP_ADDR": "consul:8501", "CONSUL_HTTP_SSL": "TRUE"}, "https://consul:8501"),
        ({"CONSUL_HTTP_ADDR": "consul:8501", "CONSUL_HTTP_SSL": "false"}, "http://consul:8501"),
        ({"CONSUL_HTTP_ADDR": "https://consul:8501"}, "https://consul:8501"),
        ({"CONSUL_HOST": "consul"}, "http://consul:8500"),
        ({"CONSUL_HOST": "consul", "CONSUL_PORT": "8600"}, "http://consul:8600"),
        ({"CONSUL_HOST": "consul", "CONSUL_PORT": ""}, "http://consul:8500"),
        ({"CONSUL_HTTP_ADDR": "official:8500", "CONSUL_HOST": "spring"}, "http://official:8500"),
    ],
)
def test_find_url(environment: dict[str, str], url: str | None) -> None:
    assert find_url(environment) == url


@pytest.mark.parametrize("port", ["http", "0", "65536", "-1"])
def test_find_url_refuses_invalid_ports(port: str) -> None:
    with pytest.raises(ConfigurationError, match="CONSUL_PORT must be a port number"):
        _ = find_url({"CONSUL_HOST": "consul", "CONSUL_PORT": port})


@pytest.mark.parametrize(
    ("environment", "token"),
    [
        ({}, None),
        ({"CONSUL_HTTP_TOKEN": ""}, None),
        ({"CONSUL_HTTP_TOKEN": "a0b1c2d3e4f5"}, "a0b1c2d3e4f5"),
    ],
)
def test_find_token(environment: dict[str, str], token: str | None) -> None:
    assert find_token(environment) == token


@pytest.mark.parametrize(
    ("duration", "formatted"),
    [
        (timedelta(0), "0s"),
        (timedelta(seconds=10), "10s"),
        (timedelta(minutes=1), "60s"),
        (timedelta(milliseconds=1500), "1500ms"),
        (timedelta(microseconds=250), "250us"),
    ],
)
def test_format_duration(duration: timedelta, formatted: str) -> None:
    assert format_duration(duration) == formatted


def test_format_duration_refuses_negative_durations() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        _ = format_duration(timedelta(seconds=-1))
