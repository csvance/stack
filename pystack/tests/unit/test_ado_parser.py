"""ADO remote URL parsing — ports lib/az_helpers.sh:40-72."""

from __future__ import annotations

import pytest

from stack_core.state_store import AdoRemote, parse_ado_remote, resolve_project


class TestParseAdoRemote:
    def test_hosted_https(self):
        result = parse_ado_remote("https://dev.azure.com/myorg/myproj/_git/myrepo")
        assert result == AdoRemote(
            org_url="https://dev.azure.com/myorg",
            project="myproj",
            repo="myrepo",
        )

    def test_hosted_https_with_user_info(self):
        result = parse_ado_remote("https://user@dev.azure.com/myorg/myproj/_git/myrepo")
        assert result.org_url == "https://dev.azure.com/myorg"

    def test_legacy_visualstudio(self):
        result = parse_ado_remote("https://myorg.visualstudio.com/myproj/_git/myrepo")
        assert result == AdoRemote(
            org_url="https://myorg.visualstudio.com",
            project="myproj",
            repo="myrepo",
        )

    def test_on_prem_server(self):
        result = parse_ado_remote("https://ado.internal.example.com/tfs/DefaultCollection/myproj/_git/myrepo")
        assert result.project == "myproj"
        assert result.repo == "myrepo"
        assert "ado.internal.example.com" in result.org_url

    def test_ssh(self):
        result = parse_ado_remote("git@ssh.dev.azure.com:v3/myorg/myproj/myrepo")
        assert result == AdoRemote(
            org_url="https://dev.azure.com/myorg",
            project="myproj",
            repo="myrepo",
        )

    def test_strips_dot_git(self):
        result = parse_ado_remote("https://dev.azure.com/myorg/myproj/_git/myrepo.git")
        assert result.repo == "myrepo"

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError):
            parse_ado_remote("https://github.com/foo/bar")


def test_resolve_project():
    assert resolve_project("https://dev.azure.com/o/p/_git/r") == "p"
