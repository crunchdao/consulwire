"""Durations in the format Consul's Go code parses."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Final, cast

_PART: Final = re.compile(r"(\d+)(us|ms|s|m|h)")

_UNITS: Final = {
    "us": timedelta(microseconds=1),
    "ms": timedelta(milliseconds=1),
    "s": timedelta(seconds=1),
    "m": timedelta(minutes=1),
    "h": timedelta(hours=1),
}


def format_duration(duration: timedelta) -> str:
    """Format a duration the way Go parses it: ``10s``, ``1500ms`` or ``250us``.

    Raises:
        ValueError: The duration is negative.
    """
    if duration < timedelta(0):
        message = f"a duration must not be negative, not {duration}"
        raise ValueError(message)

    microseconds = duration // timedelta(microseconds=1)

    if microseconds % 1_000_000 == 0:
        return f"{microseconds // 1_000_000}s"

    if microseconds % 1_000 == 0:
        return f"{microseconds // 1_000}ms"

    return f"{microseconds}us"


def parse_duration(duration: str) -> timedelta:
    """Read a duration written the way Go formats it, like ``1m30s`` or ``1500ms``.

    Raises:
        ValueError: The text is not a duration.
    """
    if not duration or _PART.sub("", duration):
        message = f"not a duration: `{duration}`"
        raise ValueError(message)

    total = timedelta(0)

    for part in _PART.finditer(duration):
        amount, unit = cast("tuple[str, str]", part.groups())
        total += int(amount) * _UNITS[unit]

    return total
