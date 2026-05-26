"""The four phases of stack creation: prepare, decompose, manifest, publish.

Each phase is a pure function with no CLI concerns. The CLI commands in
``stack_cli.commands.create`` are thin wrappers that parse args, build the
clients (Redis, ADO), invoke a phase, and render the result.

All four phases are idempotent: re-running with the same inputs is safe.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from redis import Redis

from stack_core import git_ops, pr_templates, state_store, topology, verify
from stack_core import manifest as mf
from stack_core.ado import pr as ado_pr
from stack_core.ado.client import AdoClient
from stack_core.exceptions import (
    DecomposeError,
    PrepareError,
    PublishError,
    SentinelMissing,
    VerifyTreeMismatch,
)
from stack_core.state_store import AdoRemote, StateStoreConfig
from stack_core.types import BranchEntry, Manifest, Verification

DECOMPOSE_INPUT_SUFFIX = "-decompose-input"
SENTINEL_DIR_REL = ".git/stack"


def _sentinel_path(repo_path: Path, prefix: str) -> Path:
    return repo_path / SENTINEL_DIR_REL / f"decompose-sentinel-{prefix}.json"


def _local_md_path(repo_path: Path) -> Path:
    return repo_path / "CLAUDE.md.local"


def _branch_name(prefix: str, suffix: str, order: int) -> str:
    return f"{prefix}{suffix}{order}"


# ---------------------------------------------------------------------------
# Sentinel
# ---------------------------------------------------------------------------


class Sentinel(BaseModel):
    """The JSON document the decomposer skill writes once a stack is built."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    prefix: str = Field(min_length=1)
    branches: list[str] = Field(min_length=1)
    base_ref: str = Field(min_length=1)
    source_branch: str = Field(min_length=1)
    source_branch_tip: str = Field(min_length=1)
    branch_suffix: str = "-stacked-"
    completion_timestamp: datetime


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PrepareResult:
    input_branch: str
    input_sha: str
    base_ref: str
    base_tip_sha: str
    branch_suffix: str
    source_branch: str


def prepare(
    repo_path: Path,
    *,
    prefix: str,
    base_ref: str,
    branch_suffix: str,
    feature_branch: str | None = None,
    claude_bin: str | None = None,
) -> PrepareResult:
    if not git_ops.working_tree_clean(repo_path):
        raise PrepareError("working tree is not clean; commit or stash changes first")

    claude_path = claude_bin or os.environ.get("STACK_CLAUDE_BIN") or shutil.which("claude")
    if claude_path is None:
        raise PrepareError(
            "the `claude` binary is not on PATH; install Claude Code or set STACK_CLAUDE_BIN"
        )

    source = feature_branch or git_ops.current_branch(repo_path)
    if source is None:
        raise PrepareError("HEAD is detached and --feature was not provided")

    if not git_ops.branch_exists(repo_path, source):
        raise PrepareError(f"feature branch {source!r} does not exist locally")

    base_tip_sha = git_ops.sha_of(repo_path, base_ref)
    feature_sha = git_ops.sha_of(repo_path, source)

    if base_tip_sha == feature_sha:
        raise PrepareError(
            f"feature branch {source!r} has no commits ahead of {base_ref}"
        )
    if not git_ops.is_ancestor(repo_path, base_tip_sha, feature_sha):
        raise PrepareError(
            f"feature branch {source!r} does not descend from {base_ref}; "
            "rebase it first"
        )

    existing = git_ops.list_branches_matching(repo_path, f"{prefix}{branch_suffix}*")
    if existing:
        raise PrepareError(
            f"found existing stack branches under prefix {prefix!r}: {existing}. "
            f"Delete them or use a different --prefix."
        )

    commit_count = git_ops.count_commits_between(repo_path, base_tip_sha, feature_sha)
    input_branch = source
    input_sha = feature_sha
    if commit_count > 1:
        input_branch = f"{prefix}{DECOMPOSE_INPUT_SUFFIX}"
        input_sha = _build_input_branch(repo_path, input_branch, source, base_tip_sha)

    return PrepareResult(
        input_branch=input_branch,
        input_sha=input_sha,
        base_ref=base_ref,
        base_tip_sha=base_tip_sha,
        branch_suffix=branch_suffix,
        source_branch=source,
    )


def _build_input_branch(
    repo_path: Path,
    input_branch: str,
    source: str,
    base_tip_sha: str,
) -> str:
    """Create ``input_branch`` as a single commit on top of ``base_tip_sha``.

    Idempotent: if the branch already exists at the expected tree, reuse it.
    """
    if git_ops.branch_exists(repo_path, input_branch):
        existing_sha = git_ops.sha_of(repo_path, input_branch)
        existing_tree = git_ops.tree_of(repo_path, input_branch)
        source_tree = git_ops.tree_of(repo_path, source)
        existing_parent = git_ops.parent_sha(repo_path, input_branch)
        if existing_tree == source_tree and existing_parent == base_tip_sha:
            return existing_sha
        # Stale: rebuild.
        git_ops.delete_branch(repo_path, input_branch, force=True)

    saved_branch = git_ops.current_branch(repo_path)
    git_ops.checkout(repo_path, input_branch, create=True)
    # We're now on input_branch at source's tip. Soft-reset to base; the diff
    # becomes a single staged change. Amend-commit it with a synthesized message.
    try:
        git_ops.soft_reset(repo_path, base_tip_sha)
        message = _build_squash_message(repo_path, base_tip_sha, source)
        git_ops._git(["commit", "-m", message], repo_path)
    finally:
        if saved_branch and saved_branch != input_branch:
            git_ops.checkout(repo_path, saved_branch)
    return git_ops.sha_of(repo_path, input_branch)


def _build_squash_message(repo_path: Path, base_sha: str, source: str) -> str:
    subjects = git_ops._git(
        ["log", "--format=%s", f"{base_sha}..{source}"], repo_path
    )
    lines = subjects.splitlines()
    if not lines:
        return f"decompose-input for {source}"
    return f"decompose-input: {source}\n\n" + "\n".join(f"- {s}" for s in reversed(lines))


# ---------------------------------------------------------------------------
# decompose
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecomposeResult:
    sentinel: Sentinel


_CLAUDE_LOCAL_TEMPLATE = """\
# Decompose this branch (transient instructions for `stack decompose`)

This file is auto-generated by `stack decompose` and deleted when the command
exits. Do not commit it (it is gitignored).

When asked to decompose, invoke the `stacked-diff-decomposer` skill with:

- **Prefix**: `{prefix}`
- **Branch suffix**: `{branch_suffix}` (so branches are named `{prefix}{branch_suffix}1`, `{prefix}{branch_suffix}2`, ...)
- **Input branch**: `{input_branch}`
- **Base ref**: `{base_ref}`
- **Sentinel path**: `.git/stack/decompose-sentinel-{prefix}.json`

The skill must produce the stack of branches AND write the sentinel JSON. The
calling CLI consumes the sentinel and constructs the manifest in Redis from
git state. Do not write a manifest yourself.

Branches must descend in order: `{prefix}{branch_suffix}1` from `{base_ref}`,
each subsequent branch from the one before it. Tree-hash equality with
`{input_branch}` is required.
"""


def decompose(
    repo_path: Path,
    *,
    prepare_result: PrepareResult,
    prefix: str,
    force: bool = False,
    claude_bin: str | None = None,
    subprocess_runner: Callable[[list[str], Path], int] | None = None,
) -> DecomposeResult:
    sentinel_path = _sentinel_path(repo_path, prefix)

    if sentinel_path.exists() and not force:
        # Idempotent path: if a valid sentinel exists, reuse it.
        return DecomposeResult(sentinel=_load_sentinel(sentinel_path))

    if force:
        _force_cleanup(repo_path, prefix, prepare_result.branch_suffix, sentinel_path)

    sentinel_path.parent.mkdir(parents=True, exist_ok=True)

    local_md = _local_md_path(repo_path)
    local_md.write_text(
        _CLAUDE_LOCAL_TEMPLATE.format(
            prefix=prefix,
            branch_suffix=prepare_result.branch_suffix,
            input_branch=prepare_result.input_branch,
            base_ref=prepare_result.base_ref,
        )
    )

    binary = claude_bin or os.environ.get("STACK_CLAUDE_BIN") or shutil.which("claude")
    if binary is None:
        local_md.unlink(missing_ok=True)
        raise DecomposeError("`claude` binary not on PATH")

    runner = subprocess_runner or _default_subprocess_runner
    try:
        exit_code = runner([binary], repo_path)
    finally:
        local_md.unlink(missing_ok=True)

    if exit_code != 0:
        raise DecomposeError(f"`claude` exited with code {exit_code}")

    if not sentinel_path.exists():
        raise SentinelMissing(str(sentinel_path))

    return DecomposeResult(sentinel=_load_sentinel(sentinel_path))


def _default_subprocess_runner(args: list[str], cwd: Path) -> int:
    return subprocess.run(args, cwd=str(cwd), check=False).returncode


def _load_sentinel(path: Path) -> Sentinel:
    return Sentinel.model_validate_json(path.read_text(encoding="utf-8"))


def _force_cleanup(
    repo_path: Path,
    prefix: str,
    branch_suffix: str,
    sentinel_path: Path,
) -> None:
    sentinel_path.unlink(missing_ok=True)
    for branch in git_ops.list_branches_matching(repo_path, f"{prefix}{branch_suffix}*"):
        git_ops.delete_branch(repo_path, branch, force=True)


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------


def manifest(
    redis_client: Redis,
    repo_path: Path,
    *,
    sentinel: Sentinel,
    project: str,
    code_repo: str,
    actor: str = "stack-cli",
    state_config: StateStoreConfig | None = None,
) -> Manifest:
    base_tip_sha = git_ops.sha_of(repo_path, sentinel.base_ref)

    branches: list[BranchEntry] = []
    commit_shas: list[str] = []
    for index, name in enumerate(sentinel.branches, start=1):
        if not git_ops.branch_exists(repo_path, name):
            raise PublishError(f"branch {name!r} is in the sentinel but missing locally")
        commit_sha = git_ops.sha_of(repo_path, name)
        parent_sha = git_ops.parent_sha(repo_path, name)
        tree_hash = git_ops.tree_of(repo_path, name)
        subject = git_ops.commit_subject(repo_path, name)
        body = git_ops.commit_body(repo_path, name)
        files = git_ops.files_changed(repo_path, parent_sha, commit_sha)

        expected_branch_name = _branch_name(sentinel.prefix, sentinel.branch_suffix, index)
        if name != expected_branch_name:
            raise PublishError(
                f"branch {name!r} does not match expected name {expected_branch_name!r} "
                f"(prefix={sentinel.prefix!r} suffix={sentinel.branch_suffix!r})"
            )

        expected_parent = commit_shas[-1] if commit_shas else base_tip_sha
        if parent_sha != expected_parent:
            raise PublishError(
                f"branch {name!r} parent {parent_sha} does not match expected {expected_parent}"
            )

        branches.append(
            BranchEntry(
                order=index,
                name=name,
                commit_sha=commit_sha,
                parent_sha=parent_sha,
                tree_hash=tree_hash,
                subject=subject,
                body=body,
                files_changed=files,
            )
        )
        commit_shas.append(commit_sha)

    # Defense-in-depth tree-hash check: cherry-pick recorded commits onto base
    # and compare against the top branch's tree.
    reference_tree = verify.compute_reference_tip(repo_path, base_tip_sha, commit_shas)
    top_tree = branches[-1].tree_hash
    if reference_tree != top_tree:
        raise VerifyTreeMismatch(top_tree, reference_tree)

    verification = Verification(
        passed=True,
        method="tree-hash-equality",
        original_tree=git_ops.tree_of(repo_path, sentinel.source_branch_tip),
        stack_tip_tree=top_tree,
        last_verified_at=datetime.now(UTC),
    )

    new_manifest = mf.new_manifest(
        prefix=sentinel.prefix,
        code_repo=code_repo,
        base_ref=sentinel.base_ref,
        branch_suffix=sentinel.branch_suffix,
        source_branch=sentinel.source_branch,
        source_branch_tip=sentinel.source_branch_tip,
        branches=branches,
        verification=verification,
    )

    config = state_config or StateStoreConfig()
    return state_store.update_manifest(
        redis_client,
        project,
        sentinel.prefix,
        lambda _current: new_manifest,
        audit_message="manifest created by stack manifest",
        actor=actor,
        operation="manifest",
        event_type="manifest_created",
        config=config,
    )


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------


def publish(
    redis_client: Redis,
    repo_path: Path,
    ado_client: AdoClient,
    ado_remote: AdoRemote,
    *,
    prefix: str,
    remote: str = "origin",
    confirmed: Callable[[Manifest], bool] | None = None,
    actor: str = "stack-cli",
    state_config: StateStoreConfig | None = None,
) -> Manifest:
    config = state_config or StateStoreConfig()
    current = state_store.get_manifest(redis_client, ado_remote.project, prefix, config=config)
    if current is None:
        raise PublishError(
            f"no manifest in Redis for project={ado_remote.project!r} prefix={prefix!r}; "
            "run `stack manifest` first"
        )

    if confirmed is not None and not confirmed(current):
        return current

    for entry in current.branches:
        git_ops.push_branch(repo_path, remote, entry.name)

    pr_updates: list[tuple[str, int, str]] = []
    for index, entry in enumerate(current.branches):
        target_branch = (
            current.branches[index - 1].name if index > 0 else current.base_ref
        )
        pr = _sync_pr_for_branch(
            ado_client,
            ado_remote,
            current,
            entry,
            target_branch,
            key_prefix=config.key_prefix,
        )
        pr_updates.append((entry.name, pr.pr_id, pr.web_url))

    def _modify(m: Manifest | None) -> Manifest:
        if m is None:
            raise PublishError("manifest disappeared from Redis during publish")
        updated = m
        for name, pr_id, pr_url in pr_updates:
            updated = mf.with_pr_recorded(updated, name, pr_id, pr_url)
        return updated

    after = state_store.update_manifest(
        redis_client,
        ado_remote.project,
        prefix,
        _modify,
        audit_message="PR ids recorded by stack publish",
        actor=actor,
        operation="publish",
        event_type="prs_published",
        config=config,
    )

    _refresh_root_description(ado_client, ado_remote, after, key_prefix=config.key_prefix)

    return after


def _sync_pr_for_branch(
    ado_client: AdoClient,
    ado_remote: AdoRemote,
    manifest_now: Manifest,
    entry: BranchEntry,
    target_branch: str,
    *,
    key_prefix: str,
) -> ado_pr.PullRequest:
    existing = ado_pr.list_for_branch(
        ado_client,
        ado_remote.project,
        ado_remote.repo,
        entry.name,
        organization_url=ado_remote.org_url,
    )
    active = [p for p in existing if p.status == "active"]
    abandoned = [p for p in existing if p.status == "abandoned"]
    completed = [p for p in existing if p.status == "completed"]

    title = f"[Part {entry.order}] {entry.subject}"
    description = _render_pr_body(manifest_now, entry, key_prefix=key_prefix)

    if completed and not active and not abandoned:
        return completed[0]

    if active:
        return ado_pr.update(
            ado_client,
            ado_remote.project,
            ado_remote.repo,
            active[0].pr_id,
            target_branch=target_branch,
            title=title,
            description=description,
            organization_url=ado_remote.org_url,
        )

    if abandoned:
        return ado_pr.update(
            ado_client,
            ado_remote.project,
            ado_remote.repo,
            abandoned[0].pr_id,
            target_branch=target_branch,
            title=title,
            description=description,
            status="active",
            organization_url=ado_remote.org_url,
        )

    return ado_pr.create(
        ado_client,
        ado_remote.project,
        ado_remote.repo,
        source_branch=entry.name,
        target_branch=target_branch,
        title=title,
        description=description,
        organization_url=ado_remote.org_url,
    )


def _render_pr_body(
    manifest_now: Manifest,
    entry: BranchEntry,
    *,
    key_prefix: str,
) -> str:
    manifest_pointer = pr_templates.manifest_path(
        key_prefix, manifest_now.code_repo.split("/")[0], manifest_now.prefix
    )
    bottom = topology.bottom_branch(manifest_now)
    if entry.name == bottom.name:
        return pr_templates.render(
            "pr_root",
            ORDER=str(entry.order),
            COMMIT_SUBJECT=entry.subject,
            COMMIT_BODY=entry.body,
            STACK_LIST=pr_templates.build_stack_list(manifest_now),
            MANIFEST_PATH=manifest_pointer,
        )
    root_link = bottom.pr_url or (f"#{bottom.pr_id}" if bottom.pr_id else "(pending)")
    return pr_templates.render(
        "pr_leaf",
        ORDER=str(entry.order),
        COMMIT_SUBJECT=entry.subject,
        COMMIT_BODY=entry.body,
        ROOT_LINK=root_link,
        MANIFEST_PATH=manifest_pointer,
    )


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
    description = _render_pr_body(manifest_after, bottom, key_prefix=key_prefix)
    ado_pr.update(
        ado_client,
        ado_remote.project,
        ado_remote.repo,
        bottom.pr_id,
        description=description,
        organization_url=ado_remote.org_url,
    )
