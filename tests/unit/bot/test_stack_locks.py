"""Per-stack asyncio.Lock registry serializes same-stack work."""

from __future__ import annotations

import asyncio

import pytest

from stack_bot import stack_locks


@pytest.fixture(autouse=True)
def _clear():
    stack_locks.reset_for_testing()
    yield
    stack_locks.reset_for_testing()


def test_get_returns_same_lock_per_key():
    a1 = stack_locks.get("proj", "feat")
    a2 = stack_locks.get("proj", "feat")
    b = stack_locks.get("proj", "other")
    assert a1 is a2
    assert a1 is not b


def test_different_stacks_do_not_block_each_other():
    async def run():
        sequence: list[str] = []

        async def hold_a():
            async with stack_locks.acquire_for("proj", "a"):
                sequence.append("a-start")
                await asyncio.sleep(0)
                sequence.append("a-end")

        async def hold_b():
            async with stack_locks.acquire_for("proj", "b"):
                sequence.append("b-start")
                await asyncio.sleep(0)
                sequence.append("b-end")

        await asyncio.gather(hold_a(), hold_b())
        # Both should have started before either ended (different locks).
        assert sequence[:2] == ["a-start", "b-start"] or sequence[:2] == ["b-start", "a-start"]

    asyncio.run(run())


def test_same_stack_serializes():
    async def run():
        sequence: list[str] = []

        async def hold(tag: str):
            async with stack_locks.acquire_for("proj", "same"):
                sequence.append(f"{tag}-start")
                await asyncio.sleep(0)
                sequence.append(f"{tag}-end")

        await asyncio.gather(hold("first"), hold("second"))
        # Whichever ran first must fully complete before the other starts.
        if sequence[0] == "first-start":
            assert sequence == ["first-start", "first-end", "second-start", "second-end"]
        else:
            assert sequence == ["second-start", "second-end", "first-start", "first-end"]

    asyncio.run(run())
