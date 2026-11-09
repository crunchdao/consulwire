"""A session, as Consul describes it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeAlias

if TYPE_CHECKING:
    from datetime import timedelta

SessionBehavior: TypeAlias = Literal["release", "delete"]
"""What happens to the keys a session holds when it ends: released, or deleted."""


@dataclass(frozen=True, slots=True)
class Session:
    """A session, which can hold locks on keys of the store until it ends.

    Attributes:
        id: Session ID, given to ``acquire=`` and ``release=`` of :meth:`consulwire.kv.KV.put`.
        name: Name shown in the UI.
        node: Name of the node the session was created on: it ends when the node fails.
        behavior: What happens to the keys it holds when it ends.
        ttl: Time after the last renewal before it ends, ``None`` for a session without one.
        lock_delay: Time a key it held stays unlockable after it ends.
        create_index: Index of the write that created it.
    """

    id: str
    name: str
    node: str
    behavior: SessionBehavior
    ttl: timedelta | None
    lock_delay: timedelta
    create_index: int
