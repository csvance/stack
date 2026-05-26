"""Config loading: file lookup, env interpolation, validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from stack_core.config import load_config


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(body)
    return path


def test_minimal_config(tmp_path, monkeypatch):
    monkeypatch.delenv("STACK_REDIS_PASSWORD", raising=False)
    path = _write(
        tmp_path,
        """
ado:
  organization_url: https://dev.azure.com/o
  pat: secret
""",
    )
    config = load_config(path)
    assert config.ado.organization_url == "https://dev.azure.com/o"
    assert config.ado.pat == "secret"
    assert config.redis.host == "localhost"
    assert config.redis.port == 6379
    assert config.branch_suffix == "-stacked-"


def test_env_interpolation(tmp_path, monkeypatch):
    monkeypatch.setenv("STACK_ADO_PAT", "from-env")
    path = _write(
        tmp_path,
        """
ado:
  organization_url: https://dev.azure.com/o
  pat: ${oc.env:STACK_ADO_PAT}
""",
    )
    config = load_config(path)
    assert config.ado.pat == "from-env"


def test_missing_required_field_raises(tmp_path):
    path = _write(tmp_path, "ado:\n  organization_url: https://x\n")
    with pytest.raises(ValidationError):
        load_config(path)


def test_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.yaml")


def test_extra_fields_rejected(tmp_path):
    path = _write(
        tmp_path,
        """
ado:
  organization_url: https://dev.azure.com/o
  pat: x
unknown_top: 1
""",
    )
    with pytest.raises(ValidationError):
        load_config(path)


def test_stack_config_env_override(tmp_path, monkeypatch):
    path = _write(
        tmp_path,
        """
ado:
  organization_url: https://dev.azure.com/o
  pat: x
""",
    )
    monkeypatch.setenv("STACK_CONFIG", str(path))
    config = load_config()
    assert config.ado.pat == "x"
