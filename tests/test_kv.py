from __future__ import annotations

import base64
import json
from datetime import timedelta

import httpx
import pytest

from consulwire import HTTPStatusError, ResponseError
from consulwire._transport import Request
from consulwire.kv import Entry
from consulwire.kv._requests import BlockingQuery, parse_entry
from tests.support import FakeAgent


def entry_payload(key: str = "my-service/flag", value: bytes | None = b"on") -> dict[str, object]:
    return {
        "Key": key,
        "Value": None if value is None else base64.b64encode(value).decode(),
        "Flags": 7,
        "CreateIndex": 10,
        "ModifyIndex": 12,
        "LockIndex": 1,
        "Session": "a0b1c2d3-e4f5-a6b7-c8d9-e0f1a2b3c4d5",
    }


def answering(body: object, *, index: int = 12) -> FakeAgent:
    return FakeAgent(body=json.dumps(body), headers={"X-Consul-Index": str(index)})


# --------------------------------------------------------------------------- the requests


def test_get_entry_reads_every_field() -> None:
    agent = answering([entry_payload()])

    entry = agent.consul().kv.get_entry("my-service/flag")

    assert agent.calls() == [("GET", "/v1/kv/my-service/flag")]
    assert agent.requests[0].url.query == b""
    assert entry == Entry(
        key="my-service/flag",
        value=b"on",
        flags=7,
        modify_index=12,
        create_index=10,
        lock_index=1,
        session="a0b1c2d3-e4f5-a6b7-c8d9-e0f1a2b3c4d5",
    )


def test_a_key_without_value_holds_empty_bytes() -> None:
    agent = answering([entry_payload(value=None)])

    assert agent.consul().kv.get("my-service/flag") == b""


def test_keys_are_quoted_but_keep_their_slashes() -> None:
    agent = FakeAgent(status_code=404)

    assert agent.consul().kv.get("my service/a?b") is None

    assert agent.requests[0].url.raw_path == b"/v1/kv/my%20service/a%3Fb"


def test_put_sends_the_raw_value_with_its_options() -> None:
    agent = answering(True)  # noqa: FBT003  # the body Consul answers

    written = agent.consul().kv.put("my-service/flag", "énabled", flags=7, cas=12)

    request = agent.requests[0]
    assert written is True
    assert (request.method, request.url.path) == ("PUT", "/v1/kv/my-service/flag")
    assert dict(request.url.params) == {"flags": "7", "cas": "12"}
    assert request.content == "énabled".encode()


def test_put_without_options_sends_no_parameters() -> None:
    agent = answering(True)  # noqa: FBT003  # the body Consul answers

    _ = agent.consul().kv.put("my-service/data", b"\x00\x01")

    assert agent.requests[0].url.query == b""
    assert agent.requests[0].content == b"\x00\x01"


def test_delete_sends_the_check_and_set_index() -> None:
    agent = answering(False)  # noqa: FBT003  # the body Consul answers

    assert agent.consul().kv.delete("my-service/flag", cas=12) is False

    assert agent.calls() == [("DELETE", "/v1/kv/my-service/flag")]
    assert dict(agent.requests[0].url.params) == {"cas": "12"}


def test_delete_prefix_recurses() -> None:
    agent = answering(True)  # noqa: FBT003  # the body Consul answers

    agent.consul().kv.delete_prefix("my-service/")

    assert agent.calls() == [("DELETE", "/v1/kv/my-service/")]
    assert dict(agent.requests[0].url.params) == {"recurse": "true"}


def test_delete_prefix_refuses_the_whole_store() -> None:
    agent = FakeAgent()

    with pytest.raises(ValueError, match="every key"):
        agent.consul().kv.delete_prefix("")

    assert agent.requests == []


def test_keys_sends_the_separator() -> None:
    agent = answering(["my-service/database/", "my-service/flag"])

    keys = agent.consul().kv.keys("my-service/", separator="/")

    assert keys == ["my-service/database/", "my-service/flag"]
    assert dict(agent.requests[0].url.params) == {"keys": "true", "separator": "/"}


def test_missing_keys_are_empty_lists() -> None:
    consul = FakeAgent(status_code=404).consul()

    assert consul.kv.get_entry("missing") is None
    assert consul.kv.entries("missing/") == []
    assert consul.kv.keys("missing/") == []


def test_other_errors_are_raised() -> None:
    consul = FakeAgent(status_code=403, body="Permission denied").consul()

    with pytest.raises(HTTPStatusError, match="403 to GET /v1/kv/secret: Permission denied"):
        _ = consul.kv.get("secret")


def test_a_blocking_query_waits_longer_than_the_timeout() -> None:
    agent = answering([entry_payload()])
    consul = agent.consul()

    _ = next(consul.kv.watch("my-service/flag", wait=timedelta(seconds=32)))

    request = agent.requests[0]
    assert dict(request.url.params) == {"index": "0", "wait": "32s"}
    assert request.extensions["timeout"] == {
        "connect": 44.0,
        "read": 44.0,
        "write": 44.0,
        "pool": 44.0,
    }


def test_a_request_keeps_the_client_timeout() -> None:
    agent = answering([entry_payload()])

    _ = agent.consul().kv.get("my-service/flag")

    assert agent.requests[0].extensions["timeout"]["read"] == 10.0


# --------------------------------------------------------------------------- the answers


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        json.dumps({"Key": "a"}),
        json.dumps(["a"]),
        json.dumps([{**entry_payload(), "Key": 1}]),
        json.dumps([{**entry_payload(), "Value": 1}]),
        json.dumps([{**entry_payload(), "Session": 1}]),
        json.dumps([{**entry_payload(), "ModifyIndex": "12"}]),
        json.dumps([{**entry_payload(), "Value": "not base64!"}]),
    ],
    ids=["not JSON", "an object", "a string", "key", "value", "session", "index", "base64"],
)
def test_malformed_entries_are_response_errors(body: str) -> None:
    consul = FakeAgent(body=body).consul()

    with pytest.raises(ResponseError, match="consul answered GET /v1/kv/a without"):
        _ = consul.kv.get("a")


@pytest.mark.parametrize("body", [{"a": 1}, ["a", 1]], ids=["an object", "a number"])
def test_malformed_keys_are_response_errors(body: object) -> None:
    consul = answering(body).consul()

    with pytest.raises(ResponseError, match="without a list of keys"):
        _ = consul.kv.keys()


def test_malformed_write_answers_are_response_errors() -> None:
    consul = answering("yes").consul()

    with pytest.raises(ResponseError, match="without true or false"):
        _ = consul.kv.put("a", "b")


@pytest.mark.parametrize("headers", [{}, {"X-Consul-Index": "-1"}], ids=["missing", "negative"])
def test_a_watch_needs_the_index_header(headers: dict[str, str]) -> None:
    consul = FakeAgent(body=json.dumps([entry_payload()]), headers=headers).consul()

    with pytest.raises(ResponseError, match="without an X-Consul-Index header"):
        _ = next(consul.kv.watch("a"))


def answer(index: int, body: object) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"X-Consul-Index": str(index)},
        json=body,
        request=httpx.Request("GET", "http://consul:8500/v1/kv/a"),
    )


def test_a_blocking_query_reports_only_changes() -> None:
    sent_indexes: list[int] = []

    def request(index: int) -> Request:
        sent_indexes.append(index)
        return Request("GET", "/v1/kv/a")

    query = BlockingQuery(request, parse_entry)

    _ = query.request()
    first = query.receive(answer(12, [entry_payload(value=b"1")]))
    _ = query.request()
    same = query.receive(answer(12, [entry_payload(value=b"1")]))
    _ = query.request()
    changed = query.receive(answer(13, [entry_payload(value=b"2")]))
    _ = query.request()

    assert first is not None
    assert first[0] is not None
    assert first[0].value == b"1"
    assert same is None
    assert changed is not None
    assert changed[0] is not None
    assert changed[0].value == b"2"
    assert sent_indexes == [0, 12, 12, 13]


def test_a_blocking_query_starts_over_when_the_index_goes_backwards() -> None:
    sent_indexes: list[int] = []

    def request(index: int) -> Request:
        sent_indexes.append(index)
        return Request("GET", "/v1/kv/a")

    query = BlockingQuery(request, parse_entry)

    _ = query.receive(answer(12, []))
    _ = query.request()
    _ = query.receive(answer(5, []))
    _ = query.request()
    _ = query.receive(answer(0, []))
    _ = query.request()

    assert sent_indexes == [12, 0, 1]


def test_printing_an_entry_never_shows_its_value() -> None:
    entry = Entry(
        key="my-service/password",
        value=b"a0b1c2d3e4f5",
        flags=0,
        modify_index=12,
        create_index=10,
        lock_index=0,
        session=None,
    )

    assert "a0b1c2d3e4f5" not in repr(entry)
    assert "a0b1c2d3e4f5" not in str(entry)
    assert "my-service/password" in repr(entry)


def test_malformed_answers_never_show_the_body() -> None:
    body = json.dumps([{**entry_payload(value=b"a0b1c2d3e4f5"), "Flags": "7"}])
    consul = FakeAgent(body=body).consul()

    with pytest.raises(ResponseError) as raised:
        _ = consul.kv.get("my-service/password")

    assert base64.b64encode(b"a0b1c2d3e4f5").decode() not in str(raised.value)
