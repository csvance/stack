"""Idempotency token claim backed by Redis ``SET NX EX``.

Each ADO webhook delivery includes a ``notificationId``. We atomically claim
the token before spawning a background task. Redelivered events with the same
id cannot acquire the claim and are silently dropped (the bot returns 200).
"""

from __future__ import annotations

from redis import Redis


def claim(
    redis_client: Redis,
    *,
    key_prefix: str,
    project: str,
    notification_id: str,
    ttl_seconds: int,
) -> bool:
    """Return True if we won the claim; False if it was already taken."""
    key = f"{key_prefix}:{project}:idempotency:{notification_id}"
    result = redis_client.set(key, b"1", nx=True, ex=ttl_seconds)
    return bool(result)
