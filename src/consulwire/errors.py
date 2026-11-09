"""Errors raised by consulwire."""

from __future__ import annotations


class ConsulError(Exception):
    """Base class of every error raised by this package."""


class ConfigurationError(ConsulError, ValueError):
    """An environment variable holds a value the client cannot use."""


class TransportError(ConsulError):
    """The agent could not be reached: connection refused, timeout, DNS failure."""


class ResponseError(ConsulError):
    """The agent answered with a body that does not have the shape of its API."""


class HTTPStatusError(ConsulError):
    """The agent answered with an error status.

    Attributes:
        status_code: HTTP status of the response.
        body: Response body. Consul explains its refusals in plain text, like
            ``Invalid check: TTL must be > 0 for TTL checks``.
    """

    status_code: int
    body: str

    def __init__(self, method: str, path: str, status_code: int, body: str) -> None:
        explanation = body.strip() or "(empty body)"
        super().__init__(f"consul answered {status_code} to {method} {path}: {explanation}")

        self.status_code = status_code
        self.body = body
