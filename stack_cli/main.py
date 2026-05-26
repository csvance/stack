"""Typer entry point for the ``stack`` CLI.

The app has global options (``--yes``, ``--dry-run``, ``--verbose``, ``--stack``)
attached at the root callback. Per-command modules import ``app`` and register
themselves via ``app.command()``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import typer

from stack_cli.commands import create as create_cmd
from stack_cli.commands import status as status_cmd


@dataclass
class GlobalOptions:
    yes: bool = False
    dry_run: bool = False
    verbose: bool = False
    stack_prefix: str | None = None
    repo_path: Path = Path(".")
    no_color: bool = False


app = typer.Typer(
    name="stack",
    help="CLI for the stacked-diff workflow.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def _root(
    ctx: typer.Context,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompts."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Do not perform side effects."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging."),
    stack_prefix: str | None = typer.Option(
        None, "--stack", help="Operate on a specific stack prefix."
    ),
    repo: Path = typer.Option(
        Path("."), "--repo", help="Repository path (defaults to current directory)."
    ),
    no_color: bool = typer.Option(False, "--no-color", help="Disable ANSI color output."),
) -> None:
    ctx.obj = GlobalOptions(
        yes=yes,
        dry_run=dry_run,
        verbose=verbose,
        stack_prefix=stack_prefix,
        repo_path=repo,
        no_color=no_color,
    )


app.command(name="status")(status_cmd.status)
app.command(name="prepare")(create_cmd.prepare)
app.command(name="decompose")(create_cmd.decompose)
app.command(name="manifest")(create_cmd.manifest)
app.command(name="publish")(create_cmd.publish)
app.command(name="create")(create_cmd.create)


if __name__ == "__main__":
    app()
