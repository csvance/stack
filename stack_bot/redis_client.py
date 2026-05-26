"""Helpers for building the Redis client the bot uses.

We use the sync ``redis.Redis`` client and wrap calls in ``asyncio.to_thread``
from inside async handlers. This keeps the codebase aligned with the CLI's
sync usage (the state-store module is sync) without forcing an async rewrite.
"""

from __future__ import annotations

from redis import Redis

from stack_bot.config import RedisConfig


def connect(config: RedisConfig) -> Redis:
    return Redis(
        host=config.host,
        port=config.port,
        password=config.password,
        db=config.db,
        decode_responses=False,
    )
