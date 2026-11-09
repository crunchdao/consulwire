"""A key of the store, as Consul describes it."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Entry:
    """A key of the store, with its value and the indexes used for check-and-set.

    Attributes:
        key: Full key, like ``my-service/feature-flag``.
        value: Raw value, decoded by the application, like ``entry.value.decode()``. Empty when the
            key holds none. Left out of ``repr()``, so printing or logging an entry never shows a
            secret.
        flags: Number the application attached to the key, ``0`` by default.
        modify_index: Index of the last write. Given as ``cas``, a write only happens when the key
            did not change since.
        create_index: Index of the write that created the key.
        lock_index: Number of times a session acquired the key.
        session: Session holding the lock on the key.
    """

    key: str
    value: bytes = field(repr=False)
    flags: int
    modify_index: int
    create_index: int
    lock_index: int
    session: str | None
