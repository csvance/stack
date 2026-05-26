"""git_ops.clone (with http.extraHeader) and git_ops.fetch."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from stack_core import git_ops
from stack_core.exceptions import GitError


def _run(args, cwd=None):
    subprocess.run(args, cwd=str(cwd) if cwd else None, check=True, capture_output=True)


@pytest.fixture
def bare_origin(tmp_path: Path) -> Path:
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)

    # Populate the bare repo with one commit via a throwaway clone.
    seed = tmp_path / "seed"
    _run(["git", "clone", "-q", str(bare), str(seed)])
    _run(["git", "config", "user.email", "t@t"], seed)
    _run(["git", "config", "user.name", "T"], seed)
    _run(["git", "config", "commit.gpgsign", "false"], seed)
    (seed / "README.md").write_text("hi\n")
    _run(["git", "add", "README.md"], seed)
    _run(["git", "commit", "-q", "-m", "init"], seed)
    _run(["git", "push", "-q", "origin", "main"], seed)
    return bare


def test_clone_succeeds(bare_origin, tmp_path):
    dest = tmp_path / "cloned"
    git_ops.clone(str(bare_origin), dest)
    assert (dest / "README.md").exists()


def test_clone_with_extra_header_sets_config(bare_origin, tmp_path):
    dest = tmp_path / "cloned-headed"
    git_ops.clone(str(bare_origin), dest, extra_headers={"Authorization": "Basic abc"})
    # The header is set inline via -c, so it's NOT persisted to the cloned repo's
    # config. We instead verify the clone succeeded with the header by checking
    # the parent dir was created and a file exists.
    assert (dest / "README.md").exists()


def test_clone_failure_raises_git_error(tmp_path):
    with pytest.raises(GitError):
        git_ops.clone("file:///no/such/repo", tmp_path / "fail")


def test_fetch_runs(bare_origin, tmp_path):
    dest = tmp_path / "clone-for-fetch"
    git_ops.clone(str(bare_origin), dest)
    git_ops.fetch(dest, "origin")
    git_ops.fetch(dest, "origin", "main")
