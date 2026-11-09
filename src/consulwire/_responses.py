"""Read the JSON answers of the agent strictly.

A value of the wrong type raises a :class:`ResponseError` naming it, like ``consul answered GET
/v1/health/service/web with `Service.Port` that is not a number``. The message never quotes the
body, which may hold the values of keys.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from consulwire.errors import ResponseError

if TYPE_CHECKING:
    import httpx


def json_body(response: httpx.Response) -> object:
    try:
        return cast("object", response.json())
    except ValueError as error:
        raise malformed(response, "a body that is not JSON") from error


def json_list(response: httpx.Response) -> list[object]:
    body = json_body(response)
    if not isinstance(body, list):
        raise malformed(response, "a body that is not a list")

    return cast("list[object]", body)


def json_strings(response: httpx.Response) -> list[str]:
    body = json_body(response)
    if not _is_strings(body):
        raise malformed(response, "a body that is not a list of texts")

    return cast("list[str]", body)


class Fields:
    """The fields of a JSON object of an answer.

    A missing or ``null`` field reads as empty: ``""``, ``0``, ``()`` or ``{}``.
    """

    def __init__(self, response: httpx.Response, raw: object, label: str = "a body") -> None:
        """Wrap an object of the answer.

        Raises:
            ResponseError: The value is not an object, described by ``label`` like ``an entry``.
        """
        if not isinstance(raw, dict):
            raise malformed(response, f"{label} that is not an object")

        self._response: httpx.Response = response
        self._values: dict[str, object] = cast("dict[str, object]", raw)
        self._prefix: str = ""

    def names(self) -> list[str]:
        """Return the names of the fields, sorted."""
        return sorted(self._values)

    def string(self, name: str) -> str:
        value = self._values.get(name) or ""
        if not isinstance(value, str):
            raise self.invalid(name, "text")

        return value

    def integer(self, name: str) -> int:
        value = self._values.get(name) or 0
        if not isinstance(value, int) or isinstance(value, bool):
            raise self.invalid(name, "a number")

        return value

    def strings(self, name: str) -> tuple[str, ...]:
        value = self._values.get(name)
        if value is None:
            return ()

        if not _is_strings(value):
            raise self.invalid(name, "a list of texts")

        return tuple(cast("list[str]", value))

    def string_map(self, name: str) -> dict[str, str]:
        value = self._values.get(name)
        if value is None:
            return {}

        if not isinstance(value, dict) or not all(
            isinstance(key, str) and isinstance(item, str)
            for key, item in cast("dict[object, object]", value).items()
        ):
            raise self.invalid(name, "an object of texts")

        return cast("dict[str, str]", value)

    def object(self, name: str) -> Fields:
        """Return a nested object. Its fields are named after this one, like ``Service.Port``."""
        nested = Fields(self._response, self._values.get(name), f"`{self._prefix}{name}`")
        nested._prefix = f"{self._prefix}{name}."

        return nested

    def invalid(self, name: str, kind: str) -> ResponseError:
        """Describe a field that is not of the ``kind`` it should be, like ``base64``."""
        return malformed(self._response, f"`{self._prefix}{name}` that is not {kind}")


def _is_strings(value: object) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, str) for item in cast("list[object]", value)
    )


def malformed(response: httpx.Response, problem: str) -> ResponseError:
    request = response.request

    return ResponseError(f"consul answered {request.method} {request.url.path} with {problem}")
