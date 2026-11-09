"""Requests and answers of the key/value API, written once for both clients."""

from __future__ import annotations

import base64
import binascii
from typing import TYPE_CHECKING
from urllib.parse import quote

from consulwire._blocking import blocking_parameters
from consulwire._responses import Fields, json_body, json_list, json_strings, malformed
from consulwire._transport import NOT_FOUND, Request
from consulwire.kv._entry import Entry

if TYPE_CHECKING:
    from datetime import timedelta

    import httpx


def get(key: str, *, index: int = 0, wait: timedelta | None = None) -> Request:
    """Read one key. With ``wait``, a blocking query held until the key changes after ``index``.

    Request:
        ```http
        GET /v1/kv/my-service/flag?index=12&wait=300s
        ```

    Response, a list of one entry, the value in base64 (``on``); ``404`` without body when the key
    does not exist:
        ```http
        200 OK
        X-Consul-Index: 12

        [
            {
                "Key": "my-service/flag",
                "Value": "b24=",
                "Flags": 0,
                "CreateIndex": 10,
                "ModifyIndex": 12,
                "LockIndex": 0
            }
        ]
        ```
    """
    return Request(
        "GET",
        _path(key),
        parameters=blocking_parameters(index, wait),
        wait=wait,
        allow_not_found=True,
    )


def entries(prefix: str, *, index: int = 0, wait: timedelta | None = None) -> Request:
    """Read every key starting with the prefix, blocking like :func:`get` with ``wait``.

    Request:
        ```http
        GET /v1/kv/my-service/?recurse=true&index=12&wait=300s
        ```

    Response, the entries sorted by key, a value without content being ``null``; ``404`` without
    body when no key starts with the prefix:
        ```http
        200 OK
        X-Consul-Index: 14

        [
            {
                "Key": "my-service/flag",
                "Value": "b24=",
                "Flags": 0,
                "CreateIndex": 10,
                "ModifyIndex": 12,
                "LockIndex": 0
            },
            {
                "Key": "my-service/notes",
                "Value": null,
                "Flags": 0,
                "CreateIndex": 14,
                "ModifyIndex": 14,
                "LockIndex": 0
            }
        ]
        ```
    """
    return Request(
        "GET",
        _path(prefix),
        parameters={"recurse": "true", **blocking_parameters(index, wait)},
        wait=wait,
        allow_not_found=True,
    )


def keys(prefix: str, *, separator: str | None) -> Request:
    """List the names of the keys starting with the prefix, cut after ``separator``.

    Request:
        ```http
        GET /v1/kv/my-service/?keys=true&separator=/
        ```

    Response, sorted; ``404`` without body when no key starts with the prefix:
        ```http
        200 OK

        ["my-service/database/", "my-service/flag"]
        ```
    """
    parameters = {"keys": "true"}

    if separator is not None:
        parameters["separator"] = separator

    return Request("GET", _path(prefix), parameters=parameters, allow_not_found=True)


def put(  # noqa: PLR0913  # the options of a Consul write
    key: str,
    value: bytes | str,
    *,
    flags: int | None,
    cas: int | None,
    acquire: str | None,
    release: str | None,
) -> Request:
    """Write a key, the raw value as body. ``acquire`` and ``release`` take a session ID.

    Request:
        ```http
        PUT /v1/kv/my-service/flag?flags=7&cas=12

        on
        ```

    Response, ``false`` when ``cas`` did not match, or the lock is held by another session:
        ```http
        200 OK

        true
        ```

    Raises:
        ValueError: Both ``acquire`` and ``release`` are given.
    """
    if acquire is not None and release is not None:
        message = "a write either acquires the lock of a key or releases it, not both"
        raise ValueError(message)

    parameters: dict[str, str] = {}

    if flags is not None:
        parameters["flags"] = str(flags)

    if cas is not None:
        parameters["cas"] = str(cas)

    if acquire is not None:
        parameters["acquire"] = acquire

    if release is not None:
        parameters["release"] = release

    content = value.encode() if isinstance(value, str) else value

    return Request("PUT", _path(key), parameters=parameters, content=content)


def delete(key: str, *, cas: int | None) -> Request:
    """Delete a key, missing or not.

    Request:
        ```http
        DELETE /v1/kv/my-service/flag?cas=12
        ```

    Response, ``false`` only when ``cas`` did not match:
        ```http
        200 OK

        true
        ```
    """
    return Request("DELETE", _path(key), parameters={} if cas is None else {"cas": str(cas)})


def delete_prefix(prefix: str) -> Request:
    """Delete every key starting with the prefix.

    Request:
        ```http
        DELETE /v1/kv/my-service/?recurse=true
        ```

    Response:
        ```http
        200 OK

        true
        ```

    Raises:
        ValueError: The prefix is empty, which would delete every key of the store.
    """
    if not prefix:
        message = "an empty prefix would delete every key of the store"
        raise ValueError(message)

    return Request("DELETE", _path(prefix), parameters={"recurse": "true"})


def parse_entries(response: httpx.Response) -> list[Entry]:
    if response.status_code == NOT_FOUND:
        return []

    return [_entry(Fields(response, raw, "an entry")) for raw in json_list(response)]


def parse_entry(response: httpx.Response) -> Entry | None:
    found = parse_entries(response)

    return found[0] if found else None


def parse_keys(response: httpx.Response) -> list[str]:
    if response.status_code == NOT_FOUND:
        return []

    return json_strings(response)


def parse_boolean(response: httpx.Response) -> bool:
    body = json_body(response)
    if not isinstance(body, bool):
        raise malformed(response, "a body that is not true or false")

    return body


def _path(key: str) -> str:
    return f"/v1/kv/{quote(key, safe='/')}"


def _entry(fields: Fields) -> Entry:
    try:
        value = base64.b64decode(fields.string("Value"), validate=True)
    except binascii.Error as error:
        raise fields.invalid("Value", "base64") from error  # noqa: EM101  # a field name

    return Entry(
        key=fields.string("Key"),
        value=value,
        flags=fields.integer("Flags"),
        modify_index=fields.integer("ModifyIndex"),
        create_index=fields.integer("CreateIndex"),
        lock_index=fields.integer("LockIndex"),
        session=fields.string("Session") or None,
    )
