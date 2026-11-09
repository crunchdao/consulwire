"""Strictly typed Consul client, with integrations that register Flask and FastAPI applications.

Example:
    ```python
    from datetime import timedelta

    from consulwire import Consul

    with Consul() as consul:
        consul.agent.register_service(
            "my-service",
            address="my-service",
            port=8000,
            checks=[{"http": "http://my-service:8000/health", "interval": timedelta(seconds=10)}],
        )
    ```
"""

from __future__ import annotations

from consulwire._client import AsyncConsul, Consul
from consulwire._version import __version__
from consulwire.errors import ConfigurationError, ConsulError, HTTPStatusError, TransportError

__all__ = [
    "AsyncConsul",
    "ConfigurationError",
    "Consul",
    "ConsulError",
    "HTTPStatusError",
    "TransportError",
    "__version__",
]
