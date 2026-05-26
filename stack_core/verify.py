"""Tree-hash verification via cherry-pick into a throwaway worktree.

Used by the ``manifest`` create phase as a defense-in-depth check on the
decomposer's output: cherry-pick each recorded commit onto the resolved base
tip in a fresh detached worktree, then capture the tree hash at the final
commit. The caller compares against the expected tree (the source-branch tip's
tree). A mismatch means the recorded commits don't reproduce the original
feature branch's working state, which would otherwise slip through.

Ports the bash CLI's ``lib/verify.sh:11-37``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from stack_core import git_ops
from stack_core.exceptions import GitError, VerifyConflict


def compute_reference_tip(repo_path: Path, base_sha: str, commits: list[str]) -> str:
    """Cherry-pick ``commits`` onto ``base_sha`` in a temp worktree; return the final tree hash.

    Raises :class:`VerifyConflict` if any cherry-pick fails. The worktree is
    removed in either case (try/finally).
    """
    if not commits:
        return git_ops.tree_of(repo_path, base_sha)

    with tempfile.TemporaryDirectory(prefix="stack-verify-") as tmp:
        worktree = Path(tmp) / "wt"
        git_ops.worktree_add_detached(repo_path, worktree, base_sha)
        try:
            for sha in commits:
                try:
                    git_ops.cherry_pick(worktree, sha)
                except GitError:
                    conflicting = git_ops.conflicting_paths(worktree)
                    git_ops.cherry_pick_abort(worktree)
                    raise VerifyConflict(sha, conflicting) from None
            return git_ops.tree_of(worktree, "HEAD")
        finally:
            git_ops.worktree_remove(repo_path, worktree, force=True)
