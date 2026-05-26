"""Pydantic models for the ADO webhook payloads we consume.

We pick out only the fields the bot needs and ignore the rest. ADO sends a lot;
this keeps parsing tight and forward-compatible.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _Repository(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str
    project: dict[str, str] = Field(default_factory=dict)


class _PullRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    pullRequestId: int
    sourceRefName: str
    targetRefName: str
    status: str  # ADO: "active" | "completed" | "abandoned"
    repository: _Repository


class _Resource(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    pullRequestId: int
    sourceRefName: str
    targetRefName: str
    status: str
    repository: _Repository


class AdoPullRequestEvent(BaseModel):
    """A minimal view of an ADO pull-request webhook payload."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    eventType: str
    notificationId: int
    resource: _Resource

    @property
    def pr_id(self) -> int:
        return self.resource.pullRequestId

    @property
    def source_branch(self) -> str:
        return self._strip_refs_heads(self.resource.sourceRefName)

    @property
    def target_branch(self) -> str:
        return self._strip_refs_heads(self.resource.targetRefName)

    @property
    def status(self) -> str:
        return self.resource.status

    @property
    def project(self) -> str:
        return self.resource.repository.project.get("name", "")

    @property
    def repo(self) -> str:
        return self.resource.repository.name

    @staticmethod
    def _strip_refs_heads(ref: str) -> str:
        prefix = "refs/heads/"
        return ref[len(prefix):] if ref.startswith(prefix) else ref
