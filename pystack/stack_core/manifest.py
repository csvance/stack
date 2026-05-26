"""Pure helpers for constructing and transforming Manifest values.

No I/O. Every function returns a new immutable Manifest; the input is never modified.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from stack_core.exceptions import TopologyError
from stack_core.types import BranchEntry, Manifest, Verification


def utcnow() -> datetime:
    """Single source of truth for "now"; overridable in tests by monkeypatching."""
    return datetime.now(UTC)


def new_manifest(
    *,
    prefix: str,
    code_repo: str,
    base_ref: str,
    branch_suffix: str = "-stacked-",
    source_branch: str,
    source_branch_tip: str,
    branches: list[BranchEntry],
    verification: Verification,
    clock: Callable[[], datetime] = utcnow,
) -> Manifest:
    """Construct a new manifest with created_at/last_update set from the clock."""
    now = clock()
    return Manifest(
        version=1,
        prefix=prefix,
        code_repo=code_repo,
        base_ref=base_ref,
        branch_suffix=branch_suffix,
        source_branch=source_branch,
        source_branch_tip=source_branch_tip,
        created_at=now,
        last_update=now,
        branches=branches,
        verification=verification,
    )


def with_branch_updated(
    manifest: Manifest,
    name: str,
    *,
    clock: Callable[[], datetime] = utcnow,
    **changes: object,
) -> Manifest:
    """Return a copy of the manifest with one branch entry's fields replaced.

    The full manifest is re-validated, so callers updating SHA fields must also
    update neighbors as needed. Typical use is to record PR ids after publish.
    """
    target_index = _find_branch_index(manifest, name)
    target = manifest.branches[target_index]
    updated_branch = BranchEntry.model_validate({**target.model_dump(), **changes})
    new_branches = list(manifest.branches)
    new_branches[target_index] = updated_branch
    return _rebuild(manifest, branches=new_branches, last_update=clock())


def with_branches_replaced(
    manifest: Manifest,
    new_branches: list[BranchEntry],
    *,
    clock: Callable[[], datetime] = utcnow,
) -> Manifest:
    """Replace the entire branch list, preserving manifest-level fields.

    Used by land and sync, which rebase the remaining branches and produce a new
    set of entries (with new SHAs). The Manifest validator enforces invariants on
    the new list, including order monotonicity and parent-chain consistency.
    """
    return _rebuild(manifest, branches=new_branches, last_update=clock())


def with_pr_recorded(
    manifest: Manifest,
    name: str,
    pr_id: int,
    pr_url: str,
    *,
    clock: Callable[[], datetime] = utcnow,
) -> Manifest:
    return with_branch_updated(manifest, name, pr_id=pr_id, pr_url=pr_url, clock=clock)


def with_verification(
    manifest: Manifest,
    verification: Verification,
    *,
    clock: Callable[[], datetime] = utcnow,
) -> Manifest:
    return _rebuild(manifest, verification=verification, last_update=clock())


def _rebuild(manifest: Manifest, **overrides: object) -> Manifest:
    """Reconstruct a Manifest with overrides, going through full validation."""
    data = manifest.model_dump()
    data.update(overrides)
    return Manifest.model_validate(data)


def _find_branch_index(manifest: Manifest, name: str) -> int:
    for i, entry in enumerate(manifest.branches):
        if entry.name == name:
            return i
    raise TopologyError(f"branch {name!r} not in manifest (prefix={manifest.prefix!r})")
