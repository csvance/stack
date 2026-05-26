"""Idempotency token claim against fakeredis."""

from __future__ import annotations

import fakeredis

from stack_bot import idempotency


def _claim(client, notif="abc"):
    return idempotency.claim(
        client, key_prefix="stack", project="proj",
        notification_id=notif, ttl_seconds=3600,
    )


def test_first_claim_wins():
    client = fakeredis.FakeRedis(decode_responses=False)
    assert _claim(client) is True


def test_second_claim_for_same_id_fails():
    client = fakeredis.FakeRedis(decode_responses=False)
    assert _claim(client, "x") is True
    assert _claim(client, "x") is False


def test_different_ids_each_claim_independently():
    client = fakeredis.FakeRedis(decode_responses=False)
    assert _claim(client, "a") is True
    assert _claim(client, "b") is True


def test_ttl_set():
    client = fakeredis.FakeRedis(decode_responses=False)
    _claim(client, "abc")
    ttl = client.ttl("stack:proj:idempotency:abc")
    assert 0 < ttl <= 3600
