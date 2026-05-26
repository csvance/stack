"""POST /webhooks/ado handler.

Synchronous path: parse + filter + idempotency-claim. On a fresh claim, spawn
the background landing handler via ``asyncio.create_task`` and return 200
within milliseconds.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from fastapi import APIRouter, Request, Response

from stack_bot import idempotency
from stack_bot.handlers import land as land_handler
from stack_bot.webhooks.models import AdoPullRequestEvent
from stack_core import state_store
from stack_core.state_store import AdoRemote

logger = logging.getLogger(__name__)

router = APIRouter()

PR_MERGED_EVENT = "git.pullrequest.merged"


def _derive_prefix(branch: str, branch_suffix: str) -> str | None:
    pattern = rf"^(.+){re.escape(branch_suffix)}(\d+)$"
    m = re.match(pattern, branch)
    if not m:
        return None
    return m.group(1)


@router.post("/webhooks/ado")
async def receive_ado(request: Request) -> Response:
    state = request.app.state

    if request.headers.get("x-ms-signature") is None:
        logger.warning("inbound webhook missing X-MS-Signature; accepting (validation deferred)")

    payload: Any = await request.json()
    try:
        event = AdoPullRequestEvent.model_validate(payload)
    except Exception:
        logger.exception("malformed ADO webhook payload")
        return Response(status_code=200)

    if event.eventType != PR_MERGED_EVENT:
        return Response(status_code=200)

    config = state.config

    prefix = _derive_prefix(event.source_branch, config.branch_suffix)
    if prefix is None:
        return Response(status_code=200)

    project = event.project or _first_project(config)
    if project is None:
        logger.warning("no project resolvable from event or config; returning 200")
        return Response(status_code=200)

    redis_client = state.redis_client
    manifest = await asyncio.to_thread(
        state_store.get_manifest, redis_client, project, prefix,
    )
    if manifest is None:
        return Response(status_code=200)
    if manifest.branches[0].name != event.source_branch:
        return Response(status_code=200)

    notif_id = str(event.notificationId)
    claimed = await asyncio.to_thread(
        idempotency.claim,
        redis_client,
        key_prefix=config.redis.key_prefix,
        project=project,
        notification_id=notif_id,
        ttl_seconds=config.redis.idempotency_ttl_days * 24 * 60 * 60,
    )
    if not claimed:
        return Response(status_code=200)

    ado_remote = AdoRemote(
        org_url=config.ado.organization_url,
        project=project,
        repo=event.repo,
    )
    task = asyncio.create_task(
        land_handler.handle(
            config, redis_client, ado_remote,
            prefix=prefix, bottom_pr_id=event.pr_id,
        )
    )
    state.tasks.add(task)
    task.add_done_callback(state.tasks.discard)
    return Response(status_code=200)


def _first_project(config: Any) -> str | None:
    if not config.projects:
        return None
    return str(config.projects[0].name)
