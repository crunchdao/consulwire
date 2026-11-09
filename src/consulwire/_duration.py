"""Durations in the format Consul's Go code parses."""

from __future__ import annotations

from datetime import timedelta


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
