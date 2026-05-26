"""Pure manifest helpers."""

from __future__ import annotations

from datetime import timedelta

import pytest

from stack_core import manifest as mf
from stack_core.exceptions import TopologyError
from stack_core.types import BranchEntry
from tests.conftest import FIXED_TIME, sha


class TestNewManifest:
    def test_sets_clock_fields(self, sample_branches, sample_verification, fixed_clock):
        m = mf.new_manifest(
            prefix="feat",
            code_repo="proj/repo",
            base_ref="origin/main",
            source_branch="feat",
            source_branch_tip=sha(50),
            branches=sample_branches,
            verification=sample_verification,
            clock=fixed_clock,
        )
        assert m.created_at == FIXED_TIME
        assert m.last_update == FIXED_TIME


class TestWithBranchUpdated:
    def test_records_pr_id(self, sample_manifest, fixed_clock):
        updated = mf.with_branch_updated(
            sample_manifest, "feat-stacked-2", pr_id=42, pr_url="http://pr/42", clock=fixed_clock
        )
        target = next(b for b in updated.branches if b.name == "feat-stacked-2")
        assert target.pr_id == 42
        assert target.pr_url == "http://pr/42"

    def test_bumps_last_update(self, sample_manifest):
        later = FIXED_TIME + timedelta(hours=1)
        updated = mf.with_branch_updated(
            sample_manifest, "feat-stacked-2", pr_id=42, clock=lambda: later
        )
        assert updated.last_update == later
        assert sample_manifest.last_update == FIXED_TIME  # original unchanged

    def test_returns_new_instance(self, sample_manifest):
        updated = mf.with_branch_updated(sample_manifest, "feat-stacked-2", pr_id=42)
        assert updated is not sample_manifest

    def test_raises_on_missing_branch(self, sample_manifest):
        with pytest.raises(TopologyError):
            mf.with_branch_updated(sample_manifest, "feat-stacked-99", pr_id=1)


class TestWithBranchesReplaced:
    def test_replaces_with_valid_list(self, sample_manifest, fixed_clock):
        new_branches = [
            BranchEntry(
                order=2,
                name="feat-stacked-2",
                commit_sha=sha(20),
                parent_sha=sha(0),  # new parent (base ref tip)
                tree_hash=sha(120),
                subject="rebased",
            ),
            BranchEntry(
                order=3,
                name="feat-stacked-3",
                commit_sha=sha(30),
                parent_sha=sha(20),
                tree_hash=sha(130),
                subject="rebased",
            ),
        ]
        landed = mf.with_branches_replaced(sample_manifest, new_branches, clock=fixed_clock)
        assert [b.order for b in landed.branches] == [2, 3]
        assert [b.commit_sha for b in landed.branches] == [sha(20), sha(30)]

    def test_re_validates_parent_chain(self, sample_manifest):
        from pydantic import ValidationError

        broken = [
            BranchEntry(
                order=2,
                name="feat-stacked-2",
                commit_sha=sha(20),
                parent_sha=sha(0),
                tree_hash=sha(120),
                subject="r",
            ),
            BranchEntry(
                order=3,
                name="feat-stacked-3",
                commit_sha=sha(30),
                parent_sha=sha(99),  # broken
                tree_hash=sha(130),
                subject="r",
            ),
        ]
        with pytest.raises(ValidationError):
            mf.with_branches_replaced(sample_manifest, broken)


class TestWithVerification:
    def test_replaces_verification(self, sample_manifest, sample_verification):
        new_ver = sample_verification.model_copy(update={"passed": False})
        updated = mf.with_verification(sample_manifest, new_ver)
        assert updated.verification.passed is False
        assert sample_manifest.verification.passed is True
