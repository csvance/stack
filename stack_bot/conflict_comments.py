"""Conflict-handoff comment templates and label constants."""

from __future__ import annotations

STACK_CONFLICT_LABEL = "stack-conflict"


def render_conflict_handoff(
    branch: str,
    new_base: str,
    conflicting_paths: list[str],
) -> str:
    """Level-1 conflict comment: initial conflict during rebase."""
    files = "\n".join(f"  - {p}" for p in conflicting_paths) if conflicting_paths else "  - <unknown>"
    return f"""I couldn't complete the rebase on this stack.

What happened:
- Tried to rebase `{branch}` onto `{new_base}`
- Got conflicts in:
{files}

To resolve:
1. Check out the branch: `git checkout {branch}`
2. Rebase onto the current base: `git rebase {new_base}`
3. Resolve the conflicts in the files listed above
4. Force-push with lease: `git push --force-with-lease`

I'll retry automatically when I see the branch update.
"""


def render_tree_mismatch_handoff(branch: str, new_base: str) -> str:
    """When defense-in-depth tree-hash verification fails after a clean rebase."""
    return f"""I rebased `{branch}` onto `{new_base}` without conflicts, but the result didn't match the expected tree.

I restored the stack to its pre-rebase state from a backup. This usually means
the recorded commits don't reproduce the original feature branch when applied
in order to the new base.

Action required: investigate the divergence (likely a commit was amended or
the base ref moved in an unexpected way) and re-run `stack land` manually
after sorting it out.
"""
