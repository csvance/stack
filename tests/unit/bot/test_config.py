"""BotConfig loading: env interpolation, validation, missing fields."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from stack_bot.config import load_config

MIN_CONFIG = """
ado:
  organization_url: https://dev.azure.com/o
  pat: secret
projects:
  - name: myproj
smtp:
  host: smtp.example.com
  from_address: bot@example.com
  to_addresses: [ops@example.com]
webhooks:
  bot_url: https://stackbot.example.com
"""


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(body)
    return path


def test_minimal_config_loads(tmp_path):
    config = load_config(_write(tmp_path, MIN_CONFIG))
    assert config.ado.pat == "secret"
    assert config.projects[0].name == "myproj"
    assert config.smtp.host == "smtp.example.com"
    assert config.webhooks.bot_url == "https://stackbot.example.com"
    assert config.branch_suffix == "-stacked-"
    assert config.redis.idempotency_ttl_days == 7


def test_env_interpolation(tmp_path, monkeypatch):
    monkeypatch.setenv("STACKBOT_ADO_PAT", "from-env")
    body = MIN_CONFIG.replace("pat: secret", "pat: ${oc.env:STACKBOT_ADO_PAT}")
    config = load_config(_write(tmp_path, body))
    assert config.ado.pat == "from-env"


def test_missing_required_field_rejected(tmp_path):
    body = """
ado:
  organization_url: https://x
  pat: x
projects: []
smtp:
  host: x
  from_address: x@x
  to_addresses: [x@x]
webhooks:
  bot_url: https://x
"""
    with pytest.raises(ValidationError):
        load_config(_write(tmp_path, body))


def test_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.yaml")


def test_extra_field_rejected(tmp_path):
    body = MIN_CONFIG + "\nunknown_top: 1\n"
    with pytest.raises(ValidationError):
        load_config(_write(tmp_path, body))


def test_env_path_override(tmp_path, monkeypatch):
    path = _write(tmp_path, MIN_CONFIG)
    monkeypatch.setenv("STACKBOT_CONFIG", str(path))
    config = load_config()
    assert config.projects[0].name == "myproj"
