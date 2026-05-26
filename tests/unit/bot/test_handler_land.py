"""handlers.land: mocked operations.land covering each result/exception branch."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import fakeredis
import pytest

from stack_bot import stack_locks
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
from stack_bot.handlers import land as land_handler
from stack_core.exceptions import LandConflict, VerifyTreeMismatch
from stack_core.operations.land import LandResult
from stack_core.state_store import AdoRemote


@pytest.fixture(autouse=True)
def _reset_locks():
    stack_locks.reset_for_testing()
    yield
    stack_locks.reset_for_testing()


def _config(tmp_path: Path) -> BotConfig:
    return BotConfig(
        ado=AdoConfig(organization_url="https://dev.azure.com/myorg", pat="x"),
        projects=[ProjectConfig(name="myproj")],
        redis=RedisConfig(),
        branch_suffix="-stacked-",
        workspaces=WorkspaceConfig(base_dir=str(tmp_path)),
        server=ServerConfig(),
        operations=OperationsConfig(),
        smtp=SmtpConfig(host="s", from_address="b@x", to_addresses=["a@x"]),
        webhooks=WebhooksConfig(bot_url="https://x"),
        identity=IdentityConfig(),
    )


def _ado_remote() -> AdoRemote:
    return AdoRemote(org_url="https://dev.azure.com/myorg", project="myproj", repo="myrepo")


def _patch_workspace():
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_clone(_config, _remote):
        yield Path("/tmp/fake-workspace")

    return patch("stack_bot.handlers.land.ephemeral_clone", new=fake_clone)


def test_landed_result_logs(tmp_path, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="stack_bot.handlers.land")
    config = _config(tmp_path)
    redis_client = fakeredis.FakeRedis(decode_responses=False)

    def fake_land(*args, **kwargs):
        return LandResult(action="landed", reason=None, manifest_after=None)

    with _patch_workspace(), \
         patch("stack_bot.handlers.land.land_op.land", side_effect=fake_land):
        asyncio.run(
            land_handler.handle(
                config, redis_client, _ado_remote(),
                prefix="feat", bottom_pr_id=101,
            )
        )
    assert any("action=landed" in rec.message for rec in caplog.records)


def test_conflict_posts_comment_and_label(tmp_path):
    config = _config(tmp_path)
    redis_client = fakeredis.FakeRedis(decode_responses=False)

    def fake_land(*args, **kwargs):
        raise LandConflict(["f2.txt", "f3.txt"])

    posted: dict = {}

    async def fake_list_labels(*args, **kwargs):
        return []

    async def fake_add_comment(_client, _remote, pr_id, content):
        posted["pr_id"] = pr_id
        posted["content"] = content
        return 1

    async def fake_add_label(_client, _remote, pr_id, label):
        posted["label"] = label

    with _patch_workspace(), \
         patch("stack_bot.handlers.land.land_op.land", side_effect=fake_land), \
         patch("stack_bot.handlers.land.notifications.list_labels", side_effect=fake_list_labels), \
         patch("stack_bot.handlers.land.notifications.add_comment", side_effect=fake_add_comment), \
         patch("stack_bot.handlers.land.notifications.add_label", side_effect=fake_add_label):
        asyncio.run(
            land_handler.handle(
                config, redis_client, _ado_remote(),
                prefix="feat", bottom_pr_id=101,
            )
        )
    assert posted["pr_id"] == 101
    assert "f2.txt" in posted["content"]
    assert posted["label"] == "stack-conflict"


def test_conflict_suppressed_when_label_already_present(tmp_path):
    config = _config(tmp_path)
    redis_client = fakeredis.FakeRedis(decode_responses=False)

    def fake_land(*args, **kwargs):
        raise LandConflict(["f.txt"])

    calls: dict[str, int] = {"comment": 0, "label": 0}

    async def fake_list_labels(*args, **kwargs):
        return ["stack-conflict"]

    async def fake_add_comment(*args, **kwargs):
        calls["comment"] += 1
        return 1

    async def fake_add_label(*args, **kwargs):
        calls["label"] += 1

    with _patch_workspace(), \
         patch("stack_bot.handlers.land.land_op.land", side_effect=fake_land), \
         patch("stack_bot.handlers.land.notifications.list_labels", side_effect=fake_list_labels), \
         patch("stack_bot.handlers.land.notifications.add_comment", side_effect=fake_add_comment), \
         patch("stack_bot.handlers.land.notifications.add_label", side_effect=fake_add_label):
        asyncio.run(
            land_handler.handle(
                config, redis_client, _ado_remote(),
                prefix="feat", bottom_pr_id=101,
            )
        )
    assert calls == {"comment": 0, "label": 0}


def test_tree_mismatch_posts_handoff(tmp_path):
    config = _config(tmp_path)
    redis_client = fakeredis.FakeRedis(decode_responses=False)

    def fake_land(*args, **kwargs):
        raise VerifyTreeMismatch("aaa" * 14 + "aaaaaaaa", "bbb" * 14 + "bbbbbbbb")

    posted: dict = {}

    async def fake_list_labels(*args, **kwargs):
        return []

    async def fake_add_comment(_client, _remote, pr_id, content):
        posted["content"] = content
        return 1

    async def fake_add_label(_client, _remote, pr_id, label):
        posted["label"] = label

    with _patch_workspace(), \
         patch("stack_bot.handlers.land.land_op.land", side_effect=fake_land), \
         patch("stack_bot.handlers.land.notifications.list_labels", side_effect=fake_list_labels), \
         patch("stack_bot.handlers.land.notifications.add_comment", side_effect=fake_add_comment), \
         patch("stack_bot.handlers.land.notifications.add_label", side_effect=fake_add_label):
        asyncio.run(
            land_handler.handle(
                config, redis_client, _ado_remote(),
                prefix="feat", bottom_pr_id=101,
            )
        )
    assert "didn't match the expected tree" in posted["content"]
    assert posted["label"] == "stack-conflict"


def test_unhandled_exception_triggers_smtp_alert(tmp_path):
    config = _config(tmp_path)
    redis_client = fakeredis.FakeRedis(decode_responses=False)

    def fake_land(*args, **kwargs):
        raise RuntimeError("boom")

    sent: dict = {}

    def fake_send(category, subject, body, smtp):
        sent["category"] = category
        sent["subject"] = subject

    with _patch_workspace(), \
         patch("stack_bot.handlers.land.land_op.land", side_effect=fake_land), \
         patch("stack_bot.handlers.land.alerts.send", side_effect=fake_send):
        asyncio.run(
            land_handler.handle(
                config, redis_client, _ado_remote(),
                prefix="feat", bottom_pr_id=101,
            )
        )
    assert "UNHANDLED_EXCEPTION" in sent["category"]
    assert "myproj/feat" in sent["subject"]
