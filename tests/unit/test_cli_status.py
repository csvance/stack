"""CLI smoke test for `pystack status` using typer's CliRunner."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import fakeredis
import pytest
from typer.testing import CliRunner

from stack_cli.main import app
from stack_core import state_store
from stack_core.types import BranchEntry, Manifest, Verification
from tests.conftest import FIXED_TIME, sha


def _run(args, cwd):
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True)


def _sha(repo: Path, rev: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"{rev}^{{commit}}"],
        cwd=str(repo), check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def cli_repo(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-q", "-b", "main"], repo)
    _run(["git", "config", "user.email", "t@t"], repo)
    _run(["git", "config", "user.name", "T"], repo)
    _run(["git", "config", "commit.gpgsign", "false"], repo)
    _run(["git", "remote", "add", "origin", "https://dev.azure.com/myorg/myproj/_git/myrepo"], repo)
    (repo / "README.md").write_text("hi\n")
    _run(["git", "add", "README.md"], repo)
    _run(["git", "commit", "-q", "-m", "base"], repo)
    base_sha = _sha(repo, "HEAD")

    _run(["git", "checkout", "-q", "-b", "feat-build"], repo)
    (repo / "a.txt").write_text("a")
    _run(["git", "add", "a.txt"], repo)
    _run(["git", "commit", "-q", "-m", "part 1"], repo)
    c1 = _sha(repo, "HEAD")
    _run(["git", "branch", "feat-stacked-1"], repo)

    (repo / "b.txt").write_text("b")
    _run(["git", "add", "b.txt"], repo)
    _run(["git", "commit", "-q", "-m", "part 2"], repo)
    c2 = _sha(repo, "HEAD")
    _run(["git", "branch", "feat-stacked-2"], repo)
    _run(["git", "checkout", "-q", "feat-stacked-2"], repo)

    return repo, base_sha, c1, c2


@pytest.fixture
def cli_config(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
ado:
  organization_url: https://dev.azure.com/myorg
  pat: dummy-pat
"""
    )
    monkeypatch.setenv("STACK_CONFIG", str(config_path))
    monkeypatch.setenv("NO_COLOR", "1")
    return config_path


@pytest.fixture
def shared_fakeredis():
    client = fakeredis.FakeRedis(decode_responses=False)
    with patch("stack_cli.commands.status._connect_redis", return_value=client):
        yield client
    client.close()


def _make_manifest(base_sha, c1, c2) -> Manifest:
    return Manifest(
        prefix="feat",
        code_repo="myproj/myrepo",
        base_ref="main",
        branch_suffix="-stacked-",
        source_branch="feat",
        source_branch_tip=c2,
        created_at=FIXED_TIME,
        last_update=FIXED_TIME,
        branches=[
            BranchEntry(
                order=1, name="feat-stacked-1",
                commit_sha=c1, parent_sha=base_sha, tree_hash=sha(101), subject="part 1",
            ),
            BranchEntry(
                order=2, name="feat-stacked-2",
                commit_sha=c2, parent_sha=c1, tree_hash=sha(102), subject="part 2",
            ),
        ],
        verification=Verification(
            passed=True, method="tree-hash-equality",
            original_tree=sha(200), stack_tip_tree=sha(102), last_verified_at=FIXED_TIME,
        ),
    )


def test_status_human_no_drift(cli_repo, cli_config, shared_fakeredis):
    repo, base_sha, c1, c2 = cli_repo
    m = _make_manifest(base_sha, c1, c2)
    state_store.update_manifest(shared_fakeredis, "myproj", "feat", lambda _: m, "create", "test")

    runner = CliRunner()
    result = runner.invoke(app, ["--repo", str(repo), "status"])
    assert result.exit_code == 0, result.output
    assert "feat-stacked-1" in result.output
    assert "no drift" in result.output


def test_status_json_output(cli_repo, cli_config, shared_fakeredis):
    repo, base_sha, c1, c2 = cli_repo
    m = _make_manifest(base_sha, c1, c2)
    state_store.update_manifest(shared_fakeredis, "myproj", "feat", lambda _: m, "create", "test")

    runner = CliRunner()
    result = runner.invoke(app, ["--repo", str(repo), "status", "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["prefix"] == "feat"
    assert len(payload["branches"]) == 2
    assert payload["drifts"] == []


def test_status_returns_exit_1_on_drift(cli_repo, cli_config, shared_fakeredis):
    repo, base_sha, c1, c2 = cli_repo
    m = _make_manifest(base_sha, c1, c2)
    state_store.update_manifest(shared_fakeredis, "myproj", "feat", lambda _: m, "create", "test")
    _run(["git", "branch", "-D", "feat-stacked-1"], repo)
    _run(["git", "checkout", "-q", "main"], repo)

    runner = CliRunner()
    result = runner.invoke(app, ["--repo", str(repo), "--stack", "feat", "status"])
    assert result.exit_code == 1, result.output
    assert "MISSING_BRANCH" in result.output


def test_status_missing_manifest_exits_2(cli_repo, cli_config, shared_fakeredis):
    repo, _, _, _ = cli_repo
    runner = CliRunner()
    result = runner.invoke(app, ["--repo", str(repo), "status"])
    assert result.exit_code == 2
