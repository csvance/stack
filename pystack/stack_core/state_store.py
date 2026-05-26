"""Redis-backed transactional manifest CRUD.

The state store is the source of truth for all stacks the system manages. Every
mutation goes through :func:`update_manifest`, which uses Redis WATCH/MULTI/EXEC
to provide optimistic concurrency control and writes an audit log entry in the
same transaction.

Key structure (the ``key_prefix`` defaults to ``"stack"`` and is configurable):

- ``<key_prefix>:<project>:manifest:<prefix>`` — manifest JSON (string)
- ``<key_prefix>:<project>:audit:<prefix>`` — audit log (list, newest-first via LPUSH)
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from pydantic import ValidationError
from redis import Redis
from redis.exceptions import (
    ConnectionError as RedisConnectionError,
)
from redis.exceptions import (
    TimeoutError as RedisTimeoutError,
)
from redis.exceptions import (
    WatchError,
)

from stack_core.exceptions import (
    ManifestValidationError,
    RedisUnavailable,
    RetryExhausted,
)
from stack_core.types import AuditEntry, Manifest

DEFAULT_KEY_PREFIX = "stack"
DEFAULT_AUDIT_CAP = 1000
DEFAULT_AUDIT_TTL_SECONDS = 30 * 24 * 60 * 60
DEFAULT_MAX_RETRIES = 5


@dataclass(frozen=True)
class StateStoreConfig:
    key_prefix: str = DEFAULT_KEY_PREFIX
    audit_cap: int = DEFAULT_AUDIT_CAP
    audit_ttl_seconds: int = DEFAULT_AUDIT_TTL_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES


_DEFAULT_CONFIG = StateStoreConfig()


def _manifest_key(config: StateStoreConfig, project: str, prefix: str) -> str:
    return f"{config.key_prefix}:{project}:manifest:{prefix}"


def _audit_key(config: StateStoreConfig, project: str, prefix: str) -> str:
    return f"{config.key_prefix}:{project}:audit:{prefix}"


def _manifest_key_pattern(config: StateStoreConfig, project: str) -> str:
    return f"{config.key_prefix}:{project}:manifest:*"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def get_manifest(
    client: Redis,
    project: str,
    prefix: str,
    *,
    config: StateStoreConfig = _DEFAULT_CONFIG,
) -> Manifest | None:
    try:
        raw = cast("bytes | str | None", client.get(_manifest_key(config, project, prefix)))
    except (RedisConnectionError, RedisTimeoutError) as exc:
        raise RedisUnavailable(str(exc)) from exc
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return _parse_manifest(raw)


def update_manifest(
    client: Redis,
    project: str,
    prefix: str,
    modify_fn: Callable[[Manifest | None], Manifest],
    audit_message: str,
    actor: str,
    *,
    operation: str = "update",
    event_type: str = "manifest_updated",
    details: dict[str, object] | None = None,
    config: StateStoreConfig = _DEFAULT_CONFIG,
    clock: Callable[[], datetime] = _utcnow,
) -> Manifest:
    """Atomically update a manifest using WATCH/MULTI/EXEC.

    ``modify_fn`` receives the current manifest (``None`` if creating new) and
    returns the desired manifest. If the returned value equals the current
    manifest, the call is a no-op (no write, no audit entry).
    """
    manifest_key = _manifest_key(config, project, prefix)
    audit_key = _audit_key(config, project, prefix)

    for _attempt in range(config.max_retries):
        try:
            with client.pipeline(transaction=True) as pipe:
                pipe.watch(manifest_key)  # type: ignore[no-untyped-call]
                raw = cast("bytes | str | None", pipe.get(manifest_key))
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                current = _parse_manifest(raw) if raw is not None else None

                desired = modify_fn(current)
                if desired == current:
                    pipe.unwatch()
                    return desired

                audit_entry = AuditEntry(
                    timestamp=clock(),
                    event_type=event_type,
                    operation=operation,
                    actor=actor,
                    details={"message": audit_message, **(details or {})},
                )

                pipe.multi()
                pipe.set(manifest_key, desired.model_dump_json())
                pipe.lpush(audit_key, audit_entry.model_dump_json())
                pipe.ltrim(audit_key, 0, config.audit_cap - 1)
                pipe.expire(audit_key, config.audit_ttl_seconds)
                pipe.execute()
            return desired
        except WatchError:
            continue
        except (RedisConnectionError, RedisTimeoutError) as exc:
            raise RedisUnavailable(str(exc)) from exc

    raise RetryExhausted(config.max_retries)


def delete_manifest(
    client: Redis,
    project: str,
    prefix: str,
    audit_message: str,
    actor: str,
    *,
    operation: str = "delete",
    event_type: str = "manifest_deleted",
    details: dict[str, object] | None = None,
    config: StateStoreConfig = _DEFAULT_CONFIG,
    clock: Callable[[], datetime] = _utcnow,
) -> None:
    manifest_key = _manifest_key(config, project, prefix)
    audit_key = _audit_key(config, project, prefix)
    audit_entry = AuditEntry(
        timestamp=clock(),
        event_type=event_type,
        operation=operation,
        actor=actor,
        details={"message": audit_message, **(details or {})},
    )

    try:
        with client.pipeline(transaction=True) as pipe:
            pipe.multi()
            pipe.delete(manifest_key)
            pipe.lpush(audit_key, audit_entry.model_dump_json())
            pipe.ltrim(audit_key, 0, config.audit_cap - 1)
            pipe.expire(audit_key, config.audit_ttl_seconds)
            pipe.execute()
    except (RedisConnectionError, RedisTimeoutError) as exc:
        raise RedisUnavailable(str(exc)) from exc


def list_manifests(
    client: Redis,
    project: str,
    *,
    config: StateStoreConfig = _DEFAULT_CONFIG,
) -> list[str]:
    pattern = _manifest_key_pattern(config, project)
    suffix_offset = len(f"{config.key_prefix}:{project}:manifest:")
    try:
        keys = cast("Iterable[bytes | str]", client.scan_iter(match=pattern, count=200))
        result: list[str] = []
        for key in keys:
            text = key.decode("utf-8") if isinstance(key, bytes) else key
            result.append(text[suffix_offset:])
        result.sort()
        return result
    except (RedisConnectionError, RedisTimeoutError) as exc:
        raise RedisUnavailable(str(exc)) from exc


def get_audit_log(
    client: Redis,
    project: str,
    prefix: str,
    limit: int = 100,
    *,
    config: StateStoreConfig = _DEFAULT_CONFIG,
) -> list[AuditEntry]:
    if limit <= 0:
        return []
    audit_key = _audit_key(config, project, prefix)
    try:
        raws = cast("list[bytes | str]", client.lrange(audit_key, 0, limit - 1))
    except (RedisConnectionError, RedisTimeoutError) as exc:
        raise RedisUnavailable(str(exc)) from exc
    entries: list[AuditEntry] = []
    for raw in raws:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        entries.append(AuditEntry.model_validate_json(text))
    return entries


def _parse_manifest(raw: str) -> Manifest:
    try:
        return Manifest.model_validate_json(raw)
    except ValidationError as exc:
        raise ManifestValidationError("manifest", str(exc)) from exc


# ---------------------------------------------------------------------------
# ADO remote URL parsing
# ---------------------------------------------------------------------------

_SSH_RE = re.compile(r"^git@ssh\.dev\.azure\.com:v3/(?P<org>[^/]+)/(?P<project>[^/]+)/(?P<repo>[^/?#]+)")
_HTTPS_RE = re.compile(r"^(?P<left>https?://[^\s]+)/_git/(?P<repo>[^/?#]+)")
_USERINFO_RE = re.compile(r"^(https?://)[^/]*@(.*)$")


@dataclass(frozen=True)
class AdoRemote:
    """Parsed Azure DevOps remote URL.

    Mirrors the bash CLI's ``az::resolve_repo`` (lib/az_helpers.sh:40-72) and
    supports the same set of URL shapes: SSH, hosted HTTPS, ``.visualstudio.com``
    legacy, and Azure DevOps Server on-prem.
    """

    org_url: str
    project: str
    repo: str


def parse_ado_remote(url: str) -> AdoRemote:
    """Parse an ADO remote URL into its constituent parts. Raises ``ValueError``."""
    ssh_match = _SSH_RE.match(url)
    if ssh_match:
        return AdoRemote(
            org_url=f"https://dev.azure.com/{ssh_match.group('org')}",
            project=ssh_match.group("project"),
            repo=_strip_dot_git(ssh_match.group("repo")),
        )
    https_match = _HTTPS_RE.match(url)
    if https_match:
        left = https_match.group("left")
        repo = _strip_dot_git(https_match.group("repo"))
        project = left.rsplit("/", 1)[-1]
        org_url = left.rsplit("/", 1)[0]
        userinfo_match = _USERINFO_RE.match(org_url)
        if userinfo_match:
            org_url = userinfo_match.group(1) + userinfo_match.group(2)
        return AdoRemote(org_url=org_url, project=project, repo=repo)
    raise ValueError(f"could not parse Azure DevOps URL: {url!r}")


def resolve_project(remote_url: str) -> str:
    """Return the project segment of an ADO remote URL."""
    return parse_ado_remote(remote_url).project


def _strip_dot_git(repo: str) -> str:
    return repo[: -len(".git")] if repo.endswith(".git") else repo
