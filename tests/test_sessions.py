from __future__ import annotations

import json
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from consulwire import HTTPStatusError, ResponseError
from consulwire.session import Session
from consulwire.testing import NODE, InMemoryAgent
from tests.support import FakeAgent, answering

if TYPE_CHECKING:
    from consulwire import Consul

SESSION_ID = "a0b1c2d3-e4f5-a6b7-c8d9-e0f1a2b3c4d5"


def session_payload(**overrides: object) -> dict[str, object]:
    return {
        "ID": SESSION_ID,
        "Name": "jobs/nightly-report",
        "Node": "node-1",
        "LockDelay": 15_000_000_000,
        "Behavior": "release",
        "TTL": "15s",
        "NodeChecks": ["serfHealth"],
        "ServiceChecks": None,
        "CreateIndex": 42,
        "ModifyIndex": 42,
        **overrides,
    }


# --------------------------------------------------------------------------- the requests


def test_create_sends_every_field() -> None:
    agent = answering({"ID": SESSION_ID})

    session_id = agent.consul().session.create(
        name="jobs/nightly-report",
        ttl=timedelta(seconds=15),
        lock_delay=timedelta(seconds=5),
        behavior="delete",
        service_checks=["service:my-service"],
    )

    assert session_id == SESSION_ID
    assert agent.calls() == [("PUT", "/v1/session/create")]
    assert agent.sent_json() == {
        "Name": "jobs/nightly-report",
        "TTL": "15s",
        "LockDelay": "5s",
        "Behavior": "delete",
        "ServiceChecks": [{"ID": "service:my-service"}],
    }


def test_create_leaves_out_what_is_not_set() -> None:
    agent = answering({"ID": SESSION_ID})

    _ = agent.consul().session.create()

    assert agent.sent_json() == {"Behavior": "release"}


def test_create_needs_the_session_id() -> None:
    with pytest.raises(ResponseError, match="with no session ID"):
        _ = answering({}).consul().session.create()


def test_renew_tells_whether_the_session_still_exists() -> None:
    assert answering([session_payload()]).consul().session.renew(SESSION_ID) is True

    agent = FakeAgent(status_code=404, body=f"Session id '{SESSION_ID}' not found")
    assert agent.consul().session.renew(SESSION_ID) is False
    assert agent.calls() == [("PUT", f"/v1/session/renew/{SESSION_ID}")]


def test_destroy() -> None:
    agent = answering(True)  # noqa: FBT003  # the body Consul answers

    agent.consul().session.destroy(SESSION_ID)

    assert agent.calls() == [("PUT", f"/v1/session/destroy/{SESSION_ID}")]


def test_info_reads_every_field() -> None:
    agent = answering([session_payload()])

    assert agent.consul().session.info(SESSION_ID) == Session(
        id=SESSION_ID,
        name="jobs/nightly-report",
        node="node-1",
        behavior="release",
        ttl=timedelta(seconds=15),
        lock_delay=timedelta(seconds=15),
        create_index=42,
    )
    assert agent.calls() == [("GET", f"/v1/session/info/{SESSION_ID}")]


def test_a_session_without_ttl() -> None:
    session = answering([session_payload(TTL="")]).consul().session.info(SESSION_ID)

    assert session is not None
    assert session.ttl is None


@pytest.mark.parametrize("body", [[], None], ids=["empty", "null"])
def test_an_ended_session_has_no_info(body: object) -> None:
    assert answering(body).consul().session.info(SESSION_ID) is None


@pytest.mark.parametrize(
    ("overrides", "problem"),
    [
        ({"Behavior": "keep"}, "`Behavior` that is not release or delete"),
        ({"TTL": "a while"}, "`TTL` that is not a duration"),
        ({"LockDelay": "15s"}, "`LockDelay` that is not a number"),
    ],
    ids=["behavior", "ttl", "lock delay"],
)
def test_malformed_sessions_are_response_errors(overrides: dict[str, object], problem: str) -> None:
    consul = FakeAgent(body=json.dumps([session_payload(**overrides)])).consul()

    with pytest.raises(ResponseError, match=problem):
        _ = consul.session.info(SESSION_ID)


def test_put_acquires_and_releases_for_a_session() -> None:
    agent = answering(True)  # noqa: FBT003  # the body Consul answers
    consul = agent.consul()

    _ = consul.kv.put("lock", "holder", acquire=SESSION_ID)
    _ = consul.kv.put("lock", "holder", release=SESSION_ID)

    assert [dict(request.url.params) for request in agent.requests] == [
        {"acquire": SESSION_ID},
        {"release": SESSION_ID},
    ]


def test_put_refuses_to_acquire_and_release_at_once() -> None:
    agent = FakeAgent()

    with pytest.raises(ValueError, match="not both"):
        _ = agent.consul().kv.put("lock", "", acquire=SESSION_ID, release=SESSION_ID)

    assert agent.requests == []


@pytest.mark.anyio
async def test_the_async_sections() -> None:
    agent = InMemoryAgent()

    async with agent.async_consul() as consul:
        session_id = await consul.session.create(ttl=timedelta(seconds=10))

        assert await consul.session.renew(session_id) is True
        assert await consul.kv.put("lock", "", acquire=session_id) is True

        session = await consul.session.info(session_id)
        assert session is not None
        assert session.ttl == timedelta(seconds=10)

        await consul.session.destroy(session_id)

        assert await consul.session.info(session_id) is None
        assert await consul.session.renew(session_id) is False


# --------------------------------------------------------------------------- the in-memory agent


@pytest.fixture
def consul() -> Consul:
    return InMemoryAgent().consul()


def test_in_memory_sessions(consul: Consul) -> None:
    session_id = consul.session.create(name="job", lock_delay=timedelta(seconds=3))

    session = consul.session.info(session_id)
    assert session is not None
    assert (session.name, session.node, session.lock_delay) == ("job", NODE, timedelta(seconds=3))

    default = consul.session.info(consul.session.create())
    assert default is not None
    assert (default.ttl, default.lock_delay) == (None, timedelta(seconds=15))


def test_one_session_holds_a_key_at_a_time(consul: Consul) -> None:
    first = consul.session.create()
    second = consul.session.create()

    assert consul.kv.put("lock", "first", acquire=first) is True
    assert consul.kv.put("lock", "first again", acquire=first) is True
    assert consul.kv.put("lock", "second", acquire=second) is False
    assert consul.kv.put("lock", "second", release=second) is False

    entry = consul.kv.get_entry("lock")
    assert entry is not None
    assert (entry.value, entry.session, entry.lock_index) == (b"first again", first, 1)

    assert consul.kv.put("lock", "unlocked", release=first) is True
    assert consul.kv.put("lock", "second", acquire=second) is True

    entry = consul.kv.get_entry("lock")
    assert entry is not None
    assert (entry.session, entry.lock_index) == (second, 2)


def test_a_plain_write_keeps_the_lock(consul: Consul) -> None:
    session_id = consul.session.create()
    _ = consul.kv.put("lock", "holder", acquire=session_id)

    _ = consul.kv.put("lock", "rewritten")

    entry = consul.kv.get_entry("lock")
    assert entry is not None
    assert (entry.value, entry.session) == (b"rewritten", session_id)


def test_an_unknown_session_cannot_acquire(consul: Consul) -> None:
    with pytest.raises(HTTPStatusError, match='invalid session "missing"'):
        _ = consul.kv.put("lock", "", acquire="missing")


@pytest.mark.parametrize(
    ("behavior", "left"),
    [("release", {"lock": b"holder", "other": b"kept"}), ("delete", {"other": b"kept"})],
)
def test_ending_a_session_frees_its_keys(consul: Consul, behavior: str, left: object) -> None:
    session_id = consul.session.create(behavior="delete" if behavior == "delete" else "release")
    _ = consul.kv.put("lock", "holder", acquire=session_id)
    _ = consul.kv.put("other", "kept")

    consul.session.destroy(session_id)
    consul.session.destroy(session_id)

    assert {entry.key: entry.value for entry in consul.kv.entries()} == left
    assert all(entry.session is None for entry in consul.kv.entries())
