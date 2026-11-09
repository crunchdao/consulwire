"""Requests and answers of the session API, written once for both clients."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING
from urllib.parse import quote

from consulwire._duration import format_duration, parse_duration
from consulwire._responses import Fields, json_body, json_list, malformed
from consulwire._transport import NOT_FOUND, Payload, Request
from consulwire.session._session import Session, SessionBehavior

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx

_BEHAVIORS: tuple[SessionBehavior, ...] = ("release", "delete")


def create(
    *,
    name: str | None,
    ttl: timedelta | None,
    lock_delay: timedelta | None,
    behavior: SessionBehavior,
    service_checks: Sequence[str],
) -> Request:
    """Create a session on the agent's node. Unset fields are left to Consul.

    Request:
        ```http
        PUT /v1/session/create

        {
            "Name": "jobs/nightly-report",
            "TTL": "15s",
            "LockDelay": "15s",
            "Behavior": "release",
            "ServiceChecks": [{"ID": "service:my-service"}]
        }
        ```

    Response:
        ```http
        200 OK

        {"ID": "a0b1c2d3-e4f5-a6b7-c8d9-e0f1a2b3c4d5"}
        ```
    """
    payload: Payload = {"Behavior": behavior}

    if name is not None:
        payload["Name"] = name

    if ttl is not None:
        payload["TTL"] = format_duration(ttl)

    if lock_delay is not None:
        payload["LockDelay"] = format_duration(lock_delay)

    if service_checks:
        payload["ServiceChecks"] = [{"ID": check_id} for check_id in service_checks]

    return Request("PUT", "/v1/session/create", payload=payload)


def renew(session_id: str) -> Request:
    """Renew a session with a TTL, for another TTL.

    Request:
        ```http
        PUT /v1/session/renew/a0b1c2d3-e4f5-a6b7-c8d9-e0f1a2b3c4d5
        ```

    Response, the session like :func:`info`; ``404`` when it ended:
        ```http
        200 OK

        [{"ID": "a0b1c2d3-e4f5-a6b7-c8d9-e0f1a2b3c4d5", "TTL": "15s", ...}]
        ```
    """
    return Request("PUT", f"/v1/session/renew/{_quoted(session_id)}", allow_not_found=True)


def destroy(session_id: str) -> Request:
    """End a session: the keys it holds are released or deleted, after its lock delay.

    Request:
        ```http
        PUT /v1/session/destroy/a0b1c2d3-e4f5-a6b7-c8d9-e0f1a2b3c4d5
        ```

    Response, also for a session that already ended:
        ```http
        200 OK

        true
        ```
    """
    return Request("PUT", f"/v1/session/destroy/{_quoted(session_id)}")


def info(session_id: str) -> Request:
    """Read a session. ``LockDelay`` is in nanoseconds, ``TTL`` in Go's format.

    Request:
        ```http
        GET /v1/session/info/a0b1c2d3-e4f5-a6b7-c8d9-e0f1a2b3c4d5
        ```

    Response, empty, or ``null`` on older agents, when the session ended:
        ```http
        200 OK
        X-Consul-Index: 42

        [
            {
                "ID": "a0b1c2d3-e4f5-a6b7-c8d9-e0f1a2b3c4d5",
                "Name": "jobs/nightly-report",
                "Node": "node-1",
                "LockDelay": 15000000000,
                "Behavior": "release",
                "TTL": "15s",
                "NodeChecks": ["serfHealth"],
                "ServiceChecks": null,
                "CreateIndex": 42,
                "ModifyIndex": 42
            }
        ]
        ```
    """
    return Request("GET", f"/v1/session/info/{_quoted(session_id)}")


def parse_created(response: httpx.Response) -> str:
    session_id = Fields(response, json_body(response)).string("ID")
    if not session_id:
        raise malformed(response, "no session ID")

    return session_id


def parse_renewed(response: httpx.Response) -> bool:
    return response.status_code != NOT_FOUND


def parse_session(response: httpx.Response) -> Session | None:
    if json_body(response) is None:
        return None

    sessions = [_session(Fields(response, raw, "a session")) for raw in json_list(response)]

    return sessions[0] if sessions else None


def _session(fields: Fields) -> Session:
    behavior = fields.string("Behavior") or "release"
    if behavior not in _BEHAVIORS:
        raise fields.invalid("Behavior", "release or delete")  # noqa: EM101  # a field name

    ttl = fields.string("TTL")

    try:
        parsed_ttl = parse_duration(ttl) if ttl else None
    except ValueError as error:
        raise fields.invalid("TTL", "a duration") from error  # noqa: EM101  # a field name

    return Session(
        id=fields.string("ID"),
        name=fields.string("Name"),
        node=fields.string("Node"),
        behavior=behavior,
        ttl=parsed_ttl,
        lock_delay=timedelta(microseconds=fields.integer("LockDelay") / 1000),
        create_index=fields.integer("CreateIndex"),
    )


def _quoted(session_id: str) -> str:
    return quote(session_id, safe="")
