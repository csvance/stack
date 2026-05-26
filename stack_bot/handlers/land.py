"""Background handler that runs the landing operation for one webhook event.

Lifecycle:
1. Acquire per-stack lock so concurrent webhooks for the same stack serialize.
2. Spin up an ephemeral workspace via ``workspace_mgr.ephemeral_clone``.
3. Call ``stack_core.operations.land`` synchronously inside ``asyncio.to_thread``.
4. Handle the result: LandResult variants are logged; LandConflict / VerifyTreeMismatch
   post a PR comment + the ``stack-conflict`` label; other exceptions trigger an
   SMTP alert (we don't leak tracebacks onto the PR).
"""

from __future__ import annotations

import asyncio
import logging
import traceback

from redis import Redis

from stack_bot import alerts, conflict_comments, notifications, stack_locks
from stack_bot.config import BotConfig
from stack_bot.workspace_mgr import ephemeral_clone
from stack_core.ado.client import AdoClient
from stack_core.exceptions import LandConflict, VerifyTreeMismatch
from stack_core.operations import land as land_op
from stack_core.state_store import AdoRemote

logger = logging.getLogger(__name__)


async def handle(
    config: BotConfig,
    redis_client: Redis,
    ado_remote: AdoRemote,
    *,
    prefix: str,
    bottom_pr_id: int,
) -> None:
    async with stack_locks.acquire_for(ado_remote.project, prefix):
        try:
            async with ephemeral_clone(config, ado_remote) as workspace:
                ado_client = AdoClient(config.ado.organization_url, config.ado.pat)
                try:
                    result = await asyncio.to_thread(
                        land_op.land,
                        redis_client, workspace, ado_client, ado_remote,
                        prefix=prefix,
                    )
                finally:
                    ado_client.close()
            logger.info(
                "land result: project=%s prefix=%s action=%s reason=%s",
                ado_remote.project, prefix, result.action, result.reason,
            )
        except LandConflict as exc:
            logger.info("land conflict: project=%s prefix=%s", ado_remote.project, prefix)
            await _post_conflict_comment(
                config, ado_remote, bottom_pr_id, prefix, exc.conflicting_paths,
            )
        except VerifyTreeMismatch:
            logger.info("land tree mismatch: project=%s prefix=%s", ado_remote.project, prefix)
            await _post_tree_mismatch_comment(config, ado_remote, bottom_pr_id, prefix)
        except Exception as exc:
            logger.exception(
                "unhandled exception in land handler: project=%s prefix=%s",
                ado_remote.project, prefix,
            )
            alerts.send(
                alerts.AlertCategory.UNHANDLED_EXCEPTION,
                f"stackbot land failure: {ado_remote.project}/{prefix}",
                f"{exc!r}\n\n{traceback.format_exc()}",
                config.smtp,
            )


async def _post_conflict_comment(
    config: BotConfig,
    ado_remote: AdoRemote,
    pr_id: int,
    prefix: str,
    conflicting_paths: list[str],
) -> None:
    ado_client = AdoClient(config.ado.organization_url, config.ado.pat)
    try:
        existing = await notifications.list_labels(ado_client, ado_remote, pr_id)
        if conflict_comments.STACK_CONFLICT_LABEL in existing:
            logger.info(
                "conflict label already present, suppressing duplicate comment: pr=%s",
                pr_id,
            )
            return
        message = conflict_comments.render_conflict_handoff(
            branch=f"{prefix}-stacked-2",
            new_base=f"{ado_remote.project}/main",
            conflicting_paths=conflicting_paths,
        )
        await notifications.add_comment(ado_client, ado_remote, pr_id, message)
        await notifications.add_label(
            ado_client, ado_remote, pr_id, conflict_comments.STACK_CONFLICT_LABEL,
        )
    finally:
        ado_client.close()


async def _post_tree_mismatch_comment(
    config: BotConfig,
    ado_remote: AdoRemote,
    pr_id: int,
    prefix: str,
) -> None:
    ado_client = AdoClient(config.ado.organization_url, config.ado.pat)
    try:
        existing = await notifications.list_labels(ado_client, ado_remote, pr_id)
        if conflict_comments.STACK_CONFLICT_LABEL in existing:
            return
        message = conflict_comments.render_tree_mismatch_handoff(
            branch=f"{prefix}-stacked-2",
            new_base=f"{ado_remote.project}/main",
        )
        await notifications.add_comment(ado_client, ado_remote, pr_id, message)
        await notifications.add_label(
            ado_client, ado_remote, pr_id, conflict_comments.STACK_CONFLICT_LABEL,
        )
    finally:
        ado_client.close()
