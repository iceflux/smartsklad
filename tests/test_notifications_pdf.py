import smtplib
from email import message_from_bytes

import pytest

from app.config import settings
from app.modules.notifications.email import send_email


class _CapturingSMTP:
    sent = []

    def __init__(self, host, port, *, timeout=None):
        pass
    def starttls(self): pass
    def login(self, user, pw): pass
    def send_message(self, msg): self.sent.append(msg)
    def __enter__(self): return self
    def __exit__(self, *e): pass


@pytest.fixture
def capture(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "localhost")
    monkeypatch.setattr(smtplib, "SMTP", _CapturingSMTP)
    _CapturingSMTP.sent = []
    return _CapturingSMTP.sent


def test_send_email_with_attachment_adds_pdf(capture):
    pdf = b"%PDF-1.7 fake order pdf content"
    send_email("buyer@example.com", "Заказ № 1", "тело", attachment=pdf, filename="order-1.pdf")
    assert len(capture) == 1
    msg = message_from_bytes(capture[0].as_bytes())
    parts = [p for p in msg.walk() if p.get_content_disposition() == "attachment"]
    assert len(parts) == 1
    part = parts[0]
    assert part.get_filename() == "order-1.pdf"
    assert part.get_content_type() == "application/pdf"
    assert part.get_payload(decode=True).startswith(b"%PDF")


def test_send_email_without_attachment_has_no_attachment(capture):
    send_email("buyer@example.com", "s", "body")
    msg = message_from_bytes(capture[0].as_bytes())
    assert not [p for p in msg.walk() if p.get_content_disposition() == "attachment"]
    assert b"body" in capture[0].as_bytes()  # текст тела сохранён