"""Pure functions over manifest data. No I/O."""

from __future__ import annotations

from stack_core.exceptions import TopologyError
from stack_core.types import BranchEntry, Manifest


def branch_by_name(manifest: Manifest, name: str) -> BranchEntry | None:
    for entry in manifest.branches:
        if entry.name == name:
            return entry
    return None


def require_branch(manifest: Manifest, name: str) -> BranchEntry:
    entry = branch_by_name(manifest, name)
    if entry is None:
        raise TopologyError(f"branch {name!r} not in manifest (prefix={manifest.prefix!r})")
    return entry


def bottom_branch(manifest: Manifest) -> BranchEntry:
    return manifest.branches[0]


def top_branch(manifest: Manifest) -> BranchEntry:
    return manifest.branches[-1]


def branch_above(manifest: Manifest, name: str) -> BranchEntry | None:
    index = _index_of(manifest, name)
    if index == len(manifest.branches) - 1:
        return None
    return manifest.branches[index + 1]


def branch_below(manifest: Manifest, name: str) -> BranchEntry | None:
    index = _index_of(manifest, name)
    if index == 0:
        return None
    return manifest.branches[index - 1]


def expected_parent_sha(manifest: Manifest, name: str, base_ref_tip: str) -> str:
    """Return the SHA the named branch's first commit should descend from."""
    below = branch_below(manifest, name)
    if below is None:
        return base_ref_tip
    return below.commit_sha


def validate_chain(manifest: Manifest) -> None:
    """Re-run the parent-chain invariant explicitly.

    The Manifest validator already runs this on construction; this function exists
    for callers that want to validate after reading from an external source (e.g.,
    re-deserializing JSON they don't trust).
    """
    for i in range(1, len(manifest.branches)):
        prev = manifest.branches[i - 1]
        curr = manifest.branches[i]
        if curr.parent_sha != prev.commit_sha:
            raise TopologyError(
                f"branch {curr.name!r} parent_sha {curr.parent_sha} does not match "
                f"previous branch {prev.name!r} commit_sha {prev.commit_sha}"
            )


def _index_of(manifest: Manifest, name: str) -> int:
    for i, entry in enumerate(manifest.branches):
        if entry.name == name:
            return i
    raise TopologyError(f"branch {name!r} not in manifest (prefix={manifest.prefix!r})")
