"""stack_core.operations.land: real git, fakeredis, respx-mocked ADO."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import fakeredis
import httpx
import pytest
import respx

from stack_core import state_store
from stack_core.ado.client import AdoClient
from stack_core.exceptions import LandConflict
from stack_core.operations import land as land_op
from stack_core.types import BranchEntry, Manifest, Verification
from tests.conftest import FIXED_TIME
from tests.conftest import sha as sha_helper

ORG_URL = "https://dev.azure.com/myorg"
PROJECT = "myproj"
REPO_NAME = "myrepo"
PAT = "pat"


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


@pytest.fixture
def landing_repo(tmp_path: Path):
    """A repo + bare origin with a 3-branch stack where the bottom has been merged.

    Setup:
      - main contains the base commit AND the bottom's changes (simulating a
        merged-and-squashed bottom).
      - feat-stacked-1 still points at the unmerged-but-now-superseded bottom.
      - feat-stacked-2 and feat-stacked-3 stack on top of feat-stacked-1.
    """
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

    # Build the 3-branch feature stack first (before main moves forward).
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

    # Push the stack branches to origin so subsequent --force-with-lease pushes work.
    for branch in ("feat-stacked-1", "feat-stacked-2", "feat-stacked-3"):
        _run(["git", "push", "-q", "origin", branch], repo)

    # Now simulate the bottom merging: advance origin/main to a commit whose
    # tree equals feat-stacked-1's. We do this by squash-merging feat-stacked-1
    # into main locally and pushing.
    _run(["git", "checkout", "-q", "main"], repo)
    _run(["git", "merge", "-q", "--squash", "feat-stacked-1"], repo)
    _run(["git", "commit", "-q", "-m", "land: part 1"], repo)
    main_after_land = _sha(repo, "HEAD")
    _run(["git", "push", "-q", "origin", "main"], repo)

    # git-branchless init so branchless_move works.
    _run(["git", "branchless", "init"], repo)

    return {
        "repo": repo,
        "bare": bare,
        "base_sha": base_sha,
        "c1": c1, "c2": c2, "c3": c3,
        "t1": t1, "t2": t2, "t3": t3,
        "main_after_land": main_after_land,
    }


def _manifest_for(setup, *, pr_ids=(101, 102, 103)) -> Manifest:
    branches = [
        BranchEntry(
            order=1, name="feat-stacked-1",
            commit_sha=setup["c1"], parent_sha=setup["base_sha"],
            tree_hash=setup["t1"], subject="part 1",
            pr_id=pr_ids[0], pr_url=f"https://example/pr/{pr_ids[0]}",
        ),
        BranchEntry(
            order=2, name="feat-stacked-2",
            commit_sha=setup["c2"], parent_sha=setup["c1"],
            tree_hash=setup["t2"], subject="part 2",
            pr_id=pr_ids[1], pr_url=f"https://example/pr/{pr_ids[1]}",
        ),
        BranchEntry(
            order=3, name="feat-stacked-3",
            commit_sha=setup["c3"], parent_sha=setup["c2"],
            tree_hash=setup["t3"], subject="part 3",
            pr_id=pr_ids[2], pr_url=f"https://example/pr/{pr_ids[2]}",
        ),
    ]
    return Manifest(
        prefix="feat", code_repo="myproj/myrepo", base_ref="main",
        branch_suffix="-stacked-", source_branch="feat",
        source_branch_tip=setup["c3"],
        created_at=FIXED_TIME, last_update=FIXED_TIME,
        branches=branches,
        verification=Verification(
            passed=True, method="tree-hash-equality",
            original_tree=sha_helper(200), stack_tip_tree=setup["t3"],
            last_verified_at=FIXED_TIME,
        ),
    )


def _ado_remote() -> state_store.AdoRemote:
    return state_store.parse_ado_remote(
        f"{ORG_URL}/{PROJECT}/_git/{REPO_NAME}"
    )


def _wire_ado_mocks(*, bottom_status: str = "completed", pr_ids=(101, 102, 103)) -> dict[str, Any]:
    base = f"{ORG_URL}/{PROJECT}/_apis/git/repositories/{REPO_NAME}/pullrequests"
    captured: dict[str, Any] = {"updates": []}

    def show_handler(request):
        pr_id = int(request.url.path.rsplit("/", 1)[-1])
        # Bottom (first id) status configurable; others active.
        status = bottom_status if pr_id == pr_ids[0] else "active"
        return httpx.Response(
            200,
            json={
                "pullRequestId": pr_id,
                "status": status,
                "sourceRefName": f"refs/heads/feat-stacked-{pr_ids.index(pr_id) + 1}",
                "targetRefName": "refs/heads/main",
                "title": "T",
                "description": "D",
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
                "title": body.get("title", "T"),
                "description": body.get("description", "D"),
            },
        )

    respx.get(url__regex=rf"{base}/\d+").mock(side_effect=show_handler)
    respx.patch(url__regex=rf"{base}/\d+").mock(side_effect=update_handler)
    return captured


@respx.mock
def test_no_op_when_manifest_missing(landing_repo):
    repo = landing_repo["repo"]
    client = fakeredis.FakeRedis(decode_responses=False)
    with AdoClient(ORG_URL, PAT) as ado_client:
        result = land_op.land(client, repo, ado_client, _ado_remote(), prefix="feat")
    assert result.action == "no_op"
    assert result.reason == "no_manifest"


@respx.mock
def test_no_op_when_bottom_not_merged(landing_repo):
    repo = landing_repo["repo"]
    _wire_ado_mocks(bottom_status="active")
    client = fakeredis.FakeRedis(decode_responses=False)
    state_store.update_manifest(
        client, "myproj", "feat", lambda _: _manifest_for(landing_repo),
        "create", "test",
    )
    with AdoClient(ORG_URL, PAT) as ado_client:
        result = land_op.land(client, repo, ado_client, _ado_remote(), prefix="feat")
    assert result.action == "no_op"
    assert result.reason == "bottom_not_merged"


@respx.mock
def test_single_branch_stack_deletes_manifest(landing_repo):
    repo = landing_repo["repo"]
    setup = landing_repo
    _wire_ado_mocks(bottom_status="completed")
    client = fakeredis.FakeRedis(decode_responses=False)

    single = Manifest(
        prefix="feat", code_repo="myproj/myrepo", base_ref="main",
        branch_suffix="-stacked-", source_branch="feat",
        source_branch_tip=setup["c1"],
        created_at=FIXED_TIME, last_update=FIXED_TIME,
        branches=[
            BranchEntry(
                order=1, name="feat-stacked-1",
                commit_sha=setup["c1"], parent_sha=setup["base_sha"],
                tree_hash=setup["t1"], subject="only", pr_id=101,
            ),
        ],
        verification=Verification(
            passed=True, method="tree-hash-equality",
            original_tree=sha_helper(200), stack_tip_tree=setup["t1"],
            last_verified_at=FIXED_TIME,
        ),
    )
    state_store.update_manifest(
        client, "myproj", "feat", lambda _: single, "create", "test",
    )

    with AdoClient(ORG_URL, PAT) as ado_client:
        result = land_op.land(client, repo, ado_client, _ado_remote(), prefix="feat")

    assert result.action == "manifest_deleted"
    assert state_store.get_manifest(client, "myproj", "feat") is None


@respx.mock
def test_landed_drops_bottom_rebases_remaining(landing_repo):
    repo = landing_repo["repo"]
    setup = landing_repo
    captured = _wire_ado_mocks(bottom_status="completed")
    client = fakeredis.FakeRedis(decode_responses=False)
    state_store.update_manifest(
        client, "myproj", "feat", lambda _: _manifest_for(setup), "create", "test",
    )

    with AdoClient(ORG_URL, PAT) as ado_client:
        result = land_op.land(client, repo, ado_client, _ado_remote(), prefix="feat")

    assert result.action == "landed"
    after = result.manifest_after
    assert after is not None
    assert len(after.branches) == 2
    assert [b.order for b in after.branches] == [2, 3]
    # Parent of new bottom is the new main tip.
    assert after.branches[0].parent_sha == setup["main_after_land"]
    # Chain is consistent.
    assert after.branches[1].parent_sha == after.branches[0].commit_sha

    # The new bottom's PR (102) was retargeted to main, and the same PR's
    # description was refreshed afterwards (so we expect at least two updates
    # against pr_id 102).
    retargets = [u for u in captured["updates"] if u["pr_id"] == 102]
    assert any("targetRefName" in u["body"] for u in retargets)


@respx.mock
def test_landed_skipped_when_branchless_uninitialized(landing_repo, monkeypatch):
    """Conflict during branchless move raises LandConflict.

    To force a conflict reliably, we monkeypatch git_ops.branchless_move to
    raise GitError mid-operation, simulating an irrecoverable rebase failure.
    """
    repo = landing_repo["repo"]
    setup = landing_repo
    _wire_ado_mocks(bottom_status="completed")
    client = fakeredis.FakeRedis(decode_responses=False)
    state_store.update_manifest(
        client, "myproj", "feat", lambda _: _manifest_for(setup), "create", "test",
    )

    from stack_core.exceptions import GitError as _GitError

    def boom(*args, **kwargs):
        raise _GitError(["git", "branchless", "move"], "conflict in f2.txt\nfeat-stacked-2", 1)

    monkeypatch.setattr("stack_core.operations.land.git_ops.branchless_move", boom)
    monkeypatch.setattr(
        "stack_core.operations.land.git_ops.conflicting_paths",
        lambda _cwd: ["f2.txt"],
    )
    monkeypatch.setattr(
        "stack_core.operations.land.git_ops.branchless_abort", lambda _cwd: None,
    )

    with AdoClient(ORG_URL, PAT) as ado_client, pytest.raises(LandConflict) as excinfo:
        land_op.land(client, repo, ado_client, _ado_remote(), prefix="feat")
    assert "f2.txt" in excinfo.value.conflicting_paths

    # Manifest is unchanged: snapshot was taken but not restored (conflict
    # leaves the snapshot in place; the caller can roll back if they choose).
    after = state_store.get_manifest(client, "myproj", "feat")
    assert after is not None
    assert len(after.branches) == 3
