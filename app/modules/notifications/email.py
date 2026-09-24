import smtplib
from email.message import EmailMessage

from app.config import settings


def send_email(to: str, subject: str, body: str, *, attachment: bytes | None = None,
               filename: str = "document.pdf", attachment_subtype: str = "pdf") -> None:
    if not settings.smtp_host:
        return
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if attachment is not None:
        msg.add_attachment(attachment, maintype="application",
                           subtype=attachment_subtype, filename=filename)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)