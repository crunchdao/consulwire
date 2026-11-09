"""Requests of the agent API, written once for both clients."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, TypeAlias
from urllib.parse import quote

from consulwire._duration import format_duration
from consulwire._transport import Payload, Request

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from consulwire.agent._checks import Check

CheckReport: TypeAlias = Literal["pass", "warn", "fail"]
"""What a service reports to a TTL check, named like the endpoints of the agent."""


def register_service(  # noqa: PLR0913  # the fields of a Consul service
    name: str,
    *,
    service_id: str | None,
    address: str | None,
    port: int | None,
    tags: Sequence[str],
    meta: Mapping[str, str] | None,
    checks: Sequence[Check],
    replace_existing_checks: bool,
) -> Request:
    """Register a service instance, with Consul's field names. Unset fields are left out.

    Request:
        ```http
        PUT /v1/agent/service/register?replace-existing-checks=true

        {
            "Name": "my-service",
            "ID": "my-service",
            "Address": "10.0.0.5",
            "Port": 8000,
            "Tags": ["internal"],
            "Meta": {"version": "1.2.0"},
            "Checks": [
                {
                    "HTTP": "http://10.0.0.5:8000/health",
                    "Interval": "10s",
                    "DeregisterCriticalServiceAfter": "60s"
                },
                {"TCP": "10.0.0.5:8000", "Interval": "30s", "Timeout": "1500ms"}
            ]
        }
        ```

    Response, without body; ``400`` with the reason in plain text for an invalid service:
        ```http
        200 OK
        ```
    """
    payload: Payload = {"Name": name}

    if service_id is not None:
        payload["ID"] = service_id

    if address is not None:
        payload["Address"] = address

    if port is not None:
        payload["Port"] = port

    if tags:
        payload["Tags"] = list(tags)

    if meta:
        payload["Meta"] = dict(meta)

    if checks:
        payload["Checks"] = [_check_payload(check) for check in checks]

    return Request(
        "PUT",
        "/v1/agent/service/register",
        parameters={"replace-existing-checks": "true"} if replace_existing_checks else {},
        payload=payload,
    )


def deregister_service(service_id: str) -> Request:
    """Remove a service instance and its checks.

    Request:
        ```http
        PUT /v1/agent/service/deregister/my-service
        ```

    Response, without body; ``404`` for an unknown ID:
        ```http
        200 OK
        ```
    """
    return Request("PUT", f"/v1/agent/service/deregister/{quote(service_id, safe='')}")


def report_check(check_id: str, status: CheckReport, *, note: str | None) -> Request:
    """Report the status of a TTL check: ``pass``, ``warn`` or ``fail``.

    Request:
        ```http
        PUT /v1/agent/check/pass/consumer-alive?note=42%20messages%20consumed
        ```

    Response, without body; ``404`` for an unknown check:
        ```http
        200 OK
        ```
    """
    return Request(
        "PUT",
        f"/v1/agent/check/{status}/{quote(check_id, safe='')}",
        parameters={} if note is None else {"note": note},
    )


def _check_payload(check: Check) -> Payload:
    payload: Payload

    if "ttl" in check:
        payload = {"TTL": format_duration(check["ttl"])}
    else:
        if "http" in check:
            payload = {"HTTP": check["http"]}

            if "method" in check:
                payload["Method"] = check["method"]

            if "headers" in check:
                payload["Header"] = {name: [value] for name, value in check["headers"].items()}

            if check.get("tls_skip_verify"):
                payload["TLSSkipVerify"] = True
        else:
            payload = {"TCP": check["tcp"]}

        payload["Interval"] = format_duration(check["interval"])

        if "timeout" in check:
            payload["Timeout"] = format_duration(check["timeout"])

    if "deregister_critical_after" in check:
        payload["DeregisterCriticalServiceAfter"] = format_duration(
            check["deregister_critical_after"]
        )

    if "id" in check:
        payload["CheckID"] = check["id"]

    if "name" in check:
        payload["Name"] = check["name"]

    return payload
