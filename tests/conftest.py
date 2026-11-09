from __future__ import annotations

import pytest

CONSUL_VARIABLES = (
    "CONSUL_HTTP_ADDR",
    "CONSUL_HTTP_SSL",
    "CONSUL_HTTP_TOKEN",
    "CONSUL_HOST",
    "CONSUL_PORT",
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def no_consul_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in CONSUL_VARIABLES:
        monkeypatch.delenv(name, raising=False)
