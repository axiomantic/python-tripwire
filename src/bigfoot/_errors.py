"""All bigfoot exception classes.

This module imports NOTHING from other bigfoot modules to prevent circular imports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bigfoot._firewall_request import FirewallRequest


class BigfootError(Exception):
    """Base class for all bigfoot errors."""


class UnmockedInteractionError(BigfootError):
    """Raised at call time: an interaction fired with no matching registered mock.

    Message includes: source description, args/kwargs, copy-pasteable mock hint.
    """

    def __init__(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        hint: str,
    ) -> None:
        self.source_id = source_id
        self.args_tuple = args
        self.kwargs = kwargs
        self.hint = hint
        super().__init__(
            f"Unmocked call to {source_id!r}.\n\n"
            f"Add a mock before entering the sandbox:\n"
            f"{hint}\n\n"
            f"Then assert it after the sandbox closes:\n"
            f"    with bigfoot:\n"
            f"        # ... your code that triggers the call\n"
            f"    # assert_* call here (REQUIRED)"
        )


class UnassertedInteractionsError(BigfootError):
    """Raised at teardown: timeline contains interactions not matched by assert_interaction().

    Message lists each unasserted interaction with copy-pasteable assert hint.
    """

    def __init__(self, interactions: list[Any], hint: str) -> None:
        self.interactions = interactions
        self.hint = hint
        count = len(interactions)
        header = f"{count} interaction{'s were' if count > 1 else ' was'} not asserted."
        super().__init__(f"{header}\n\n{hint}")


class UnusedMocksError(BigfootError):
    """Raised at teardown: registered mocks with required=True were never triggered.

    Message lists each unused mock with hint to either remove or set required=False.
    """

    def __init__(self, mocks: list[Any], hint: str) -> None:
        self.mocks = mocks
        self.hint = hint
        super().__init__(f"{hint}")


class VerificationError(BigfootError):
    """Raised at teardown when BOTH UnassertedInteractionsError and UnusedMocksError apply.

    Contains both reports in separate sections.
    """

    def __init__(
        self,
        unasserted: UnassertedInteractionsError | None,
        unused: UnusedMocksError | None,
    ) -> None:
        self.unasserted = unasserted
        self.unused = unused

        sections: list[str] = []
        if unasserted is not None:
            sections.append(f"--- Unasserted Interactions ---\n{unasserted}")
        if unused is not None:
            sections.append(f"--- Unused Mocks ---\n{unused}")

        if sections:
            message = "\n\n".join(sections)
        else:
            message = "VerificationError: (no details)"

        super().__init__(message)


class InteractionMismatchError(BigfootError):
    """Raised by assert_interaction() when expected source/fields don't match
    the next interaction in the timeline.

    Message includes: expected description, actual next interaction, remaining timeline.
    """

    def __init__(
        self,
        expected: object,
        actual: object,
        hint: str,
    ) -> None:
        self.expected = expected
        self.actual = actual
        self.hint = hint
        super().__init__(hint)


class SandboxNotActiveError(BigfootError):
    """Raised when an intercepted call fires but no sandbox is active.

    Attributes:
        source_id: Identifier of the interceptor that fired without a sandbox.

    Message includes hint: 'Did you forget bigfoot_verifier fixture or sandbox() CM?'
    """

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id
        super().__init__(
            f"SandboxNotActiveError: source_id={source_id!r}, "
            "hint='Did you forget bigfoot_verifier fixture or sandbox() CM?'"
        )


class AssertionInsideSandboxError(BigfootError):
    """Raised when assert_interaction(), in_any_order(), or verify_all() is called
    while a sandbox is active on that verifier instance.

    Assertions must be made after the sandbox exits, not during it.
    """

    def __init__(self) -> None:
        super().__init__(
            "AssertionInsideSandboxError: assert_interaction(), in_any_order(), and verify_all() "
            "must be called after the sandbox has exited, not while it is active. "
            "Exit the sandbox first, then make assertions."
        )


class NoActiveVerifierError(BigfootError):
    """Raised when a module-level bigfoot function is called outside a test context."""

    def __str__(self) -> str:
        return (
            "NoActiveVerifierError: no active bigfoot verifier. "
            "Module-level bigfoot functions (mock, sandbox, assert_interaction, etc.) "
            "require an active test context. Ensure bigfoot is installed as a pytest "
            "plugin (it registers automatically) and you are running inside a pytest test."
        )


class ConflictError(BigfootError):
    """Raised at activate() time if target method is already patched by another library.

    Message names the conflicting library and the patched target.
    """

    def __init__(self, target: str, patcher: str) -> None:
        self.target = target
        self.patcher = patcher
        super().__init__(f"ConflictError: target={target!r}, patcher={patcher!r}")


class MissingAssertionFieldsError(BigfootError):
    """Raised by assert_interaction() when the caller omits one or more assertable
    fields from **expected.

    Attributes:
        missing_fields: frozenset of field names that were required but absent.
    """

    def __init__(
        self,
        missing_fields: frozenset[str],
        provided_fields: frozenset[str] | None = None,
    ) -> None:
        self.missing_fields = missing_fields
        self.provided_fields = provided_fields
        missing_str = ", ".join(sorted(missing_fields))
        lines = [
            f"Missing assertion fields: {missing_str}",
        ]
        if provided_fields is not None:
            provided_str = ", ".join(sorted(provided_fields))
            lines.append(f"  Provided: {provided_str}")
        lines.append("")
        lines.append(
            "Include them in **expected or use a dirty-equals"
            " matcher (e.g., IsAnything())"
        )
        lines.append(
            "if the value is not the focus of this assertion."
        )
        super().__init__("\n".join(lines))


class AutoAssertError(BigfootError):
    """Raised when mark_asserted() is called while record() is in progress.

    This indicates the auto-assert anti-pattern: a plugin calling
    timeline.mark_asserted() immediately after record() inside its intercept
    hook, bypassing the requirement for explicit test assertions.
    """


class AllWildcardAssertionError(BigfootError):
    """Raised when all assertion fields are wildcards (e.g., AnyThing()).

    All-wildcard assertions verify nothing. Use real expected values
    for at least some fields.
    """

    def __init__(self, interaction: object, hint: str) -> None:
        self.interaction = interaction
        self.hint = hint
        super().__init__(
            "All assertion fields are wildcards. This assertion verifies nothing.\n\n"
            "Here's what actually happened -- paste this instead:\n\n"
            f"{hint}"
        )


class BigfootConfigError(BigfootError):
    """Raised when [tool.bigfoot] configuration is invalid.

    Examples: mutually exclusive keys, unknown plugin names, wrong types.
    """


class GuardedCallError(BigfootError):
    """Raised when a call is blocked by the bigfoot firewall.

    The error message shows:
    1. Exactly what was attempted (URL, host, command, path)
    2. The most specific allow rule to fix it (not blanket allow)
    3. All three configuration syntaxes (mark, context manager, TOML)
    4. Link to docs
    """

    def __init__(
        self,
        source_id: str,
        plugin_name: str,
        firewall_request: FirewallRequest | None = None,
    ) -> None:
        self.source_id = source_id
        self.plugin_name = plugin_name
        self.firewall_request = firewall_request
        super().__init__(self._build_message())

    def _build_message(self) -> str:
        req = self.firewall_request
        lines = [
            f"GuardedCallError: {self.source_id!r} blocked by bigfoot firewall.",
            "",
        ]

        # Section 1: What was attempted
        lines.append("  Attempted:")
        if req is not None:
            lines.append(f"    {self._describe_request(req)}")
        else:
            lines.append(f"    {self.source_id} (no request details available)")
        lines.append("")

        # Section 2: Recommended fix (most specific)
        mark_fix, cm_fix, toml_fix = self._recommend_fix(req)

        lines.append("  Fix with @pytest.mark.allow:")
        lines.append("")
        lines.append(f"    {mark_fix}")
        lines.append("    def test_something():")
        lines.append("        ...")
        lines.append("")
        lines.append("  Fix with context manager (scoped to a block):")
        lines.append("")
        lines.append(f"    {cm_fix}")
        lines.append("        ...")
        lines.append("")
        lines.append("  Fix in pyproject.toml:")
        lines.append("")
        for toml_line in toml_fix:
            lines.append(f"    {toml_line}")
        lines.append("")

        # Section 3: Or mock it
        lines.append("  Or mock the call with a sandbox:")
        lines.append("")
        lines.append("    with bigfoot:")
        lines.append("        ...")
        lines.append("")
        lines.append("  Docs: https://bigfoot.readthedocs.io/guides/guard-mode/")

        return "\n".join(lines)

    def _describe_request(self, req: FirewallRequest) -> str:
        """Human-readable description of what was attempted."""
        from bigfoot._firewall_request import (  # noqa: PLC0415
            Boto3FirewallRequest,
            DatabaseFirewallRequest,
            FileIoFirewallRequest,
            HttpFirewallRequest,
            McpFirewallRequest,
            NetworkFirewallRequest,
            RedisFirewallRequest,
            SubprocessFirewallRequest,
        )

        if isinstance(req, HttpFirewallRequest):
            url = f"{req.scheme}://{req.host}:{req.port}{req.path}"
            return f"{req.method} {url}"
        if isinstance(req, RedisFirewallRequest):
            return f"redis://{req.host}:{req.port}/{req.db} {req.command}"
        if isinstance(req, SubprocessFirewallRequest):
            return f"subprocess: {req.command}" if req.command else f"subprocess: {req.binary}"
        if isinstance(req, FileIoFirewallRequest):
            return f"file_io: {req.operation} {req.path}"
        if isinstance(req, Boto3FirewallRequest):
            return f"boto3: {req.service}.{req.operation}"
        if isinstance(req, DatabaseFirewallRequest):
            return f"sqlite: {req.database_path}"
        if isinstance(req, McpFirewallRequest):
            return f"mcp: tool={req.tool_name} uri={req.uri}"
        if isinstance(req, NetworkFirewallRequest):
            return f"{req.protocol}://{req.host}:{req.port}"
        return f"{req.protocol}: (details unavailable)"

    def _recommend_fix(
        self, req: FirewallRequest | None,
    ) -> tuple[str, str, list[str]]:
        """Generate the most specific fix for mark, context manager, and TOML.

        Returns (mark_str, cm_str, toml_lines).
        """
        if req is None:
            # Fallback: coarse plugin-level fix
            return (
                f'@pytest.mark.allow("{self.plugin_name}")',
                f'with bigfoot.allow("{self.plugin_name}"):',
                [
                    "[tool.bigfoot.firewall]",
                    f'allow = ["{self.plugin_name}:*"]',
                ],
            )

        from bigfoot._firewall_request import (  # noqa: PLC0415
            Boto3FirewallRequest,
            FileIoFirewallRequest,
            HttpFirewallRequest,
            RedisFirewallRequest,
            SubprocessFirewallRequest,
        )

        if isinstance(req, HttpFirewallRequest):
            m_str = f'M(protocol="http", host="{req.host}", path="{req.path}")'
            return (
                f"@pytest.mark.allow({m_str})",
                f"with bigfoot.allow({m_str}):",
                [
                    "[tool.bigfoot.firewall]",
                    f'allow = ["{req.scheme}://{req.host}{req.path}"]',
                ],
            )

        if isinstance(req, RedisFirewallRequest):
            m_str = f'M(protocol="redis", host="{req.host}", port={req.port})'
            return (
                f"@pytest.mark.allow({m_str})",
                f"with bigfoot.allow({m_str}):",
                [
                    "[tool.bigfoot.firewall]",
                    f'allow = ["redis://{req.host}:{req.port}"]',
                ],
            )

        if isinstance(req, SubprocessFirewallRequest):
            m_str = f'M(protocol="subprocess", binary="{req.binary}")'
            return (
                f"@pytest.mark.allow({m_str})",
                f"with bigfoot.allow({m_str}):",
                [
                    "[tool.bigfoot.firewall]",
                    f'allow = ["subprocess:{req.binary}"]',
                ],
            )

        if isinstance(req, FileIoFirewallRequest):
            m_str = f'M(protocol="file_io", path="{req.path}", operation="{req.operation}")'
            return (
                f"@pytest.mark.allow({m_str})",
                f"with bigfoot.allow({m_str}):",
                [
                    "[tool.bigfoot.firewall]",
                    'allow = ["file_io:*"]  # or restrict to specific paths',
                ],
            )

        if isinstance(req, Boto3FirewallRequest):
            m_str = f'M(protocol="boto3", service="{req.service}", operation="{req.operation}")'
            return (
                f"@pytest.mark.allow({m_str})",
                f"with bigfoot.allow({m_str}):",
                [
                    "[tool.bigfoot.firewall]",
                    'allow = ["boto3:*"]  # or restrict to specific services',
                ],
            )

        # Generic network protocol
        m_str = f'M(protocol="{req.protocol}")'
        return (
            f"@pytest.mark.allow({m_str})",
            f"with bigfoot.allow({m_str}):",
            [
                "[tool.bigfoot.firewall]",
                f'allow = ["{req.protocol}:*"]',
            ],
        )


class GuardedCallWarning(UserWarning):
    """Emitted when guard mode is set to 'warn' and an I/O call fires
    outside a sandbox without allow() permission.

    Filter with:
        warnings.filterwarnings("ignore", category=GuardedCallWarning)
    """


class InvalidStateError(BigfootError):
    """Raised when a state-machine method is called from an invalid state.

    Attributes:
        source_id: Identifier of the source that triggered the call.
        method: Name of the method that was called.
        current_state: The state the machine was in when the call was made.
        valid_states: The frozenset of states from which the call is permitted.
    """

    def __init__(
        self,
        source_id: str,
        method: str,
        current_state: str,
        valid_states: frozenset[str],
    ) -> None:
        self.source_id = source_id
        self.method = method
        self.current_state = current_state
        self.valid_states = valid_states
        super().__init__(
            f"'{method}' called in state '{current_state}'; valid from: {valid_states!r}"
        )
