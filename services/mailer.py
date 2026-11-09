"""Email: password resets and notifications.

With POR_SMTP_HOST set, mail goes out over SMTP (from the worker when Redis jobs are on). Without it, each message
is written to POR_MAIL_DIR as an .eml file and logged, so local development needs no mail server.
"""

from __future__ import annotations

import logging
import smtplib
import time
import uuid
from email.message import EmailMessage
from pathlib import Path

from services import jobs, metrics
from services.config import get_settings

log = logging.getLogger("por.mail")

_sent: list[EmailMessage] = []  # recent messages, for tests and debugging


def outbox() -> list[EmailMessage]:
    return _sent


def send(to: str, subject: str, body: str) -> None:
    """Queue an email (sent inline when jobs run inline)."""
    jobs.enqueue("send_email", to=to, subject=subject, body=body)


@jobs.job("send_email")
def deliver(to: str, subject: str, body: str) -> None:
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = s.mail_from
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = f"<{uuid.uuid4().hex}@proof-of-review>"
    msg.set_content(body)
    _sent.append(msg)
    del _sent[:-200]
    if s.smtp_host:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
            if s.smtp_starttls:
                smtp.starttls()
            if s.smtp_username:
                smtp.login(s.smtp_username, s.smtp_password or "")
            smtp.send_message(msg)
        metrics.inc("por_emails_total", {"via": "smtp"})
        log.info("email sent to %s: %s", to, subject)
        return
    folder = Path(s.mail_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{int(time.time())}-{uuid.uuid4().hex[:8]}.eml"
    path.write_bytes(bytes(msg))
    metrics.inc("por_emails_total", {"via": "outbox"})
    log.info("email to %s written to %s (no POR_SMTP_HOST set): %s", to, path, subject)
