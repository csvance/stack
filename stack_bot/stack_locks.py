"""Per-stack ``asyncio.Lock`` registry.

The bot serializes background work on the same stack so that two webhooks for
the same (project, prefix) don't race on git state in distinct ephemeral
workspaces. Locks are created lazily and never evicted; total count is bounded
by the number of distinct stacks the bot ever handles.

If this ever moves to a multi-replica deployment, the lock has to become a
distributed Redis lock. Single-process is fine for now.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}


def get(project: str, prefix: str) -> asyncio.Lock:
    key = (project, prefix)
    lock = _LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[key] = lock
    return lock


@asynccontextmanager
async def acquire_for(project: str, prefix: str) -> AsyncIterator[None]:
    lock = get(project, prefix)
    async with lock:
        yield


def reset_for_testing() -> None:
    _LOCKS.clear()
