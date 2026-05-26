"""PR-specific operations layered on :class:`AdoClient`."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from stack_core.ado.client import AdoClient
from stack_core.ado.urls import (
    API_VERSION,
    pr_label_path,
    pr_labels_path,
    pr_list_path,
    pr_path,
    pr_threads_path,
    pr_web_url,
)

PrStatus = Literal["active", "completed", "abandoned"]


class PullRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    pr_id: int = Field(alias="pullRequestId")
    status: PrStatus
    source_branch: str = Field(alias="sourceRefName")
    target_branch: str = Field(alias="targetRefName")
    title: str
    description: str = ""
    web_url: str = ""

    @classmethod
    def from_api(cls, payload: dict[str, Any], organization_url: str, project: str, repo: str) -> PullRequest:
        web_url = pr_web_url(organization_url, project, repo, int(payload["pullRequestId"]))
        return cls.model_validate({**payload, "web_url": web_url})


def _normalize_ref(name: str) -> str:
    return name if name.startswith("refs/") else f"refs/heads/{name}"


def list_for_branch(
    client: AdoClient,
    project: str,
    repo: str,
    source_branch: str,
    *,
    status: PrStatus | Literal["all"] = "all",
    organization_url: str,
) -> list[PullRequest]:
    response = client.get(
        pr_list_path(project, repo),
        **{
            "api-version": API_VERSION,
            "searchCriteria.sourceRefName": _normalize_ref(source_branch),
            "searchCriteria.status": status,
        },
    )
    payload = response.json()
    return [
        PullRequest.from_api(item, organization_url, project, repo)
        for item in payload.get("value", [])
    ]


def show(
    client: AdoClient,
    project: str,
    repo: str,
    pr_id: int,
    *,
    organization_url: str,
) -> PullRequest:
    response = client.get(pr_path(project, repo, pr_id), **{"api-version": API_VERSION})
    return PullRequest.from_api(response.json(), organization_url, project, repo)


def create(
    client: AdoClient,
    project: str,
    repo: str,
    *,
    source_branch: str,
    target_branch: str,
    title: str,
    description: str,
    organization_url: str,
) -> PullRequest:
    body = {
        "sourceRefName": _normalize_ref(source_branch),
        "targetRefName": _normalize_ref(target_branch),
        "title": title,
        "description": description,
    }
    response = client.post(pr_list_path(project, repo), body, **{"api-version": API_VERSION})
    return PullRequest.from_api(response.json(), organization_url, project, repo)


def update(
    client: AdoClient,
    project: str,
    repo: str,
    pr_id: int,
    *,
    target_branch: str | None = None,
    title: str | None = None,
    description: str | None = None,
    status: PrStatus | None = None,
    organization_url: str,
) -> PullRequest:
    body: dict[str, Any] = {}
    if target_branch is not None:
        body["targetRefName"] = _normalize_ref(target_branch)
    if title is not None:
        body["title"] = title
    if description is not None:
        body["description"] = description
    if status is not None:
        body["status"] = status
    response = client.patch(pr_path(project, repo, pr_id), body, **{"api-version": API_VERSION})
    return PullRequest.from_api(response.json(), organization_url, project, repo)


def add_comment(
    client: AdoClient,
    project: str,
    repo: str,
    pr_id: int,
    content: str,
    *,
    organization_url: str,
) -> int:
    """Post a single-comment thread on the PR. Returns the new thread id."""
    body = {
        "comments": [{"parentCommentId": 0, "content": content, "commentType": "text"}],
        "status": "active",
    }
    response = client.post(
        pr_threads_path(project, repo, pr_id), body, **{"api-version": API_VERSION}
    )
    return int(response.json()["id"])


def add_label(
    client: AdoClient,
    project: str,
    repo: str,
    pr_id: int,
    label: str,
    *,
    organization_url: str,
) -> None:
    client.post(
        pr_labels_path(project, repo, pr_id),
        {"name": label},
        **{"api-version": API_VERSION},
    )


def remove_label(
    client: AdoClient,
    project: str,
    repo: str,
    pr_id: int,
    label: str,
    *,
    organization_url: str,
) -> None:
    client.delete(pr_label_path(project, repo, pr_id, label), **{"api-version": API_VERSION})


def list_labels(
    client: AdoClient,
    project: str,
    repo: str,
    pr_id: int,
    *,
    organization_url: str,
) -> list[str]:
    response = client.get(pr_labels_path(project, repo, pr_id), **{"api-version": API_VERSION})
    payload = response.json()
    return [item["name"] for item in payload.get("value", [])]
