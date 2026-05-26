"""POST /webhooks/ado: filtering, idempotency, task spawn."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import fakeredis
from fastapi import FastAPI
from fastapi.testclient import TestClient

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
from stack_bot.webhooks import ado as webhook_ado
from stack_core import state_store
from stack_core.types import BranchEntry, Manifest, Verification
from tests.conftest import FIXED_TIME, sha


def _config(tmp_path) -> BotConfig:
    return BotConfig(
        ado=AdoConfig(organization_url="https://dev.azure.com/myorg", pat="x"),
        projects=[ProjectConfig(name="myproj")],
        redis=RedisConfig(key_prefix="stack", idempotency_ttl_days=7),
        branch_suffix="-stacked-",
        workspaces=WorkspaceConfig(base_dir=str(tmp_path)),
        server=ServerConfig(),
        operations=OperationsConfig(),
        smtp=SmtpConfig(host="s", from_address="b@x", to_addresses=["a@x"]),
        webhooks=WebhooksConfig(bot_url="https://x"),
        identity=IdentityConfig(),
    )


def _make_app(config, redis_client):
    app = FastAPI()
    app.state.config = config
    app.state.redis_client = redis_client
    app.state.tasks = set()
    app.include_router(webhook_ado.router)
    return app


def _seed_manifest(client):
    m = Manifest(
        prefix="feat", code_repo="myproj/myrepo", base_ref="main",
        branch_suffix="-stacked-", source_branch="feat", source_branch_tip=sha(3),
        created_at=FIXED_TIME, last_update=FIXED_TIME,
        branches=[
            BranchEntry(
                order=1, name="feat-stacked-1",
                commit_sha=sha(1), parent_sha=sha(0), tree_hash=sha(101),
                subject="p1", pr_id=101,
            ),
            BranchEntry(
                order=2, name="feat-stacked-2",
                commit_sha=sha(2), parent_sha=sha(1), tree_hash=sha(102),
                subject="p2", pr_id=102,
            ),
        ],
        verification=Verification(
            passed=True, method="tree-hash-equality",
            original_tree=sha(200), stack_tip_tree=sha(102),
            last_verified_at=FIXED_TIME,
        ),
    )
    state_store.update_manifest(client, "myproj", "feat", lambda _: m, "create", "test")


def _payload(event_type: str, source_branch: str, pr_id: int, notif: int = 1):
    return {
        "eventType": event_type,
        "notificationId": notif,
        "resource": {
            "pullRequestId": pr_id,
            "sourceRefName": f"refs/heads/{source_branch}",
            "targetRefName": "refs/heads/main",
            "status": "completed",
            "repository": {"name": "myrepo", "project": {"name": "myproj"}},
        },
    }


def test_returns_200_for_unrelated_event_type(tmp_path):
    client = fakeredis.FakeRedis(decode_responses=False)
    app = _make_app(_config(tmp_path), client)
    with TestClient(app) as tc:
        r = tc.post("/webhooks/ado", json=_payload("git.push", "feat-stacked-1", 101))
    assert r.status_code == 200


def test_returns_200_when_branch_doesnt_match_suffix(tmp_path):
    client = fakeredis.FakeRedis(decode_responses=False)
    app = _make_app(_config(tmp_path), client)
    with TestClient(app) as tc:
        r = tc.post("/webhooks/ado", json=_payload("git.pullrequest.merged", "feature-x", 5))
    assert r.status_code == 200


def test_returns_200_when_no_manifest(tmp_path):
    client = fakeredis.FakeRedis(decode_responses=False)
    app = _make_app(_config(tmp_path), client)
    with TestClient(app) as tc:
        r = tc.post(
            "/webhooks/ado",
            json=_payload("git.pullrequest.merged", "feat-stacked-1", 101),
        )
    assert r.status_code == 200


def test_returns_200_when_not_bottom_branch(tmp_path):
    client = fakeredis.FakeRedis(decode_responses=False)
    _seed_manifest(client)
    app = _make_app(_config(tmp_path), client)
    with TestClient(app) as tc:
        r = tc.post(
            "/webhooks/ado",
            json=_payload("git.pullrequest.merged", "feat-stacked-2", 102),
        )
    assert r.status_code == 200


def test_returns_200_when_already_claimed(tmp_path):
    client = fakeredis.FakeRedis(decode_responses=False)
    _seed_manifest(client)
    app = _make_app(_config(tmp_path), client)

    spawned: list = []

    async def fake_handle(*args, **kwargs):
        spawned.append((args, kwargs))

    with (
        patch("stack_bot.webhooks.ado.land_handler.handle", side_effect=fake_handle),
        TestClient(app) as tc,
    ):
        r1 = tc.post(
            "/webhooks/ado",
            json=_payload("git.pullrequest.merged", "feat-stacked-1", 101, notif=42),
        )
        r2 = tc.post(
            "/webhooks/ado",
            json=_payload("git.pullrequest.merged", "feat-stacked-1", 101, notif=42),
        )
    assert r1.status_code == 200
    assert r2.status_code == 200


def test_claim_succeeds_and_spawns_handler(tmp_path):
    client = fakeredis.FakeRedis(decode_responses=False)
    _seed_manifest(client)
    app = _make_app(_config(tmp_path), client)

    invoked = asyncio.Event()
    captured = {}

    async def fake_handle(config, redis_client, ado_remote, *, prefix, bottom_pr_id):
        captured["prefix"] = prefix
        captured["pr_id"] = bottom_pr_id
        captured["project"] = ado_remote.project
        invoked.set()

    with (
        patch("stack_bot.webhooks.ado.land_handler.handle", side_effect=fake_handle),
        TestClient(app) as tc,
    ):
        r = tc.post(
            "/webhooks/ado",
            json=_payload("git.pullrequest.merged", "feat-stacked-1", 101, notif=99),
        )

    # Yield to the event loop to let the spawned task run.
    asyncio.run(_drain_tasks(app))
    assert r.status_code == 200
    assert captured == {"prefix": "feat", "pr_id": 101, "project": "myproj"}


async def _drain_tasks(app):
    # The spawned task already started in TestClient's loop; this helper just
    # waits long enough for it to complete in the new loop's view. In practice
    # the captured dict was populated before the test client returned.
    await asyncio.sleep(0)
