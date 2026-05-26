"""SMTP-based alert dispatch keyed by category.

Best-effort: SMTP failures are logged and never propagate, so an unreachable
mail server can't cascade into bot failures. Categories indicate the alert
class and let the operator filter mail rules.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from enum import StrEnum

from stack_bot.config import SmtpConfig

logger = logging.getLogger(__name__)


class AlertCategory(StrEnum):
    STARTUP_FAILURE = "STARTUP_FAILURE"
    REDIS_UNAVAILABLE = "REDIS_UNAVAILABLE"
    UNHANDLED_EXCEPTION = "UNHANDLED_EXCEPTION"
    ADO_PERSISTENT_FAILURE = "ADO_PERSISTENT_FAILURE"
    WEBHOOK_RECONCILIATION_FAILURE = "WEBHOOK_RECONCILIATION_FAILURE"


def send(category: AlertCategory, subject: str, body: str, config: SmtpConfig) -> None:
    """Send an SMTP alert. Best-effort: failures are logged, not raised."""
    message = EmailMessage()
    message["From"] = config.from_address
    message["To"] = ", ".join(config.to_addresses)
    message["Subject"] = f"[stackbot {category}] {subject}"
    message.set_content(body)

    try:
        smtp_cls = smtplib.SMTP_SSL if not config.use_tls else smtplib.SMTP
        with smtp_cls(config.host, config.port) as smtp:
            if config.use_tls:
                smtp.starttls()
            if config.username and config.password:
                smtp.login(config.username, config.password)
            smtp.send_message(message)
    except Exception as exc:
        logger.exception("SMTP alert failed: category=%s subject=%s reason=%r", category, subject, exc)
