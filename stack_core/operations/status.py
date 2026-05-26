"""Read-only stack status: load manifest, observe git, report drift."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from redis import Redis

from stack_core import drift, git_ops, state_store, topology
from stack_core.ado import pr as ado_pr
from stack_core.ado.client import AdoClient
from stack_core.exceptions import ManifestNotFoundError
from stack_core.types import Manifest


class StatusResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    manifest: Manifest
    base_ref_tip: str
    head_branch: str | None
    head_sha: str | None
    git_state: dict[str, drift.BranchGitState]
    drifts: list[drift.Drift] = Field(default_factory=list)


def _current_head(repo_path: Path) -> tuple[str | None, str | None]:
    branch = git_ops.current_branch(repo_path)
    try:
        sha: str | None = git_ops.sha_of(repo_path, "HEAD")
    except Exception:
        sha = None
    return branch, sha


def _branch_snapshot(repo_path: Path, name: str) -> drift.BranchGitState:
    if not git_ops.branch_exists(repo_path, name):
        return drift.BranchGitState(exists=False)
    try:
        tip = git_ops.sha_of(repo_path, name)
    except Exception:
        return drift.BranchGitState(exists=True)
    try:
        parent = git_ops.parent_sha(repo_path, name)
    except Exception:
        parent = None
    return drift.BranchGitState(exists=True, tip_sha=tip, parent_sha=parent)


def run(
    redis_client: Redis,
    repo_path: Path,
    project: str,
    prefix: str,
    *,
    ado_client: AdoClient | None = None,
    ado_project: str | None = None,
    ado_repo: str | None = None,
    organization_url: str | None = None,
) -> StatusResult:
    """Load the manifest and compute drift. Pure read-only.

    If ``ado_client`` is provided, also queries the bottom PR's status to detect
    ``PR_MERGED_BOTTOM``. Without an ADO client, that check is skipped.
    """
    manifest = state_store.get_manifest(redis_client, project, prefix)
    if manifest is None:
        raise ManifestNotFoundError(project, prefix)

    base_ref_tip = git_ops.sha_of(repo_path, manifest.base_ref)
    git_state = {entry.name: _branch_snapshot(repo_path, entry.name) for entry in manifest.branches}
    head_branch, head_sha = _current_head(repo_path)

    drifts: list[drift.Drift] = []
    drifts.extend(
        drift.detect_branch_drift(
            manifest,
            git_state,
            base_ref_tip,
            head_branch=head_branch,
            head_sha=head_sha,
        )
    )
    drifts.extend(drift.detect_base_moved(manifest, base_ref_tip))

    if ado_client is not None and ado_project and ado_repo and organization_url:
        bottom = topology.bottom_branch(manifest)
        if bottom.pr_id is not None:
            pr = ado_pr.show(
                ado_client,
                ado_project,
                ado_repo,
                bottom.pr_id,
                organization_url=organization_url,
            )
            drifts.extend(drift.detect_pr_merged_bottom(manifest, pr.status))

    return StatusResult(
        manifest=manifest,
        base_ref_tip=base_ref_tip,
        head_branch=head_branch,
        head_sha=head_sha,
        git_state=git_state,
        drifts=drifts,
    )
