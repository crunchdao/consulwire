"""Requests and answers of the health API, written once for both clients."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote

from consulwire._blocking import blocking_parameters
from consulwire._responses import Fields, json_list
from consulwire._transport import Request
from consulwire.catalog._instance import ServiceInstance

if TYPE_CHECKING:
    from datetime import timedelta

    import httpx


def service(
    name: str,
    *,
    tag: str | None,
    healthy_only: bool,
    index: int = 0,
    wait: timedelta | None = None,
) -> Request:
    """List the instances of a service with their checks. With ``wait``, a blocking query.

    Request:
        ```http
        GET /v1/health/service/scoring-service?passing=true&tag=internal&index=42&wait=300s
        ```

    Response, empty for an unknown service. ``Service.Address`` is empty when the instance
    registered none, ``Node.Address`` is used then:
        ```http
        200 OK
        X-Consul-Index: 42

        [
            {
                "Node": {"Node": "node-1", "Address": "10.0.0.2"},
                "Service": {
                    "ID": "scoring-service",
                    "Service": "scoring-service",
                    "Address": "10.0.0.5",
                    "Port": 8005,
                    "Tags": ["internal"],
                    "Meta": {"version": "1.2.0"}
                },
                "Checks": [
                    {"CheckID": "serfHealth", "Status": "passing"},
                    {"CheckID": "service:scoring-service", "Status": "passing"}
                ]
            }
        ]
        ```
    """
    parameters: dict[str, str] = {}

    if healthy_only:
        parameters["passing"] = "true"

    if tag is not None:
        parameters["tag"] = tag

    return Request(
        "GET",
        f"/v1/health/service/{quote(name, safe='')}",
        parameters={**parameters, **blocking_parameters(index, wait)},
        wait=wait,
    )


def parse_instances(response: httpx.Response) -> list[ServiceInstance]:
    return [_instance(Fields(response, raw, "an instance")) for raw in json_list(response)]


def _instance(entry: Fields) -> ServiceInstance:
    node = entry.object("Node")
    service = entry.object("Service")

    return ServiceInstance(
        id=service.string("ID"),
        name=service.string("Service"),
        address=service.string("Address") or node.string("Address"),
        port=service.integer("Port"),
        tags=service.strings("Tags"),
        meta=service.string_map("Meta"),
        node=node.string("Node"),
    )
