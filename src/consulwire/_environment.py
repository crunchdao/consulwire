"""Find the agent address and token in the environment.

Two conventions are supported, the official one first:

- ``CONSUL_HTTP_ADDR``, ``CONSUL_HTTP_SSL`` and ``CONSUL_HTTP_TOKEN``, read by the ``consul``
  command and the official clients.
- ``CONSUL_HOST`` and ``CONSUL_PORT``, read by Spring Cloud Consul and ``flask_consulate``.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Final

from consulwire.errors import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Mapping

DEFAULT_URL: Final = "http://localhost:8500"

DEFAULT_PORT: Final = 8500


def find_url(environment: Mapping[str, str] | None = None) -> str | None:
    """Return the agent URL from the environment, or ``None`` when no variable is set.

    ``CONSUL_HTTP_ADDR`` is used first. Without a scheme (``consul:8500``), the scheme is
    ``https`` when ``CONSUL_HTTP_SSL`` is ``true``, ``http`` otherwise. Then ``CONSUL_HOST``, with
    ``CONSUL_PORT`` or ``8500``. Empty values count as missing.

    Raises:
        ConfigurationError: ``CONSUL_PORT`` is not a port number.
    """
    if environment is None:
        environment = os.environ

    address = environment.get("CONSUL_HTTP_ADDR")
    if address:
        if "://" in address:
            return address

        secure = environment.get("CONSUL_HTTP_SSL", "").lower() in {"true", "1"}
        scheme = "https" if secure else "http"

        return f"{scheme}://{address}"

    host = environment.get("CONSUL_HOST")
    if host:
        port = _port(environment.get("CONSUL_PORT"))

        return f"http://{host}:{port}"

    return None


def find_token(environment: Mapping[str, str] | None = None) -> str | None:
    """Return the ACL token from ``CONSUL_HTTP_TOKEN``, or ``None`` when it is not set or empty."""
    if environment is None:
        environment = os.environ

    return environment.get("CONSUL_HTTP_TOKEN") or None


def _port(value: str | None) -> int:
    if not value:
        return DEFAULT_PORT

    if not value.isdigit() or not 0 < int(value) < 65536:  # noqa: PLR2004  # the TCP port range
        message = f"environment variable CONSUL_PORT must be a port number, not `{value}`"
        raise ConfigurationError(message)

    return int(value)
