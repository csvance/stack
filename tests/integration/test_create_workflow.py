"""End-to-end create workflow: prepare → decompose → manifest → publish.

The ``claude`` subprocess is stubbed with a Python script that writes a
deterministic sentinel + branches. The ADO API is mocked with respx. The Redis
backend is fakeredis. The remote push is a no-op via a bare-repo origin so
``git push`` is real but harmless.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import fakeredis
import httpx
import pytest
import respx

from stack_core import state_store
from stack_core.ado.client import AdoClient
from stack_core.operations import create as create_op

ORG_URL = "https://dev.azure.com/myorg"


def _run(args, cwd):
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True)


def _sha(repo, rev):
    return subprocess.run(
        ["git", "rev-parse", "--verify", f"{rev}^{{commit}}"],
        cwd=str(repo), check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def feature_repo_with_origin(tmp_path: Path):
    """A feature repo wired to a bare-repo origin so `git push` works."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)

    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-q", "-b", "main"], repo)
    _run(["git", "config", "user.email", "t@t"], repo)
    _run(["git", "config", "user.name", "T"], repo)
    _run(["git", "config", "commit.gpgsign", "false"], repo)
    # Point origin to the bare repo for fetch/push, but rewrite remote URL to
    # the ADO URL for code under test that parses it. We set two values:
    #   - the real fetch/push URL is the bare repo (file://)
    #   - origin's URL string seen by `git remote get-url` is the ADO URL
    # Achieve this by setting two remotes: origin (ADO-shaped) and bare-origin
    # (the file path). Tests that push call git_ops.push_branch(remote="bare").
    _run(["git", "remote", "add", "origin", "https://dev.azure.com/myorg/myproj/_git/myrepo"], repo)
    _run(["git", "remote", "add", "bare", f"file://{bare}"], repo)

    (repo / "README.md").write_text("base\n")
    _run(["git", "add", "README.md"], repo)
    _run(["git", "commit", "-q", "-m", "base"], repo)
    # Push base to bare so subsequent --force-with-lease pushes work.
    _run(["git", "push", "-q", "bare", "main"], repo)

    _run(["git", "checkout", "-q", "-b", "feat"], repo)
    for i in range(1, 3):
        (repo / f"f{i}.txt").write_text(str(i))
        _run(["git", "add", f"f{i}.txt"], repo)
        _run(["git", "commit", "-q", "-m", f"feature part {i}"], repo)

    return repo


@pytest.fixture
def claude_stub_script(tmp_path: Path) -> Path:
    stub = tmp_path / "claude-stub.py"
    stub.write_text(
        '''#!/usr/bin/env python3
import json, os, re, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

cwd = Path(os.getcwd())
local = (cwd / "CLAUDE.md.local").read_text()
prefix = re.search(r"Prefix.*?: `([^`]+)`", local).group(1)
suffix = re.search(r"Branch suffix.*?: `([^`]+)`", local).group(1)
input_branch = re.search(r"Input branch.*?: `([^`]+)`", local).group(1)
base_ref = re.search(r"Base ref.*?: `([^`]+)`", local).group(1)

def sh(*args):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

base_sha = sh("git", "rev-parse", "--verify", base_ref + "^{commit}")
input_sha = sh("git", "rev-parse", "--verify", input_branch + "^{commit}")
files = sh("git", "diff", "--name-only", base_sha, input_sha).splitlines()

branches = []
for i, f in enumerate(files, start=1):
    name = f"{prefix}{suffix}{i}"
    parent = base_ref if i == 1 else branches[-1]
    sh("git", "checkout", "-q", parent)
    sh("git", "checkout", "-q", "-b", name)
    sh("git", "checkout", input_branch, "--", f)
    sh("git", "add", f)
    sh("git", "commit", "-q", "-m", f"part {i}: {f}")
    branches.append(name)

sentinel_dir = cwd / ".git" / "stack"
sentinel_dir.mkdir(parents=True, exist_ok=True)
(sentinel_dir / f"decompose-sentinel-{prefix}.json").write_text(json.dumps({
    "prefix": prefix,
    "branches": branches,
    "base_ref": base_ref,
    "source_branch": input_branch,
    "source_branch_tip": input_sha,
    "branch_suffix": suffix,
    "completion_timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
}))
'''
    )
    stub.chmod(0o755)
    return stub


@respx.mock
def test_full_create_workflow(feature_repo_with_origin, claude_stub_script):
    repo = feature_repo_with_origin
    redis_client = fakeredis.FakeRedis(decode_responses=False)
    ado_client = AdoClient(ORG_URL, "fake-pat", retry_initial_delay=0.0)
    ado_remote = state_store.parse_ado_remote(
        "https://dev.azure.com/myorg/myproj/_git/myrepo"
    )

    next_pr_id = {"v": 100}

    def list_handler(request):
        return httpx.Response(200, json={"value": []})

    def create_handler(request):
        import json as _json
        body = _json.loads(request.content)
        next_pr_id["v"] += 1
        return httpx.Response(
            201,
            json={
                "pullRequestId": next_pr_id["v"],
                "status": "active",
                "sourceRefName": body["sourceRefName"],
                "targetRefName": body["targetRefName"],
                "title": body.get("title", ""),
                "description": body.get("description", ""),
            },
        )

    def update_handler(request):
        import json as _json
        pr_id = int(request.url.path.rsplit("/", 1)[-1])
        body = _json.loads(request.content) if request.content else {}
        return httpx.Response(
            200,
            json={
                "pullRequestId": pr_id,
                "status": body.get("status", "active"),
                "sourceRefName": body.get("sourceRefName", "refs/heads/x"),
                "targetRefName": body.get("targetRefName", "refs/heads/main"),
                "title": body.get("title", ""),
                "description": body.get("description", ""),
            },
        )

    base_url = f"{ORG_URL}/myproj/_apis/git/repositories/myrepo/pullrequests"
    respx.get(base_url).mock(side_effect=list_handler)
    respx.post(base_url).mock(side_effect=create_handler)
    respx.patch(httpx.URL(base_url)).mock(side_effect=update_handler)
    respx.patch(url__regex=rf"{base_url}/\d+").mock(side_effect=update_handler)

    # Phase 1: prepare
    prepare_result = create_op.prepare(
        repo, prefix="feat", base_ref="main", branch_suffix="-stacked-",
        feature_branch="feat", claude_bin="/usr/bin/true",
    )

    # Phase 2: decompose (via the claude stub)
    decompose_result = create_op.decompose(
        repo, prepare_result=prepare_result, prefix="feat",
        claude_bin=str(claude_stub_script),
        subprocess_runner=lambda args, cwd: subprocess.run(
            ["python3", str(claude_stub_script)], cwd=str(cwd), check=False
        ).returncode,
    )
    assert len(decompose_result.sentinel.branches) == 2

    # Phase 3: manifest
    manifest_obj = create_op.manifest(
        redis_client, repo,
        sentinel=decompose_result.sentinel,
        project=ado_remote.project, code_repo="myproj/myrepo",
    )
    assert manifest_obj.prefix == "feat"
    assert len(manifest_obj.branches) == 2

    # Phase 4: publish (uses bare remote for actual pushes)
    after = create_op.publish(
        redis_client, repo, ado_client, ado_remote,
        prefix="feat",
        remote="bare",  # use the bare-repo remote, not the ADO-shaped one
        confirmed=lambda _m: True,
    )

    pr_ids = [b.pr_id for b in after.branches]
    assert all(pid is not None for pid in pr_ids)
    assert len(set(pr_ids)) == 2  # distinct PR ids per branch

    ado_client.close()
