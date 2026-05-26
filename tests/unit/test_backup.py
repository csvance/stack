"""Git-ref snapshot create/restore."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stack_core import backup, git_ops
from stack_core.exceptions import GitError


def _run(args, cwd):
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True)


def _sha(repo, rev):
    return subprocess.run(
        ["git", "rev-parse", "--verify", f"{rev}^{{commit}}"],
        cwd=str(repo), check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def repo_with_stack(tmp_path: Path):
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

    _run(["git", "checkout", "-q", "-b", "feat-stacked-1"], repo)
    (repo / "f1.txt").write_text("1")
    _run(["git", "add", "f1.txt"], repo)
    _run(["git", "commit", "-q", "-m", "part 1"], repo)
    c1 = _sha(repo, "HEAD")

    _run(["git", "checkout", "-q", "-b", "feat-stacked-2"], repo)
    (repo / "f2.txt").write_text("2")
    _run(["git", "add", "f2.txt"], repo)
    _run(["git", "commit", "-q", "-m", "part 2"], repo)
    c2 = _sha(repo, "HEAD")

    _run(["git", "checkout", "-q", "main"], repo)
    return repo, base_sha, c1, c2


def test_snapshot_creates_backup_refs(repo_with_stack):
    repo, _, c1, c2 = repo_with_stack
    when = datetime(2026, 5, 25, 14, 23, 11, tzinfo=UTC)
    snap_id = backup.snapshot(repo, "land", "feat", ["feat-stacked-1", "feat-stacked-2"], clock=when)

    assert snap_id == "land-20260525T142311Z"
    refs = git_ops._git(
        ["for-each-ref", "--format=%(refname) %(objectname)", "refs/backup/stack/feat/"],
        repo,
    )
    lines = sorted(refs.splitlines())
    assert lines[0].startswith(f"refs/backup/stack/feat/{snap_id}/feat-stacked-1 {c1}")
    assert lines[1].startswith(f"refs/backup/stack/feat/{snap_id}/feat-stacked-2 {c2}")


def test_restore_moves_branches_back(repo_with_stack):
    repo, _, c1, c2 = repo_with_stack
    snap_id = backup.snapshot(repo, "land", "feat", ["feat-stacked-1", "feat-stacked-2"])

    # Mutate the branches: reset stacked-1 to main.
    main_sha = git_ops.sha_of(repo, "main")
    git_ops._git(["update-ref", "refs/heads/feat-stacked-1", main_sha], repo)
    assert git_ops.sha_of(repo, "feat-stacked-1") == main_sha

    backup.restore(repo, "feat", snap_id)
    assert git_ops.sha_of(repo, "feat-stacked-1") == c1
    assert git_ops.sha_of(repo, "feat-stacked-2") == c2


def test_restore_missing_snapshot_raises(repo_with_stack):
    repo, *_ = repo_with_stack
    with pytest.raises(GitError):
        backup.restore(repo, "feat", "land-nope")
