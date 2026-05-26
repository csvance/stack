"""Pydantic models for manifests, branches, audit entries, and operation results."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _validate_sha(value: str, field_name: str) -> str:
    if not _SHA_RE.match(value):
        raise ValueError(f"{field_name} must be a 40-character lowercase hex sha, got {value!r}")
    return value


class BranchEntry(BaseModel):
    """One branch in a stack manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    order: int = Field(ge=1)
    name: str = Field(min_length=1)
    commit_sha: str
    parent_sha: str
    tree_hash: str
    subject: str
    body: str = ""
    files_changed: list[str] = Field(default_factory=list)
    pr_id: int | None = None
    pr_url: str | None = None

    @model_validator(mode="after")
    def _validate_shas(self) -> BranchEntry:
        _validate_sha(self.commit_sha, "commit_sha")
        _validate_sha(self.parent_sha, "parent_sha")
        _validate_sha(self.tree_hash, "tree_hash")
        return self


class Verification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: bool
    method: Literal["tree-hash-equality"]
    original_tree: str
    stack_tip_tree: str
    last_verified_at: datetime

    @model_validator(mode="after")
    def _validate_trees(self) -> Verification:
        _validate_sha(self.original_tree, "original_tree")
        _validate_sha(self.stack_tip_tree, "stack_tip_tree")
        return self


class Manifest(BaseModel):
    """A complete stack manifest as persisted in Redis."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    prefix: str = Field(min_length=1)
    code_repo: str = Field(min_length=1)
    base_ref: str = Field(min_length=1)
    branch_suffix: str = "-stacked-"
    source_branch: str = Field(min_length=1)
    source_branch_tip: str
    created_at: datetime
    last_update: datetime
    branches: list[BranchEntry] = Field(min_length=1)
    verification: Verification

    @model_validator(mode="after")
    def _validate_invariants(self) -> Manifest:
        _validate_sha(self.source_branch_tip, "source_branch_tip")
        self._validate_orders()
        self._validate_branch_names()
        self._validate_parent_chain()
        return self

    def _validate_orders(self) -> None:
        seen: set[int] = set()
        last = 0
        for entry in self.branches:
            if entry.order in seen:
                raise ValueError(f"duplicate order {entry.order} in branches")
            if entry.order <= last:
                raise ValueError(
                    f"branches must be strictly ascending by order; got {entry.order} after {last}"
                )
            seen.add(entry.order)
            last = entry.order

    def _validate_branch_names(self) -> None:
        for entry in self.branches:
            expected = f"{self.prefix}{self.branch_suffix}{entry.order}"
            if entry.name != expected:
                raise ValueError(
                    f"branch name {entry.name!r} does not match expected {expected!r} "
                    f"(prefix={self.prefix!r} suffix={self.branch_suffix!r} order={entry.order})"
                )

    def _validate_parent_chain(self) -> None:
        for i in range(1, len(self.branches)):
            prev = self.branches[i - 1]
            curr = self.branches[i]
            if curr.parent_sha != prev.commit_sha:
                raise ValueError(
                    f"branch {curr.name!r} parent_sha {curr.parent_sha} does not match "
                    f"previous branch {prev.name!r} commit_sha {prev.commit_sha}"
                )


class AuditEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timestamp: datetime
    event_type: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)


class OperationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    success: bool
    message: str
    manifest_after: Manifest | None = None
    audit_entries: list[AuditEntry] = Field(default_factory=list)
