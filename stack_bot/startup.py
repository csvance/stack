"""Preflight checks: config, SMTP, ADO, Redis, webhook reconciliation.

Each check that fails sends an SMTP alert (when SMTP is available) and exits
with a category-specific code. The order matters: SMTP must work before the
later checks can alert through it.

Webhook reconciliation is best-effort: it tries to ensure the bot's URL is
registered for ``git.pullrequest.merged`` on each configured project. If
``managed_by_bot`` is False, the reconciliation step is skipped.
"""

from __future__ import annotations

import logging
import smtplib
import sys
from typing import Protocol

from redis import Redis

from stack_bot import alerts
from stack_bot.config import BotConfig
from stack_core.ado.client import AdoClient

logger = logging.getLogger(__name__)

EXIT_CONFIG_INVALID = 1
EXIT_SMTP_UNAVAILABLE = 2
EXIT_ADO_UNREACHABLE = 3
EXIT_REDIS_UNREACHABLE = 4
EXIT_WEBHOOK_RECONCILE = 5


class SmtpProbe(Protocol):
    def __call__(self, config: BotConfig) -> None: ...


class WebhookReconciler(Protocol):
    def __call__(self, config: BotConfig, ado_client: AdoClient) -> None: ...


def run_preflight(
    config: BotConfig,
    redis_client: Redis,
    ado_client: AdoClient,
    *,
    smtp_probe: SmtpProbe | None = None,
    webhook_reconciler: WebhookReconciler | None = None,
) -> None:
    """Run preflight steps 2-5 (step 1 is loading the config itself).

    ``smtp_probe`` and ``webhook_reconciler`` are injectable for tests; the
    defaults are real implementations.
    """
    probe: SmtpProbe = smtp_probe or _default_smtp_probe
    reconciler: WebhookReconciler = webhook_reconciler or _default_webhook_reconciler

    try:
        probe(config)
    except Exception as exc:
        logger.error("SMTP preflight failed: %r", exc)
        sys.exit(EXIT_SMTP_UNAVAILABLE)

    try:
        ado_client.get("/_apis/connectionData", **{"api-version": "7.1"})
    except Exception as exc:
        logger.error("ADO preflight failed: %r", exc)
        alerts.send(
            alerts.AlertCategory.STARTUP_FAILURE,
            "ADO unreachable",
            f"GET /_apis/connectionData failed: {exc!r}",
            config.smtp,
        )
        sys.exit(EXIT_ADO_UNREACHABLE)

    try:
        redis_client.ping()
    except Exception as exc:
        logger.error("Redis preflight failed: %r", exc)
        alerts.send(
            alerts.AlertCategory.STARTUP_FAILURE,
            "Redis unreachable",
            f"PING failed: {exc!r}",
            config.smtp,
        )
        sys.exit(EXIT_REDIS_UNREACHABLE)

    if config.webhooks.managed_by_bot and config.webhooks.reconcile_on_startup:
        try:
            reconciler(config, ado_client)
        except Exception as exc:
            logger.error("Webhook reconciliation failed: %r", exc)
            alerts.send(
                alerts.AlertCategory.WEBHOOK_RECONCILIATION_FAILURE,
                "Webhook reconciliation failed",
                f"{exc!r}",
                config.smtp,
            )
            sys.exit(EXIT_WEBHOOK_RECONCILE)


def _default_smtp_probe(config: BotConfig) -> None:
    smtp_cls = smtplib.SMTP_SSL if not config.smtp.use_tls else smtplib.SMTP
    with smtp_cls(config.smtp.host, config.smtp.port) as smtp:
        if config.smtp.use_tls:
            smtp.starttls()
        if config.smtp.username and config.smtp.password:
            smtp.login(config.smtp.username, config.smtp.password)
        smtp.noop()


def _default_webhook_reconciler(config: BotConfig, ado_client: AdoClient) -> None:
    """Stub: log the desired set without mutating ADO service hooks.

    Real reconciliation against the Service Hooks publishers/subscriptions
    endpoints lands in a follow-up. For now, log the intended state so an
    operator can manually verify, and raise if no projects are configured.
    """
    if not config.projects:
        raise ValueError("no projects configured; cannot reconcile webhooks")
    target_url = config.webhooks.bot_url.rstrip("/") + "/webhooks/ado"
    for project in config.projects:
        logger.info(
            "webhook reconcile (stub): project=%s url=%s event=git.pullrequest.merged",
            project.name, target_url,
        )
