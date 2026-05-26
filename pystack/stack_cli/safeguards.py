"""Safeguards against accidentally driving bot-managed stacks from the CLI.

Phase 2: placeholder. The CLI is the only writer, so the check is a no-op unless
``--allow-managed`` is passed (in which case we print a notice — harmless during
Phase 2 since no bot exists yet). Phase 4 will make this check actually refuse
when StackBot is the owner of a stack.
"""

from __future__ import annotations

from stack_cli import output


def ensure_not_managed(prefix: str, *, allow_managed: bool) -> None:
    """Phase-4 hook. In Phase 2 this only emits a notice when overridden."""
    if allow_managed:
        output.warn(
            f"--allow-managed: bypassing bot-ownership check for {prefix!r} "
            "(no-op in Phase 2; will refuse without this flag once StackBot ships)"
        )
