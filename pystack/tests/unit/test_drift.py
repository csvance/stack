"""Drift detection: one test per category."""

from __future__ import annotations

from stack_core import drift
from stack_core.drift import BranchGitState, DriftCategory
from tests.conftest import sha


def test_missing_branch(sample_manifest):
    base_tip = sha(0)
    git_state = {
        "feat-stacked-1": BranchGitState(exists=False),
        "feat-stacked-2": BranchGitState(
            exists=True, tip_sha=sha(2), parent_sha=sha(1)
        ),
        "feat-stacked-3": BranchGitState(
            exists=True, tip_sha=sha(3), parent_sha=sha(2)
        ),
    }
    drifts = drift.detect_branch_drift(sample_manifest, git_state, base_tip)
    assert len(drifts) == 1
    assert drifts[0].category is DriftCategory.MISSING_BRANCH
    assert drifts[0].branch == "feat-stacked-1"


def test_branch_moved(sample_manifest):
    base_tip = sha(0)
    git_state = {
        "feat-stacked-1": BranchGitState(exists=True, tip_sha=sha(99), parent_sha=sha(0)),
        "feat-stacked-2": BranchGitState(exists=True, tip_sha=sha(2), parent_sha=sha(1)),
        "feat-stacked-3": BranchGitState(exists=True, tip_sha=sha(3), parent_sha=sha(2)),
    }
    drifts = drift.detect_branch_drift(sample_manifest, git_state, base_tip)
    cats = [d.category for d in drifts]
    assert DriftCategory.BRANCH_MOVED in cats


def test_uncommitted_ahead(sample_manifest):
    base_tip = sha(0)
    git_state = {
        "feat-stacked-1": BranchGitState(exists=True, tip_sha=sha(1), parent_sha=sha(0)),
        "feat-stacked-2": BranchGitState(exists=True, tip_sha=sha(2), parent_sha=sha(1)),
        "feat-stacked-3": BranchGitState(exists=True, tip_sha=sha(3), parent_sha=sha(2)),
    }
    drifts = drift.detect_branch_drift(
        sample_manifest, git_state, base_tip,
        head_branch="feat-stacked-2", head_sha=sha(98),
    )
    cats = [(d.category, d.branch) for d in drifts]
    assert (DriftCategory.UNCOMMITTED_AHEAD, "feat-stacked-2") in cats


def test_parent_drift(sample_manifest):
    base_tip = sha(0)
    git_state = {
        "feat-stacked-1": BranchGitState(exists=True, tip_sha=sha(1), parent_sha=sha(0)),
        "feat-stacked-2": BranchGitState(
            exists=True,
            tip_sha=sha(2),
            parent_sha=sha(77),  # wrong parent
        ),
        "feat-stacked-3": BranchGitState(exists=True, tip_sha=sha(3), parent_sha=sha(2)),
    }
    drifts = drift.detect_branch_drift(sample_manifest, git_state, base_tip)
    cats = [(d.category, d.branch) for d in drifts]
    assert (DriftCategory.PARENT_DRIFT, "feat-stacked-2") in cats


def test_no_drift(sample_manifest):
    base_tip = sha(0)
    git_state = {
        "feat-stacked-1": BranchGitState(exists=True, tip_sha=sha(1), parent_sha=sha(0)),
        "feat-stacked-2": BranchGitState(exists=True, tip_sha=sha(2), parent_sha=sha(1)),
        "feat-stacked-3": BranchGitState(exists=True, tip_sha=sha(3), parent_sha=sha(2)),
    }
    drifts = drift.detect_branch_drift(sample_manifest, git_state, base_tip)
    assert drifts == []


def test_base_moved_only_when_recorded(sample_manifest):
    """Manifest has no base_tip_sha -> no BASE_MOVED."""
    drifts = drift.detect_base_moved(sample_manifest, sha(999))
    assert drifts == []


def test_pr_merged_bottom(sample_manifest):
    drifts = drift.detect_pr_merged_bottom(sample_manifest, "completed")
    assert len(drifts) == 1
    assert drifts[0].category is DriftCategory.PR_MERGED_BOTTOM
    assert drifts[0].branch == "feat-stacked-1"


def test_pr_merged_bottom_skipped_for_active(sample_manifest):
    assert drift.detect_pr_merged_bottom(sample_manifest, "active") == []
    assert drift.detect_pr_merged_bottom(sample_manifest, None) == []
