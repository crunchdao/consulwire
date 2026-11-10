"""The registration of ``runserver``, configured by the ``CONSULWIRE`` setting."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final, TypedDict, cast

from django.core.exceptions import ImproperlyConfigured
from django.urls import Resolver404, resolve
from typing_extensions import Required

from consulwire._client import Consul
from consulwire.integrations._common import (
    DEFAULT_CHECK_INTERVAL,
    DEFAULT_DEREGISTER_CRITICAL_AFTER,
    DEFAULT_HEALTH_PATH,
    Registration,
    hide_health_checks,
    resolve_address,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import timedelta

APP_NAME: Final = "consulwire.integrations.django"

APP_LABEL: Final = "consulwire"

ACCESS_LOGGERS: Final = ("django.server",)

logger = logging.getLogger("consulwire.integrations.django")


class ConsulSettings(TypedDict, total=False):
    """The ``CONSULWIRE`` setting, to annotate it in the settings module.

    Attributes:
        name: Name other services look this one up by. Required.
        health_path: Route Consul checks, ``/health`` by default. The project declares it.
        address: Address other services connect to. By default, the host given to
            ``runserver`` when it is a specific one, otherwise the local IP address the system
            uses to reach the agent.
        check_interval: Time between two health checks, 10 seconds by default.
        deregister_critical_after: Time after which Consul removes an instance whose health check
            keeps failing, a minute by default. ``None`` keeps them.
        tags: Service tags.
        meta: Service key and value pairs.
        log_health_checks: Keep the health check requests in the ``runserver`` log.
    """

    name: Required[str]
    health_path: str
    address: str
    check_interval: timedelta
    deregister_critical_after: timedelta | None
    tags: Sequence[str]
    meta: Mapping[str, str]
    log_health_checks: bool


class RunserverRegistration:
    """Register the development server once ``runserver`` listens."""

    def __init__(self, configuration: ConsulSettings) -> None:
        self._address: str | None = configuration.get("address")
        self._log_health_checks: bool = configuration.get("log_health_checks", False)
        self._registration: Registration = Registration(
            name=configuration["name"],
            health_path=configuration.get("health_path", DEFAULT_HEALTH_PATH),
            check_interval=configuration.get("check_interval", DEFAULT_CHECK_INTERVAL),
            deregister_critical_after=configuration.get(
                "deregister_critical_after", DEFAULT_DEREGISTER_CRITICAL_AFTER
            ),
            tags=configuration.get("tags", ()),
            meta=configuration.get("meta", {}),
            logger=logger,
        )

    @classmethod
    def from_settings(cls, configuration: object) -> RunserverRegistration:
        """Read the ``CONSULWIRE`` setting.

        Raises:
            ImproperlyConfigured: The setting is missing, or has no ``name``.
        """
        name = (
            cast("dict[str, object]", configuration).get("name")
            if isinstance(configuration, dict)
            else None
        )

        if not isinstance(name, str):
            message = 'CONSULWIRE must be a dictionary with the name of the service: {"name": ...}'
            raise ImproperlyConfigured(message)

        return cls(cast("ConsulSettings", configuration))

    def register(self, *, host: str | None, port: int) -> None:
        """Register the application listening on this host and port, if an agent is configured.

        Raises:
            ConsulError: The agent could not be reached, or refused the registration.
        """
        health_path = self._registration.health_path

        if not self._log_health_checks:
            hide_health_checks(ACCESS_LOGGERS, health_path)

        try:
            _ = resolve(health_path)
        except Resolver404:
            logger.warning(
                "no route at %s, Consul's health checks will fail - name=%s",
                health_path,
                self._registration.name,
            )

        consul, _ = self._registration.agent_client(None, Consul)
        if consul is None:
            return

        with consul:
            address = resolve_address(self._address, host, consul.url)
            self._registration.register(consul, address, port)
