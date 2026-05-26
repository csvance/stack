"""Integration test fixtures: parameterized over fakeredis and a real Redis.

Tests that need a Redis client use the ``redis_client`` fixture. By default, only
the fakeredis backend runs. Pass ``--redis-url redis://host:port`` to also exercise
the same tests against a real Redis instance.
"""

from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import urlparse

import fakeredis
import pytest
import redis as redis_lib


def pytest_addoption(parser):
    parser.addoption(
        "--redis-url",
        action="store",
        default=None,
        help="If set, also run state-store tests against this Redis instance.",
    )


def _backends(config):
    backends = ["fakeredis"]
    if config.getoption("--redis-url"):
        backends.append("real")
    return backends


def pytest_generate_tests(metafunc):
    if "redis_backend" in metafunc.fixturenames:
        metafunc.parametrize("redis_backend", _backends(metafunc.config))


@pytest.fixture
def redis_client(request, redis_backend: str) -> Iterator[redis_lib.Redis]:
    if redis_backend == "fakeredis":
        client = fakeredis.FakeRedis(decode_responses=False)
        try:
            yield client
        finally:
            client.flushdb()
            client.close()
        return

    url = request.config.getoption("--redis-url")
    parsed = urlparse(url)
    # Use db=15 (the highest standard slot) and flush before/after to isolate tests.
    client = redis_lib.Redis(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        password=parsed.password,
        db=15,
        decode_responses=False,
    )
    client.flushdb()
    try:
        yield client
    finally:
        client.flushdb()
        client.close()
