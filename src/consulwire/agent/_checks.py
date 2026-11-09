"""Health check definitions, written as plain dictionaries the type checkers verify."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeAlias, TypedDict

from typing_extensions import Required, final

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import timedelta


@final
class HTTPCheck(TypedDict, total=False):
    """Healthy while a request to the URL answers a 2xx status. Consul counts a 429 as a warning.

    Attributes:
        http: URL the agent requests, as reachable from the agent.
        interval: Time between two requests.
        timeout: Time before a request counts as failed. Consul uses 10 seconds by default.
        method: HTTP method, ``GET`` by default.
        headers: Request headers.
        tls_skip_verify: Accept any TLS certificate.
        deregister_critical_after: Remove the whole service once the check has been critical this
            long, for instances that died without deregistering. Consul reaps every 30 seconds, and
            ignores values under a minute.
        id: Check ID, ``service:<service id>`` by default.
        name: Check name shown in the UI.
    """

    http: Required[str]
    interval: Required[timedelta]
    timeout: timedelta
    method: str
    headers: Mapping[str, str]
    tls_skip_verify: bool
    deregister_critical_after: timedelta
    id: str
    name: str


@final
class TCPCheck(TypedDict, total=False):
    """Healthy while a TCP connection to the address can be opened.

    Attributes:
        tcp: ``host:port`` the agent connects to, as reachable from the agent.
        interval: Time between two connections.
        timeout: Time before a connection counts as failed. Consul uses 10 seconds by default.
        deregister_critical_after: See :class:`HTTPCheck`.
        id: Check ID, ``service:<service id>`` by default.
        name: Check name shown in the UI.
    """

    tcp: Required[str]
    interval: Required[timedelta]
    timeout: timedelta
    deregister_critical_after: timedelta
    id: str
    name: str


@final
class TTLCheck(TypedDict, total=False):
    """Healthy while the service itself reports so, before the time to live runs out.

    The service calls :meth:`consulwire.agent.Agent.pass_check` regularly, more often than ``ttl``.
    The check starts critical, until the first report.

    Attributes:
        ttl: Time after the last report before the check turns critical.
        deregister_critical_after: See :class:`HTTPCheck`.
        id: Check ID, to report to. ``service:<service id>`` by default, followed by ``:1``,
            ``:2``... when the service has several checks: giving one is clearer.
        name: Check name shown in the UI.
    """

    ttl: Required[timedelta]
    deregister_critical_after: timedelta
    id: str
    name: str


Check: TypeAlias = "HTTPCheck | TCPCheck | TTLCheck"
