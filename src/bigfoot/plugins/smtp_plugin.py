"""SmtpPlugin: intercepts smtplib.SMTP via class replacement."""

import smtplib
import threading
from typing import Any, ClassVar

from bigfoot._context import _get_verifier_or_raise
from bigfoot._state_machine_plugin import StateMachinePlugin
from bigfoot._timeline import Interaction

# ---------------------------------------------------------------------------
# Import-time constant -- captured BEFORE any patches are installed.
# ---------------------------------------------------------------------------

_ORIGINAL_SMTP: Any = smtplib.SMTP

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
        plugin._execute_step(handle, "connect", (host,), {"port": port}, "smtp:connect")

    def ehlo(self, name: str = "") -> tuple[int, bytes]:
        plugin = _find_smtp_plugin()
        handle = plugin._lookup_session(self)
        return plugin._execute_step(handle, "ehlo", (name,), {}, "smtp:ehlo")  # type: ignore[no-any-return]

    def helo(self, name: str = "") -> tuple[int, bytes]:
        plugin = _find_smtp_plugin()
        handle = plugin._lookup_session(self)
        return plugin._execute_step(handle, "helo", (name,), {}, "smtp:helo")  # type: ignore[no-any-return]

    def starttls(self, **kwargs: Any) -> tuple[int, bytes]:  # noqa: ANN401
        plugin = _find_smtp_plugin()
        handle = plugin._lookup_session(self)
        return plugin._execute_step(handle, "starttls", (), kwargs, "smtp:starttls")  # type: ignore[no-any-return]

    def login(self, user: str, password: str) -> tuple[int, bytes]:
        plugin = _find_smtp_plugin()
        handle = plugin._lookup_session(self)
        return plugin._execute_step(handle, "login", (user, password), {}, "smtp:login")  # type: ignore[no-any-return]

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
            handle, "sendmail", (from_addr, to_addrs, msg), {}, "smtp:sendmail"
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
        return plugin._execute_step(handle, "send_message", (msg,), {}, "smtp:send_message")  # type: ignore[no-any-return]

    def quit(self) -> tuple[int, bytes]:
        plugin = _find_smtp_plugin()
        handle = plugin._lookup_session(self)
        result = plugin._execute_step(handle, "quit", (), {}, "smtp:quit")
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
        method = interaction.details.get("method", "?")
        args = interaction.details.get("args", ())
        parts = [repr(a) for a in args]
        return f"[SmtpPlugin] smtp.{method}({', '.join(parts)})"

    def format_mock_hint(self, interaction: Interaction) -> str:
        method = interaction.details.get("method", "?")
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
        pm = "bigfoot.smtp_mock"
        method = interaction.details.get("method", "?")
        return f"    # {pm}: session step '{method}' recorded (state-machine, auto-asserted)"

    def format_unused_mock_hint(self, mock_config: object) -> str:
        step: Any = mock_config
        method = getattr(step, "method", "?")
        return (
            f"smtplib.SMTP.{method}(...) was mocked (required=True) but never called.\n"
            f"Registered at:\n{getattr(step, 'registration_traceback', '')}"
        )
