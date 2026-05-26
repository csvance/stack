"""Subprocess wrappers around git and git-branchless.

Each function takes an explicit ``cwd`` and returns stdout as a string (stripped
of the trailing newline) or raises :class:`GitError`. No global state, no environment
side effects beyond what the subprocess inherits.

The behavioral reference is the bash CLI's ``lib/git_helpers.sh`` and
``lib/branchless.sh``. Comparison testing in Phase 2 confirms parity.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from stack_core.exceptions import GitError

_IN_PROGRESS_MARKERS = (
    "MERGE_HEAD",
    "REBASE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
)
_IN_PROGRESS_DIRS = ("rebase-merge", "rebase-apply")


def _run(
    args: list[str],
    cwd: Path | str,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise GitError(args, result.stderr, result.returncode)
    return result


def _git(args: list[str], cwd: Path | str, *, check: bool = True) -> str:
    result = _run(["git", *args], cwd, check=check)
    return result.stdout.rstrip("\n")


def sha_of(cwd: Path | str, rev: str) -> str:
    """Resolve ``rev`` to a 40-char commit SHA."""
    return _git(["rev-parse", "--verify", f"{rev}^{{commit}}"], cwd)


def tree_of(cwd: Path | str, rev: str) -> str:
    """Resolve ``rev`` to its tree SHA."""
    return _git(["rev-parse", "--verify", f"{rev}^{{tree}}"], cwd)


def parent_sha(cwd: Path | str, rev: str) -> str:
    """Return the first parent of ``rev``."""
    return _git(["rev-parse", "--verify", f"{rev}^{{commit}}^"], cwd)


def commit_subject(cwd: Path | str, rev: str) -> str:
    return _git(["log", "-1", "--format=%s", rev], cwd)


def commit_body(cwd: Path | str, rev: str) -> str:
    return _git(["log", "-1", "--format=%b", rev], cwd)


def ref_exists(cwd: Path | str, ref: str) -> bool:
    result = _run(["git", "show-ref", "--verify", "--quiet", ref], cwd, check=False)
    return result.returncode == 0


def branch_exists(cwd: Path | str, name: str) -> bool:
    return ref_exists(cwd, f"refs/heads/{name}")


def list_branches_matching(cwd: Path | str, pattern: str) -> list[str]:
    """List local branches whose name matches the given glob pattern.

    The pattern is passed straight to ``git for-each-ref`` as the ref glob, e.g.
    ``refs/heads/my-feature-stacked-*``.
    """
    out = _git(["for-each-ref", "--format=%(refname:short)", f"refs/heads/{pattern}"], cwd)
    if not out:
        return []
    return out.splitlines()


def is_ancestor(cwd: Path | str, ancestor: str, descendant: str) -> bool:
    result = _run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd,
        check=False,
    )
    if result.returncode in (0, 1):
        return result.returncode == 0
    raise GitError(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        result.stderr,
        result.returncode,
    )


def files_changed(cwd: Path | str, rev_a: str, rev_b: str) -> list[str]:
    out = _git(["diff", "--name-only", rev_a, rev_b], cwd)
    if not out:
        return []
    return out.splitlines()


def remote_url(cwd: Path | str, remote: str = "origin") -> str:
    """Return the configured URL for the given git remote."""
    return _git(["remote", "get-url", remote], cwd)


def current_branch(cwd: Path | str) -> str | None:
    """Return the currently checked-out branch name, or ``None`` if HEAD is detached."""
    result = _run(["git", "symbolic-ref", "--short", "--quiet", "HEAD"], cwd, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.rstrip("\n") or None


def working_tree_clean(cwd: Path | str) -> bool:
    """Clean working tree AND no in-progress rebase/merge/cherry-pick/revert."""
    porcelain = _git(["status", "--porcelain", "--untracked-files=no"], cwd)
    if porcelain.strip():
        return False
    git_dir = Path(_git(["rev-parse", "--git-dir"], cwd))
    if not git_dir.is_absolute():
        git_dir = Path(cwd) / git_dir
    for marker in _IN_PROGRESS_MARKERS:
        if (git_dir / marker).exists():
            return False
    return all(not (git_dir / d).is_dir() for d in _IN_PROGRESS_DIRS)


def branchless_available() -> bool:
    """Return True if the git-branchless executable is on PATH."""
    return shutil.which("git-branchless") is not None


def branchless_move(
    cwd: Path | str,
    src: str,
    dest: str,
    *,
    force_rewrite: bool = True,
    merge: bool = True,
) -> None:
    """Run ``git branchless move`` to rebase ``src`` and descendants onto ``dest``.

    Defaults mirror the bash CLI: ``--force-rewrite`` (stack branches are typically
    already pushed, which branchless treats as "public") and ``--merge`` (pause on
    conflict rather than abort).
    """
    args = ["branchless", "move", "--source", src, "--dest", dest]
    if force_rewrite:
        args.append("--force-rewrite")
    if merge:
        args.append("--merge")
    _git(args, cwd)


def branchless_continue(cwd: Path | str) -> None:
    """Continue a paused branchless move via ``git rebase --continue``.

    branchless 0.11 has no ``continue`` subcommand; after a conflict during
    ``move --merge`` the operation falls back to an on-disk rebase.
    """
    _git(["rebase", "--continue"], cwd)


def branchless_abort(cwd: Path | str) -> None:
    """Best-effort abort of an in-progress branchless/rebase/cherry-pick/merge."""
    git_dir = Path(_git(["rev-parse", "--git-dir"], cwd))
    if not git_dir.is_absolute():
        git_dir = Path(cwd) / git_dir
    if (git_dir / "rebase-merge").is_dir() or (git_dir / "rebase-apply").is_dir():
        _run(["git", "rebase", "--abort"], cwd, check=False)
        return
    if (git_dir / "CHERRY_PICK_HEAD").exists():
        _run(["git", "cherry-pick", "--abort"], cwd, check=False)
        return
    if (git_dir / "MERGE_HEAD").exists():
        _run(["git", "merge", "--abort"], cwd, check=False)


def worktree_add_detached(cwd: Path | str, path: Path | str, rev: str) -> None:
    _git(["worktree", "add", "--detach", str(path), rev], cwd)


def worktree_remove(cwd: Path | str, path: Path | str, *, force: bool = False) -> None:
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(path))
    _git(args, cwd)
