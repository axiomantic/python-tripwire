# Email Service Example

Demonstrates tripwire's SMTP plugin with full state machine assertions.

The application module (`app.py`) sends a welcome email through an SMTP
server using `smtplib`. The test (`test_app.py`) scripts an entire SMTP
session (connect, ehlo, starttls, login, send_message, quit) and verifies
each step with `tripwire.smtp_mock` assertion helpers.

Run: `python -m pytest examples/email_service/ -v`
