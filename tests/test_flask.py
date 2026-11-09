from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, NamedTuple, cast

import flask
import flask.cli
import pytest
import werkzeug.serving

from consulwire import Consul, ConsulError
from consulwire.integrations import flask as consul_flask
from consulwire.integrations._common import HealthCheckLogFilter
from consulwire.integrations.flask import ConsulExtension
from tests.support import FakeAgent

if TYPE_CHECKING:
    from tests.conftest import LocalAddressLookups

ALL_INTERFACES = "0.0.0.0"


class ReloaderCase(NamedTuple):
    application_debug: bool
    debug: bool | None
    use_reloader: bool | None
    flask_debug: str | None
    load_dotenv: bool


class Server:
    """Stands for werkzeug's server: records what `Flask.run` started, and when."""

    def __init__(self, agent: FakeAgent) -> None:
        self.agent: FakeAgent = agent
        self.started: list[tuple[str, int]] = []
        self.registrations_before_start: list[int] = []
        self.use_reloader: list[object] = []

    def run_simple(self, hostname: str, port: int, _application: object, **options: object) -> None:
        self.started.append((hostname, port))
        self.registrations_before_start.append(len(self.agent.requests))
        self.use_reloader.append(options.get("use_reloader"))


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("FLASK_DEBUG", "FLASK_RUN_FROM_CLI", "WERKZEUG_RUN_MAIN"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def dotenv_files(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Stand for the .env files: variables put here are loaded by `flask.cli.load_dotenv`."""
    variables: dict[str, str] = {}

    def load_dotenv() -> bool:
        for name, value in variables.items():
            monkeypatch.setenv(name, value)

        return bool(variables)

    monkeypatch.setattr(flask.cli, "load_dotenv", load_dotenv)

    return variables


@pytest.fixture
def agent() -> FakeAgent:
    return FakeAgent()


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch, agent: FakeAgent) -> Server:
    server = Server(agent)
    monkeypatch.setattr(werkzeug.serving, "run_simple", server.run_simple)

    return server


def registered(agent: FakeAgent, field: str) -> object:
    payload = agent.sent_json()
    assert isinstance(payload, dict)

    return cast("dict[str, object]", payload)[field]


def health_log_filters(logger_name: str) -> list[HealthCheckLogFilter]:
    return [
        item
        for item in logging.getLogger(logger_name).filters
        if isinstance(item, HealthCheckLogFilter)
    ]


def test_run_registers_before_listening(
    server: Server,
    agent: FakeAgent,
    local_address: LocalAddressLookups,
) -> None:
    application = flask.Flask(__name__)
    _ = ConsulExtension(
        application,
        name="my-service",
        consul=agent.consul(),
        tags=["internal"],
        meta={"v": "1"},
    )

    application.run(host=ALL_INTERFACES, port=8000, load_dotenv=False)

    assert server.started == [(ALL_INTERFACES, 8000)]
    assert server.registrations_before_start == [1]
    assert local_address.agent_urls == ["http://consul:8500"]
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


def test_run_uses_the_flask_default_port(
    server: Server,
    agent: FakeAgent,
    local_address: LocalAddressLookups,
) -> None:
    application = flask.Flask(__name__)
    _ = ConsulExtension(application, name="my-service", consul=agent.consul())

    application.run(load_dotenv=False)

    assert server.started == [("127.0.0.1", 5000)]
    assert len(local_address.agent_urls) == 1
    assert registered(agent, "Port") == 5000


def test_run_uses_the_server_name(server: Server, agent: FakeAgent) -> None:
    application = flask.Flask(__name__)
    application.config["SERVER_NAME"] = "my-service.internal:8100"
    _ = ConsulExtension(application, name="my-service", consul=agent.consul())

    application.run(load_dotenv=False)

    assert server.started == [("my-service.internal", 8100)]
    assert registered(agent, "Address") == "my-service.internal"
    assert registered(agent, "Port") == 8100


def test_a_specific_host_is_the_address(server: Server, agent: FakeAgent) -> None:
    application = flask.Flask(__name__)
    _ = ConsulExtension(
        application,
        name="my-service",
        consul=agent.consul(),
        deregister_critical_after=None,
    )

    application.run(host="fd00::2", port=8000, load_dotenv=False)

    assert server.started == [("fd00::2", 8000)]
    assert registered(agent, "Address") == "fd00::2"
    assert registered(agent, "Checks") == [
        {"HTTP": "http://[fd00::2]:8000/health", "Interval": "10s"},
    ]


def test_the_given_address_wins_over_the_host(server: Server, agent: FakeAgent) -> None:
    application = flask.Flask(__name__)
    _ = ConsulExtension(application, name="my-service", consul=agent.consul(), address="my-service")

    application.run(host="10.0.0.9", port=8000, load_dotenv=False)

    assert server.started == [("10.0.0.9", 8000)]
    assert registered(agent, "Address") == "my-service"


@pytest.mark.parametrize("host", [ALL_INTERFACES, "::", "127.0.0.1", "localhost"])
def test_wildcard_and_loopback_hosts_find_the_address(
    server: Server,
    agent: FakeAgent,
    local_address: LocalAddressLookups,
    host: str,
) -> None:
    application = flask.Flask(__name__)
    _ = ConsulExtension(application, name="my-service", consul=agent.consul())

    application.run(host=host, port=8000, load_dotenv=False)

    assert len(server.started) == 1
    assert len(local_address.agent_urls) == 1
    assert registered(agent, "Address") == "10.0.0.5"


def test_check_options_are_registered(server: Server, agent: FakeAgent) -> None:
    application = flask.Flask(__name__)
    _ = ConsulExtension(
        application,
        name="my-service",
        consul=agent.consul(),
        address="a",
        check_interval=timedelta(seconds=30),
        deregister_critical_after=timedelta(minutes=5),
    )

    application.run(port=8000, load_dotenv=False)

    assert len(server.started) == 1
    assert registered(agent, "Checks") == [
        {
            "HTTP": "http://a:8000/health",
            "Interval": "30s",
            "DeregisterCriticalServiceAfter": "300s",
        },
    ]


def test_the_agent_comes_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
    server: Server,
    agent: FakeAgent,
    local_address: LocalAddressLookups,
) -> None:
    monkeypatch.setenv("CONSUL_HOST", "consul")
    monkeypatch.setenv("CONSUL_PORT", "8600")
    monkeypatch.setattr(consul_flask, "Consul", agent.consul)

    application = flask.Flask(__name__)
    _ = ConsulExtension(application, name="my-service")
    application.run(port=8000, load_dotenv=False)

    assert local_address.agent_urls == ["http://consul:8600"]
    assert str(agent.requests[0].url) == "http://consul:8600/v1/agent/service/register"
    assert server.registrations_before_start == [1]


def test_no_agent_configured_skips_the_registration(
    server: Server,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    application = flask.Flask(__name__)
    extension = ConsulExtension(application, name="my-service")

    application.run(port=8000, load_dotenv=False)

    assert server.started == [("127.0.0.1", 8000)]
    assert "consul registration skipped, no agent configured - name=my-service" in caplog.text
    assert extension.register(port=8000) is False


def test_reloader_registers_only_in_the_serving_process(
    monkeypatch: pytest.MonkeyPatch,
    server: Server,
    agent: FakeAgent,
) -> None:
    application = flask.Flask(__name__)
    _ = ConsulExtension(application, name="my-service", consul=agent.consul(), address="a")

    application.run(port=8000, debug=True, load_dotenv=False)  # noqa: S201  # the reloader is tested

    assert len(server.started) == 1
    assert agent.requests == []

    monkeypatch.setenv("WERKZEUG_RUN_MAIN", "true")
    application.run(port=8000, debug=True, load_dotenv=False)  # noqa: S201  # the reloader is tested

    assert len(agent.requests) == 1


@pytest.mark.parametrize(
    "case",
    [
        ReloaderCase(
            application_debug=False,
            debug=None,
            use_reloader=None,
            flask_debug=None,
            load_dotenv=True,
        ),
        ReloaderCase(
            application_debug=True,
            debug=None,
            use_reloader=None,
            flask_debug=None,
            load_dotenv=True,
        ),
        ReloaderCase(
            application_debug=False,
            debug=True,
            use_reloader=None,
            flask_debug=None,
            load_dotenv=True,
        ),
        ReloaderCase(
            application_debug=True,
            debug=False,
            use_reloader=None,
            flask_debug=None,
            load_dotenv=True,
        ),
        ReloaderCase(
            application_debug=False,
            debug=True,
            use_reloader=False,
            flask_debug=None,
            load_dotenv=True,
        ),
        ReloaderCase(
            application_debug=False,
            debug=None,
            use_reloader=True,
            flask_debug=None,
            load_dotenv=True,
        ),
        ReloaderCase(
            application_debug=False,
            debug=None,
            use_reloader=None,
            flask_debug="1",
            load_dotenv=True,
        ),
        ReloaderCase(
            application_debug=False,
            debug=None,
            use_reloader=None,
            flask_debug="1",
            load_dotenv=False,
        ),
        ReloaderCase(
            application_debug=True, debug=None, use_reloader=None, flask_debug="0", load_dotenv=True
        ),
        ReloaderCase(
            application_debug=False,
            debug=False,
            use_reloader=None,
            flask_debug="1",
            load_dotenv=True,
        ),
    ],
)
def test_reloader_detection_follows_flask(
    monkeypatch: pytest.MonkeyPatch,
    server: Server,
    agent: FakeAgent,
    case: ReloaderCase,
) -> None:
    """Whatever the settings, registers exactly when Flask serves without the reloader."""
    if case.flask_debug is not None:
        monkeypatch.setenv("FLASK_DEBUG", case.flask_debug)

    application = flask.Flask(__name__)
    application.debug = case.application_debug
    _ = ConsulExtension(application, name="my-service", consul=agent.consul(), address="a")

    if case.use_reloader is None:
        application.run(port=8000, debug=case.debug, load_dotenv=case.load_dotenv)
    else:
        application.run(
            port=8000,
            debug=case.debug,
            load_dotenv=case.load_dotenv,
            use_reloader=case.use_reloader,
        )

    assert len(server.use_reloader) == 1
    assert len(agent.requests) == (0 if server.use_reloader[0] else 1)


def test_dotenv_files_are_loaded_before_registering(
    server: Server,
    agent: FakeAgent,
    dotenv_files: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dotenv_files["CONSUL_HOST"] = "consul-from-dotenv"
    monkeypatch.setattr(consul_flask, "Consul", agent.consul)

    application = flask.Flask(__name__)
    _ = ConsulExtension(application, name="my-service", address="a")
    application.run(port=8000)

    assert str(agent.requests[0].url) == "http://consul-from-dotenv:8500/v1/agent/service/register"
    assert server.registrations_before_start == [1]


def test_dotenv_files_are_not_loaded_when_disabled(
    server: Server,
    dotenv_files: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    dotenv_files["CONSUL_HOST"] = "consul-from-dotenv"

    application = flask.Flask(__name__)
    _ = ConsulExtension(application, name="my-service", address="a")
    application.run(port=8000, load_dotenv=False)

    assert len(server.started) == 1
    assert "consul registration skipped, no agent configured" in caplog.text


def test_random_port_is_not_registered(
    server: Server,
    agent: FakeAgent,
    caplog: pytest.LogCaptureFixture,
) -> None:
    application = flask.Flask(__name__)
    _ = ConsulExtension(application, name="my-service", consul=agent.consul(), address="a")

    application.run(port=0, load_dotenv=False)

    assert server.started == [("127.0.0.1", 0)]
    assert agent.requests == []
    assert "consul registration skipped, port 0 is a random port - name=my-service" in caplog.text

    with application.test_client() as client:
        assert client.get("/health").status_code == 200


def test_flask_cli_runs_are_left_alone(
    monkeypatch: pytest.MonkeyPatch,
    server: Server,
    agent: FakeAgent,
) -> None:
    monkeypatch.setenv("FLASK_RUN_FROM_CLI", "true")
    application = flask.Flask(__name__)
    _ = ConsulExtension(application, name="my-service", consul=agent.consul())

    application.run(port=8000, load_dotenv=False)

    assert server.started == []
    assert agent.requests == []


def test_registration_failure_stops_the_start(server: Server) -> None:
    agent = FakeAgent(status_code=400, body="Invalid check")
    application = flask.Flask(__name__)
    _ = ConsulExtension(application, name="my-service", consul=agent.consul(), address="a")

    with pytest.raises(ConsulError, match="Invalid check"):
        application.run(port=8000, load_dotenv=False)

    assert server.started == []


def test_a_missing_health_route_is_added_before_serving(server: Server) -> None:
    application = flask.Flask(__name__)
    _ = ConsulExtension(application, name="my-service")

    assert "/health" not in [rule.rule for rule in application.url_map.iter_rules()]

    application.run(port=8000, load_dotenv=False)

    assert len(server.started) == 1

    with application.test_client() as client:
        assert client.get("/health").status_code == 200
        assert client.post("/health").status_code == 405


def test_the_application_health_route_declared_later_is_kept(server: Server) -> None:
    application = flask.Flask(__name__)
    _ = ConsulExtension(application, name="my-service")
    application.add_url_rule("/health", view_func=lambda: "database is fine")

    application.run(port=8000, load_dotenv=False)

    assert len(server.started) == 1

    with application.test_client() as client:
        assert client.get("/health").get_data() == b"database is fine"


def test_a_given_health_path_is_added_when_missing(server: Server, agent: FakeAgent) -> None:
    application = flask.Flask(__name__)
    _ = ConsulExtension(
        application,
        name="my-service",
        health_path="/status",
        consul=agent.consul(),
        address="a",
        deregister_critical_after=None,
    )

    application.run(port=8000, load_dotenv=False)

    assert len(server.started) == 1
    assert registered(agent, "Checks") == [{"HTTP": "http://a:8000/status", "Interval": "10s"}]

    with application.test_client() as client:
        assert client.get("/status").status_code == 200
        assert client.get("/health").status_code == 404


def test_a_given_health_route_is_used_as_it_is(server: Server, agent: FakeAgent) -> None:
    application = flask.Flask(__name__)
    application.add_url_rule("/status", view_func=lambda: "custom")
    _ = ConsulExtension(
        application,
        name="my-service",
        health_path="/status",
        consul=agent.consul(),
        address="a",
    )

    application.run(port=8000, load_dotenv=False)

    assert len(server.started) == 1

    with application.test_client() as client:
        assert client.get("/status").get_data() == b"custom"


def test_manual_registration_adds_the_health_route(agent: FakeAgent) -> None:
    application = flask.Flask(__name__)
    extension = ConsulExtension(application, name="my-service", consul=agent.consul(), address="a")

    assert extension.register(port=8000) is True
    assert registered(agent, "Port") == 8000

    with application.test_client() as client:
        assert client.get("/health").status_code == 200


@pytest.mark.usefixtures("server")
def test_never_deregistered_unless_asked(agent: FakeAgent) -> None:
    application = flask.Flask(__name__)
    extension = ConsulExtension(application, name="my-service", consul=agent.consul(), address="a")

    application.run(port=8000, load_dotenv=False)

    assert agent.calls() == [("PUT", "/v1/agent/service/register")]

    extension.deregister()
    extension.deregister()

    assert agent.calls() == [
        ("PUT", "/v1/agent/service/register"),
        ("PUT", "/v1/agent/service/deregister/my-service"),
    ]


def test_deregister_without_registration_does_nothing(agent: FakeAgent) -> None:
    ConsulExtension(flask.Flask(__name__), name="my-service", consul=agent.consul()).deregister()

    assert agent.requests == []


def test_deregistration_failures_are_logged(
    agent: FakeAgent,
    caplog: pytest.LogCaptureFixture,
) -> None:
    extension = ConsulExtension(
        flask.Flask(__name__),
        name="my-service",
        consul=agent.consul(),
        address="a",
    )
    _ = extension.register(port=8000)

    agent.status_code = 500
    agent.body = "agent is shutting down"
    extension.deregister()
    extension.deregister()

    assert "consul deregistration failed - name=my-service" in caplog.text
    assert len(agent.requests) == 2


def test_two_applications_stay_apart(server: Server, agent: FakeAgent) -> None:
    consul = agent.consul()
    first = flask.Flask("first")
    second = flask.Flask("second")
    _ = ConsulExtension(first, name="first-service", consul=consul, address="a")
    _ = ConsulExtension(second, name="second-service", consul=consul, address="b")

    first.run(port=8001, load_dotenv=False)
    second.run(port=8002, load_dotenv=False)

    assert server.started == [("127.0.0.1", 8001), ("127.0.0.1", 8002)]
    payloads = [
        cast("dict[str, object]", json.loads(request.content)) for request in agent.requests
    ]
    assert [(payload["Name"], payload["Address"], payload["Port"]) for payload in payloads] == [
        ("first-service", "a", 8001),
        ("second-service", "b", 8002),
    ]


def test_extension_lifecycle_errors() -> None:
    application = flask.Flask(__name__)

    with pytest.raises(LookupError, match="no consul extension"):
        _ = ConsulExtension.of(application)

    application.extensions["consul"] = "something else"

    with pytest.raises(TypeError, match="is a str, not a ConsulExtension"):
        _ = ConsulExtension.of(application)

    del application.extensions["consul"]

    extension = ConsulExtension(name="my-service", consul=Consul("http://consul:8500"))

    with pytest.raises(RuntimeError, match="before registering"):
        _ = extension.register(port=8000)

    extension.init_app(application)

    with pytest.raises(RuntimeError, match="already set up"):
        extension.init_app(flask.Flask(__name__))

    with pytest.raises(RuntimeError, match="already set up"):
        _ = ConsulExtension(application, name="another")

    assert ConsulExtension.of(application) is extension


@pytest.mark.parametrize("logger_name", consul_flask.ACCESS_LOGGERS)
def test_health_checks_are_hidden_from_the_access_logs_once(logger_name: str) -> None:
    _ = ConsulExtension(flask.Flask(__name__), name="first")
    _ = ConsulExtension(flask.Flask(__name__), name="second")

    filters = health_log_filters(logger_name)
    assert [item.health_path for item in filters] == ["/health"]

    def record(message: str) -> logging.LogRecord:
        return logging.LogRecord(logger_name, logging.INFO, __file__, 1, message, None, None)

    assert filters[0].filter(record('127.0.0.1 - - "GET /health HTTP/1.1" 200 -')) is False
    assert filters[0].filter(record('127.0.0.1 - - "GET /healthy HTTP/1.1" 200 -')) is True
    assert filters[0].filter(record('127.0.0.1 - - "POST /reviews HTTP/1.1" 200 -')) is True


def test_health_checks_can_stay_in_the_access_logs() -> None:
    _ = ConsulExtension(flask.Flask(__name__), name="my-service", log_health_checks=True)

    assert health_log_filters("werkzeug") == []


def test_other_servers_get_the_health_route_at_the_first_request() -> None:
    application = flask.Flask(__name__)
    _ = ConsulExtension(application, name="my-service")

    @application.get("/hello")
    def hello() -> str:
        return "hello"

    assert "/health" not in [rule.rule for rule in application.url_map.iter_rules()]

    with application.test_client() as client:
        assert client.get("/hello").get_data() == b"hello"
        assert client.get("/health").status_code == 200


def test_other_servers_keep_the_application_health_route() -> None:
    application = flask.Flask(__name__)
    _ = ConsulExtension(application, name="my-service")
    application.add_url_rule("/health", view_func=lambda: "database is fine")

    with application.test_client() as client:
        assert client.get("/health").get_data() == b"database is fine"
