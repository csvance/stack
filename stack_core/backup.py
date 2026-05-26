"""Git-ref snapshots for safe rollback of stack mutations.

A snapshot captures the SHA each named branch points at, stored under
``refs/backup/stack/<prefix>/<op>-<ts>/<branch>``. Restoring resets each branch
to its recorded SHA. Ports the bash CLI's ``lib/backup.sh``.

Ref naming is deterministic so multiple snapshots from the same operation
sit alongside each other in timestamp order, and discovery is a simple
``git for-each-ref refs/backup/stack/<prefix>/`` walk.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from stack_core import git_ops
from stack_core.exceptions import GitError

REF_NAMESPACE = "refs/backup/stack"


def _snapshot_id(operation: str, now: datetime | None = None) -> str:
    when = now or datetime.now(UTC)
    stamp = when.strftime("%Y%m%dT%H%M%SZ")
    return f"{operation}-{stamp}"


def _ref(prefix: str, snapshot_id: str, branch: str) -> str:
    return f"{REF_NAMESPACE}/{prefix}/{snapshot_id}/{branch}"


def snapshot(
    repo_path: Path,
    operation: str,
    prefix: str,
    branches: list[str],
    *,
    clock: datetime | None = None,
) -> str:
    """Create a snapshot of the given branches. Returns the snapshot id.

    Raises :class:`GitError` if any branch's tip cannot be resolved.
    """
    snap_id = _snapshot_id(operation, clock)
    for branch in branches:
        sha = git_ops.sha_of(repo_path, branch)
        git_ops._git(["update-ref", _ref(prefix, snap_id, branch), sha], repo_path)
    return snap_id


def restore(repo_path: Path, prefix: str, snapshot_id: str) -> None:
    """Reset each branch in the snapshot to its recorded SHA.

    The snapshot refs themselves are left in place; callers may delete them
    once they are sure they no longer need them.
    """
    pattern = f"{REF_NAMESPACE}/{prefix}/{snapshot_id}/"
    listing = git_ops._git(
        ["for-each-ref", "--format=%(refname) %(objectname)", f"{pattern}*"],
        repo_path,
    )
    if not listing:
        raise GitError(
            ["for-each-ref", f"{pattern}*"],
            f"no snapshot refs found for {snapshot_id}",
            0,
        )
    for line in listing.splitlines():
        ref, sha = line.split(" ", 1)
        branch = ref[len(pattern):]
        git_ops._git(["update-ref", f"refs/heads/{branch}", sha], repo_path)
