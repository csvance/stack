"""Shared test fixtures: clocks and SHA helpers used across unit and integration."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from stack_core.types import BranchEntry, Manifest, Verification

FIXED_TIME = datetime(2026, 5, 25, 14, 23, 11, tzinfo=UTC)


def sha(seed: int) -> str:
    """Deterministic 40-char hex SHA from an integer seed; collision-resistant enough for tests."""
    base = f"{seed:040x}"
    return base[:40]


@pytest.fixture
def fixed_clock():
    def _now() -> datetime:
        return FIXED_TIME

    return _now


@pytest.fixture
def sample_branches():
    """A 3-branch stack with consistent parent chain. branches[0].parent_sha == base_tip."""
    base_tip = sha(0)
    return [
        BranchEntry(
            order=1,
            name="feat-stacked-1",
            commit_sha=sha(1),
            parent_sha=base_tip,
            tree_hash=sha(101),
            subject="part 1",
            body="",
            files_changed=["a.py"],
        ),
        BranchEntry(
            order=2,
            name="feat-stacked-2",
            commit_sha=sha(2),
            parent_sha=sha(1),
            tree_hash=sha(102),
            subject="part 2",
            body="",
            files_changed=["b.py"],
        ),
        BranchEntry(
            order=3,
            name="feat-stacked-3",
            commit_sha=sha(3),
            parent_sha=sha(2),
            tree_hash=sha(103),
            subject="part 3",
            body="",
            files_changed=["c.py"],
        ),
    ]


@pytest.fixture
def sample_verification():
    return Verification(
        passed=True,
        method="tree-hash-equality",
        original_tree=sha(200),
        stack_tip_tree=sha(103),
        last_verified_at=FIXED_TIME,
    )


@pytest.fixture
def sample_manifest(sample_branches, sample_verification):
    return Manifest(
        prefix="feat",
        code_repo="proj/repo",
        base_ref="origin/main",
        branch_suffix="-stacked-",
        source_branch="feat",
        source_branch_tip=sha(50),
        created_at=FIXED_TIME,
        last_update=FIXED_TIME,
        branches=sample_branches,
        verification=sample_verification,
    )
