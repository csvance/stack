"""Async wrappers around the sync ``stack_core.ado.pr`` PR comment / label helpers.

The bot's handlers are async; the underlying ADO client is sync. We wrap each
call in ``asyncio.to_thread`` so blocking HTTP doesn't stall the event loop.
"""

from __future__ import annotations

import asyncio

from stack_core.ado import pr as ado_pr
from stack_core.ado.client import AdoClient
from stack_core.state_store import AdoRemote


async def add_comment(
    ado_client: AdoClient, ado_remote: AdoRemote, pr_id: int, content: str
) -> int:
    return await asyncio.to_thread(
        ado_pr.add_comment,
        ado_client, ado_remote.project, ado_remote.repo, pr_id, content,
        organization_url=ado_remote.org_url,
    )


async def add_label(
    ado_client: AdoClient, ado_remote: AdoRemote, pr_id: int, label: str
) -> None:
    await asyncio.to_thread(
        ado_pr.add_label,
        ado_client, ado_remote.project, ado_remote.repo, pr_id, label,
        organization_url=ado_remote.org_url,
    )


async def remove_label(
    ado_client: AdoClient, ado_remote: AdoRemote, pr_id: int, label: str
) -> None:
    await asyncio.to_thread(
        ado_pr.remove_label,
        ado_client, ado_remote.project, ado_remote.repo, pr_id, label,
        organization_url=ado_remote.org_url,
    )


async def list_labels(
    ado_client: AdoClient, ado_remote: AdoRemote, pr_id: int
) -> list[str]:
    return await asyncio.to_thread(
        ado_pr.list_labels,
        ado_client, ado_remote.project, ado_remote.repo, pr_id,
        organization_url=ado_remote.org_url,
    )
