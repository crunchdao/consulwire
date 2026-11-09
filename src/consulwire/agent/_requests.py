"""Requests of the agent API, written once for both clients."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote

from consulwire._duration import format_duration
from consulwire._transport import Payload, Request

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from consulwire.agent._checks import Check


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
    return Request("PUT", f"/v1/agent/service/deregister/{quote(service_id, safe='')}")


def _check_payload(check: Check) -> Payload:
    payload: Payload

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
