"""Email notification service using smtplib."""

import smtplib
from email.message import EmailMessage


def send_welcome_email(to_addr: str, name: str) -> None:
    """Send a welcome email via SMTP."""
    msg = EmailMessage()
    msg["Subject"] = f"Welcome, {name}!"
    msg["From"] = "noreply@example.com"
    msg["To"] = to_addr
    msg.set_content(f"Hi {name}, welcome to our service!")

    server = smtplib.SMTP("smtp.example.com", 587)
    server.ehlo("example.com")
    server.starttls()
    server.login("noreply@example.com", "secret")
    server.send_message(msg)
    server.quit()
