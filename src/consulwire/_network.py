"""Find the address this process is reachable at, as seen from the agent."""

from __future__ import annotations

import ipaddress
import socket
from typing import Final, TypeAlias, cast
from urllib.parse import urlsplit

from consulwire.errors import TransportError

SocketAddress: TypeAlias = "tuple[str, int] | tuple[str, int, int, int] | tuple[int, bytes]"
"""Any address ``socket.getaddrinfo`` returns: IPv4, IPv6, or a raw one."""

_DEFAULT_ROUTE_PROBES: Final[dict[socket.AddressFamily, SocketAddress]] = {
    socket.AF_INET: ("192.0.2.1", 9),
    socket.AF_INET6: ("2001:db8::1", 9),
}
"""Documentation addresses (RFC 5737 and RFC 3849), IPv4 first: never used by anyone, so only the
default route leads to them."""


def url_host(address: str) -> str:
    """Return the address as the host of a URL: IPv6 addresses go in brackets."""
    return f"[{address}]" if ":" in address else address


def find_local_address(agent_url: str) -> str:
    """Return the local IP address the agent can reach this process at.

    Spring Cloud Consul registers the first network interface that is not a loopback. This asks the
    system routing table instead, which picks the interface on the agent's network: the container
    IP in a Docker network, the LAN IP when the agent is on another machine.

    When the agent is reached through a loopback address, its own loopback may not be this
    process: an agent in a Docker container reached through a published port sees itself at
    ``127.0.0.1``. The interface of the default route is used then, IPv4 first, like Spring does.
    The loopback address is kept only when there is no default route at all.

    Nothing is sent: UDP sockets are only connected, never written to.

    Raises:
        TransportError: The agent host does not resolve, or no route leads to it.
    """
    parts = urlsplit(agent_url)
    host = parts.hostname or "localhost"
    port = parts.port or (443 if parts.scheme == "https" else 80)

    try:
        family, _, _, _, agent_address = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)[0]
        local_address = _route_source(family, agent_address)
    except OSError as error:
        message = f"could not find the local address towards the consul agent at {host}: {error}"
        raise TransportError(message) from error

    if not ipaddress.ip_address(local_address).is_loopback:
        return local_address

    for probe_family, probe_destination in _DEFAULT_ROUTE_PROBES.items():
        default_route_address = _route_source_if_any(probe_family, probe_destination)

        if default_route_address is not None:
            return default_route_address

    return local_address


def _route_source_if_any(family: socket.AddressFamily, destination: SocketAddress) -> str | None:
    try:
        return _route_source(family, destination)
    except OSError:
        return None


def _route_source(family: socket.AddressFamily, destination: SocketAddress) -> str:
    """Return the local address the system would send from to reach the destination."""
    with socket.socket(family, socket.SOCK_DGRAM) as probe:
        probe.connect(destination)
        local_address = cast("tuple[str, int]", probe.getsockname())

    return local_address[0]
