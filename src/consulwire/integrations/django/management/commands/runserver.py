"""``runserver``, registering the development server in Consul once it listens."""

from __future__ import annotations

from consulwire.integrations.django._runserver import command_class

Command = command_class()
