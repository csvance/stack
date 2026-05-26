"""``pystack status`` command implementation."""

from __future__ import annotations

import json
import re
import sys
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from redis import Redis

from stack_cli import output
from stack_cli.safeguards import ensure_not_managed
from stack_core import state_store
from stack_core.ado.client import AdoClient
from stack_core.config import StackConfig, load_config
from stack_core.exceptions import ManifestNotFoundError
from stack_core.operations import status as status_op

if TYPE_CHECKING:
    from stack_cli.main import GlobalOptions


class StatusFormat(StrEnum):
    HUMAN = "human"
    JSON = "json"


def status(
    ctx: typer.Context,
    format: StatusFormat = typer.Option(
        StatusFormat.HUMAN, "--format", help="Output format."
    ),
    check_prs: bool = typer.Option(
        False, "--check-prs", help="Query ADO for bottom PR status (slower)."
    ),
    allow_managed: bool = typer.Option(False, "--allow-managed", hidden=True),
) -> None:
    """Report drift between the manifest and observable git/ADO state."""
    options: GlobalOptions = ctx.obj
    config = load_config()
    repo_path = options.repo_path.resolve()

    origin_url = _origin_url(repo_path)
    ado_remote = state_store.parse_ado_remote(origin_url)
    project = ado_remote.project

    prefix = _resolve_prefix(options.stack_prefix, repo_path, config.branch_suffix)
    ensure_not_managed(prefix, allow_managed=allow_managed)

    redis_client = _connect_redis(config)

    ado_client: AdoClient | None = None
    try:
        if check_prs:
            ado_client = AdoClient(config.ado.organization_url, config.ado.pat)
        try:
            result = status_op.run(
                redis_client,
                repo_path,
                project,
                prefix,
                ado_client=ado_client,
                ado_project=ado_remote.project,
                ado_repo=ado_remote.repo,
                organization_url=config.ado.organization_url,
            )
        except ManifestNotFoundError as exc:
            output.error(str(exc))
            raise typer.Exit(code=2) from exc
    finally:
        if ado_client is not None:
            ado_client.close()
        redis_client.close()

    if format is StatusFormat.JSON:
        _render_json(result)
    else:
        _render_human(result)

    raise typer.Exit(code=1 if result.drifts else 0)


def _connect_redis(config: StackConfig) -> Redis:
    return Redis(
        host=config.redis.host,
        port=config.redis.port,
        password=config.redis.password,
        db=config.redis.db,
        decode_responses=False,
    )


def _origin_url(repo_path: Path) -> str:
    from stack_core import git_ops

    return git_ops.remote_url(repo_path, "origin")


_PREFIX_FROM_BRANCH_TEMPLATE = "^(.+){suffix}(\\d+)$"


def _resolve_prefix(explicit: str | None, repo_path: Path, suffix: str) -> str:
    if explicit:
        return explicit
    from stack_core import git_ops

    current = git_ops.current_branch(repo_path)
    if current is None:
        output.error("HEAD is detached; pass --stack <prefix>")
        raise typer.Exit(code=2)
    pattern = _PREFIX_FROM_BRANCH_TEMPLATE.format(suffix=re.escape(suffix))
    m = re.match(pattern, current)
    if not m:
        output.error(
            f"current branch {current!r} does not match suffix {suffix!r}; "
            "pass --stack <prefix>"
        )
        raise typer.Exit(code=2)
    return m.group(1)


def _render_human(result: status_op.StatusResult) -> None:
    m = result.manifest
    print(f"Stack: {output.emphasize(m.prefix)}  (base_ref={m.base_ref}, branches={len(m.branches)})")
    print(output.dim(f"  base_ref_tip={result.base_ref_tip[:12]}  head_branch={result.head_branch}"))
    print()
    for entry in m.branches:
        state = result.git_state.get(entry.name)
        suffix = ""
        if state is None or not state.exists:
            suffix = output.dim("  [missing]")
        elif state.tip_sha and state.tip_sha != entry.commit_sha:
            suffix = output.dim(f"  [moved: {state.tip_sha[:12]}]")
        pr = f"  PR #{entry.pr_id}" if entry.pr_id else ""
        print(f"  {entry.order:>3}. {entry.name}  {entry.commit_sha[:12]}{pr}{suffix}")
        print(output.dim(f"        {entry.subject}"))

    print()
    if not result.drifts:
        output.success("no drift detected", file=sys.stdout)
        return
    output.warn(f"{len(result.drifts)} drift record(s) detected", file=sys.stdout)
    for d in result.drifts:
        location = f"{d.branch}: " if d.branch else ""
        print(f"  - [{d.category.value}] {location}{d.detail}")


def _render_json(result: status_op.StatusResult) -> None:
    payload = {
        "prefix": result.manifest.prefix,
        "base_ref": result.manifest.base_ref,
        "base_ref_tip": result.base_ref_tip,
        "head_branch": result.head_branch,
        "head_sha": result.head_sha,
        "branches": [
            {
                "order": b.order,
                "name": b.name,
                "commit_sha": b.commit_sha,
                "pr_id": b.pr_id,
                "tip_sha": result.git_state[b.name].tip_sha if b.name in result.git_state else None,
                "exists": result.git_state[b.name].exists if b.name in result.git_state else False,
            }
            for b in result.manifest.branches
        ],
        "drifts": [
            {"category": d.category.value, "branch": d.branch, "detail": d.detail}
            for d in result.drifts
        ],
    }
    json.dump(payload, sys.stdout, indent=2)
    print()
