from __future__ import annotations

import threading
import time
from datetime import timedelta
from typing import TYPE_CHECKING

import anyio
import httpx
import pytest

from consulwire import AsyncConsul, Consul
from consulwire import locks as consul_locks
from consulwire.testing import AGENT_URL, InMemoryAgent

if TYPE_CHECKING:
    from collections.abc import Callable

TTL = timedelta(milliseconds=200)
"""Short enough for renewals to happen during a test: the in-memory agent has no lower bound."""


@pytest.fixture(autouse=True)
def short_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(consul_locks, "RETRY_INTERVAL", timedelta(milliseconds=20))


@pytest.fixture
def agent() -> InMemoryAgent:
    return InMemoryAgent()


@pytest.fixture
def consul(agent: InMemoryAgent) -> Consul:
    return agent.consul()


def failing(
    agent: InMemoryAgent, refuse: Callable[[httpx.Request], httpx.Response | None]
) -> Consul:
    """Return a client of the agent whose requests ``refuse`` answers, when it answers."""
    inner = agent.transport()

    def handle(request: httpx.Request) -> httpx.Response:
        return refuse(request) or inner.handle_request(request)

    return Consul(AGENT_URL, transport=httpx.MockTransport(handle))


def async_failing(
    agent: InMemoryAgent,
    refuse: Callable[[httpx.Request], httpx.Response | None],
) -> AsyncConsul:
    inner = agent.async_transport()

    async def handle(request: httpx.Request) -> httpx.Response:
        return refuse(request) or await inner.handle_async_request(request)

    return AsyncConsul(AGENT_URL, transport=httpx.MockTransport(handle))


def refuse_first_acquire() -> Callable[[httpx.Request], httpx.Response | None]:
    """Refuse the first try on a free key, like Consul during the lock delay of its last holder."""
    refused: list[httpx.Request] = []

    def refuse(request: httpx.Request) -> httpx.Response | None:
        if "acquire" in request.url.params and not refused:
            refused.append(request)
            return httpx.Response(200, json=False)

        return None

    return refuse


def failing_path(part: str) -> Callable[[httpx.Request], httpx.Response | None]:
    def refuse(request: httpx.Request) -> httpx.Response | None:
        if part in str(request.url):
            return httpx.Response(500, text="agent is restarting")

        return None

    return refuse


# --------------------------------------------------------------------------- the sync lock


def test_a_lock_holds_the_key_while_entered(consul: Consul) -> None:
    with consul.lock("job", value="worker-1", ttl=TTL) as lock:
        entry = consul.kv.get_entry("job")
        session_id = str(lock.session_id)

        assert entry is not None
        assert (entry.value, entry.session, entry.lock_index, lock.lock_index) == (
            b"worker-1",
            session_id,
            1,
            1,
        )
        assert not lock.lost.is_set()

    entry = consul.kv.get_entry("job")
    assert entry is not None
    assert entry.session is None
    assert (lock.session_id, lock.lock_index) == (None, None)
    assert consul.session.info(session_id) is None


def test_one_holder_at_a_time(consul: Consul) -> None:
    with (
        consul.lock("job", ttl=TTL),
        pytest.raises(TimeoutError, match="held by another session"),
        consul.lock("job", ttl=TTL, timeout=timedelta(0)),
    ):
        pytest.fail("the lock was taken twice")


def test_a_timeout_stops_waiting(consul: Consul) -> None:
    with consul.lock("job", ttl=TTL):
        started = time.monotonic()

        with pytest.raises(TimeoutError), consul.lock("job", ttl=TTL, timeout=TTL):
            pytest.fail("the lock was taken twice")

        assert time.monotonic() - started >= TTL.total_seconds()


def test_waiters_take_the_lock_in_turn(agent: InMemoryAgent, consul: Consul) -> None:
    taken: list[int | None] = []

    def wait_for_the_lock() -> None:
        with agent.consul().lock("job", ttl=TTL) as lock:
            taken.append(lock.lock_index)

    with consul.lock("job", ttl=TTL) as first:
        waiter = threading.Thread(target=wait_for_the_lock)
        waiter.start()
        time.sleep(0.1)

        assert taken == []
        taken.append(first.lock_index)

    waiter.join(5)
    assert taken == [1, 2]


def test_a_free_key_refused_is_tried_again(agent: InMemoryAgent) -> None:
    consul = failing(agent, refuse_first_acquire())

    with consul.lock("job", ttl=TTL) as lock:
        assert lock.lock_index == 1


def test_a_lost_session_sets_lost(consul: Consul, caplog: pytest.LogCaptureFixture) -> None:
    with consul.lock("job", ttl=TTL) as lock:
        consul.session.destroy(str(lock.session_id))

        assert lock.lost.wait(2)

    assert "consul lock lost, its session ended - key=job" in caplog.text


def test_renewals_failing_for_a_ttl_lose_the_lock(
    agent: InMemoryAgent,
    caplog: pytest.LogCaptureFixture,
) -> None:
    consul = failing(agent, failing_path("/v1/session/renew/"))

    with consul.lock("job", ttl=TTL) as lock:
        assert lock.lost.wait(2)

    assert "consul lock renewal failed - key=job" in caplog.text


def test_a_failed_release_ends_the_session_anyway(
    agent: InMemoryAgent,
    caplog: pytest.LogCaptureFixture,
) -> None:
    consul = failing(agent, failing_path("release="))

    with consul.lock("job", ttl=TTL):
        pass

    entry = agent.consul().kv.get_entry("job")

    assert "consul lock release failed, it ends with its session - key=job" in caplog.text
    assert entry is not None
    assert entry.session is None


def test_a_failed_end_is_left_to_the_ttl(
    agent: InMemoryAgent,
    caplog: pytest.LogCaptureFixture,
) -> None:
    consul = failing(agent, failing_path("/v1/session/destroy/"))

    with consul.lock("job", ttl=TTL):
        pass

    assert "consul session end failed, it expires after its TTL - key=job" in caplog.text


def fail_computation() -> None:
    message = "computation failed"
    raise ValueError(message)


def test_an_error_in_the_block_releases_the_lock(consul: Consul) -> None:
    with pytest.raises(ValueError, match="computation failed"), consul.lock("job", ttl=TTL):
        fail_computation()

    entry = consul.kv.get_entry("job")
    assert entry is not None
    assert entry.session is None


def test_a_lock_cannot_be_entered_twice(consul: Consul) -> None:
    lock = consul.lock("job", ttl=TTL)

    with lock, pytest.raises(RuntimeError, match="already held"), lock:
        pass


def test_leaving_a_lock_not_held_does_nothing(consul: Consul) -> None:
    consul.lock("job", ttl=TTL).__exit__(None, None, None)

    assert consul.kv.get_entry("job") is None


# --------------------------------------------------------------------------- the async lock


@pytest.mark.anyio
async def test_the_async_lock(agent: InMemoryAgent) -> None:
    async with agent.async_consul() as consul:
        async with consul.lock("job", value="worker-1", ttl=TTL) as lock:
            entry = await consul.kv.get_entry("job")

            assert entry is not None
            assert (entry.value, entry.session, lock.lock_index) == (
                b"worker-1",
                lock.session_id,
                1,
            )

            with pytest.raises(TimeoutError):
                async with consul.lock("job", ttl=TTL, timeout=timedelta(0)):
                    pytest.fail("the lock was taken twice")

            await consul.session.destroy(str(lock.session_id))

            with anyio.fail_after(2):
                await lock.lost.wait()

        assert lock.session_id is None

        with pytest.raises(RuntimeError, match="already held"):
            async with lock, lock:
                pass

        await lock.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_the_async_lock_waits_and_retries(agent: InMemoryAgent) -> None:
    consul = async_failing(agent, refuse_first_acquire())

    async with consul.lock("job", ttl=TTL) as first:
        with pytest.raises(TimeoutError):
            async with consul.lock("job", ttl=TTL, timeout=TTL):
                pytest.fail("the lock was taken twice")

        assert first.lock_index == 1

    async with consul.lock("job", ttl=TTL) as second:
        assert second.lock_index == 2


@pytest.mark.anyio
async def test_the_async_lock_failures_are_logged(
    agent: InMemoryAgent,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response | None:
        for part in ("/v1/session/renew/", "release=", "/v1/session/destroy/"):
            response = failing_path(part)(request)
            if response is not None:
                return response

        return None

    consul = async_failing(agent, refuse)

    async with consul.lock("job", ttl=TTL) as lock:
        with anyio.fail_after(2):
            await lock.lost.wait()

    assert "consul lock renewal failed - key=job" in caplog.text
    assert "consul lock release failed" in caplog.text
    assert "consul session end failed" in caplog.text


@pytest.mark.anyio
async def test_async_waiters_take_the_lock_in_turn(agent: InMemoryAgent) -> None:
    taken: list[int | None] = []

    async with agent.async_consul() as consul:

        async def wait_for_the_lock() -> None:
            async with consul.lock("job", ttl=TTL) as lock:
                taken.append(lock.lock_index)

        async with anyio.create_task_group() as group, consul.lock("job", ttl=TTL) as first:
            _ = group.start_soon(wait_for_the_lock)
            await anyio.sleep(0.1)

            assert taken == []
            taken.append(first.lock_index)

    assert taken == [1, 2]
