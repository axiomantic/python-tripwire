# SmtpPlugin Guide

`SmtpPlugin` replaces `smtplib.SMTP` with a fake class that routes all SMTP operations through a session script. It is included in core tripwire -- no extra required.

## Setup

In pytest, access `SmtpPlugin` through the `tripwire.smtp_mock` proxy. It auto-creates the plugin for the current test on first use:

```python
import tripwire

def test_send_email():
    (tripwire.smtp_mock
        .new_session()
        .expect("connect",  returns=None)
        .expect("ehlo",     returns=(250, b"OK"))
        .expect("sendmail", returns={})
        .expect("quit",     returns=(221, b"Bye")))

    with tripwire:
        import smtplib
        smtp = smtplib.SMTP("mail.example.com", 25)
        smtp.ehlo()
        smtp.sendmail("from@example.com", ["to@example.com"], "Subject: hi\r\n\r\nhello")
        smtp.quit()

    tripwire.smtp_mock.assert_connect(host="mail.example.com", port=25)
    tripwire.smtp_mock.assert_ehlo(name="")
    tripwire.smtp_mock.assert_sendmail(
        from_addr="from@example.com",
        to_addrs=["to@example.com"],
        msg="Subject: hi\r\n\r\nhello",
    )
    tripwire.smtp_mock.assert_quit()
```

For manual use outside pytest, construct `SmtpPlugin` explicitly:

```python
from tripwire import StrictVerifier
from tripwire.plugins.smtp_plugin import SmtpPlugin

verifier = StrictVerifier()
smtp = SmtpPlugin(verifier)
```

Each verifier may have at most one `SmtpPlugin`. A second `SmtpPlugin(verifier)` raises `ValueError`.

## State machine

```
disconnected --connect--> connected --ehlo/helo--> greeted
greeted --starttls--> greeted          (optional, self-loop)
greeted --login--> authenticated       (optional)
greeted/authenticated/sending --sendmail/send_message--> sending
sending/greeted/authenticated --quit--> closed
```

The `connect` step fires automatically during `smtplib.SMTP(host, port)` construction. After that, the SMTP protocol requires a greeting (`ehlo` or `helo`) before any mail operations. `starttls` and `login` are optional intermediate steps.

## Scripting a session

Use `new_session()` to create a `SessionHandle` and chain `.expect()` calls:

```python
(tripwire.smtp_mock
    .new_session()
    .expect("connect",  returns=None)
    .expect("ehlo",     returns=(250, b"OK"))
    .expect("starttls", returns=(220, b"Ready"))
    .expect("login",    returns=(235, b"Auth OK"))
    .expect("sendmail", returns={})
    .expect("quit",     returns=(221, b"Bye")))
```

### `expect()` parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `method` | `str` | required | Step name (see below) |
| `returns` | `Any` | required | Value returned by the step (see below) |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused step causes `UnusedMocksError` at teardown |

### Return values by step

| Step | `returns` type | Description |
|---|---|---|
| `connect` | `None` | Connection is established implicitly |
| `ehlo` | `tuple[int, bytes]` | SMTP response code and message |
| `helo` | `tuple[int, bytes]` | SMTP response code and message |
| `starttls` | `tuple[int, bytes]` | SMTP response code and message |
| `login` | `tuple[int, bytes]` | SMTP response code and message |
| `sendmail` | `dict[str, tuple[int, bytes]]` | Empty dict for success; maps refused recipients to error codes |
| `send_message` | `dict[str, tuple[int, bytes]]` | Same as `sendmail` |
| `quit` | `tuple[int, bytes]` | SMTP response code and message |

## Asserting interactions

Each step records an interaction on the timeline. Use the typed assertion helpers on `tripwire.smtp_mock`:

### `assert_connect(*, host, port)`

```python
tripwire.smtp_mock.assert_connect(host="mail.example.com", port=587)
```

### `assert_ehlo(*, name)`

```python
tripwire.smtp_mock.assert_ehlo(name="")
```

### `assert_helo(*, name)`

```python
tripwire.smtp_mock.assert_helo(name="")
```

### `assert_starttls()`

No fields are required.

```python
tripwire.smtp_mock.assert_starttls()
```

### `assert_login(*, user, password)`

```python
tripwire.smtp_mock.assert_login(user="user@example.com", password="s3cret")
```

### `assert_sendmail(*, from_addr, to_addrs, msg)`

```python
tripwire.smtp_mock.assert_sendmail(
    from_addr="from@example.com",
    to_addrs=["to@example.com"],
    msg="Subject: hello\r\n\r\nhello",
)
```

### `assert_send_message(*, msg)`

```python
tripwire.smtp_mock.assert_send_message(msg=email_message_object)
```

### `assert_quit()`

No fields are required.

```python
tripwire.smtp_mock.assert_quit()
```

## Full authenticated flow

The full flow with TLS and authentication:

```python
import smtplib
import tripwire

def send_secure_email(host, port, user, password, from_addr, to_addrs, body):
    smtp = smtplib.SMTP(host, port)
    smtp.ehlo()
    smtp.starttls()
    smtp.login(user, password)
    smtp.sendmail(from_addr, to_addrs, body)
    smtp.quit()

def test_send_secure_email():
    (tripwire.smtp_mock
        .new_session()
        .expect("connect",  returns=None)
        .expect("ehlo",     returns=(250, b"OK"))
        .expect("starttls", returns=(220, b"Ready"))
        .expect("login",    returns=(235, b"Auth OK"))
        .expect("sendmail", returns={})
        .expect("quit",     returns=(221, b"Bye")))

    with tripwire:
        send_secure_email(
            "smtp.example.com", 587,
            "user@example.com", "s3cret",
            "user@example.com", ["recipient@example.com"],
            "Subject: Report\r\n\r\nSee attached.",
        )

    tripwire.smtp_mock.assert_connect(host="smtp.example.com", port=587)
    tripwire.smtp_mock.assert_ehlo(name="")
    tripwire.smtp_mock.assert_starttls()
    tripwire.smtp_mock.assert_login(user="user@example.com", password="s3cret")
    tripwire.smtp_mock.assert_sendmail(
        from_addr="user@example.com",
        to_addrs=["recipient@example.com"],
        msg="Subject: Report\r\n\r\nSee attached.",
    )
    tripwire.smtp_mock.assert_quit()
```

## Unauthenticated flow

Skip `starttls` and `login` for servers that do not require authentication:

```python
def test_send_unauthenticated_email():
    (tripwire.smtp_mock
        .new_session()
        .expect("connect",  returns=None)
        .expect("ehlo",     returns=(250, b"OK"))
        .expect("sendmail", returns={})
        .expect("quit",     returns=(221, b"Bye")))

    with tripwire:
        smtp = smtplib.SMTP("mail.example.com", 25)
        smtp.ehlo()
        smtp.sendmail("from@example.com", ["to@example.com"], "Subject: test\r\n\r\ntest")
        smtp.quit()

    tripwire.smtp_mock.assert_connect(host="mail.example.com", port=25)
    tripwire.smtp_mock.assert_ehlo(name="")
    tripwire.smtp_mock.assert_sendmail(
        from_addr="from@example.com",
        to_addrs=["to@example.com"],
        msg="Subject: test\r\n\r\ntest",
    )
    tripwire.smtp_mock.assert_quit()
```

The state machine validates that `sendmail` is called from `greeted` (after `ehlo` without login) or from `authenticated` (after login). Calling `sendmail` from `connected` (skipping `ehlo`) raises `InvalidStateError`.

## Using `helo` instead of `ehlo`

Some legacy servers use `HELO` instead of `EHLO`. The state machine treats both identically:

```python
def test_helo_flow():
    (tripwire.smtp_mock
        .new_session()
        .expect("connect",  returns=None)
        .expect("helo",     returns=(250, b"OK"))
        .expect("sendmail", returns={})
        .expect("quit",     returns=(221, b"Bye")))

    with tripwire:
        smtp = smtplib.SMTP("mail.example.com", 25)
        smtp.helo()
        smtp.sendmail("from@example.com", ["to@example.com"], "Subject: test\r\n\r\ntest")
        smtp.quit()

    tripwire.smtp_mock.assert_connect(host="mail.example.com", port=25)
    tripwire.smtp_mock.assert_helo(name="")
    tripwire.smtp_mock.assert_sendmail(
        from_addr="from@example.com",
        to_addrs=["to@example.com"],
        msg="Subject: test\r\n\r\ntest",
    )
    tripwire.smtp_mock.assert_quit()
```

## Using `send_message`

`send_message` accepts an `email.message.EmailMessage` object and works the same as `sendmail` from a state machine perspective:

```python
from email.message import EmailMessage

def test_send_message():
    msg = EmailMessage()
    msg["Subject"] = "Report"
    msg["From"] = "from@example.com"
    msg["To"] = "to@example.com"
    msg.set_content("See attached.")

    (tripwire.smtp_mock
        .new_session()
        .expect("connect",      returns=None)
        .expect("ehlo",         returns=(250, b"OK"))
        .expect("send_message", returns={})
        .expect("quit",         returns=(221, b"Bye")))

    with tripwire:
        smtp = smtplib.SMTP("mail.example.com", 25)
        smtp.ehlo()
        smtp.send_message(msg)
        smtp.quit()

    tripwire.smtp_mock.assert_connect(host="mail.example.com", port=25)
    tripwire.smtp_mock.assert_ehlo(name="")
    tripwire.smtp_mock.assert_send_message(msg=msg)
    tripwire.smtp_mock.assert_quit()
```
