"""User configuration loader.

Reads ``~/.stack/config.yaml`` (overridable via the ``STACK_CONFIG`` env var) via
OmegaConf with environment-variable interpolation, then validates into Pydantic
settings models. Missing required fields fail loudly at command start.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from omegaconf import OmegaConf
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_CONFIG_PATH = "~/.stack/config.yaml"


class RedisConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    host: str = "localhost"
    port: int = 6379
    password: str | None = None
    db: int = 0
    key_prefix: str = "stack"


class AdoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_url: str = Field(min_length=1)
    pat: str = Field(min_length=1)


class StackConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    redis: RedisConfig = Field(default_factory=RedisConfig)
    ado: AdoConfig
    branch_suffix: str = "-stacked-"


def config_path() -> Path:
    """Return the resolved config file path."""
    raw = os.environ.get("STACK_CONFIG", DEFAULT_CONFIG_PATH)
    return Path(raw).expanduser()


def load_config(path: Path | None = None) -> StackConfig:
    """Load and validate the config from the given path (default: ``config_path()``)."""
    resolved = path if path is not None else config_path()
    if not resolved.exists():
        raise FileNotFoundError(
            f"config file not found: {resolved} "
            "(set STACK_CONFIG or create ~/.stack/config.yaml)"
        )
    raw = OmegaConf.load(str(resolved))
    container: Any = OmegaConf.to_container(raw, resolve=True)
    if not isinstance(container, dict):
        raise ValueError(f"config root must be a mapping, got {type(container).__name__}")
    return StackConfig.model_validate(container)
