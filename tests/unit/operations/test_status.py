"""Status operation: load manifest from Redis, observe git, return drift records."""

from __future__ import annotations

import subprocess
from pathlib import Path

import fakeredis
import pytest

from stack_core import state_store
from stack_core.exceptions import ManifestNotFoundError
from stack_core.operations import status as status_op
from stack_core.types import BranchEntry, Manifest, Verification
from tests.conftest import FIXED_TIME, sha


def _run(args, cwd):
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True)


@pytest.fixture
def real_repo(tmp_path: Path):
    """A git repo with main at base and feat-stacked-1/2/3 stacked above it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-q", "-b", "main"], repo)
    _run(["git", "config", "user.email", "t@t"], repo)
    _run(["git", "config", "user.name", "T"], repo)
    _run(["git", "config", "commit.gpgsign", "false"], repo)
    (repo / "README.md").write_text("base\n")
    _run(["git", "add", "README.md"], repo)
    _run(["git", "commit", "-q", "-m", "base"], repo)
    base_sha = _sha(repo, "HEAD")

    _run(["git", "checkout", "-q", "-b", "feat-build"], repo)
    shas = [base_sha]
    for i in range(1, 4):
        (repo / f"f{i}.txt").write_text(str(i))
        _run(["git", "add", f"f{i}.txt"], repo)
        _run(["git", "commit", "-q", "-m", f"part {i}"], repo)
        _run(["git", "branch", f"feat-stacked-{i}"], repo)
        shas.append(_sha(repo, "HEAD"))
    _run(["git", "checkout", "-q", "main"], repo)
    return repo, shas


def _sha(repo: Path, rev: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"{rev}^{{commit}}"],
        cwd=str(repo), check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def _real_manifest(base_sha, c1, c2, c3) -> Manifest:
    return Manifest(
        prefix="feat",
        code_repo="proj/repo",
        base_ref="main",
        branch_suffix="-stacked-",
        source_branch="feat",
        source_branch_tip=c3,
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
            BranchEntry(
                order=3, name="feat-stacked-3",
                commit_sha=c3, parent_sha=c2, tree_hash=sha(103), subject="part 3",
            ),
        ],
        verification=Verification(
            passed=True, method="tree-hash-equality",
            original_tree=sha(200), stack_tip_tree=sha(103), last_verified_at=FIXED_TIME,
        ),
    )


def test_raises_when_manifest_missing(tmp_path, real_repo):
    repo, _ = real_repo
    client = fakeredis.FakeRedis(decode_responses=False)
    with pytest.raises(ManifestNotFoundError):
        status_op.run(client, repo, "proj", "feat")


def test_no_drift_on_fresh_stack(real_repo):
    repo, shas = real_repo
    base_sha, c1, c2, c3 = shas
    m = _real_manifest(base_sha, c1, c2, c3)
    client = fakeredis.FakeRedis(decode_responses=False)
    state_store.update_manifest(client, "proj", "feat", lambda _: m, "create", "test")

    result = status_op.run(client, repo, "proj", "feat")
    assert result.drifts == []
    assert result.base_ref_tip == base_sha
    assert result.git_state["feat-stacked-1"].tip_sha == c1
    assert result.git_state["feat-stacked-2"].tip_sha == c2
    assert result.git_state["feat-stacked-3"].tip_sha == c3


def test_detects_missing_branch(real_repo):
    repo, shas = real_repo
    base_sha, c1, c2, c3 = shas
    m = _real_manifest(base_sha, c1, c2, c3)
    client = fakeredis.FakeRedis(decode_responses=False)
    state_store.update_manifest(client, "proj", "feat", lambda _: m, "create", "test")
    _run(["git", "branch", "-D", "feat-stacked-2"], repo)

    result = status_op.run(client, repo, "proj", "feat")
    cats = [d.category.value for d in result.drifts]
    assert "MISSING_BRANCH" in cats
