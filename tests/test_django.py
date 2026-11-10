from __future__ import annotations

import io
import logging
from datetime import timedelta
from typing import TYPE_CHECKING

import django
import pytest
from django.apps import AppConfig, apps
from django.conf import settings
from django.contrib.staticfiles.management.commands.runserver import (
    Command as StaticfilesRunserver,
)
from django.core.exceptions import ImproperlyConfigured
from django.core.management import BaseCommand, load_command_class
from django.core.management.commands.runserver import Command as RunserverCommand

from consulwire.integrations._common import HealthCheckLogFilter
from consulwire.integrations.django import _registration, _runserver
from consulwire.integrations.django._registration import APP_NAME, RunserverRegistration
from consulwire.integrations.django._runserver import (
    RegisteringRunserver,
    command_class,
    next_runserver,
)
from consulwire.integrations.django.apps import ConsulConfig, warn_when_overridden
from tests.support import FakeAgent

if TYPE_CHECKING:
    from tests.conftest import LocalAddressLookups

if not settings.configured:
    settings.configure(
        DEBUG=True,
        SECRET_KEY="a0b1c2d3e4f5g6h7i8j9",
        ALLOWED_HOSTS=["*"],
        ROOT_URLCONF="tests.django_urls",
        INSTALLED_APPS=[APP_NAME, "django.contrib.staticfiles"],
        STATIC_URL="static/",
        CONSULWIRE={"name": "my-service", "tags": ["django"]},
    )
    django.setup()


@pytest.fixture
def agent(monkeypatch: pytest.MonkeyPatch) -> FakeAgent:
    """An agent configured in the environment, answering every request."""
    agent = FakeAgent()
    monkeypatch.setenv("CONSUL_HOST", "consul")
    monkeypatch.setattr(_registration, "Consul", agent.consul)

    return agent


def runserver(host: str) -> RunserverCommand:
    """Return the command as Django leaves it before binding, its banner kept out of the output."""
    command = type(load_command_class(APP_NAME, "runserver"))(stdout=io.StringIO())
    assert isinstance(command, RunserverCommand)

    vars(command).update(addr=host, _raw_ipv6=":" in host)

    return command


# --------------------------------------------------------------------------- the command


def test_runserver_extends_the_one_it_replaces() -> None:
    command = load_command_class(APP_NAME, "runserver")

    assert isinstance(command, RegisteringRunserver)
    assert isinstance(command, StaticfilesRunserver)


def test_without_another_runserver_it_extends_django_s() -> None:
    configuration = apps.get_app_config("consulwire")

    assert next_runserver([configuration]) is RunserverCommand
    assert next_runserver([]) is RunserverCommand


def test_apps_without_a_runserver_of_their_own_are_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    ours = apps.get_app_config("consulwire")
    without = AppConfig.create("django.contrib.contenttypes")
    staticfiles = apps.get_app_config("staticfiles")

    assert next_runserver([ours, without, staticfiles]) is StaticfilesRunserver

    # A runserver that is not one cannot be extended: Django's is then.
    def another_command(_app_name: str, _name: str) -> BaseCommand:
        return BaseCommand()

    monkeypatch.setattr(_runserver, "load_command_class", another_command)

    assert next_runserver([ours, staticfiles]) is RunserverCommand


def test_the_command_class_is_the_registering_one_alone_over_django_s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def django_runserver(_configurations: object) -> type[RunserverCommand]:
        return RunserverCommand

    monkeypatch.setattr(_runserver, "next_runserver", django_runserver)

    assert command_class() is RegisteringRunserver


def test_an_app_overriding_runserver_first_is_reported(caplog: pytest.LogCaptureFixture) -> None:
    warn_when_overridden({"runserver": APP_NAME})
    assert caplog.text == ""

    warn_when_overridden({"runserver": "daphne"})
    assert "runserver does not register in consul, daphne overrides it" in caplog.text


def test_the_app_reads_the_setting() -> None:
    configuration = apps.get_app_config("consulwire")

    assert isinstance(configuration, ConsulConfig)
    assert isinstance(configuration.registration, RunserverRegistration)


@pytest.mark.parametrize("value", [None, {}, {"name": 1}, ["my-service"]])
def test_the_setting_needs_a_name(value: object) -> None:
    with pytest.raises(ImproperlyConfigured, match="CONSULWIRE must be a dictionary"):
        _ = RunserverRegistration.from_settings(value)


# --------------------------------------------------------------------------- the registration


@pytest.mark.parametrize(
    ("host", "address"),
    [("0.0.0.0", "10.0.0.5"), ("0", "10.0.0.5"), ("::", "10.0.0.5"), ("10.1.2.3", "10.1.2.3")],
    ids=["wildcard", "shorthand", "IPv6 wildcard", "specific host"],
)
def test_runserver_registers_once_bound(
    agent: FakeAgent,
    local_address: LocalAddressLookups,
    host: str,
    address: str,
) -> None:
    runserver(host).on_bind(8123)

    assert agent.calls() == [("PUT", "/v1/agent/service/register")]
    assert agent.sent_json() == {
        "Name": "my-service",
        "ID": "my-service",
        "Address": address,
        "Port": 8123,
        "Tags": ["django"],
        "Checks": [
            {
                "HTTP": f"http://{address}:8123/health",
                "Interval": "10s",
                "DeregisterCriticalServiceAfter": "60s",
            },
        ],
    }
    assert len(local_address.agent_urls) == (0 if address == host else 1)


def test_no_agent_configured_skips_the_registration(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)

    runserver("0.0.0.0").on_bind(8000)

    assert "consul registration skipped, no agent configured - name=my-service" in caplog.text


def test_options_are_registered(agent: FakeAgent) -> None:
    registration = RunserverRegistration(
        {
            "name": "my-service",
            "address": "my-service",
            "check_interval": timedelta(seconds=30),
            "deregister_critical_after": None,
            "meta": {"version": "1"},
        }
    )

    registration.register(host="10.1.2.3", port=8000)

    assert agent.sent_json() == {
        "Name": "my-service",
        "ID": "my-service",
        "Address": "my-service",
        "Port": 8000,
        "Meta": {"version": "1"},
        "Checks": [{"HTTP": "http://my-service:8000/health", "Interval": "30s"}],
    }


def test_a_missing_health_route_is_reported(
    agent: FakeAgent,
    caplog: pytest.LogCaptureFixture,
) -> None:
    registration = RunserverRegistration({"name": "my-service", "health_path": "/status"})

    registration.register(host=None, port=8000)

    assert "no route at /status, Consul's health checks will fail - name=my-service" in caplog.text
    assert agent.calls() == [("PUT", "/v1/agent/service/register")]


def health_filters() -> list[HealthCheckLogFilter]:
    return [
        item
        for item in logging.getLogger("django.server").filters
        if isinstance(item, HealthCheckLogFilter)
    ]


@pytest.mark.usefixtures("agent")
def test_health_checks_are_hidden_from_the_runserver_log() -> None:
    RunserverRegistration({"name": "my-service"}).register(host=None, port=8000)

    [health_filter] = health_filters()

    assert health_filter.health_path == "/health"

    # The request line of the runserver log.
    record = logging.LogRecord(
        "django.server",
        logging.INFO,
        __file__,
        1,
        '"%s" %s %s',
        ("GET /health HTTP/1.1", 200, 0),
        None,
    )
    assert health_filter.filter(record) is False


@pytest.mark.usefixtures("agent")
def test_health_checks_can_stay_in_the_runserver_log() -> None:
    RunserverRegistration({"name": "my-service", "log_health_checks": True}).register(
        host=None, port=8000
    )

    assert health_filters() == []
