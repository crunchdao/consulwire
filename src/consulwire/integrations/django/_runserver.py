"""``runserver``, extended to register the development server once it listens."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from django.apps import apps
from django.core.management import find_commands, load_command_class
from django.core.management.commands.runserver import Command as RunserverCommand
from typing_extensions import override

from consulwire.integrations.django._registration import APP_LABEL, APP_NAME

if TYPE_CHECKING:
    from collections.abc import Sequence

    from django.apps import AppConfig

    from consulwire.integrations.django.apps import ConsulConfig


class RegisteringRunserver(RunserverCommand):
    """``runserver``, registering the server in the process serving the requests.

    ``on_bind`` runs once the socket is bound, with its port: in the process restarted by the
    autoreloader on every change, or in the only one with ``--noreload``.
    """

    @override
    def on_bind(self, server_port: int) -> None:
        super().on_bind(server_port)

        configuration = cast("ConsulConfig", apps.get_app_config(APP_LABEL))
        host = cast("object", getattr(self, "addr", None))

        # "0" is the shorthand of runserver for 0.0.0.0: any address, found automatically then.
        listening = host if isinstance(host, str) and host != "0" else None

        configuration.registration.register(host=listening, port=server_port)


def next_runserver(app_configs: Sequence[AppConfig]) -> type[RunserverCommand]:
    """Return the ``runserver`` Django would run without this app.

    The one of the first app listed after this one that has its own, like
    ``django.contrib.staticfiles`` or Daphne, otherwise Django's.
    """
    names = [configuration.name for configuration in app_configs]
    after: Sequence[AppConfig] = (
        app_configs[names.index(APP_NAME) + 1 :] if APP_NAME in names else ()
    )

    for configuration in after:
        if "runserver" in find_commands(str(Path(configuration.path) / "management")):
            command = load_command_class(configuration.name, "runserver")

            if isinstance(command, RunserverCommand):
                return type(command)

    return RunserverCommand


def command_class() -> type[RunserverCommand]:
    """Return ``runserver``, registering, on top of the one that would run without this app."""
    base = next_runserver(list(apps.get_app_configs()))

    if base is RunserverCommand:
        return RegisteringRunserver

    return type("Command", (RegisteringRunserver, base), {})
