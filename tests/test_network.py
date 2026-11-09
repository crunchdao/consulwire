from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from consulwire import TransportError, _network
from consulwire._network import find_local_address

if TYPE_CHECKING:
    import socket

    from consulwire._network import SocketAddress


def fake_routes(monkeypatch: pytest.MonkeyPatch, routes: dict[str, str]) -> list[str]:
    """Replace the routing table: destination host to local source address, no route otherwise."""
    asked: list[str] = []

    def route_source(_family: socket.AddressFamily, destination: SocketAddress) -> str:
        host = str(destination[0])
        asked.append(host)

        if host not in routes:
            message = "network is unreachable"
            raise OSError(message)

        return routes[host]

    monkeypatch.setattr(_network, "_route_source", route_source)

    return asked


def test_the_route_towards_the_agent_gives_the_address(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = fake_routes(monkeypatch, {"10.0.0.2": "10.0.0.5"})

    assert find_local_address("http://10.0.0.2:8500") == "10.0.0.5"
    assert asked == ["10.0.0.2"]


def test_a_loopback_agent_uses_the_default_route(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = fake_routes(monkeypatch, {"127.0.0.1": "127.0.0.1", "192.0.2.1": "192.168.1.10"})

    assert find_local_address("http://127.0.0.1:8500") == "192.168.1.10"
    assert asked == ["127.0.0.1", "192.0.2.1"]


def test_the_ipv6_default_route_is_the_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _ = fake_routes(monkeypatch, {"127.0.0.1": "127.0.0.1", "2001:db8::1": "fd00::5"})

    assert find_local_address("http://127.0.0.1:8500") == "fd00::5"


def test_the_loopback_is_kept_without_default_route(monkeypatch: pytest.MonkeyPatch) -> None:
    _ = fake_routes(monkeypatch, {"127.0.0.1": "127.0.0.1"})

    assert find_local_address("http://127.0.0.1:8500") == "127.0.0.1"


def test_an_agent_without_route_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _ = fake_routes(monkeypatch, {})

    with pytest.raises(TransportError, match="network is unreachable"):
        _ = find_local_address("http://10.0.0.2:8500")


def test_the_real_routing_table_gives_an_address() -> None:
    assert find_local_address("http://127.0.0.1:8500")


def test_an_unknown_host_raises() -> None:
    with pytest.raises(TransportError, match=r"local address towards the consul agent at consul\."):
        _ = find_local_address("http://consul.invalid:8500")
