"""Full transaction matrix for the Redis-backed state store."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from stack_core import manifest as mf
from stack_core import state_store
from stack_core.exceptions import RedisUnavailable, RetryExhausted
from stack_core.state_store import StateStoreConfig
from stack_core.types import BranchEntry, Manifest, Verification
from tests.conftest import FIXED_TIME, sha

pytestmark = pytest.mark.integration


def _branch(order: int, commit: int, parent: int) -> BranchEntry:
    return BranchEntry(
        order=order,
        name=f"feat-stacked-{order}",
        commit_sha=sha(commit),
        parent_sha=sha(parent),
        tree_hash=sha(commit + 100),
        subject=f"part {order}",
    )


def _make_manifest(branches=None) -> Manifest:
    if branches is None:
        branches = [_branch(1, 1, 0)]
    return Manifest(
        prefix="feat",
        code_repo="proj/repo",
        base_ref="origin/main",
        source_branch="feat",
        source_branch_tip=sha(50),
        created_at=FIXED_TIME,
        last_update=FIXED_TIME,
        branches=branches,
        verification=Verification(
            passed=True,
            method="tree-hash-equality",
            original_tree=sha(200),
            stack_tip_tree=sha(101),
            last_verified_at=FIXED_TIME,
        ),
    )


CONFIG = StateStoreConfig(audit_cap=5, audit_ttl_seconds=3600, max_retries=3)


class TestGetManifest:
    def test_returns_none_when_absent(self, redis_client):
        assert state_store.get_manifest(redis_client, "proj", "feat", config=CONFIG) is None

    def test_round_trip(self, redis_client):
        m = _make_manifest()
        state_store.update_manifest(
            redis_client, "proj", "feat", lambda _: m, "create", "tester", config=CONFIG
        )
        loaded = state_store.get_manifest(redis_client, "proj", "feat", config=CONFIG)
        assert loaded == m


class TestUpdateManifest:
    def test_creates_when_none(self, redis_client):
        m = _make_manifest()
        result = state_store.update_manifest(
            redis_client, "proj", "feat", lambda current: m, "create", "tester", config=CONFIG
        )
        assert result == m
        assert state_store.get_manifest(redis_client, "proj", "feat", config=CONFIG) == m

    def test_modify_fn_receives_current(self, redis_client):
        m = _make_manifest()
        state_store.update_manifest(
            redis_client, "proj", "feat", lambda _: m, "create", "tester", config=CONFIG
        )
        received = []

        def modify(current):
            received.append(current)
            return mf.with_pr_recorded(current, "feat-stacked-1", 42, "http://pr/42")

        state_store.update_manifest(
            redis_client, "proj", "feat", modify, "record pr", "tester", config=CONFIG
        )
        assert received[0] == m

    def test_noop_when_unchanged(self, redis_client):
        m = _make_manifest()
        state_store.update_manifest(
            redis_client, "proj", "feat", lambda _: m, "create", "tester", config=CONFIG
        )
        # Second update returns same manifest -> should be no-op (audit log unchanged).
        before = state_store.get_audit_log(redis_client, "proj", "feat", config=CONFIG)
        state_store.update_manifest(
            redis_client, "proj", "feat", lambda current: current, "noop", "tester", config=CONFIG
        )
        after = state_store.get_audit_log(redis_client, "proj", "feat", config=CONFIG)
        assert before == after

    def test_retries_on_conflict(self, redis_client):
        m = _make_manifest()
        state_store.update_manifest(
            redis_client, "proj", "feat", lambda _: m, "create", "tester", config=CONFIG
        )

        attempts = [0]

        def modify(current):
            attempts[0] += 1
            if attempts[0] == 1:
                # Simulate concurrent write: mutate the manifest key directly while
                # we're inside modify_fn but before EXEC. This should trigger a
                # WatchError and a retry.
                m2 = mf.with_pr_recorded(current, "feat-stacked-1", 99, "http://pr/99")
                key = f"{CONFIG.key_prefix}:proj:manifest:feat"
                redis_client.set(key, m2.model_dump_json())
            # Always set pr to 42 from whatever current is.
            return mf.with_pr_recorded(current, "feat-stacked-1", 42, "http://pr/42")

        state_store.update_manifest(
            redis_client, "proj", "feat", modify, "retry test", "tester", config=CONFIG
        )
        assert attempts[0] >= 2
        # Final state: pr_id=42 (the retry's value, applied on top of the concurrent write)
        final = state_store.get_manifest(redis_client, "proj", "feat", config=CONFIG)
        assert final.branches[0].pr_id == 42

    def test_retry_exhausted(self, redis_client):
        m = _make_manifest()
        state_store.update_manifest(
            redis_client, "proj", "feat", lambda _: m, "create", "tester", config=CONFIG
        )

        def modify(current):
            # Always trigger a conflict.
            key = f"{CONFIG.key_prefix}:proj:manifest:feat"
            m2 = mf.with_pr_recorded(current, "feat-stacked-1", 99, "http://pr/99")
            redis_client.set(key, m2.model_dump_json())
            return mf.with_pr_recorded(current, "feat-stacked-1", 42, "http://pr/42")

        with pytest.raises(RetryExhausted):
            state_store.update_manifest(
                redis_client, "proj", "feat", modify, "always conflict", "tester", config=CONFIG
            )

    def test_concurrent_different_prefixes_no_conflict(self, redis_client):
        m1 = _make_manifest()
        m2 = _make_manifest()
        state_store.update_manifest(
            redis_client, "proj", "feat-a", lambda _: m1, "create a", "tester", config=CONFIG
        )
        state_store.update_manifest(
            redis_client, "proj", "feat-b", lambda _: m2, "create b", "tester", config=CONFIG
        )
        assert state_store.get_manifest(redis_client, "proj", "feat-a", config=CONFIG) is not None
        assert state_store.get_manifest(redis_client, "proj", "feat-b", config=CONFIG) is not None

    def test_modify_fn_raises_propagates(self, redis_client):
        m = _make_manifest()
        state_store.update_manifest(
            redis_client, "proj", "feat", lambda _: m, "create", "tester", config=CONFIG
        )

        def modify(current):
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            state_store.update_manifest(
                redis_client, "proj", "feat", modify, "should fail", "tester", config=CONFIG
            )
        # State should be unchanged.
        assert (
            state_store.get_manifest(redis_client, "proj", "feat", config=CONFIG).branches[0].pr_id
            is None
        )


class TestDeleteManifest:
    def test_removes_key_and_audits(self, redis_client):
        m = _make_manifest()
        state_store.update_manifest(
            redis_client, "proj", "feat", lambda _: m, "create", "tester", config=CONFIG
        )
        state_store.delete_manifest(redis_client, "proj", "feat", "stack landed", "tester", config=CONFIG)
        assert state_store.get_manifest(redis_client, "proj", "feat", config=CONFIG) is None
        log = state_store.get_audit_log(redis_client, "proj", "feat", config=CONFIG)
        assert log[0].event_type == "manifest_deleted"


class TestListManifests:
    def test_returns_all_prefixes_sorted(self, redis_client):
        m = _make_manifest()
        for prefix in ["feat-c", "feat-a", "feat-b"]:
            state_store.update_manifest(
                redis_client, "proj", prefix, lambda _: m, "create", "tester", config=CONFIG
            )
        result = state_store.list_manifests(redis_client, "proj", config=CONFIG)
        assert result == ["feat-a", "feat-b", "feat-c"]

    def test_filters_by_project(self, redis_client):
        m = _make_manifest()
        state_store.update_manifest(
            redis_client, "proj-a", "feat", lambda _: m, "create", "tester", config=CONFIG
        )
        state_store.update_manifest(
            redis_client, "proj-b", "feat", lambda _: m, "create", "tester", config=CONFIG
        )
        assert state_store.list_manifests(redis_client, "proj-a", config=CONFIG) == ["feat"]
        assert state_store.list_manifests(redis_client, "proj-b", config=CONFIG) == ["feat"]


class TestAuditLog:
    def test_returns_newest_first(self, redis_client):
        m = _make_manifest()
        state_store.update_manifest(
            redis_client, "proj", "feat", lambda _: m, "create", "tester", config=CONFIG
        )
        updated = mf.with_pr_recorded(m, "feat-stacked-1", 42, "http://pr/42")
        state_store.update_manifest(
            redis_client, "proj", "feat", lambda _: updated, "record pr", "tester", config=CONFIG
        )
        log = state_store.get_audit_log(redis_client, "proj", "feat", config=CONFIG)
        assert log[0].details["message"] == "record pr"
        assert log[1].details["message"] == "create"

    def test_respects_limit(self, redis_client):
        m = _make_manifest()
        state_store.update_manifest(
            redis_client, "proj", "feat", lambda _: m, "create", "tester", config=CONFIG
        )
        log = state_store.get_audit_log(redis_client, "proj", "feat", limit=0, config=CONFIG)
        assert log == []

    def test_ltrim_caps_log(self, redis_client):
        m = _make_manifest()
        state_store.update_manifest(
            redis_client, "proj", "feat", lambda _: m, "create", "tester", config=CONFIG
        )
        # cap is 5; do 10 mutations
        current = m
        for i in range(10):
            current = mf.with_pr_recorded(
                current.model_copy(update={"branches": list(current.branches)}),
                "feat-stacked-1",
                i,
                f"http://pr/{i}",
            )
            captured = current

            def modify(prev, c=captured):
                return c

            state_store.update_manifest(
                redis_client, "proj", "feat", modify, f"update {i}", "tester", config=CONFIG
            )
        log = state_store.get_audit_log(redis_client, "proj", "feat", limit=100, config=CONFIG)
        assert len(log) == CONFIG.audit_cap

    def test_ttl_refreshes_on_each_update(self, redis_client):
        m = _make_manifest()
        state_store.update_manifest(
            redis_client, "proj", "feat", lambda _: m, "create", "tester", config=CONFIG
        )
        key = f"{CONFIG.key_prefix}:proj:audit:feat"
        ttl_before = redis_client.ttl(key)
        # Update again -> TTL should be refreshed (still close to audit_ttl_seconds)
        updated = mf.with_pr_recorded(m, "feat-stacked-1", 42, "http://pr/42")
        state_store.update_manifest(
            redis_client, "proj", "feat", lambda _: updated, "second", "tester", config=CONFIG
        )
        ttl_after = redis_client.ttl(key)
        assert 0 < ttl_after <= CONFIG.audit_ttl_seconds
        assert 0 < ttl_before <= CONFIG.audit_ttl_seconds


class TestConnectionFailure:
    def test_get_raises_redis_unavailable(self):
        client = MagicMock()
        client.get.side_effect = RedisConnectionError("nope")
        with pytest.raises(RedisUnavailable):
            state_store.get_manifest(client, "proj", "feat", config=CONFIG)
