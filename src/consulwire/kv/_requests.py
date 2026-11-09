"""Requests and answers of the key/value API, written once for both clients."""

from __future__ import annotations

import base64
import binascii
from typing import TYPE_CHECKING, Generic, TypeVar, cast
from urllib.parse import quote

from consulwire._duration import format_duration
from consulwire._transport import NOT_FOUND, Request
from consulwire.errors import ResponseError
from consulwire.kv._entry import Entry

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import timedelta

    import httpx

_T = TypeVar("_T")


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
        parameters=_blocking_parameters(index, wait),
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
        parameters={"recurse": "true", **_blocking_parameters(index, wait)},
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


def put(key: str, value: bytes | str, *, flags: int | None, cas: int | None) -> Request:
    """Write a key, the raw value as body.

    Request:
        ```http
        PUT /v1/kv/my-service/flag?flags=7&cas=12

        on
        ```

    Response, ``false`` only when ``cas`` did not match:
        ```http
        200 OK

        true
        ```
    """
    parameters: dict[str, str] = {}

    if flags is not None:
        parameters["flags"] = str(flags)

    if cas is not None:
        parameters["cas"] = str(cas)

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

    body = _json(response)
    if not isinstance(body, list):
        raise _unexpected(response, "a list of entries")

    return [_entry(response, raw) for raw in cast("list[object]", body)]


def parse_entry(response: httpx.Response) -> Entry | None:
    found = parse_entries(response)

    return found[0] if found else None


def parse_keys(response: httpx.Response) -> list[str]:
    if response.status_code == NOT_FOUND:
        return []

    body = _json(response)
    if not isinstance(body, list):
        raise _unexpected(response, "a list of keys")

    found = cast("list[object]", body)
    if not all(isinstance(key, str) for key in found):
        raise _unexpected(response, "a list of keys")

    return cast("list[str]", found)


def parse_boolean(response: httpx.Response) -> bool:
    body = _json(response)
    if not isinstance(body, bool):
        raise _unexpected(response, "true or false")

    return body


def parse_index(response: httpx.Response) -> int:
    index = cast("str", response.headers.get("X-Consul-Index", ""))
    if not index.isdigit():
        raise _unexpected(response, "an X-Consul-Index header")

    return int(index)


class BlockingQuery(Generic[_T]):
    """Index bookkeeping of a watch, shared by both clients.

    Consul holds a blocking query until the data changes or ``wait`` runs out, then answers the
    current data either way. Only changes are reported: the first answer, then each different one.
    """

    def __init__(
        self,
        request: Callable[[int], Request],
        parse: Callable[[httpx.Response], _T],
    ) -> None:
        self._request: Callable[[int], Request] = request
        self._parse: Callable[[httpx.Response], _T] = parse
        self._index: int = 0
        self._last: tuple[_T] | None = None

    def request(self) -> Request:
        return self._request(self._index)

    def receive(self, response: httpx.Response) -> tuple[_T] | None:
        """Return the data in a tuple when it changed, ``None`` otherwise."""
        index = parse_index(response)

        # Following Consul's guidance: an index going backwards means the agent's state was reset,
        # so start over, and an index of 0 would make the next query return at once.
        self._index = 0 if index < self._index else max(index, 1)

        data = self._parse(response)

        if self._last is not None and self._last[0] == data:
            return None

        self._last = (data,)

        return self._last


def _path(key: str) -> str:
    return f"/v1/kv/{quote(key, safe='/')}"


def _blocking_parameters(index: int, wait: timedelta | None) -> dict[str, str]:
    if wait is None:
        return {}

    return {"index": str(index), "wait": format_duration(wait)}


def _json(response: httpx.Response) -> object:
    try:
        return cast("object", response.json())
    except ValueError as error:
        raise _unexpected(response, "JSON") from error


def _entry(response: httpx.Response, raw: object) -> Entry:
    if not isinstance(raw, dict):
        raise _unexpected(response, "a list of entries")

    fields = cast("dict[str, object]", raw)

    key = fields.get("Key")
    value = fields.get("Value")
    session = fields.get("Session")
    indexes = [fields.get(name, 0) for name in ("Flags", "ModifyIndex", "CreateIndex", "LockIndex")]

    if (
        not isinstance(key, str)
        or (value is not None and not isinstance(value, str))
        or (session is not None and not isinstance(session, str))
        or not all(isinstance(number, int) for number in indexes)
    ):
        raise _unexpected(response, "a list of entries")

    flags, modify_index, create_index, lock_index = cast("list[int]", indexes)

    try:
        decoded = base64.b64decode(value, validate=True) if value else b""
    except binascii.Error as error:
        raise _unexpected(response, "base64 values") from error

    return Entry(
        key=key,
        value=decoded,
        flags=flags,
        modify_index=modify_index,
        create_index=create_index,
        lock_index=lock_index,
        session=session or None,
    )


def _unexpected(response: httpx.Response, expected: str) -> ResponseError:
    """Describe a malformed answer without its body, which holds the values of the keys."""
    request = response.request

    return ResponseError(f"consul answered {request.method} {request.url.path} without {expected}")
