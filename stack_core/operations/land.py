"""The landing operation: drop a merged bottom and rebase the remaining stack.

Called by StackBot when ADO reports a PR-merged event for the bottom of a
managed stack. Mechanical only; conflicts and tree-hash mismatches surface as
structured exceptions for the caller to translate into PR comments.

The operation is idempotent in the trivial sense: if the bottom PR is no longer
``completed`` (because someone else already landed, or it was never merged), or
the manifest is missing, the operation returns a no-op result without
mutating state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict
from redis import Redis

from stack_core import backup, git_ops, pr_templates, state_store, topology, verify
from stack_core import manifest as mf
from stack_core.ado import pr as ado_pr
from stack_core.ado.client import AdoClient
from stack_core.exceptions import (
    GitError,
    LandConflict,
    LandError,
    VerifyTreeMismatch,
)
from stack_core.state_store import AdoRemote, StateStoreConfig
from stack_core.types import BranchEntry, Manifest, Verification


class LandResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Literal["landed", "no_op", "manifest_deleted"]
    reason: str | None = None
    manifest_after: Manifest | None = None


def land(
    redis_client: Redis,
    repo_path: Path,
    ado_client: AdoClient,
    ado_remote: AdoRemote,
    *,
    prefix: str,
    actor: str = "stackbot",
    remote: str = "origin",
    state_config: StateStoreConfig | None = None,
) -> LandResult:
    config = state_config or StateStoreConfig()

    current = state_store.get_manifest(redis_client, ado_remote.project, prefix, config=config)
    if current is None:
        return LandResult(action="no_op", reason="no_manifest")

    bottom = topology.bottom_branch(current)
    if bottom.pr_id is None:
        return LandResult(action="no_op", reason="bottom_has_no_pr")
    bottom_pr = ado_pr.show(
        ado_client, ado_remote.project, ado_remote.repo, bottom.pr_id,
        organization_url=ado_remote.org_url,
    )
    if bottom_pr.status != "completed":
        return LandResult(action="no_op", reason="bottom_not_merged")

    if len(current.branches) == 1:
        state_store.delete_manifest(
            redis_client, ado_remote.project, prefix,
            audit_message="stack drained: last branch merged",
            actor=actor,
            operation="land",
            event_type="manifest_deleted",
            config=config,
        )
        return LandResult(action="manifest_deleted")

    remaining_names = [b.name for b in current.branches[1:]]
    snapshot_id = backup.snapshot(repo_path, "land", prefix, remaining_names)

    try:
        git_ops.fetch(repo_path, remote, current.base_ref)
        new_base_tip = git_ops.sha_of(repo_path, current.base_ref)

        new_bottom_name = current.branches[1].name
        try:
            git_ops.branchless_move(
                repo_path, src=new_bottom_name, dest=new_base_tip,
                force_rewrite=True, merge=True,
            )
        except GitError as exc:
            conflicting = git_ops.conflicting_paths(repo_path)
            git_ops.branchless_abort(repo_path)
            raise LandConflict(conflicting) from exc

        rebased_branches = _rebuild_branch_entries(repo_path, current.branches[1:], new_base_tip)

        # Defense-in-depth: cherry-pick the recorded commits onto the new base
        # in a throwaway worktree and check that we land on the same tree.
        commit_shas = [b.commit_sha for b in rebased_branches]
        reference_tree = verify.compute_reference_tip(repo_path, new_base_tip, commit_shas)
        top_actual_tree = rebased_branches[-1].tree_hash
        if reference_tree != top_actual_tree:
            backup.restore(repo_path, prefix, snapshot_id)
            raise VerifyTreeMismatch(top_actual_tree, reference_tree)

        for entry in rebased_branches:
            git_ops.push_branch(repo_path, remote, entry.name)

        # Retarget the new bottom's PR to the original base ref.
        new_bottom_entry = rebased_branches[0]
        if new_bottom_entry.pr_id is not None:
            ado_pr.update(
                ado_client, ado_remote.project, ado_remote.repo, new_bottom_entry.pr_id,
                target_branch=current.base_ref,
                organization_url=ado_remote.org_url,
            )

        def _modify(m: Manifest | None) -> Manifest:
            if m is None:
                raise LandError("manifest disappeared during land")
            return mf.with_branches_replaced(m, rebased_branches)

        after = state_store.update_manifest(
            redis_client, ado_remote.project, prefix, _modify,
            audit_message=f"bottom landed: {bottom.name}",
            actor=actor,
            operation="land",
            event_type="bottom_landed",
            config=config,
        )

        _refresh_root_description(
            ado_client, ado_remote, after, key_prefix=config.key_prefix,
        )

        return LandResult(action="landed", manifest_after=after)
    except (LandConflict, VerifyTreeMismatch):
        raise
    except Exception as exc:
        # Anything else after the snapshot is taken: restore and surface.
        backup.restore(repo_path, prefix, snapshot_id)
        raise LandError(f"unexpected failure during land: {exc!r}") from exc


def _rebuild_branch_entries(
    repo_path: Path,
    previous_entries: list[BranchEntry],
    new_base_tip: str,
) -> list[BranchEntry]:
    rebuilt: list[BranchEntry] = []
    prev_sha = new_base_tip
    for entry in previous_entries:
        commit_sha = git_ops.sha_of(repo_path, entry.name)
        parent_sha = git_ops.parent_sha(repo_path, entry.name)
        tree_hash = git_ops.tree_of(repo_path, entry.name)
        files = git_ops.files_changed(repo_path, parent_sha, commit_sha)
        rebuilt.append(
            BranchEntry(
                order=entry.order,
                name=entry.name,
                commit_sha=commit_sha,
                parent_sha=parent_sha,
                tree_hash=tree_hash,
                subject=entry.subject,
                body=entry.body,
                files_changed=files,
                pr_id=entry.pr_id,
                pr_url=entry.pr_url,
            )
        )
        # Sanity: parent must match expected chain.
        if parent_sha != prev_sha:
            raise LandError(
                f"rebased branch {entry.name!r} parent {parent_sha} != expected {prev_sha}"
            )
        prev_sha = commit_sha
    # If the resulting list violates the manifest's invariants, BranchEntry's
    # frozen+validated model will raise inside `mf.with_branches_replaced`.
    _ = Verification  # keep import live for tests/imports
    return rebuilt


def _refresh_root_description(
    ado_client: AdoClient,
    ado_remote: AdoRemote,
    manifest_after: Manifest,
    *,
    key_prefix: str,
) -> None:
    bottom = topology.bottom_branch(manifest_after)
    if bottom.pr_id is None:
        return
    manifest_pointer = pr_templates.manifest_path(
        key_prefix, manifest_after.code_repo.split("/")[0], manifest_after.prefix
    )
    description = pr_templates.render(
        "pr_root",
        ORDER=str(bottom.order),
        COMMIT_SUBJECT=bottom.subject,
        COMMIT_BODY=bottom.body,
        STACK_LIST=pr_templates.build_stack_list(manifest_after),
        MANIFEST_PATH=manifest_pointer,
    )
    ado_pr.update(
        ado_client, ado_remote.project, ado_remote.repo, bottom.pr_id,
        description=description,
        organization_url=ado_remote.org_url,
    )
