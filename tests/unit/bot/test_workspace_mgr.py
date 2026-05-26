"""Ephemeral clone lifecycle."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from stack_bot import workspace_mgr
from stack_bot.config import (
    AdoConfig,
    BotConfig,
    IdentityConfig,
    OperationsConfig,
    ProjectConfig,
    RedisConfig,
    ServerConfig,
    SmtpConfig,
    WebhooksConfig,
    WorkspaceConfig,
)
from stack_core.state_store import AdoRemote


@pytest.fixture
def bare_origin(tmp_path: Path) -> Path:
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(bare), str(seed)], check=True)
    for arg in (("user.email", "t@t"), ("user.name", "T"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(seed), "config", *arg], check=True)
    (seed / "README.md").write_text("hi\n")
    subprocess.run(["git", "-C", str(seed), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(seed), "commit", "-q", "-m", "init"], check=True)
    subprocess.run(["git", "-C", str(seed), "push", "-q", "origin", "main"], check=True)
    return bare


def _config(workspaces_dir: Path) -> BotConfig:
    return BotConfig(
        ado=AdoConfig(organization_url="file://" + str(workspaces_dir), pat="x"),
        projects=[ProjectConfig(name="proj")],
        redis=RedisConfig(),
        branch_suffix="-stacked-",
        workspaces=WorkspaceConfig(base_dir=str(workspaces_dir)),
        server=ServerConfig(),
        operations=OperationsConfig(),
        smtp=SmtpConfig(host="s", from_address="b@x", to_addresses=["a@x"]),
        webhooks=WebhooksConfig(bot_url="https://stackbot"),
        identity=IdentityConfig(name="bot", email="bot@example.com"),
    )


def test_ephemeral_clone_creates_and_cleans(tmp_path: Path, bare_origin: Path, monkeypatch):
    config = _config(tmp_path / "workspaces")
    remote = AdoRemote(org_url=str(bare_origin.parent), project="origin.git", repo="")

    # Bypass the URL builder so we clone the bare repo directly.
    monkeypatch.setattr(workspace_mgr, "_clone_url", lambda _r: f"file://{bare_origin}")

    captured: dict = {}

    async def run():
        async with workspace_mgr.ephemeral_clone(config, remote) as path:
            captured["path"] = path
            captured["existed_during_yield"] = path.exists()
            captured["readme_existed"] = (path / "README.md").exists()
            # Identity was configured.
            result = subprocess.run(
                ["git", "-C", str(path), "config", "user.name"],
                capture_output=True, text=True, check=True,
            )
            captured["user_name"] = result.stdout.strip()

    asyncio.run(run())
    assert captured["existed_during_yield"] is True
    assert captured["readme_existed"] is True
    assert captured["user_name"] == "bot"
    # Cleanup happened.
    assert not captured["path"].exists()


def test_ephemeral_clone_cleans_on_exception(tmp_path: Path, bare_origin: Path, monkeypatch):
    config = _config(tmp_path / "workspaces")
    remote = AdoRemote(org_url=str(bare_origin.parent), project="origin.git", repo="")
    monkeypatch.setattr(workspace_mgr, "_clone_url", lambda _r: f"file://{bare_origin}")

    captured: dict = {}

    async def run():
        try:
            async with workspace_mgr.ephemeral_clone(config, remote) as path:
                captured["path"] = path
                raise RuntimeError("boom")
        except RuntimeError:
            pass

    asyncio.run(run())
    assert not captured["path"].exists()
