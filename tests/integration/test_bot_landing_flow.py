"""End-to-end bot landing flow via FastAPI TestClient.

The flow:
  - prepare a real bare-repo origin + working repo with a 3-branch stack and
    a squash-merged bottom on main
  - seed the manifest in fakeredis
  - monkeypatch ``ephemeral_clone`` to yield the prepared working repo
  - respx-mock the ADO endpoints we hit (PR show, PR update, labels, threads)
  - POST the webhook payload to the FastAPI test client
  - drain the spawned background task
  - assert the manifest was updated and the new bottom PR was retargeted
"""

from __future__ import annotations

import json
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import fakeredis
import httpx
import pytest
import respx

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
from stack_bot.main import build_app
from stack_core import state_store
from stack_core.types import BranchEntry, Manifest, Verification
from tests.conftest import FIXED_TIME
from tests.conftest import sha as sha_helper

ORG_URL = "https://dev.azure.com/myorg"
PROJECT = "myproj"
REPO_NAME = "myrepo"


def _run(args, cwd):
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True)


def _sha(repo, rev):
    return subprocess.run(
        ["git", "rev-parse", "--verify", f"{rev}^{{commit}}"],
        cwd=str(repo), check=True, capture_output=True, text=True,
    ).stdout.strip()


def _tree(repo, rev):
    return subprocess.run(
        ["git", "rev-parse", "--verify", f"{rev}^{{tree}}"],
        cwd=str(repo), check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture(autouse=True)
def _reset_locks():
    stack_locks.reset_for_testing()
    yield
    stack_locks.reset_for_testing()


@pytest.fixture
def landing_repo(tmp_path: Path):
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)

    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-q", "-b", "main"], repo)
    _run(["git", "config", "user.email", "t@t"], repo)
    _run(["git", "config", "user.name", "T"], repo)
    _run(["git", "config", "commit.gpgsign", "false"], repo)
    _run(["git", "remote", "add", "origin", f"file://{bare}"], repo)

    (repo / "README.md").write_text("base\n")
    _run(["git", "add", "README.md"], repo)
    _run(["git", "commit", "-q", "-m", "base"], repo)
    base_sha = _sha(repo, "HEAD")
    _run(["git", "push", "-q", "origin", "main"], repo)

    _run(["git", "checkout", "-q", "-b", "feat-stacked-1"], repo)
    (repo / "f1.txt").write_text("one\n")
    _run(["git", "add", "f1.txt"], repo)
    _run(["git", "commit", "-q", "-m", "part 1"], repo)
    c1 = _sha(repo, "HEAD")
    t1 = _tree(repo, "HEAD")

    _run(["git", "checkout", "-q", "-b", "feat-stacked-2"], repo)
    (repo / "f2.txt").write_text("two\n")
    _run(["git", "add", "f2.txt"], repo)
    _run(["git", "commit", "-q", "-m", "part 2"], repo)
    c2 = _sha(repo, "HEAD")
    t2 = _tree(repo, "HEAD")

    _run(["git", "checkout", "-q", "-b", "feat-stacked-3"], repo)
    (repo / "f3.txt").write_text("three\n")
    _run(["git", "add", "f3.txt"], repo)
    _run(["git", "commit", "-q", "-m", "part 3"], repo)
    c3 = _sha(repo, "HEAD")
    t3 = _tree(repo, "HEAD")

    for branch in ("feat-stacked-1", "feat-stacked-2", "feat-stacked-3"):
        _run(["git", "push", "-q", "origin", branch], repo)

    # Squash-merge feat-stacked-1 into main.
    _run(["git", "checkout", "-q", "main"], repo)
    _run(["git", "merge", "-q", "--squash", "feat-stacked-1"], repo)
    _run(["git", "commit", "-q", "-m", "land: part 1"], repo)
    _run(["git", "push", "-q", "origin", "main"], repo)

    _run(["git", "branchless", "init"], repo)
    return {
        "repo": repo, "bare": bare, "base_sha": base_sha,
        "c1": c1, "c2": c2, "c3": c3, "t1": t1, "t2": t2, "t3": t3,
    }


def _manifest(setup) -> Manifest:
    return Manifest(
        prefix="feat", code_repo="myproj/myrepo", base_ref="main",
        branch_suffix="-stacked-", source_branch="feat",
        source_branch_tip=setup["c3"],
        created_at=FIXED_TIME, last_update=FIXED_TIME,
        branches=[
            BranchEntry(
                order=1, name="feat-stacked-1",
                commit_sha=setup["c1"], parent_sha=setup["base_sha"],
                tree_hash=setup["t1"], subject="part 1", pr_id=101,
            ),
            BranchEntry(
                order=2, name="feat-stacked-2",
                commit_sha=setup["c2"], parent_sha=setup["c1"],
                tree_hash=setup["t2"], subject="part 2", pr_id=102,
            ),
            BranchEntry(
                order=3, name="feat-stacked-3",
                commit_sha=setup["c3"], parent_sha=setup["c2"],
                tree_hash=setup["t3"], subject="part 3", pr_id=103,
            ),
        ],
        verification=Verification(
            passed=True, method="tree-hash-equality",
            original_tree=sha_helper(200), stack_tip_tree=setup["t3"],
            last_verified_at=FIXED_TIME,
        ),
    )


def _config(tmp_path) -> BotConfig:
    return BotConfig(
        ado=AdoConfig(organization_url=ORG_URL, pat="pat"),
        projects=[ProjectConfig(name=PROJECT)],
        redis=RedisConfig(),
        branch_suffix="-stacked-",
        workspaces=WorkspaceConfig(base_dir=str(tmp_path)),
        server=ServerConfig(),
        operations=OperationsConfig(),
        smtp=SmtpConfig(host="s", from_address="b@x", to_addresses=["a@x"]),
        webhooks=WebhooksConfig(bot_url="https://stackbot"),
        identity=IdentityConfig(),
    )


def _wire_ado_mocks() -> dict[str, Any]:
    base = f"{ORG_URL}/{PROJECT}/_apis/git/repositories/{REPO_NAME}/pullrequests"
    captured: dict[str, Any] = {"updates": []}

    def show_handler(request):
        pr_id = int(request.url.path.rsplit("/", 1)[-1])
        status = "completed" if pr_id == 101 else "active"
        return httpx.Response(
            200,
            json={
                "pullRequestId": pr_id,
                "status": status,
                "sourceRefName": f"refs/heads/feat-stacked-{pr_id - 100}",
                "targetRefName": "refs/heads/main",
                "title": "T", "description": "D",
            },
        )

    def update_handler(request):
        pr_id = int(request.url.path.rsplit("/", 1)[-1])
        body = json.loads(request.content) if request.content else {}
        captured["updates"].append({"pr_id": pr_id, "body": body})
        return httpx.Response(
            200,
            json={
                "pullRequestId": pr_id,
                "status": body.get("status", "active"),
                "sourceRefName": body.get("sourceRefName", "refs/heads/x"),
                "targetRefName": body.get("targetRefName", "refs/heads/main"),
                "title": body.get("title", "T"), "description": body.get("description", "D"),
            },
        )

    respx.get(url__regex=rf"{base}/\d+").mock(side_effect=show_handler)
    respx.patch(url__regex=rf"{base}/\d+").mock(side_effect=update_handler)
    return captured


@respx.mock
def test_full_landing_flow(landing_repo, tmp_path):
    from fastapi.testclient import TestClient

    setup = landing_repo
    captured = _wire_ado_mocks()
    fr = fakeredis.FakeRedis(decode_responses=False)
    state_store.update_manifest(
        fr, PROJECT, "feat", lambda _: _manifest(setup), "create", "test",
    )

    config = _config(tmp_path)

    # Make the bot's workspace context manager yield the prepared local repo
    # rather than cloning. We rely on the fact that the local repo already has
    # an origin pointing at the bare repo, so pushes during land will work.
    @asynccontextmanager
    async def fake_clone(_config, _ado_remote):
        yield setup["repo"]

    with patch("stack_bot.handlers.land.ephemeral_clone", new=fake_clone):
        app = build_app(config)
        # Inject our fakeredis client in place of the live one.
        app.state.redis_client.close()
        app.state.redis_client = fr

        payload = {
            "eventType": "git.pullrequest.merged",
            "notificationId": 12345,
            "resource": {
                "pullRequestId": 101,
                "sourceRefName": "refs/heads/feat-stacked-1",
                "targetRefName": "refs/heads/main",
                "status": "completed",
                "repository": {"name": REPO_NAME, "project": {"name": PROJECT}},
            },
        }

        with TestClient(app) as client:
            response = client.post("/webhooks/ado", json=payload)
            assert response.status_code == 200

            async def _drain():
                for task in list(app.state.tasks):
                    await task

            client.portal.call(_drain)

    # Manifest should have lost the bottom and kept rebased entries for 2 + 3.
    after = state_store.get_manifest(fr, PROJECT, "feat")
    assert after is not None
    assert [b.order for b in after.branches] == [2, 3]
    assert [b.name for b in after.branches] == ["feat-stacked-2", "feat-stacked-3"]
    # New bottom's parent is the new main tip (not c1 anymore).
    new_main = _sha(setup["repo"], "main")
    assert after.branches[0].parent_sha == new_main

    # The new bottom (PR 102) was retargeted to main.
    retargets = [u for u in captured["updates"] if u["pr_id"] == 102 and "targetRefName" in u["body"]]
    assert retargets, "expected PR 102 to be retargeted"
