import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .crypto import decrypt_secret


class SmtpNotConfigured(Exception):
    """Raised when a send is attempted without a host/from-address set."""


def send_email(settings, to_address: str, subject: str, body: str, html: str | None = None) -> None:
    """Send one email (plain text, plus an HTML alternative when `html` is given) using the SMTP settings stored on an
    AppSettings row. Raises on any failure (unreachable host, auth
    rejected, etc.) -- callers decide how to report it, e.g. the
    settings.test-email endpoint catches this into a {"status": "failed"}
    response instead of a 500."""
    if not settings.smtp_host or not settings.smtp_from_address:
        raise SmtpNotConfigured("SMTP host and from-address must be configured first")

    if html:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body, "plain"))  # clients that cannot show HTML fall back to this
        msg.attach(MIMEText(html, "html"))
    else:
        msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from_address
    msg["To"] = to_address

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        if settings.smtp_use_tls:
            smtp.starttls()
        if settings.smtp_username and settings.smtp_encrypted_password:
            smtp.login(settings.smtp_username, decrypt_secret(settings.smtp_encrypted_password))
        smtp.sendmail(settings.smtp_from_address, [to_address], msg.as_string())
