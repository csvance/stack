"""Preflight ordering: each failure exits the documented code."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import fakeredis
import pytest

from stack_bot import startup
from stack_bot.config import (
    AdoConfig,
    BotConfig,
    IdentityConfig,
    OperationsConfig,
    ProjectConfig,
    RedisConfig,
    ServerConfig,
    SmtpConfig,
    WebhooksConfig,
    WorkspaceConfig,
)
from stack_core.ado.client import AdoClient


def _config(*, projects=("myproj",)) -> BotConfig:
    return BotConfig(
        ado=AdoConfig(organization_url="https://dev.azure.com/myorg", pat="x"),
        projects=[ProjectConfig(name=p) for p in projects],
        redis=RedisConfig(),
        branch_suffix="-stacked-",
        workspaces=WorkspaceConfig(base_dir="/tmp/stackbot-test"),
        server=ServerConfig(),
        operations=OperationsConfig(),
        smtp=SmtpConfig(host="smtp.example.com", from_address="b@x", to_addresses=["a@x"]),
        webhooks=WebhooksConfig(bot_url="https://stackbot.example.com"),
        identity=IdentityConfig(),
    )


def _good_smtp_probe(config):
    return None


def _good_reconciler(config, ado_client):
    return None


def test_smtp_failure_exits_2():
    config = _config()
    redis_client = fakeredis.FakeRedis(decode_responses=False)
    ado_client = MagicMock(spec=AdoClient)

    def bad_smtp(_config):
        raise OSError("smtp down")

    with pytest.raises(SystemExit) as exc:
        startup.run_preflight(
            config, redis_client, ado_client,
            smtp_probe=bad_smtp,
            webhook_reconciler=_good_reconciler,
        )
    assert exc.value.code == startup.EXIT_SMTP_UNAVAILABLE


def test_ado_failure_exits_3():
    config = _config()
    redis_client = fakeredis.FakeRedis(decode_responses=False)
    ado_client = MagicMock(spec=AdoClient)
    ado_client.get.side_effect = OSError("ado unreachable")

    with patch("stack_bot.startup.alerts.send"), \
         pytest.raises(SystemExit) as exc:
        startup.run_preflight(
            config, redis_client, ado_client,
            smtp_probe=_good_smtp_probe,
            webhook_reconciler=_good_reconciler,
        )
    assert exc.value.code == startup.EXIT_ADO_UNREACHABLE


def test_redis_failure_exits_4():
    config = _config()
    redis_client = MagicMock()
    redis_client.ping.side_effect = OSError("redis down")
    ado_client = MagicMock(spec=AdoClient)
    ado_client.get.return_value = MagicMock(status_code=200)

    with patch("stack_bot.startup.alerts.send"), \
         pytest.raises(SystemExit) as exc:
        startup.run_preflight(
            config, redis_client, ado_client,
            smtp_probe=_good_smtp_probe,
            webhook_reconciler=_good_reconciler,
        )
    assert exc.value.code == startup.EXIT_REDIS_UNREACHABLE


def test_webhook_reconcile_failure_exits_5():
    config = _config()
    redis_client = fakeredis.FakeRedis(decode_responses=False)
    ado_client = MagicMock(spec=AdoClient)
    ado_client.get.return_value = MagicMock(status_code=200)

    def bad_reconcile(_config, _client):
        raise RuntimeError("ado service hooks API broken")

    with patch("stack_bot.startup.alerts.send"), \
         pytest.raises(SystemExit) as exc:
        startup.run_preflight(
            config, redis_client, ado_client,
            smtp_probe=_good_smtp_probe,
            webhook_reconciler=bad_reconcile,
        )
    assert exc.value.code == startup.EXIT_WEBHOOK_RECONCILE


def test_success_path_returns_cleanly():
    config = _config()
    redis_client = fakeredis.FakeRedis(decode_responses=False)
    ado_client = MagicMock(spec=AdoClient)
    ado_client.get.return_value = MagicMock(status_code=200)

    # Should not raise.
    startup.run_preflight(
        config, redis_client, ado_client,
        smtp_probe=_good_smtp_probe,
        webhook_reconciler=_good_reconciler,
    )


def test_reconcile_skipped_when_disabled():
    config = _config().model_copy(
        update={"webhooks": WebhooksConfig(bot_url="x", managed_by_bot=False)}
    )
    redis_client = fakeredis.FakeRedis(decode_responses=False)
    ado_client = MagicMock(spec=AdoClient)
    ado_client.get.return_value = MagicMock(status_code=200)

    called: list = []

    def reconciler(c, a):
        called.append("called")

    startup.run_preflight(
        config, redis_client, ado_client,
        smtp_probe=_good_smtp_probe,
        webhook_reconciler=reconciler,
    )
    assert called == []
