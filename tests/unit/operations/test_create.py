"""Per-phase tests for prepare, decompose, manifest. publish is exercised by the
integration test (which exercises the full chain against respx + fakeredis)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import fakeredis
import pytest

from stack_core import git_ops, state_store
from stack_core.exceptions import (
    DecomposeError,
    PrepareError,
    SentinelMissing,
)
from stack_core.operations import create as create_op


def _run(args, cwd):
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True)


def _sha(repo, rev):
    return subprocess.run(
        ["git", "rev-parse", "--verify", f"{rev}^{{commit}}"],
        cwd=str(repo), check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def feature_repo(tmp_path: Path):
    """A repo with main at base and a feature branch with 3 commits."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-q", "-b", "main"], repo)
    _run(["git", "config", "user.email", "t@t"], repo)
    _run(["git", "config", "user.name", "T"], repo)
    _run(["git", "config", "commit.gpgsign", "false"], repo)
    _run(["git", "remote", "add", "origin", "https://dev.azure.com/myorg/myproj/_git/myrepo"], repo)

    (repo / "README.md").write_text("base\n")
    _run(["git", "add", "README.md"], repo)
    _run(["git", "commit", "-q", "-m", "base"], repo)
    base_sha = _sha(repo, "HEAD")

    _run(["git", "checkout", "-q", "-b", "feat"], repo)
    for i in range(1, 4):
        (repo / f"f{i}.txt").write_text(str(i))
        _run(["git", "add", f"f{i}.txt"], repo)
        _run(["git", "commit", "-q", "-m", f"feature part {i}"], repo)

    return repo, base_sha


@pytest.fixture
def claude_stub(tmp_path: Path):
    """A fake `claude` script that writes a deterministic sentinel + branches."""
    stub = tmp_path / "claude-stub.py"
    stub.write_text(
        '''#!/usr/bin/env python3
"""Stub claude-code for tests: read CLAUDE.md.local, build branches, write sentinel."""
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

# Build one branch per file from input.
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

# Verify tree-hash equality with input.
input_tree = sh("git", "rev-parse", "--verify", input_branch + "^{tree}")
top_tree = sh("git", "rev-parse", "--verify", branches[-1] + "^{tree}")
if input_tree != top_tree:
    sys.stderr.write("VERIFY FAILED in stub\\n")
    sys.exit(1)

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


class TestPrepare:
    def test_clean_repo_passes(self, feature_repo):
        repo, _ = feature_repo
        result = create_op.prepare(
            repo, prefix="feat", base_ref="main", branch_suffix="-stacked-",
            feature_branch="feat", claude_bin="/usr/bin/true",
        )
        assert result.source_branch == "feat"
        assert result.branch_suffix == "-stacked-"
        assert result.base_ref == "main"

    def test_dirty_worktree_fails(self, feature_repo):
        repo, _ = feature_repo
        (repo / "dirty.txt").write_text("wip")
        _run(["git", "add", "dirty.txt"], repo)  # staged but not committed
        with pytest.raises(PrepareError, match="working tree"):
            create_op.prepare(
                repo, prefix="feat", base_ref="main", branch_suffix="-stacked-",
                claude_bin="/usr/bin/true",
            )

    def test_no_claude_binary_fails(self, feature_repo, monkeypatch):
        repo, _ = feature_repo
        monkeypatch.delenv("STACK_CLAUDE_BIN", raising=False)
        monkeypatch.setattr("stack_core.operations.create.shutil.which", lambda _: None)
        with pytest.raises(PrepareError, match="claude"):
            create_op.prepare(
                repo, prefix="feat", base_ref="main", branch_suffix="-stacked-",
                feature_branch="feat", claude_bin=None,
            )

    def test_existing_stack_branches_fail(self, feature_repo):
        repo, _ = feature_repo
        _run(["git", "branch", "feat-stacked-1"], repo)
        with pytest.raises(PrepareError, match="existing stack branches"):
            create_op.prepare(
                repo, prefix="feat", base_ref="main", branch_suffix="-stacked-",
                feature_branch="feat", claude_bin="/usr/bin/true",
            )

    def test_multi_commit_creates_input_branch(self, feature_repo):
        repo, base_sha = feature_repo
        result = create_op.prepare(
            repo, prefix="feat", base_ref="main", branch_suffix="-stacked-",
            feature_branch="feat", claude_bin="/usr/bin/true",
        )
        assert result.input_branch == "feat-decompose-input"
        # The input branch should have a single commit on top of base.
        parent = git_ops.parent_sha(repo, result.input_branch)
        assert parent == base_sha
        # Tree should match the feature branch.
        assert git_ops.tree_of(repo, result.input_branch) == git_ops.tree_of(repo, "feat")

    def test_idempotent_rerun(self, feature_repo):
        repo, _ = feature_repo
        result1 = create_op.prepare(
            repo, prefix="feat", base_ref="main", branch_suffix="-stacked-",
            feature_branch="feat", claude_bin="/usr/bin/true",
        )
        # Re-run: existing input branch matches; should be reused.
        # First we need to delete the leftover input from previous call... wait
        # the second call will trip the "existing stack branches" check since the
        # input has the same prefix. The input branch uses prefix-decompose-input,
        # which does NOT match prefix<suffix>*, so it's not flagged.
        result2 = create_op.prepare(
            repo, prefix="feat", base_ref="main", branch_suffix="-stacked-",
            feature_branch="feat", claude_bin="/usr/bin/true",
        )
        assert result1.input_sha == result2.input_sha


class TestDecompose:
    def test_writes_sentinel(self, feature_repo, claude_stub, monkeypatch):
        repo, _ = feature_repo
        monkeypatch.setenv("STACK_CLAUDE_BIN", f"python3 {claude_stub}")

        prepare_result = create_op.prepare(
            repo, prefix="feat", base_ref="main", branch_suffix="-stacked-",
            feature_branch="feat", claude_bin="/usr/bin/true",
        )
        # The stub needs to be a single executable; pass it directly.
        result = create_op.decompose(
            repo, prepare_result=prepare_result, prefix="feat",
            claude_bin=f"python3 {claude_stub}",
            subprocess_runner=lambda args, cwd: subprocess.run(
                ["python3", str(claude_stub)], cwd=str(cwd), check=False
            ).returncode,
        )
        assert len(result.sentinel.branches) == 3
        assert result.sentinel.prefix == "feat"

    def test_missing_sentinel_after_claude_exit_raises(self, feature_repo):
        repo, _ = feature_repo
        prepare_result = create_op.prepare(
            repo, prefix="feat", base_ref="main", branch_suffix="-stacked-",
            feature_branch="feat", claude_bin="/usr/bin/true",
        )
        # Runner exits 0 but doesn't write sentinel.
        with pytest.raises(SentinelMissing):
            create_op.decompose(
                repo, prepare_result=prepare_result, prefix="feat",
                claude_bin="/usr/bin/true",
                subprocess_runner=lambda args, cwd: 0,
            )

    def test_claude_nonzero_exit_raises(self, feature_repo):
        repo, _ = feature_repo
        prepare_result = create_op.prepare(
            repo, prefix="feat", base_ref="main", branch_suffix="-stacked-",
            feature_branch="feat", claude_bin="/usr/bin/true",
        )
        with pytest.raises(DecomposeError, match="exited with code 1"):
            create_op.decompose(
                repo, prepare_result=prepare_result, prefix="feat",
                claude_bin="/usr/bin/true",
                subprocess_runner=lambda args, cwd: 1,
            )

    def test_idempotent_when_sentinel_exists(self, feature_repo):
        repo, _ = feature_repo
        prepare_result = create_op.prepare(
            repo, prefix="feat", base_ref="main", branch_suffix="-stacked-",
            feature_branch="feat", claude_bin="/usr/bin/true",
        )
        sentinel = {
            "prefix": "feat",
            "branches": ["feat-stacked-1"],
            "base_ref": "main",
            "source_branch": "feat",
            "source_branch_tip": "0" * 40,
            "branch_suffix": "-stacked-",
            "completion_timestamp": "2026-05-25T14:23:11Z",
        }
        sentinel_path = create_op._sentinel_path(repo, "feat")
        sentinel_path.parent.mkdir(parents=True, exist_ok=True)
        sentinel_path.write_text(json.dumps(sentinel))

        called = []
        result = create_op.decompose(
            repo, prepare_result=prepare_result, prefix="feat",
            claude_bin="/usr/bin/true",
            subprocess_runner=lambda args, cwd: called.append(args) or 0,
        )
        assert called == [], "claude should not be invoked when sentinel exists"
        assert result.sentinel.prefix == "feat"


class TestManifest:
    def test_writes_to_redis_with_verification(self, feature_repo, claude_stub):
        repo, _ = feature_repo
        prepare_result = create_op.prepare(
            repo, prefix="feat", base_ref="main", branch_suffix="-stacked-",
            feature_branch="feat", claude_bin="/usr/bin/true",
        )
        create_op.decompose(
            repo, prepare_result=prepare_result, prefix="feat",
            claude_bin=str(claude_stub),
            subprocess_runner=lambda args, cwd: subprocess.run(
                ["python3", str(claude_stub)], cwd=str(cwd), check=False
            ).returncode,
        )
        sentinel = create_op._load_sentinel(create_op._sentinel_path(repo, "feat"))

        client = fakeredis.FakeRedis(decode_responses=False)
        result = create_op.manifest(
            client, repo,
            sentinel=sentinel, project="myproj", code_repo="myproj/myrepo",
        )
        assert result.prefix == "feat"
        assert len(result.branches) == 3
        assert result.verification.passed is True
        # Round-trip through state store.
        loaded = state_store.get_manifest(client, "myproj", "feat")
        assert loaded == result

    def test_idempotent_rewrite(self, feature_repo, claude_stub):
        repo, _ = feature_repo
        prepare_result = create_op.prepare(
            repo, prefix="feat", base_ref="main", branch_suffix="-stacked-",
            feature_branch="feat", claude_bin="/usr/bin/true",
        )
        create_op.decompose(
            repo, prepare_result=prepare_result, prefix="feat",
            claude_bin=str(claude_stub),
            subprocess_runner=lambda args, cwd: subprocess.run(
                ["python3", str(claude_stub)], cwd=str(cwd), check=False
            ).returncode,
        )
        sentinel = create_op._load_sentinel(create_op._sentinel_path(repo, "feat"))

        client = fakeredis.FakeRedis(decode_responses=False)
        first = create_op.manifest(
            client, repo, sentinel=sentinel, project="myproj", code_repo="myproj/myrepo",
        )
        # The clock advances between calls so last_update will differ; we just
        # confirm a re-run doesn't fail.
        second = create_op.manifest(
            client, repo, sentinel=sentinel, project="myproj", code_repo="myproj/myrepo",
        )
        assert [b.commit_sha for b in first.branches] == [b.commit_sha for b in second.branches]
