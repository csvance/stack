"""Drift detection: compare manifest expectations against observable state.

A ``Drift`` record names the branch (when applicable), the category, and a human
readable detail string. Callers (operations.status, the bot's reconciler) decide
how to react. Each detector is pure: input is the manifest + a snapshot of git
state (and optionally a snapshot of ADO PR state); output is a list of records.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from stack_core import topology
from stack_core.types import Manifest


class DriftCategory(StrEnum):
    MISSING_BRANCH = "MISSING_BRANCH"
    UNCOMMITTED_AHEAD = "UNCOMMITTED_AHEAD"
    BRANCH_MOVED = "BRANCH_MOVED"
    PARENT_DRIFT = "PARENT_DRIFT"
    BASE_MOVED = "BASE_MOVED"
    PR_MERGED_BOTTOM = "PR_MERGED_BOTTOM"


class Drift(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: DriftCategory
    branch: str | None
    detail: str


class BranchGitState(BaseModel):
    """A snapshot of one local branch's git state at detection time."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    exists: bool
    tip_sha: str | None = None
    parent_sha: str | None = None


def detect_branch_drift(
    manifest: Manifest,
    git_state: Mapping[str, BranchGitState],
    base_ref_tip: str,
    *,
    head_branch: str | None = None,
    head_sha: str | None = None,
) -> list[Drift]:
    """Detect MISSING_BRANCH, BRANCH_MOVED, UNCOMMITTED_AHEAD, PARENT_DRIFT.

    ``git_state`` maps branch name to its observed state. ``base_ref_tip`` is the
    resolved tip SHA of the manifest's ``base_ref`` right now. ``head_branch`` /
    ``head_sha`` are used to detect UNCOMMITTED_AHEAD on the currently checked-out
    branch.
    """
    drifts: list[Drift] = []
    for entry in manifest.branches:
        state = git_state.get(entry.name)
        if state is None or not state.exists:
            drifts.append(
                Drift(
                    category=DriftCategory.MISSING_BRANCH,
                    branch=entry.name,
                    detail=f"local branch refs/heads/{entry.name} is absent",
                )
            )
            continue

        expected_parent = topology.expected_parent_sha(manifest, entry.name, base_ref_tip)
        if state.parent_sha is not None and state.parent_sha != expected_parent:
            drifts.append(
                Drift(
                    category=DriftCategory.PARENT_DRIFT,
                    branch=entry.name,
                    detail=(
                        f"parent is {state.parent_sha[:12]}, expected {expected_parent[:12]} "
                        "(branch needs to be rebased)"
                    ),
                )
            )

        if state.tip_sha is None:
            continue
        if state.tip_sha == entry.commit_sha:
            if (
                head_branch == entry.name
                and head_sha is not None
                and head_sha != entry.commit_sha
            ):
                drifts.append(
                    Drift(
                        category=DriftCategory.UNCOMMITTED_AHEAD,
                        branch=entry.name,
                        detail=(
                            f"branch matches manifest but HEAD is at {head_sha[:12]} "
                            "(uncommitted advance ahead of recorded tip)"
                        ),
                    )
                )
        else:
            drifts.append(
                Drift(
                    category=DriftCategory.BRANCH_MOVED,
                    branch=entry.name,
                    detail=(
                        f"local tip {state.tip_sha[:12]} differs from manifest "
                        f"{entry.commit_sha[:12]}"
                    ),
                )
            )
    return drifts


def detect_base_moved(manifest: Manifest, base_ref_tip: str) -> list[Drift]:
    """If ``base_tip_sha`` is recorded and differs from current, the base moved."""
    expected = getattr(manifest, "base_tip_sha", None)
    if expected is None:
        return []
    if expected == base_ref_tip:
        return []
    return [
        Drift(
            category=DriftCategory.BASE_MOVED,
            branch=None,
            detail=(
                f"base_ref {manifest.base_ref} tip is now {base_ref_tip[:12]}, "
                f"manifest recorded {expected[:12]}"
            ),
        )
    ]


def detect_pr_merged_bottom(manifest: Manifest, bottom_pr_status: str | None) -> list[Drift]:
    """If the bottom PR exists and is `completed`, the bottom is ready to land."""
    if bottom_pr_status != "completed":
        return []
    bottom = topology.bottom_branch(manifest)
    return [
        Drift(
            category=DriftCategory.PR_MERGED_BOTTOM,
            branch=bottom.name,
            detail=f"bottom PR for {bottom.name} is completed (ready to land)",
        )
    ]
