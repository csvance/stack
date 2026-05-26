"""Bot configuration loader.

Reads ``/etc/stackbot/config.yaml`` (overridable via ``STACKBOT_CONFIG``) via
OmegaConf with environment-variable interpolation, then validates into a
Pydantic ``BotConfig``. Missing required fields cause a clear error at startup.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from omegaconf import OmegaConf
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_CONFIG_PATH = "/etc/stackbot/config.yaml"


class AdoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_url: str = Field(min_length=1)
    pat: str = Field(min_length=1)


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)


class RedisConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    host: str = "localhost"
    port: int = 6379
    password: str | None = None
    db: int = 0
    key_prefix: str = "stack"
    idempotency_ttl_days: int = 7


class WorkspaceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_dir: str = "/var/lib/stackbot/workspaces"


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    host: str = "0.0.0.0"
    port: int = 8080


class OperationsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    background_task_timeout_seconds: int = 600
    shutdown_drain_timeout_seconds: int = 60


class SmtpConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    host: str = Field(min_length=1)
    port: int = 587
    username: str | None = None
    password: str | None = None
    use_tls: bool = True
    from_address: str = Field(min_length=1)
    to_addresses: list[str] = Field(min_length=1)


class WebhooksConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    managed_by_bot: bool = True
    bot_url: str = Field(min_length=1)
    reconcile_on_startup: bool = True


class IdentityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = "StackBot"
    email: str = "stackbot@example.com"


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    level: str = "INFO"
    format: str = "json"


class BotConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ado: AdoConfig
    projects: list[ProjectConfig] = Field(min_length=1)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    branch_suffix: str = "-stacked-"
    workspaces: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    operations: OperationsConfig = Field(default_factory=OperationsConfig)
    smtp: SmtpConfig
    webhooks: WebhooksConfig
    identity: IdentityConfig = Field(default_factory=IdentityConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


def config_path() -> Path:
    return Path(os.environ.get("STACKBOT_CONFIG", DEFAULT_CONFIG_PATH))


def load_config(path: Path | None = None) -> BotConfig:
    resolved = path if path is not None else config_path()
    if not resolved.exists():
        raise FileNotFoundError(
            f"bot config file not found: {resolved} "
            "(set STACKBOT_CONFIG or place at /etc/stackbot/config.yaml)"
        )
    raw = OmegaConf.load(str(resolved))
    container: Any = OmegaConf.to_container(raw, resolve=True)
    if not isinstance(container, dict):
        raise ValueError(f"config root must be a mapping, got {type(container).__name__}")
    return BotConfig.model_validate(container)
