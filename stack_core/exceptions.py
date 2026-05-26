"""Structured exception hierarchy for the stack core."""

from __future__ import annotations


class StackError(Exception):
    """Base for every error this package raises."""


class ManifestNotFoundError(StackError):
    def __init__(self, project: str, prefix: str) -> None:
        super().__init__(f"no manifest for project={project!r} prefix={prefix!r}")
        self.project = project
        self.prefix = prefix


class ManifestValidationError(StackError):
    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"manifest invalid: field={field!r}: {reason}")
        self.field = field
        self.reason = reason


class RetryExhausted(StackError):
    def __init__(self, attempts: int) -> None:
        super().__init__(f"transaction conflicts persisted after {attempts} attempts")
        self.attempts = attempts


class RedisUnavailable(StackError):
    def __init__(self, reason: str) -> None:
        super().__init__(f"redis unavailable: {reason}")
        self.reason = reason


class GitError(StackError):
    def __init__(self, command: list[str], stderr: str, exit_code: int) -> None:
        super().__init__(
            f"git command failed (exit {exit_code}): {' '.join(command)}\n{stderr.strip()}"
        )
        self.command = command
        self.stderr = stderr
        self.exit_code = exit_code


class TopologyError(StackError):
    def __init__(self, reason: str) -> None:
        super().__init__(f"topology error: {reason}")
        self.reason = reason


class BranchNameError(StackError):
    def __init__(self, name: str, expected_pattern: str) -> None:
        super().__init__(f"branch name {name!r} does not match pattern {expected_pattern!r}")
        self.name = name
        self.expected_pattern = expected_pattern


class VerifyConflict(StackError):
    def __init__(self, commit_sha: str, conflicting_paths: list[str]) -> None:
        files = ", ".join(conflicting_paths) if conflicting_paths else "<unknown>"
        super().__init__(f"cherry-pick of {commit_sha[:12]} conflicted on: {files}")
        self.commit_sha = commit_sha
        self.conflicting_paths = conflicting_paths


class VerifyTreeMismatch(StackError):
    def __init__(self, actual_tree: str, expected_tree: str) -> None:
        super().__init__(
            f"tree hash mismatch: stack tip {actual_tree[:12]} does not match "
            f"expected {expected_tree[:12]}"
        )
        self.actual_tree = actual_tree
        self.expected_tree = expected_tree


class CreateError(StackError):
    """Base class for create-workflow errors."""


class PrepareError(CreateError):
    pass


class DecomposeError(CreateError):
    pass


class SentinelMissing(DecomposeError):
    def __init__(self, sentinel_path: str) -> None:
        super().__init__(f"sentinel file not found at {sentinel_path}")
        self.sentinel_path = sentinel_path


class PublishError(CreateError):
    pass


class LandError(StackError):
    def __init__(self, reason: str) -> None:
        super().__init__(f"land error: {reason}")
        self.reason = reason


class LandConflict(StackError):
    def __init__(self, conflicting_paths: list[str]) -> None:
        files = ", ".join(conflicting_paths) if conflicting_paths else "<unknown>"
        super().__init__(f"branchless move conflicted on: {files}")
        self.conflicting_paths = conflicting_paths
