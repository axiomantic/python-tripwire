"""Unit tests for SmtpPlugin."""

from __future__ import annotations

import smtplib

import pytest

import bigfoot
from bigfoot._context import _current_test_verifier
from bigfoot._errors import InvalidStateError, UnmockedInteractionError
from bigfoot._state_machine_plugin import ScriptStep
from bigfoot._verifier import StrictVerifier
from bigfoot.plugins.smtp_plugin import (
    _ORIGINAL_SMTP,
    SmtpPlugin,
    _FakeSMTP,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_verifier_with_plugin() -> tuple[StrictVerifier, SmtpPlugin]:
    """Return (verifier, plugin) with plugin registered but NOT activated."""
    v = StrictVerifier()
    p = SmtpPlugin(v)
    return v, p


def _reset_install_count() -> None:
    """Force-reset the class-level install count to 0 and restore SMTP if leaked."""
    with SmtpPlugin._install_lock:
        SmtpPlugin._install_count = 0
        if SmtpPlugin._original_smtp is not None:
            smtplib.SMTP = SmtpPlugin._original_smtp  # type: ignore[misc]
            SmtpPlugin._original_smtp = None


@pytest.fixture(autouse=True)
def clean_install_count() -> None:
    """Ensure SmtpPlugin install count starts and ends at 0 for every test."""
    _reset_install_count()
    yield
    _reset_install_count()


# ---------------------------------------------------------------------------
# Static interface: _initial_state / _transitions / _unmocked_source_id
# ---------------------------------------------------------------------------


# ESCAPE: test_initial_state
#   CLAIM: _initial_state() returns "disconnected".
#   PATH:  Direct call on plugin instance.
#   CHECK: result == "disconnected".
#   MUTATION: Returning "connected" would fail the equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_initial_state() -> None:
    v, p = _make_verifier_with_plugin()
    assert p._initial_state() == "disconnected"


# ESCAPE: test_transitions_structure
#   CLAIM: _transitions() returns the exact expected dict.
#   PATH:  Direct call on plugin instance.
#   CHECK: result == exact dict mapping method names to {from_state: to_state}.
#   MUTATION: Any missing key or wrong state name fails the equality check.
#   ESCAPE: Extra keys in the dict would also fail the equality check.
def test_transitions_structure() -> None:
    v, p = _make_verifier_with_plugin()
    assert p._transitions() == {
        "connect": {"disconnected": "connected"},
        "ehlo": {"connected": "greeted"},
        "helo": {"connected": "greeted"},
        "starttls": {"greeted": "greeted"},
        "login": {"greeted": "authenticated"},
        "sendmail": {
            "greeted": "sending",
            "authenticated": "sending",
            "sending": "sending",
        },
        "send_message": {
            "greeted": "sending",
            "authenticated": "sending",
            "sending": "sending",
        },
        "quit": {
            "sending": "closed",
            "greeted": "closed",
            "authenticated": "closed",
        },
    }


# ESCAPE: test_unmocked_source_id
#   CLAIM: _unmocked_source_id() returns "smtp:connect".
#   PATH:  Direct call on plugin instance.
#   CHECK: result == "smtp:connect".
#   MUTATION: Returning a different string fails the equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_unmocked_source_id() -> None:
    v, p = _make_verifier_with_plugin()
    assert p._unmocked_source_id() == "smtp:connect"


# ---------------------------------------------------------------------------
# Activation and reference counting
# ---------------------------------------------------------------------------


# ESCAPE: test_activate_installs_patch
#   CLAIM: After activate(), smtplib.SMTP is replaced with _FakeSMTP.
#   PATH:  activate() -> _install_count == 0 -> store original -> install _FakeSMTP.
#   CHECK: smtplib.SMTP is _FakeSMTP (the fake class, not the original).
#   MUTATION: Skipping patch installation leaves original in place; identity check fails.
#   ESCAPE: Nothing reasonable -- identity comparison against _FakeSMTP class.
def test_activate_installs_patch() -> None:
    v, p = _make_verifier_with_plugin()
    assert smtplib.SMTP is _ORIGINAL_SMTP
    p.activate()
    assert smtplib.SMTP is _FakeSMTP


# ESCAPE: test_deactivate_restores_patch
#   CLAIM: After activate() then deactivate(), smtplib.SMTP is the original again.
#   PATH:  deactivate() -> _install_count reaches 0 -> restore original SMTP.
#   CHECK: smtplib.SMTP is _ORIGINAL_SMTP.
#   MUTATION: Not restoring in deactivate() leaves _FakeSMTP in place; identity check fails.
#   ESCAPE: Nothing reasonable -- identity comparison against import-time constant.
def test_deactivate_restores_patch() -> None:
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.deactivate()
    assert smtplib.SMTP is _ORIGINAL_SMTP


# ESCAPE: test_reference_counting_nested
#   CLAIM: Two activate() calls require two deactivate() calls before patch is removed.
#   PATH:  First activate -> _install_count=1; second activate -> _install_count=2 (no reinstall).
#          First deactivate -> _install_count=1 (patch remains).
#          Second deactivate -> _install_count=0 (original restored).
#   CHECK: After first deactivate, smtplib.SMTP is still _FakeSMTP.
#          After second deactivate, smtplib.SMTP is _ORIGINAL_SMTP.
#   MUTATION: Restoring on first deactivate would fail the mid-point identity check.
#   ESCAPE: Nothing reasonable -- sequential identity checks prove count-controlled restoration.
def test_reference_counting_nested() -> None:
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.activate()
    assert SmtpPlugin._install_count == 2

    p.deactivate()
    assert SmtpPlugin._install_count == 1
    assert smtplib.SMTP is _FakeSMTP

    p.deactivate()
    assert SmtpPlugin._install_count == 0
    assert smtplib.SMTP is _ORIGINAL_SMTP


# ---------------------------------------------------------------------------
# Full auth flow: connect -> ehlo -> starttls -> login -> sendmail -> quit
# ---------------------------------------------------------------------------


# ESCAPE: test_full_auth_flow
#   CLAIM: A complete SMTP auth flow (connect -> ehlo -> starttls -> login -> sendmail -> quit)
#          consumes steps in order, returns scripted values, and ends in "closed" state.
#   PATH:  sandbox -> activate -> _FakeSMTP.__init__ triggers connect step (state: connected);
#          ehlo -> state: greeted; starttls -> state: greeted (self-loop);
#          login -> state: authenticated; sendmail -> state: sending; quit -> state: closed;
#          quit also calls _release_session.
#   CHECK: connect step runs unconditionally in __init__; ehlo returns (250, b"OK");
#          starttls returns (220, b"Ready"); login returns (235, b"Auth OK");
#          sendmail returns {}; quit returns (221, b"Bye"); session released after quit.
#   MUTATION: Wrong return value for any step fails the exact equality check.
#   ESCAPE: Nothing reasonable -- exact tuple equality on all five returns.
def test_full_auth_flow() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("ehlo", returns=(250, b"OK"))
    session.expect("starttls", returns=(220, b"Ready"))
    session.expect("login", returns=(235, b"Auth OK"))
    session.expect("sendmail", returns={})
    session.expect("quit", returns=(221, b"Bye"))

    with v.sandbox():
        smtp = smtplib.SMTP("mail.example.com", 587)
        ehlo_result = smtp.ehlo()
        starttls_result = smtp.starttls()
        login_result = smtp.login("user@example.com", "password")
        sendmail_result = smtp.sendmail(
            "from@example.com", ["to@example.com"], "Subject: hi\r\n\r\nhi"
        )
        quit_result = smtp.quit()

    assert ehlo_result == (250, b"OK")
    assert starttls_result == (220, b"Ready")
    assert login_result == (235, b"Auth OK")
    assert sendmail_result == {}
    assert quit_result == (221, b"Bye")
    assert len(p._active_sessions) == 0


# ---------------------------------------------------------------------------
# No-auth flow: connect -> ehlo -> sendmail -> quit
# ---------------------------------------------------------------------------


# ESCAPE: test_no_auth_flow
#   CLAIM: A simple flow (connect -> ehlo -> sendmail -> quit) without starttls or login works.
#   PATH:  connect (in __init__) -> state: connected; ehlo -> state: greeted;
#          sendmail from "greeted" -> state: sending; quit from "sending" -> state: closed.
#   CHECK: ehlo returns (250, b"OK"); sendmail returns {}; quit returns (221, b"Bye").
#   MUTATION: Wrong sendmail from-state check would reject "greeted" -> "sending" transition.
#   ESCAPE: Nothing reasonable -- exact return values prove all steps executed.
def test_no_auth_flow() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("ehlo", returns=(250, b"OK"))
    session.expect("sendmail", returns={})
    session.expect("quit", returns=(221, b"Bye"))

    with v.sandbox():
        smtp = smtplib.SMTP("mail.example.com", 25)
        ehlo_result = smtp.ehlo()
        sendmail_result = smtp.sendmail(
            "from@example.com", ["to@example.com"], "Subject: test\r\n\r\ntest"
        )
        quit_result = smtp.quit()

    assert ehlo_result == (250, b"OK")
    assert sendmail_result == {}
    assert quit_result == (221, b"Bye")
    assert len(p._active_sessions) == 0


# ---------------------------------------------------------------------------
# helo alias: connect -> helo -> sendmail -> quit
# ---------------------------------------------------------------------------


# ESCAPE: test_helo_alias
#   CLAIM: helo() is a valid alternative to ehlo() for transitioning connected -> greeted.
#   PATH:  connect (in __init__) -> state: connected; helo -> state: greeted;
#          sendmail from "greeted" -> state: sending; quit from "sending" -> state: closed.
#   CHECK: helo returns (250, b"OK"); sendmail returns {}; quit returns (221, b"Bye").
#   MUTATION: helo missing from transitions would raise InvalidStateError at helo call.
#   ESCAPE: Nothing reasonable -- exact return values and no exception proves helo is valid.
def test_helo_alias() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("helo", returns=(250, b"OK"))
    session.expect("sendmail", returns={})
    session.expect("quit", returns=(221, b"Bye"))

    with v.sandbox():
        smtp = smtplib.SMTP("mail.example.com", 25)
        helo_result = smtp.helo()
        sendmail_result = smtp.sendmail(
            "from@example.com", ["to@example.com"], "Subject: test\r\n\r\ntest"
        )
        quit_result = smtp.quit()

    assert helo_result == (250, b"OK")
    assert sendmail_result == {}
    assert quit_result == (221, b"Bye")
    assert len(p._active_sessions) == 0


# ---------------------------------------------------------------------------
# Multiple sendmail: sending -> sending self-loop
# ---------------------------------------------------------------------------


# ESCAPE: test_multiple_sendmail
#   CLAIM: A second sendmail() call succeeds when state is "sending" (self-loop transition).
#   PATH:  connect -> ehlo -> first sendmail (greeted -> sending) ->
#          second sendmail (sending -> sending self-loop) -> quit.
#   CHECK: First sendmail returns {}; second sendmail returns {"to2@example.com": (550, b"User unknown")};
#          quit succeeds.
#   MUTATION: Missing sending -> sending self-loop would raise InvalidStateError on second sendmail.
#   ESCAPE: Nothing reasonable -- exact return values prove both sendmail calls succeeded.
def test_multiple_sendmail() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("ehlo", returns=(250, b"OK"))
    session.expect("sendmail", returns={})
    session.expect("sendmail", returns={"to2@example.com": (550, b"User unknown")})
    session.expect("quit", returns=(221, b"Bye"))

    with v.sandbox():
        smtp = smtplib.SMTP("mail.example.com", 25)
        smtp.ehlo()
        result1 = smtp.sendmail("from@example.com", ["to@example.com"], "msg1")
        result2 = smtp.sendmail("from@example.com", ["to2@example.com"], "msg2")
        quit_result = smtp.quit()

    assert result1 == {}
    assert result2 == {"to2@example.com": (550, b"User unknown")}
    assert quit_result == (221, b"Bye")
    assert len(p._active_sessions) == 0


# ---------------------------------------------------------------------------
# quit from greeted: connect -> ehlo -> quit (no send)
# ---------------------------------------------------------------------------


# ESCAPE: test_quit_from_greeted
#   CLAIM: quit() is valid from "greeted" state (without sending any mail).
#   PATH:  connect (in __init__) -> state: connected; ehlo -> state: greeted;
#          quit from "greeted" -> state: closed; session released.
#   CHECK: ehlo returns (250, b"OK"); quit returns (221, b"Bye"); active sessions empty.
#   MUTATION: Missing "greeted" in quit's from-states would raise InvalidStateError.
#   ESCAPE: Nothing reasonable -- no exception plus exact quit return value.
def test_quit_from_greeted() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("ehlo", returns=(250, b"OK"))
    session.expect("quit", returns=(221, b"Bye"))

    with v.sandbox():
        smtp = smtplib.SMTP("mail.example.com", 25)
        ehlo_result = smtp.ehlo()
        quit_result = smtp.quit()

    assert ehlo_result == (250, b"OK")
    assert quit_result == (221, b"Bye")
    assert len(p._active_sessions) == 0


# ---------------------------------------------------------------------------
# quit from authenticated: connect -> ehlo -> login -> quit
# ---------------------------------------------------------------------------


# ESCAPE: test_quit_from_authenticated
#   CLAIM: quit() is valid from "authenticated" state (logged in but no mail sent).
#   PATH:  connect -> ehlo -> login (greeted -> authenticated) ->
#          quit from "authenticated" -> state: closed; session released.
#   CHECK: login returns (235, b"Auth OK"); quit returns (221, b"Bye"); active sessions empty.
#   MUTATION: Missing "authenticated" in quit's from-states would raise InvalidStateError.
#   ESCAPE: Nothing reasonable -- no exception plus exact return values prove the flow.
def test_quit_from_authenticated() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("ehlo", returns=(250, b"OK"))
    session.expect("login", returns=(235, b"Auth OK"))
    session.expect("quit", returns=(221, b"Bye"))

    with v.sandbox():
        smtp = smtplib.SMTP("mail.example.com", 587)
        smtp.ehlo()
        login_result = smtp.login("user@example.com", "password")
        quit_result = smtp.quit()

    assert login_result == (235, b"Auth OK")
    assert quit_result == (221, b"Bye")
    assert len(p._active_sessions) == 0


# ---------------------------------------------------------------------------
# InvalidStateError: sendmail before ehlo
# ---------------------------------------------------------------------------


# ESCAPE: test_sendmail_before_ehlo_raises_invalid_state
#   CLAIM: Calling sendmail() when state is "connected" (after connect but before ehlo/helo)
#          raises InvalidStateError, because sendmail only accepts greeted/authenticated/sending.
#   PATH:  connect (in __init__) -> state: connected; sendmail ->
#          _execute_step -> state "connected" not in method_transitions["sendmail"] ->
#          InvalidStateError.
#   CHECK: InvalidStateError raised; exc.method == "sendmail";
#          exc.current_state == "connected";
#          exc.valid_states == frozenset({"greeted", "authenticated", "sending"}).
#   MUTATION: Not checking from-state would allow the call through without raising.
#   ESCAPE: Raising with wrong current_state fails the attribute check.
def test_sendmail_before_ehlo_raises_invalid_state() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    # sendmail step is intentionally NOT added -- state error fires before script check

    with v.sandbox():
        smtp = smtplib.SMTP("mail.example.com", 25)
        with pytest.raises(InvalidStateError) as exc_info:
            smtp.sendmail("from@example.com", ["to@example.com"], "msg")

    exc = exc_info.value
    assert exc.source_id == "smtp:sendmail"
    assert exc.method == "sendmail"
    assert exc.current_state == "connected"
    assert exc.valid_states == frozenset({"greeted", "authenticated", "sending"})


# ---------------------------------------------------------------------------
# get_unused_mocks: unconsumed steps
# ---------------------------------------------------------------------------


# ESCAPE: test_get_unused_mocks_unconsumed_steps
#   CLAIM: When ehlo and quit steps are never consumed, get_unused_mocks() returns them.
#   PATH:  new_session with connect + ehlo + quit steps -> connect consumed in __init__ ->
#          session in _active_sessions with two remaining required steps ->
#          get_unused_mocks() returns them.
#   CHECK: len(unused) == 2; unused[0].method == "ehlo"; unused[1].method == "quit".
#   MUTATION: Not scanning _active_sessions for remaining steps would return [].
#   ESCAPE: Returning both including connect would give len == 3; fails count check.
def test_get_unused_mocks_unconsumed_steps() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("ehlo", returns=(250, b"OK"))  # will NOT be consumed
    session.expect("quit", returns=(221, b"Bye"))  # will NOT be consumed

    with v.sandbox():
        smtplib.SMTP("mail.example.com", 25)
        # deliberately NOT calling ehlo or quit

    unused: list[ScriptStep] = p.get_unused_mocks()
    assert len(unused) == 2
    assert unused[0].method == "ehlo"
    assert unused[1].method == "quit"


# ESCAPE: test_get_unused_mocks_queued_session_never_bound
#   CLAIM: A session queued but never bound (no SMTP() called) has all its required
#          steps returned by get_unused_mocks().
#   PATH:  new_session with connect + ehlo enqueued -> no SMTP() call ->
#          _session_queue still holds handle -> get_unused_mocks() iterates _session_queue.
#   CHECK: len(unused) == 2; methods are ["connect", "ehlo"] in order.
#   MUTATION: Not iterating _session_queue would return [].
#   ESCAPE: Returning items in LIFO order would fail the method ordering check.
def test_get_unused_mocks_queued_session_never_bound() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("ehlo", returns=(250, b"OK"))

    # Never call SMTP; the session stays in the queue
    unused: list[ScriptStep] = p.get_unused_mocks()
    assert len(unused) == 2
    assert unused[0].method == "connect"
    assert unused[1].method == "ehlo"


# ---------------------------------------------------------------------------
# UnmockedInteractionError when no session queued
# ---------------------------------------------------------------------------


# ESCAPE: test_smtp_with_empty_queue_raises_unmocked
#   CLAIM: If no session is queued when smtplib.SMTP() fires, UnmockedInteractionError
#          is raised with source_id == "smtp:connect".
#   PATH:  _FakeSMTP.__init__ -> _bind_connection -> queue empty ->
#          UnmockedInteractionError(source_id="smtp:connect").
#   CHECK: UnmockedInteractionError raised; exc.source_id == "smtp:connect".
#   MUTATION: Returning a dummy session for empty queue would not raise.
#   ESCAPE: Raising with wrong source_id fails the source_id check.
def test_smtp_with_empty_queue_raises_unmocked() -> None:
    v, p = _make_verifier_with_plugin()
    # No session registered

    with v.sandbox():
        with pytest.raises(UnmockedInteractionError) as exc_info:
            smtplib.SMTP("mail.example.com", 25)

    assert exc_info.value.source_id == "smtp:connect"


# ---------------------------------------------------------------------------
# Module-level proxy: bigfoot.smtp_mock
# ---------------------------------------------------------------------------


# ESCAPE: test_smtp_mock_proxy_new_session
#   CLAIM: bigfoot.smtp_mock.new_session() returns a SessionHandle that can
#          be used to configure a session without importing SmtpPlugin directly.
#   PATH:  _SmtpProxy.__getattr__("new_session") -> get verifier -> find/create SmtpPlugin ->
#          return plugin.new_session.
#   CHECK: session is a SessionHandle instance; chaining .expect() does not raise.
#   MUTATION: Returning None instead of a SessionHandle would fail isinstance check.
#   ESCAPE: Nothing reasonable -- both the isinstance and the chained .expect() call check it.
def test_smtp_mock_proxy_new_session(bigfoot_verifier: StrictVerifier) -> None:
    from bigfoot._state_machine_plugin import SessionHandle

    session = bigfoot.smtp_mock.new_session()
    assert isinstance(session, SessionHandle)
    result = session.expect("connect", returns=None, required=False)
    assert result is session  # expect() returns self for chaining


# ESCAPE: test_smtp_mock_proxy_raises_outside_context
#   CLAIM: Accessing bigfoot.smtp_mock outside a test context raises NoActiveVerifierError.
#   PATH:  _SmtpProxy.__getattr__ -> _get_test_verifier_or_raise -> NoActiveVerifierError.
#   CHECK: NoActiveVerifierError raised.
#   MUTATION: Silently returning None would not raise and hide context failures.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_smtp_mock_proxy_raises_outside_context() -> None:
    from bigfoot._errors import NoActiveVerifierError

    token = _current_test_verifier.set(None)
    try:
        with pytest.raises(NoActiveVerifierError):
            _ = bigfoot.smtp_mock.new_session
    finally:
        _current_test_verifier.reset(token)


# ---------------------------------------------------------------------------
# Full session via module-level API: bigfoot.sandbox()
# ---------------------------------------------------------------------------


# ESCAPE: test_full_session_via_sandbox
#   CLAIM: A complete SMTP session (connect -> ehlo -> sendmail -> quit) runs end-to-end
#          through the module-level bigfoot.sandbox() API, returning the scripted values.
#   PATH:  bigfoot.smtp_mock.new_session() -> sandbox -> _FakeSMTP.__init__ ->
#          ehlo -> sendmail -> quit.
#   CHECK: sendmail_result == {}; quit_result == (221, b"Bye").
#   MUTATION: Returning wrong sendmail result would fail the equality check.
#   ESCAPE: Nothing reasonable -- exact equality on both returns.
def test_full_session_via_sandbox(bigfoot_verifier: StrictVerifier) -> None:
    session = bigfoot.smtp_mock.new_session()
    session.expect("connect", returns=None)
    session.expect("ehlo", returns=(250, b"OK"))
    session.expect("sendmail", returns={})
    session.expect("quit", returns=(221, b"Bye"))

    with bigfoot.sandbox():
        smtp = smtplib.SMTP("mail.example.com", 25)
        smtp.ehlo()
        sendmail_result = smtp.sendmail(
            "from@example.com", ["to@example.com"], "Subject: test\r\n\r\ntest"
        )
        quit_result = smtp.quit()

    assert sendmail_result == {}
    assert quit_result == (221, b"Bye")
