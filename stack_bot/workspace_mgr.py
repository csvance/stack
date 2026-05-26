"""Ephemeral workspace lifecycle: one clone per background task.

Each invocation creates a fresh directory under ``config.workspaces.base_dir``,
clones the code repo using the bot's PAT via an ``http.extraHeader`` injection,
configures ``user.name`` / ``user.email`` from ``config.identity``, yields the
path, and cleans up unconditionally.
"""

from __future__ import annotations

import asyncio
import base64
import shutil
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from stack_bot.config import BotConfig
from stack_core import git_ops
from stack_core.state_store import AdoRemote


def _basic_auth_header(pat: str) -> str:
    token = base64.b64encode(f":{pat}".encode()).decode("ascii")
    return f"Basic {token}"


def _clone_url(ado_remote: AdoRemote) -> str:
    """Render the HTTPS clone URL for the configured ADO remote."""
    base = ado_remote.org_url.rstrip("/")
    return f"{base}/{ado_remote.project}/_git/{ado_remote.repo}"


@asynccontextmanager
async def ephemeral_clone(
    config: BotConfig, ado_remote: AdoRemote
) -> AsyncIterator[Path]:
    base = Path(config.workspaces.base_dir)
    base.mkdir(parents=True, exist_ok=True)
    workspace = base / f"land-{uuid.uuid4().hex}"

    url = _clone_url(ado_remote)
    headers = {"Authorization": _basic_auth_header(config.ado.pat)}

    await asyncio.to_thread(git_ops.clone, url, workspace, extra_headers=headers)
    await asyncio.to_thread(
        git_ops._git, ["config", "user.name", config.identity.name], workspace
    )
    await asyncio.to_thread(
        git_ops._git, ["config", "user.email", config.identity.email], workspace
    )
    # Persist the auth header so subsequent fetch/push from the workspace work.
    await asyncio.to_thread(
        git_ops._git,
        ["config", "http.extraHeader", f"Authorization: {headers['Authorization']}"],
        workspace,
    )

    try:
        yield workspace
    finally:
        await asyncio.to_thread(shutil.rmtree, workspace, ignore_errors=True)
