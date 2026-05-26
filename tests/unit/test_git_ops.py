"""Exercise git_ops wrappers against real git on temporary repos.

These are unit tests (no Redis, no network) but they shell out to ``git``. Tests that
require ``git-branchless`` are skipped if it's not on PATH.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from stack_core import git_ops
from stack_core.exceptions import GitError


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A fresh git repo with a single initial commit on `main`."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    _run(["git", "init", "-q", "-b", "main"], repo_path)
    _run(["git", "config", "user.email", "test@example.com"], repo_path)
    _run(["git", "config", "user.name", "Test"], repo_path)
    _run(["git", "config", "commit.gpgsign", "false"], repo_path)
    (repo_path / "README.md").write_text("hello\n")
    _run(["git", "add", "README.md"], repo_path)
    _run(["git", "commit", "-q", "-m", "initial"], repo_path)
    return repo_path


def _run(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True)


def _commit(repo: Path, filename: str, content: str, message: str) -> str:
    (repo / filename).write_text(content)
    _run(["git", "add", filename], repo)
    _run(["git", "commit", "-q", "-m", message], repo)
    return git_ops.sha_of(repo, "HEAD")


class TestShaResolution:
    def test_sha_of_head(self, repo):
        sha = git_ops.sha_of(repo, "HEAD")
        assert len(sha) == 40
        assert all(c in "0123456789abcdef" for c in sha)

    def test_tree_of_head(self, repo):
        tree = git_ops.tree_of(repo, "HEAD")
        assert len(tree) == 40

    def test_sha_of_invalid_raises(self, repo):
        with pytest.raises(GitError):
            git_ops.sha_of(repo, "no-such-ref")

    def test_parent_sha(self, repo):
        first = git_ops.sha_of(repo, "HEAD")
        second = _commit(repo, "f.txt", "x", "second")
        assert git_ops.parent_sha(repo, second) == first


class TestRefAndBranchExists:
    def test_branch_exists(self, repo):
        assert git_ops.branch_exists(repo, "main") is True
        assert git_ops.branch_exists(repo, "nope") is False

    def test_ref_exists(self, repo):
        assert git_ops.ref_exists(repo, "refs/heads/main") is True
        assert git_ops.ref_exists(repo, "refs/heads/nope") is False


class TestListBranchesMatching:
    def test_matches_pattern(self, repo):
        _run(["git", "branch", "feat-stacked-1"], repo)
        _run(["git", "branch", "feat-stacked-2"], repo)
        _run(["git", "branch", "other"], repo)
        result = git_ops.list_branches_matching(repo, "feat-stacked-*")
        assert sorted(result) == ["feat-stacked-1", "feat-stacked-2"]

    def test_empty_when_no_match(self, repo):
        assert git_ops.list_branches_matching(repo, "nope-*") == []


class TestIsAncestor:
    def test_self_is_ancestor(self, repo):
        head = git_ops.sha_of(repo, "HEAD")
        assert git_ops.is_ancestor(repo, head, head) is True

    def test_parent_is_ancestor(self, repo):
        first = git_ops.sha_of(repo, "HEAD")
        second = _commit(repo, "f.txt", "x", "second")
        assert git_ops.is_ancestor(repo, first, second) is True
        assert git_ops.is_ancestor(repo, second, first) is False


class TestCommitMetadata:
    def test_subject_and_body(self, repo):
        _commit(repo, "f.txt", "x", "subject line\n\nbody line 1\nbody line 2")
        assert git_ops.commit_subject(repo, "HEAD") == "subject line"
        assert "body line 1" in git_ops.commit_body(repo, "HEAD")


class TestFilesChanged:
    def test_returns_changed_paths(self, repo):
        first = git_ops.sha_of(repo, "HEAD")
        (repo / "a.txt").write_text("a")
        (repo / "b.txt").write_text("b")
        _run(["git", "add", "."], repo)
        _run(["git", "commit", "-q", "-m", "two files"], repo)
        second = git_ops.sha_of(repo, "HEAD")
        result = git_ops.files_changed(repo, first, second)
        assert sorted(result) == ["a.txt", "b.txt"]

    def test_no_changes_returns_empty(self, repo):
        head = git_ops.sha_of(repo, "HEAD")
        assert git_ops.files_changed(repo, head, head) == []


class TestWorkingTreeClean:
    def test_clean_after_commit(self, repo):
        assert git_ops.working_tree_clean(repo) is True

    def test_dirty_on_staged_change(self, repo):
        (repo / "x.txt").write_text("x")
        _run(["git", "add", "x.txt"], repo)
        assert git_ops.working_tree_clean(repo) is False

    def test_dirty_on_unstaged_change(self, repo):
        (repo / "README.md").write_text("changed\n")
        assert git_ops.working_tree_clean(repo) is False


class TestWorktree:
    def test_add_and_remove(self, repo, tmp_path):
        worktree = tmp_path / "wt"
        git_ops.worktree_add_detached(repo, worktree, "HEAD")
        assert worktree.is_dir()
        assert (worktree / "README.md").exists()
        git_ops.worktree_remove(repo, worktree)
        assert not worktree.exists()


@pytest.mark.skipif(not git_ops.branchless_available(), reason="git-branchless not installed")
class TestBranchless:
    def test_move_simple(self, repo):
        first = git_ops.sha_of(repo, "HEAD")
        _run(["git", "branch", "feature"], repo)
        # init branchless after branch creation
        _run(["git", "branchless", "init"], repo)
        _run(["git", "checkout", "-q", "feature"], repo)
        _commit(repo, "feature.txt", "f", "feature work")
        _run(["git", "checkout", "-q", "main"], repo)
        # advance main
        _commit(repo, "main.txt", "m", "main moved")
        new_base = git_ops.sha_of(repo, "main")
        git_ops.branchless_move(repo, src="feature", dest=new_base)
        # feature should now descend from new main
        assert git_ops.is_ancestor(repo, new_base, "feature") is True
        # original feature commit shouldn't be reachable; the rebased one has a new SHA
        feature_sha = git_ops.sha_of(repo, "feature")
        assert feature_sha != first
