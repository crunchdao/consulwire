"""Requests and answers of the catalog API, written once for both clients."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote

from consulwire._responses import Fields, json_body, json_list
from consulwire._transport import Request
from consulwire.catalog._instance import ServiceInstance

if TYPE_CHECKING:
    import httpx


def services() -> Request:
    """List the services of the cluster, with the tags of their instances.

    Request:
        ```http
        GET /v1/catalog/services
        ```

    Response:
        ```http
        200 OK
        X-Consul-Index: 42

        {"consul": [], "web": ["internal"], "api": []}
        ```
    """
    return Request("GET", "/v1/catalog/services")


def service(name: str, *, tag: str | None) -> Request:
    """List every registered instance of a service, healthy or not.

    Request:
        ```http
        GET /v1/catalog/service/web?tag=internal
        ```

    Response, empty for an unknown service. ``Address`` is the node's, ``ServiceAddress`` the
    instance's own, empty when it registered none:
        ```http
        200 OK
        X-Consul-Index: 42

        [
            {
                "Node": "node-1",
                "Address": "10.0.0.2",
                "ServiceID": "web",
                "ServiceName": "web",
                "ServiceAddress": "10.0.0.5",
                "ServicePort": 8005,
                "ServiceTags": ["internal"],
                "ServiceMeta": {"version": "1.2.0"}
            }
        ]
        ```
    """
    return Request(
        "GET",
        f"/v1/catalog/service/{quote(name, safe='')}",
        parameters={} if tag is None else {"tag": tag},
    )


def parse_services(response: httpx.Response) -> dict[str, list[str]]:
    services = Fields(response, json_body(response))

    return {name: list(services.strings(name)) for name in services.names()}


def parse_instances(response: httpx.Response) -> list[ServiceInstance]:
    return [_instance(Fields(response, raw, "an instance")) for raw in json_list(response)]


def _instance(fields: Fields) -> ServiceInstance:
    return ServiceInstance(
        id=fields.string("ServiceID"),
        name=fields.string("ServiceName"),
        address=fields.string("ServiceAddress") or fields.string("Address"),
        port=fields.integer("ServicePort"),
        tags=fields.strings("ServiceTags"),
        meta=fields.string_map("ServiceMeta"),
        node=fields.string("Node"),
    )
