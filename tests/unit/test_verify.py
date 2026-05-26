"""Tree-hash verifier: cherry-pick onto base in throwaway worktree."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from stack_core import verify
from stack_core.exceptions import VerifyConflict


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
def linear_repo(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-q", "-b", "main"], repo)
    _run(["git", "config", "user.email", "t@t"], repo)
    _run(["git", "config", "user.name", "T"], repo)
    _run(["git", "config", "commit.gpgsign", "false"], repo)
    (repo / "README.md").write_text("base\n")
    _run(["git", "add", "README.md"], repo)
    _run(["git", "commit", "-q", "-m", "base"], repo)
    base = _sha(repo, "HEAD")

    shas = []
    for i in range(1, 4):
        (repo / f"f{i}.txt").write_text(str(i))
        _run(["git", "add", f"f{i}.txt"], repo)
        _run(["git", "commit", "-q", "-m", f"part {i}"], repo)
        shas.append(_sha(repo, "HEAD"))
    return repo, base, shas


def test_returns_base_tree_when_no_commits(linear_repo):
    repo, base, _ = linear_repo
    assert verify.compute_reference_tip(repo, base, []) == _tree(repo, base)


def test_cherry_pick_chain_matches_original(linear_repo):
    repo, base, shas = linear_repo
    expected_tree = _tree(repo, shas[-1])
    actual_tree = verify.compute_reference_tip(repo, base, shas)
    assert actual_tree == expected_tree


def test_conflict_raises(linear_repo, tmp_path):
    """Cherry-picking a commit that depends on different parent state should conflict."""
    repo, base, shas = linear_repo
    # Make a new branch that modifies f1.txt at base, so cherry-picking shas[0]
    # (which created f1.txt) would conflict if applied after a different f1.txt.
    _run(["git", "checkout", "-q", "-b", "conflict-base", base], repo)
    (repo / "f1.txt").write_text("different content")
    _run(["git", "add", "f1.txt"], repo)
    _run(["git", "commit", "-q", "-m", "different f1"], repo)
    conflict_base = _sha(repo, "HEAD")

    with pytest.raises(VerifyConflict) as excinfo:
        verify.compute_reference_tip(repo, conflict_base, [shas[0]])
    assert excinfo.value.commit_sha == shas[0]
    assert "f1.txt" in excinfo.value.conflicting_paths


def test_worktree_cleaned_up_on_conflict(linear_repo):
    """Even when verification fails, no worktrees should leak."""
    repo, base, shas = linear_repo
    _run(["git", "checkout", "-q", "-b", "conflict-base", base], repo)
    (repo / "f1.txt").write_text("clash")
    _run(["git", "add", "f1.txt"], repo)
    _run(["git", "commit", "-q", "-m", "clash"], repo)
    conflict_base = _sha(repo, "HEAD")

    with pytest.raises(VerifyConflict):
        verify.compute_reference_tip(repo, conflict_base, [shas[0]])

    worktrees = subprocess.run(
        ["git", "worktree", "list"],
        cwd=str(repo), check=True, capture_output=True, text=True,
    ).stdout
    # Only the main worktree should remain.
    assert worktrees.count("\n") == 1
