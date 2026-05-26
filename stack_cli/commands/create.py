"""``stack prepare`` / ``decompose`` / ``manifest`` / ``publish`` / ``create`` commands.

Each command parses its args, builds the Redis and (when needed) ADO clients,
invokes the corresponding phase in ``stack_core.operations.create``, and renders
the result. ``stack create`` chains all four phases.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import typer
from redis import Redis

from stack_cli import output
from stack_core import git_ops, state_store
from stack_core.ado.client import AdoClient
from stack_core.config import StackConfig, load_config
from stack_core.operations import create as create_op
from stack_core.types import Manifest

if TYPE_CHECKING:
    from stack_cli.main import GlobalOptions


def _connect_redis(config: StackConfig) -> Redis:
    return Redis(
        host=config.redis.host,
        port=config.redis.port,
        password=config.redis.password,
        db=config.redis.db,
        decode_responses=False,
    )


def _resolve_ado(repo_path: Path) -> state_store.AdoRemote:
    return state_store.parse_ado_remote(git_ops.remote_url(repo_path, "origin"))


def _default_base_ref() -> str:
    return "origin/main"


def prepare(
    ctx: typer.Context,
    prefix: str = typer.Option(..., "--prefix", "-p", help="Stack prefix."),
    base: str = typer.Option(_default_base_ref(), "--base", help="Base ref (e.g. origin/main)."),
    feature: str | None = typer.Option(
        None, "--feature", help="Feature branch (default: current branch)."
    ),
    branch_suffix: str | None = typer.Option(
        None, "--branch-suffix", help="Override the config's branch suffix."
    ),
) -> None:
    """Validate prerequisites and produce a single-commit input branch."""
    options: GlobalOptions = ctx.obj
    config = load_config()
    repo_path = options.repo_path.resolve()
    suffix = branch_suffix or config.branch_suffix
    try:
        result = create_op.prepare(
            repo_path,
            prefix=prefix,
            base_ref=base,
            branch_suffix=suffix,
            feature_branch=feature,
        )
    except Exception as exc:
        output.error(str(exc))
        raise typer.Exit(code=2) from exc
    output.success(
        f"prepare ok: input_branch={result.input_branch} "
        f"sha={result.input_sha[:12]} base={result.base_ref} suffix={result.branch_suffix}"
    )


def decompose(
    ctx: typer.Context,
    prefix: str = typer.Option(..., "--prefix", "-p", help="Stack prefix."),
    base: str = typer.Option(_default_base_ref(), "--base", help="Base ref."),
    feature: str | None = typer.Option(None, "--feature", help="Feature branch."),
    branch_suffix: str | None = typer.Option(None, "--branch-suffix"),
    force: bool = typer.Option(
        False, "--force", help="Delete any prior sentinel/branches before re-launching."
    ),
) -> None:
    """Launch Claude Code to decompose the prepared input branch."""
    options: GlobalOptions = ctx.obj
    config = load_config()
    repo_path = options.repo_path.resolve()
    suffix = branch_suffix or config.branch_suffix
    try:
        prepare_result = create_op.prepare(
            repo_path,
            prefix=prefix,
            base_ref=base,
            branch_suffix=suffix,
            feature_branch=feature,
        )
        result = create_op.decompose(
            repo_path,
            prepare_result=prepare_result,
            prefix=prefix,
            force=force,
        )
    except Exception as exc:
        output.error(str(exc))
        raise typer.Exit(code=2) from exc
    sentinel = result.sentinel
    output.success(
        f"decompose ok: prefix={sentinel.prefix} branches={len(sentinel.branches)} "
        f"source_branch_tip={sentinel.source_branch_tip[:12]}"
    )


def manifest(
    ctx: typer.Context,
    prefix: str = typer.Option(..., "--prefix", "-p", help="Stack prefix."),
) -> None:
    """Construct the manifest from git and write it to Redis."""
    options: GlobalOptions = ctx.obj
    config = load_config()
    repo_path = options.repo_path.resolve()
    sentinel_path = create_op._sentinel_path(repo_path, prefix)
    if not sentinel_path.exists():
        output.error(f"sentinel not found: {sentinel_path}; run `stack decompose` first")
        raise typer.Exit(code=2)
    sentinel = create_op._load_sentinel(sentinel_path)

    ado_remote = _resolve_ado(repo_path)
    redis_client = _connect_redis(config)
    try:
        try:
            result = create_op.manifest(
                redis_client,
                repo_path,
                sentinel=sentinel,
                project=ado_remote.project,
                code_repo=f"{ado_remote.project}/{ado_remote.repo}",
            )
        except Exception as exc:
            output.error(str(exc))
            raise typer.Exit(code=2) from exc
    finally:
        redis_client.close()
    output.success(
        f"manifest ok: prefix={result.prefix} branches={len(result.branches)} "
        f"stack_tip_tree={result.verification.stack_tip_tree[:12]}"
    )


def _interactive_confirm(manifest_obj: Manifest, yes: bool) -> bool:
    print("Stack:", manifest_obj.prefix)
    for entry in manifest_obj.branches:
        target = (
            manifest_obj.branches[entry.order - 2].name
            if entry.order > 1
            else manifest_obj.base_ref
        )
        print(f"  {entry.order:>2}. {entry.name} -> {target}   {entry.subject}")
    if yes:
        return True
    response = input("Push branches and sync PRs? [y/N] ").strip().lower()
    return response in {"y", "yes"}


def publish(
    ctx: typer.Context,
    prefix: str = typer.Option(..., "--prefix", "-p", help="Stack prefix."),
) -> None:
    """Push branches and create/update PRs for the stack."""
    options: GlobalOptions = ctx.obj
    config = load_config()
    repo_path = options.repo_path.resolve()
    ado_remote = _resolve_ado(repo_path)
    redis_client = _connect_redis(config)
    ado_client = AdoClient(config.ado.organization_url, config.ado.pat)
    try:
        try:
            result = create_op.publish(
                redis_client,
                repo_path,
                ado_client,
                ado_remote,
                prefix=prefix,
                confirmed=lambda m: _interactive_confirm(m, options.yes),
            )
        except Exception as exc:
            output.error(str(exc))
            raise typer.Exit(code=2) from exc
    finally:
        ado_client.close()
        redis_client.close()
    output.success(
        f"publish ok: prefix={result.prefix} branches={len(result.branches)} "
        f"prs={[b.pr_id for b in result.branches]}"
    )


def create(
    ctx: typer.Context,
    prefix: str = typer.Option(..., "--prefix", "-p", help="Stack prefix."),
    base: str = typer.Option(_default_base_ref(), "--base", help="Base ref."),
    feature: str | None = typer.Option(None, "--feature", help="Feature branch."),
    branch_suffix: str | None = typer.Option(None, "--branch-suffix"),
    force_decompose: bool = typer.Option(
        False, "--force-decompose", help="Pass --force to the decompose phase."
    ),
) -> None:
    """Chain prepare → decompose → manifest → publish."""
    options: GlobalOptions = ctx.obj
    config = load_config()
    repo_path = options.repo_path.resolve()
    suffix = branch_suffix or config.branch_suffix
    ado_remote = _resolve_ado(repo_path)

    redis_client = _connect_redis(config)
    ado_client = AdoClient(config.ado.organization_url, config.ado.pat)
    try:
        try:
            prepare_result = create_op.prepare(
                repo_path,
                prefix=prefix,
                base_ref=base,
                branch_suffix=suffix,
                feature_branch=feature,
            )
            output.success(f"prepared input_branch={prepare_result.input_branch}")

            decompose_result = create_op.decompose(
                repo_path,
                prepare_result=prepare_result,
                prefix=prefix,
                force=force_decompose,
            )
            output.success(
                f"decomposed into {len(decompose_result.sentinel.branches)} branches"
            )

            manifest_obj = create_op.manifest(
                redis_client,
                repo_path,
                sentinel=decompose_result.sentinel,
                project=ado_remote.project,
                code_repo=f"{ado_remote.project}/{ado_remote.repo}",
            )
            output.success(f"manifest written to Redis (prefix={manifest_obj.prefix})")

            after = create_op.publish(
                redis_client,
                repo_path,
                ado_client,
                ado_remote,
                prefix=prefix,
                confirmed=lambda m: _interactive_confirm(m, options.yes),
            )
            output.success(
                f"published: prs={[b.pr_id for b in after.branches]}"
            )
        except Exception as exc:
            output.error(str(exc))
            raise typer.Exit(code=2) from exc
    finally:
        ado_client.close()
        redis_client.close()
