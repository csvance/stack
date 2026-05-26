"""SMTP alerts: mocked smtplib, best-effort failures."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from stack_bot.alerts import AlertCategory, send
from stack_bot.config import SmtpConfig

SMTP_CONFIG = SmtpConfig(
    host="smtp.example.com",
    port=587,
    username="bot",
    password="pw",
    use_tls=True,
    from_address="bot@example.com",
    to_addresses=["ops@example.com"],
)


def test_send_uses_starttls_and_login_when_tls():
    with patch("stack_bot.alerts.smtplib.SMTP") as smtp_cls:
        instance = MagicMock()
        smtp_cls.return_value.__enter__.return_value = instance
        send(AlertCategory.REDIS_UNAVAILABLE, "down", "redis not reachable", SMTP_CONFIG)
    instance.starttls.assert_called_once()
    instance.login.assert_called_once_with("bot", "pw")
    instance.send_message.assert_called_once()


def test_send_swallows_smtp_failure():
    with patch("stack_bot.alerts.smtplib.SMTP", side_effect=OSError("nope")):
        # Should not raise.
        send(AlertCategory.UNHANDLED_EXCEPTION, "x", "y", SMTP_CONFIG)


def test_subject_includes_category():
    with patch("stack_bot.alerts.smtplib.SMTP") as smtp_cls:
        instance = MagicMock()
        smtp_cls.return_value.__enter__.return_value = instance
        send(AlertCategory.STARTUP_FAILURE, "startup blew up", "trace", SMTP_CONFIG)
    sent_message = instance.send_message.call_args[0][0]
    assert "STARTUP_FAILURE" in sent_message["Subject"]
