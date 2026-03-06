"""SmtpPlugin: intercepts smtplib.SMTP via class replacement."""

import smtplib
import threading
from typing import TYPE_CHECKING, Any, ClassVar

from bigfoot._context import _get_verifier_or_raise
from bigfoot._state_machine_plugin import StateMachinePlugin, _StepSentinel
from bigfoot._timeline import Interaction

if TYPE_CHECKING:
    from bigfoot._verifier import StrictVerifier

# ---------------------------------------------------------------------------
# Import-time constant -- captured BEFORE any patches are installed.
# ---------------------------------------------------------------------------

_ORIGINAL_SMTP: Any = smtplib.SMTP

# ---------------------------------------------------------------------------
# Source ID constants
# ---------------------------------------------------------------------------

_SOURCE_CONNECT = "smtp:connect"
_SOURCE_EHLO = "smtp:ehlo"
_SOURCE_HELO = "smtp:helo"
_SOURCE_STARTTLS = "smtp:starttls"
_SOURCE_LOGIN = "smtp:login"
_SOURCE_SENDMAIL = "smtp:sendmail"
_SOURCE_SEND_MESSAGE = "smtp:send_message"
_SOURCE_QUIT = "smtp:quit"

# ---------------------------------------------------------------------------
# Module-level helper: find the SmtpPlugin on the active verifier
# ---------------------------------------------------------------------------


def _find_smtp_plugin() -> "SmtpPlugin":
    verifier = _get_verifier_or_raise("smtp:connect")
    for plugin in verifier._plugins:
        if isinstance(plugin, SmtpPlugin):
            return plugin
    raise RuntimeError(
        "BUG: bigfoot SmtpPlugin interceptor is active but no "
        "SmtpPlugin is registered on the current verifier."
    )


# ---------------------------------------------------------------------------
# _FakeSMTP
# ---------------------------------------------------------------------------


class _FakeSMTP:
    """Fake smtplib.SMTP that routes all operations through SmtpPlugin."""

    def __init__(self, host: str = "", port: int = 0, **kwargs: Any) -> None:  # noqa: ANN401
        plugin = _find_smtp_plugin()
        plugin._bind_connection(self)  # partial init
        handle = plugin._lookup_session(self)
        # ALWAYS execute connect step unconditionally (matches real smtplib.SMTP behavior)
        plugin._execute_step(
            handle, "connect", (host,), {"port": port}, _SOURCE_CONNECT,
            details={"host": host, "port": port},
        )

    def ehlo(self, name: str = "") -> tuple[int, bytes]:
        plugin = _find_smtp_plugin()
        handle = plugin._lookup_session(self)
        return plugin._execute_step(handle, "ehlo", (name,), {}, _SOURCE_EHLO,  # type: ignore[no-any-return]
            details={"name": name})

    def helo(self, name: str = "") -> tuple[int, bytes]:
        plugin = _find_smtp_plugin()
        handle = plugin._lookup_session(self)
        return plugin._execute_step(handle, "helo", (name,), {}, _SOURCE_HELO,  # type: ignore[no-any-return]
            details={"name": name})

    def starttls(self, **kwargs: Any) -> tuple[int, bytes]:  # noqa: ANN401
        plugin = _find_smtp_plugin()
        handle = plugin._lookup_session(self)
        return plugin._execute_step(handle, "starttls", (), kwargs, _SOURCE_STARTTLS,  # type: ignore[no-any-return]
            details={})

    def login(self, user: str, password: str) -> tuple[int, bytes]:
        plugin = _find_smtp_plugin()
        handle = plugin._lookup_session(self)
        return plugin._execute_step(handle, "login", (user, password), {}, _SOURCE_LOGIN,  # type: ignore[no-any-return]
            details={"user": user, "password": password})

    def sendmail(
        self,
        from_addr: str,
        to_addrs: Any,  # noqa: ANN401
        msg: Any,  # noqa: ANN401
        mail_options: tuple[str, ...] = (),
        rcpt_options: tuple[str, ...] = (),
    ) -> dict[str, tuple[int, bytes]]:
        plugin = _find_smtp_plugin()
        handle = plugin._lookup_session(self)
        return plugin._execute_step(  # type: ignore[no-any-return]
            handle, "sendmail", (from_addr, to_addrs, msg), {}, _SOURCE_SENDMAIL,
            details={"from_addr": from_addr, "to_addrs": to_addrs, "msg": msg},
        )

    def send_message(
        self,
        msg: Any,  # noqa: ANN401
        from_addr: str | None = None,
        to_addrs: Any = None,  # noqa: ANN401
        mail_options: tuple[str, ...] = (),
        rcpt_options: tuple[str, ...] = (),
    ) -> dict[str, tuple[int, bytes]]:
        plugin = _find_smtp_plugin()
        handle = plugin._lookup_session(self)
        return plugin._execute_step(handle, "send_message", (msg,), {}, _SOURCE_SEND_MESSAGE,  # type: ignore[no-any-return]
            details={"msg": msg})

    def quit(self) -> tuple[int, bytes]:
        plugin = _find_smtp_plugin()
        handle = plugin._lookup_session(self)
        result = plugin._execute_step(handle, "quit", (), {}, _SOURCE_QUIT,
            details={})
        plugin._release_session(self)
        return result  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# SmtpPlugin
# ---------------------------------------------------------------------------


class SmtpPlugin(StateMachinePlugin):
    """SMTP interception plugin.

    Replaces smtplib.SMTP with _FakeSMTP at activate() time and restores
    the original at deactivate() time. Uses reference counting so nested
    sandboxes work correctly.

    States: disconnected -> connected -> greeted -> (authenticated|sending) -> closed
    Note: starttls is a self-loop on 'greeted' (optional).
    login transitions greeted -> authenticated.
    """

    # Class-level reference counting -- shared across all instances/verifiers.
    _install_count: ClassVar[int] = 0
    _install_lock: ClassVar[threading.Lock] = threading.Lock()

    # Saved original, restored when count reaches 0.
    _original_smtp: ClassVar[Any] = None

    def __init__(self, verifier: "StrictVerifier") -> None:
        super().__init__(verifier)
        self._connect_sentinel = _StepSentinel(_SOURCE_CONNECT)
        self._ehlo_sentinel = _StepSentinel(_SOURCE_EHLO)
        self._helo_sentinel = _StepSentinel(_SOURCE_HELO)
        self._starttls_sentinel = _StepSentinel(_SOURCE_STARTTLS)
        self._login_sentinel = _StepSentinel(_SOURCE_LOGIN)
        self._sendmail_sentinel = _StepSentinel(_SOURCE_SENDMAIL)
        self._send_message_sentinel = _StepSentinel(_SOURCE_SEND_MESSAGE)
        self._quit_sentinel = _StepSentinel(_SOURCE_QUIT)

    @property
    def connect(self) -> _StepSentinel:
        return self._connect_sentinel

    @property
    def ehlo(self) -> _StepSentinel:
        return self._ehlo_sentinel

    @property
    def helo(self) -> _StepSentinel:
        return self._helo_sentinel

    @property
    def starttls(self) -> _StepSentinel:
        return self._starttls_sentinel

    @property
    def login(self) -> _StepSentinel:
        return self._login_sentinel

    @property
    def sendmail(self) -> _StepSentinel:
        return self._sendmail_sentinel

    @property
    def send_message(self) -> _StepSentinel:
        return self._send_message_sentinel

    @property
    def quit(self) -> _StepSentinel:
        return self._quit_sentinel

    # ------------------------------------------------------------------
    # StateMachinePlugin abstract methods
    # ------------------------------------------------------------------

    def _initial_state(self) -> str:
        return "disconnected"

    def _transitions(self) -> dict[str, dict[str, str]]:
        return {
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

    def _unmocked_source_id(self) -> str:
        return "smtp:connect"

    # ------------------------------------------------------------------
    # BasePlugin lifecycle
    # ------------------------------------------------------------------

    def activate(self) -> None:
        """Reference-counted class-level patch installation."""
        with SmtpPlugin._install_lock:
            if SmtpPlugin._install_count == 0:
                SmtpPlugin._original_smtp = smtplib.SMTP
                smtplib.SMTP = _FakeSMTP  # type: ignore[assignment, misc]
            SmtpPlugin._install_count += 1

    def deactivate(self) -> None:
        with SmtpPlugin._install_lock:
            SmtpPlugin._install_count = max(0, SmtpPlugin._install_count - 1)
            if SmtpPlugin._install_count == 0:
                if SmtpPlugin._original_smtp is not None:
                    smtplib.SMTP = SmtpPlugin._original_smtp  # type: ignore[misc]
                    SmtpPlugin._original_smtp = None

    # ------------------------------------------------------------------
    # BasePlugin abstract method implementations
    # ------------------------------------------------------------------

    def format_interaction(self, interaction: Interaction) -> str:
        sid = interaction.source_id
        method = sid.split(":", 1)[-1] if ":" in sid else sid
        details = interaction.details
        if sid == _SOURCE_CONNECT:
            return (
                f"[SmtpPlugin] smtp.connect("
                f"host={details.get('host', '?')!r}, port={details.get('port', 0)!r})"
            )
        if sid == _SOURCE_EHLO:
            return f"[SmtpPlugin] smtp.ehlo(name={details.get('name', '')!r})"
        if sid == _SOURCE_HELO:
            return f"[SmtpPlugin] smtp.helo(name={details.get('name', '')!r})"
        if sid == _SOURCE_STARTTLS:
            return "[SmtpPlugin] smtp.starttls()"
        if sid == _SOURCE_LOGIN:
            return f"[SmtpPlugin] smtp.login(user={details.get('user', '?')!r})"
        if sid == _SOURCE_SENDMAIL:
            return (
                f"[SmtpPlugin] smtp.sendmail("
                f"from_addr={details.get('from_addr', '?')!r}, "
                f"to_addrs={details.get('to_addrs')!r})"
            )
        if sid == _SOURCE_SEND_MESSAGE:
            return f"[SmtpPlugin] smtp.send_message(msg={details.get('msg')!r})"
        if sid == _SOURCE_QUIT:
            return "[SmtpPlugin] smtp.quit()"
        return f"[SmtpPlugin] smtp.{method}(...)"

    def format_mock_hint(self, interaction: Interaction) -> str:
        sid = interaction.source_id
        method = sid.split(":")[-1] if ":" in sid else sid
        return f"    bigfoot.smtp_mock.new_session().expect({method!r}, returns=...)"

    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        method = source_id.split(":")[-1] if ":" in source_id else source_id
        return (
            f"smtplib.SMTP.{method}(...) was called but no session was queued.\n"
            f"Register a session with:\n"
            f"    bigfoot.smtp_mock.new_session().expect({method!r}, returns=...)"
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        sm = "bigfoot.smtp_mock"
        sid = interaction.source_id
        if sid == _SOURCE_CONNECT:
            host = interaction.details.get("host", "?")
            port = interaction.details.get("port", 0)
            return f"    {sm}.assert_connect(host={host!r}, port={port!r})"
        if sid == _SOURCE_EHLO:
            name = interaction.details.get("name", "")
            return f"    {sm}.assert_ehlo(name={name!r})"
        if sid == _SOURCE_HELO:
            name = interaction.details.get("name", "")
            return f"    {sm}.assert_helo(name={name!r})"
        if sid == _SOURCE_STARTTLS:
            return f"    {sm}.assert_starttls()"
        if sid == _SOURCE_LOGIN:
            user = interaction.details.get("user", "?")
            password = interaction.details.get("password", "?")
            return f"    {sm}.assert_login(user={user!r}, password={password!r})"
        if sid == _SOURCE_SENDMAIL:
            from_addr = interaction.details.get("from_addr", "?")
            to_addrs = interaction.details.get("to_addrs")
            msg = interaction.details.get("msg")
            return (
                f"    {sm}.assert_sendmail("
                f"from_addr={from_addr!r}, to_addrs={to_addrs!r}, msg={msg!r})"
            )
        if sid == _SOURCE_SEND_MESSAGE:
            msg = interaction.details.get("msg")
            return f"    {sm}.assert_send_message(msg={msg!r})"
        if sid == _SOURCE_QUIT:
            return f"    {sm}.assert_quit()"
        return f"    # {sm}: unknown source_id={sid!r}"

    def matches(self, interaction: Interaction, expected: dict[str, Any]) -> bool:
        """Field-by-field comparison with dirty-equals support."""
        try:
            for key, expected_val in expected.items():
                actual_val = interaction.details.get(key)
                if expected_val != actual_val:
                    return False
            return True
        except Exception:
            return False

    def assertable_fields(self, interaction: Interaction) -> frozenset[str]:
        """Return assertable fields for each step type."""
        no_data = {_SOURCE_STARTTLS, _SOURCE_QUIT}
        if interaction.source_id in no_data:
            return frozenset()
        return frozenset(interaction.details.keys())

    def assert_connect(self, *, host: str, port: int) -> None:
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(
            self._connect_sentinel, host=host, port=port
        )

    def assert_ehlo(self, *, name: str) -> None:
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(self._ehlo_sentinel, name=name)

    def assert_helo(self, *, name: str) -> None:
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(self._helo_sentinel, name=name)

    def assert_starttls(self) -> None:
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(self._starttls_sentinel)

    def assert_login(self, *, user: str, password: str) -> None:
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(
            self._login_sentinel, user=user, password=password
        )

    def assert_sendmail(self, *, from_addr: str, to_addrs: Any, msg: Any) -> None:  # noqa: ANN401
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(
            self._sendmail_sentinel, from_addr=from_addr, to_addrs=to_addrs, msg=msg
        )

    def assert_send_message(self, *, msg: Any) -> None:  # noqa: ANN401
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(self._send_message_sentinel, msg=msg)

    def assert_quit(self) -> None:
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(self._quit_sentinel)

    def format_unused_mock_hint(self, mock_config: object) -> str:
        step: Any = mock_config
        method = getattr(step, "method", "?")
        return (
            f"smtplib.SMTP.{method}(...) was mocked (required=True) but never called.\n"
            f"Registered at:\n{getattr(step, 'registration_traceback', '')}"
        )
