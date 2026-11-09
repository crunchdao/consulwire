"""An instance of a service, as other services reach it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from consulwire._network import url_host

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True, slots=True)
class ServiceInstance:
    """An instance of a service, registered on one of the agents of the cluster.

    Attributes:
        id: Instance ID, unique on its agent.
        name: Service name.
        address: Address to connect to: the one the instance registered, or its node's when it
            registered none.
        port: Port to connect to, ``0`` when the instance registered none.
        tags: Service tags.
        meta: Service key and value pairs.
        node: Name of the node the instance is registered on.
    """

    id: str
    name: str
    address: str
    port: int
    tags: tuple[str, ...]
    meta: Mapping[str, str] = field(hash=False)
    node: str

    def url(self, path: str = "", *, scheme: str = "http") -> str:
        """Return the URL of a path on this instance, like ``http://10.0.0.5:8000/score``.

        IPv6 addresses are put in brackets.
        """
        return f"{scheme}://{url_host(self.address)}:{self.port}{path}"
