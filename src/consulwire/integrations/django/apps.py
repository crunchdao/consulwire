"""The Django app of the integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from django.apps import AppConfig
from django.conf import settings
from django.core.management import get_commands
from typing_extensions import final, override

from consulwire.integrations.django._registration import (
    APP_LABEL,
    APP_NAME,
    RunserverRegistration,
    logger,
)

if TYPE_CHECKING:
    from types import ModuleType


@final
class ConsulConfig(AppConfig):
    """Reads the ``CONSULWIRE`` setting when Django starts, for ``runserver`` to register."""

    name = APP_NAME
    label = APP_LABEL

    registration: RunserverRegistration

    def __init__(self, app_name: str, app_module: ModuleType | None) -> None:
        """Read the ``CONSULWIRE`` setting.

        Raises:
            ImproperlyConfigured: The setting is missing, or has no ``name``.
        """
        super().__init__(app_name, app_module)

        self.registration = RunserverRegistration.from_settings(
            cast("object", getattr(settings, "CONSULWIRE", None))
        )

    @override
    def ready(self) -> None:
        """Warn when another app's ``runserver`` would run instead of this one."""
        warn_when_overridden(get_commands())


def warn_when_overridden(commands: dict[str, str]) -> None:
    """Warn when an app listed before this one overrides ``runserver``: it runs instead."""
    owner = commands["runserver"]

    if owner != APP_NAME:
        logger.warning(
            "runserver does not register in consul, %s overrides it: list %s before it in "
            "INSTALLED_APPS",
            owner,
            APP_NAME,
        )
