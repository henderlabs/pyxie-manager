"""Alert email delivery for findings-driven notifications.

The in-app Notification rows are written by findings.py in the same
transaction as the finding itself. Email is deliberately a separate,
best-effort step that runs AFTER that transaction commits: a slow or broken
mail server must never hold a DB transaction open or stop findings from
being recorded. Every failure here is logged and swallowed.
"""

import logging

from sqlalchemy.orm import Session

from .mail import send_email
from .models import AppSettings

log = logging.getLogger(__name__)


def send_alert_email(db: Session, subject: str, body: str) -> bool:
    """Email the configured notification recipient(s). Returns True if at
    least one message was sent. Never raises."""
    try:
        settings = db.query(AppSettings).filter(AppSettings.id == 1).one_or_none()
        if settings is None or not settings.smtp_enabled or not settings.notification_recipient:
            return False
        recipients = [a.strip() for a in settings.notification_recipient.split(",") if a.strip()]
        sent = False
        for address in recipients:
            try:
                send_email(settings, address, subject, body)
                sent = True
            except Exception as e:  # noqa: BLE001 -- one bad address must not block the rest
                log.warning("alert email to %s failed: %s", address, e)
        return sent
    except Exception as e:  # noqa: BLE001
        log.warning("alert email skipped: %s", e)
        return False
