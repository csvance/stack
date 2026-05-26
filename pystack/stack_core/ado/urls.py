"""Helpers for building ADO API and web URLs."""

from __future__ import annotations

API_VERSION = "7.1"


def pr_list_path(project: str, repo: str) -> str:
    return f"/{project}/_apis/git/repositories/{repo}/pullrequests"


def pr_path(project: str, repo: str, pr_id: int) -> str:
    return f"/{project}/_apis/git/repositories/{repo}/pullrequests/{pr_id}"


def pr_web_url(organization_url: str, project: str, repo: str, pr_id: int) -> str:
    base = organization_url.rstrip("/")
    return f"{base}/{project}/_git/{repo}/pullrequest/{pr_id}"
