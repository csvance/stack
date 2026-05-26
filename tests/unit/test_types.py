"""Validation rules on BranchEntry and Manifest."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stack_core.types import BranchEntry, Manifest, Verification
from tests.conftest import FIXED_TIME, sha


def _branch(**overrides):
    defaults = dict(
        order=1,
        name="feat-stacked-1",
        commit_sha=sha(1),
        parent_sha=sha(0),
        tree_hash=sha(100),
        subject="s",
    )
    defaults.update(overrides)
    return BranchEntry(**defaults)


def _verification():
    return Verification(
        passed=True,
        method="tree-hash-equality",
        original_tree=sha(200),
        stack_tip_tree=sha(103),
        last_verified_at=FIXED_TIME,
    )


def _manifest(branches, prefix="feat", suffix="-stacked-"):
    return Manifest(
        prefix=prefix,
        code_repo="proj/repo",
        base_ref="origin/main",
        branch_suffix=suffix,
        source_branch=prefix,
        source_branch_tip=sha(50),
        created_at=FIXED_TIME,
        last_update=FIXED_TIME,
        branches=branches,
        verification=_verification(),
    )


class TestBranchEntry:
    def test_accepts_valid(self):
        b = _branch()
        assert b.order == 1

    def test_rejects_empty_sha(self):
        with pytest.raises(ValidationError):
            _branch(commit_sha="")

    def test_rejects_short_sha(self):
        with pytest.raises(ValidationError):
            _branch(commit_sha="abc123")

    def test_rejects_uppercase_sha(self):
        sha_with_letters = "abcdef" + "0" * 34
        with pytest.raises(ValidationError):
            _branch(commit_sha=sha_with_letters.upper())

    def test_rejects_order_zero(self):
        with pytest.raises(ValidationError):
            _branch(order=0)

    def test_rejects_order_negative(self):
        with pytest.raises(ValidationError):
            _branch(order=-1)

    def test_is_frozen(self):
        b = _branch()
        with pytest.raises(ValidationError):
            b.order = 99  # type: ignore[misc]


class TestManifest:
    def test_accepts_valid_chain(self):
        branches = [
            _branch(order=1, commit_sha=sha(1), parent_sha=sha(0)),
            _branch(
                order=2,
                name="feat-stacked-2",
                commit_sha=sha(2),
                parent_sha=sha(1),
            ),
        ]
        m = _manifest(branches)
        assert m.branches[1].parent_sha == m.branches[0].commit_sha

    def test_rejects_broken_parent_chain(self):
        branches = [
            _branch(order=1, commit_sha=sha(1), parent_sha=sha(0)),
            _branch(
                order=2,
                name="feat-stacked-2",
                commit_sha=sha(2),
                parent_sha=sha(99),  # wrong; should be sha(1)
            ),
        ]
        with pytest.raises(ValidationError, match="parent_sha"):
            _manifest(branches)

    def test_rejects_non_ascending_orders(self):
        branches = [
            _branch(order=2, name="feat-stacked-2", commit_sha=sha(1), parent_sha=sha(0)),
            _branch(order=1, name="feat-stacked-1", commit_sha=sha(2), parent_sha=sha(1)),
        ]
        with pytest.raises(ValidationError, match="ascending"):
            _manifest(branches)

    def test_rejects_duplicate_orders(self):
        branches = [
            _branch(order=1, commit_sha=sha(1), parent_sha=sha(0)),
            _branch(
                order=1,
                name="feat-stacked-1",
                commit_sha=sha(2),
                parent_sha=sha(1),
            ),
        ]
        with pytest.raises(ValidationError):
            _manifest(branches)

    def test_allows_order_gaps(self):
        """After lands, order numbers may have gaps. 1, 3 is valid; 3, 1 is not."""
        branches = [
            _branch(order=2, name="feat-stacked-2", commit_sha=sha(1), parent_sha=sha(0)),
            _branch(order=5, name="feat-stacked-5", commit_sha=sha(2), parent_sha=sha(1)),
        ]
        m = _manifest(branches)
        assert [b.order for b in m.branches] == [2, 5]

    def test_branch_name_must_match_suffix_pattern(self):
        branches = [
            _branch(order=1, name="wrong-name-1", commit_sha=sha(1), parent_sha=sha(0)),
        ]
        with pytest.raises(ValidationError, match="does not match expected"):
            _manifest(branches)

    def test_branch_name_respects_custom_suffix(self):
        branches = [
            _branch(order=1, name="feat-1", commit_sha=sha(1), parent_sha=sha(0)),
        ]
        m = _manifest(branches, suffix="-")
        assert m.branch_suffix == "-"

    def test_rejects_empty_branches(self):
        with pytest.raises(ValidationError):
            _manifest([])

    def test_rejects_invalid_source_branch_tip(self):
        branches = [_branch(order=1, commit_sha=sha(1), parent_sha=sha(0))]
        with pytest.raises(ValidationError):
            Manifest(
                prefix="feat",
                code_repo="proj/repo",
                base_ref="origin/main",
                source_branch="feat",
                source_branch_tip="not-a-sha",
                created_at=FIXED_TIME,
                last_update=FIXED_TIME,
                branches=branches,
                verification=_verification(),
            )

    def test_serialization_round_trip(self):
        branches = [
            _branch(order=1, commit_sha=sha(1), parent_sha=sha(0)),
            _branch(
                order=2,
                name="feat-stacked-2",
                commit_sha=sha(2),
                parent_sha=sha(1),
            ),
        ]
        m = _manifest(branches)
        roundtripped = Manifest.model_validate_json(m.model_dump_json())
        assert roundtripped == m
