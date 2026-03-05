# src/bigfoot/_verifier.py
"""StrictVerifier, SandboxContext, and InAnyOrderContext."""

from types import TracebackType
from typing import TYPE_CHECKING, Any, Protocol

from bigfoot._context import _active_verifier, _any_order_depth
from bigfoot._errors import (
    AssertionInsideSandboxError,
    InteractionMismatchError,
    MissingAssertionFieldsError,
    UnassertedInteractionsError,
    UnusedMocksError,
    VerificationError,
)
from bigfoot._timeline import Interaction, Timeline

if TYPE_CHECKING:
    import contextvars

    from bigfoot._base_plugin import BasePlugin
    from bigfoot._mock_plugin import MockProxy


class _HasSourceId(Protocol):
    """Structural type for objects that can be used as interaction sources."""

    source_id: str


class StrictVerifier:
    """Central orchestrator: owns timeline, plugin registry, ContextVar routing."""

    def __init__(self) -> None:
        self._plugins: list[BasePlugin] = []
        self._timeline: Timeline = Timeline()

    def _register_plugin(self, plugin: "BasePlugin") -> None:
        for existing in self._plugins:
            if type(existing) is type(plugin):
                raise ValueError(
                    f"A {type(plugin).__name__} is already registered on this verifier. "
                    "Each plugin type may only be registered once per verifier."
                )
        self._plugins.append(plugin)

    def mock(self, name: str, wraps: object = None) -> "MockProxy":
        """Create or retrieve a named MockProxy. Lazily creates MockPlugin.

        If wraps is provided, method calls on the proxy with an empty queue
        are delegated to the wrapped object instead of raising
        UnmockedInteractionError. The interaction is still recorded.
        """
        from bigfoot._mock_plugin import MockPlugin

        # Find existing MockPlugin or create one
        mock_plugin: MockPlugin | None = None
        for plugin in self._plugins:
            if isinstance(plugin, MockPlugin):
                mock_plugin = plugin
                break
        if mock_plugin is None:
            mock_plugin = MockPlugin(self)

        return mock_plugin.get_or_create_proxy(name, wraps=wraps)

    def spy(self, name: str, real: object) -> "MockProxy":
        """Syntactic sugar for mock(name, wraps=real).

        Creates a MockProxy that always delegates to real, recording every
        call on the timeline without requiring mock configurations.
        """
        return self.mock(name, wraps=real)

    def _assert_no_active_sandbox(self) -> None:
        """Raise AssertionInsideSandboxError if this verifier's sandbox is currently active."""
        if _active_verifier.get() is self:
            raise AssertionInsideSandboxError()

    def assert_interaction(
        self,
        source: _HasSourceId,
        **expected: object,
    ) -> None:
        """Assert the next interaction matches source and expected fields.

        Completeness enforcement: after matching by source_id, the interaction's
        plugin.assertable_fields() is called. Any field in the returned set that
        is absent from **expected raises MissingAssertionFieldsError immediately,
        before the field-value match check.
        """
        self._assert_no_active_sandbox()
        source_id: str = source.source_id

        if _any_order_depth.get() > 0:
            # Two-pass in_any_order: Pass 1 — find an interaction matching source_id only,
            # then enforce completeness. Pass 2 — verify full field-value match.
            candidate = self._timeline.find_any_unasserted(lambda i: i.source_id == source_id)
            if candidate is None:
                remaining = self._timeline.all_unasserted()
                hint = self._format_mismatch_error(
                    source_id, expected, actual=None, remaining=remaining
                )
                raise InteractionMismatchError(
                    expected={"source_id": source_id, **expected},
                    actual=None,
                    hint=hint,
                )
            # Completeness check against the candidate
            required_fields = candidate.plugin.assertable_fields(candidate)
            missing = required_fields - set(expected.keys())
            if missing:
                raise MissingAssertionFieldsError(missing_fields=frozenset(missing))
            # Pass 2 — full field-value match (searching all unasserted, not just candidate)
            interaction = self._timeline.find_any_unasserted(
                lambda i: i.source_id == source_id and i.plugin.matches(i, expected)
            )
            if interaction is None:
                remaining = self._timeline.all_unasserted()
                hint = self._format_mismatch_error(
                    source_id, expected, actual=None, remaining=remaining
                )
                raise InteractionMismatchError(
                    expected={"source_id": source_id, **expected},
                    actual=None,
                    hint=hint,
                )
        else:
            interaction = self._timeline.peek_next_unasserted()
            if interaction is None or interaction.source_id != source_id:
                remaining = self._timeline.all_unasserted()
                hint = self._format_mismatch_error(
                    source_id, expected, actual=interaction, remaining=remaining
                )
                raise InteractionMismatchError(
                    expected={"source_id": source_id, **expected},
                    actual=interaction,
                    hint=hint,
                )
            # Completeness enforcement: check for missing required fields
            required_fields = interaction.plugin.assertable_fields(interaction)
            missing = required_fields - set(expected.keys())
            if missing:
                raise MissingAssertionFieldsError(missing_fields=frozenset(missing))
            if not interaction.plugin.matches(interaction, expected):
                remaining = self._timeline.all_unasserted()
                hint = self._format_mismatch_error(
                    source_id, expected, actual=interaction, remaining=remaining
                )
                raise InteractionMismatchError(
                    expected={"source_id": source_id, **expected},
                    actual=interaction,
                    hint=hint,
                )

        self._timeline.mark_asserted(interaction)

    def in_any_order(self) -> "InAnyOrderContext":
        """Context manager: assertions within block matched in any order.

        IMPORTANT: in_any_order() relaxes ordering globally across ALL plugins.
        It is not possible to relax ordering for only one plugin type. Any
        assert_interaction() call within this block will match any unasserted
        interaction regardless of which plugin (mock or HTTP) recorded it.
        """
        self._assert_no_active_sandbox()
        return InAnyOrderContext()

    def verify_all(self) -> None:
        """Run Enforcement 2 and 3. Called at teardown."""
        self._assert_no_active_sandbox()
        unasserted = self._timeline.all_unasserted()
        unused: list[tuple[BasePlugin, Any]] = []

        for plugin in self._plugins:
            for mock_config in plugin.get_unused_mocks():
                unused.append((plugin, mock_config))

        has_unasserted = bool(unasserted)
        has_unused = bool(unused)

        if not has_unasserted and not has_unused:
            return

        if has_unasserted and has_unused:
            raise VerificationError(
                unasserted=UnassertedInteractionsError(
                    interactions=unasserted,
                    hint=self._format_unasserted_error(unasserted),
                ),
                unused=UnusedMocksError(
                    mocks=[mc for _, mc in unused],
                    hint=self._format_unused_mocks_error(unused),
                ),
            )
        elif has_unasserted:
            raise UnassertedInteractionsError(
                interactions=unasserted,
                hint=self._format_unasserted_error(unasserted),
            )
        else:
            raise UnusedMocksError(
                mocks=[mc for _, mc in unused],
                hint=self._format_unused_mocks_error(unused),
            )

    def sandbox(self) -> "SandboxContext":
        """Return SandboxContext for this verifier (sync + async)."""
        return SandboxContext(self)

    def _format_mismatch_error(
        self,
        source_id: str,
        expected: dict[str, Any],
        actual: Interaction | None,
        remaining: list[Interaction],
    ) -> str:
        lines = [
            "Next interaction did not match assertion",
            "",
            f"  Expected source: {source_id}",
        ]
        if expected:
            fields_str = ", ".join(f"{k}={v!r}" for k, v in expected.items())
            lines.append(f"  Expected fields: {fields_str}")
        lines.append("")
        if actual is None:
            lines.append("  Actual next interaction: (none — timeline is empty or all asserted)")
        else:
            lines.append(f"  Actual next interaction (sequence={actual.sequence}):")
            lines.append(f"    {actual.plugin.format_interaction(actual)}")
        if remaining:
            lines.append("")
            lines.append(f"  Remaining timeline ({len(remaining)} interaction(s)):")
            for r in remaining:
                lines.append(f"    [{r.sequence}] {r.plugin.format_interaction(r)}")
        lines.append("")
        lines.append("  Hint: Did you forget an in_any_order() block?")
        return "\n".join(lines)

    def _format_unasserted_error(self, unasserted: list[Interaction]) -> str:
        lines = [f"{len(unasserted)} interaction(s) were not asserted", ""]
        for i in unasserted:
            lines.append(f"  [sequence={i.sequence}] {i.plugin.format_interaction(i)}")
            lines.append("    To assert this interaction:")
            lines.append(f"      {i.plugin.format_assert_hint(i)}")
            lines.append("")
        return "\n".join(lines)

    def _format_unused_mocks_error(self, unused: list[tuple["BasePlugin", Any]]) -> str:
        lines = [f"{len(unused)} mock(s) were registered but never triggered", ""]
        for plugin, mock_config in unused:
            lines.append(f"  {plugin.format_unused_mock_hint(mock_config)}")
            lines.append("")
        return "\n".join(lines)


class SandboxContext:
    """Activates all plugins. Supports both sync (with) and async (async with)."""

    def __init__(self, verifier: StrictVerifier) -> None:
        self._verifier = verifier
        self._token: contextvars.Token[Any] | None = None

    def _enter(self) -> StrictVerifier:
        self._token = _active_verifier.set(self._verifier)
        activated_so_far: list[BasePlugin] = []
        errors: list[BaseException] = []

        for plugin in self._verifier._plugins:
            try:
                plugin.activate()
                activated_so_far.append(plugin)
            except Exception as e:
                errors.append(e)
                break

        if errors:
            for plugin in reversed(activated_so_far):
                try:
                    plugin.deactivate()
                except Exception as cleanup_e:
                    errors.append(cleanup_e)
            if self._token is not None:
                _active_verifier.reset(self._token)
            raise BaseExceptionGroup("bigfoot sandbox activation failed", errors)

        return self._verifier

    def _exit(self) -> None:
        errors: list[BaseException] = []
        for plugin in reversed(self._verifier._plugins):
            try:
                plugin.deactivate()
            except Exception as e:
                errors.append(e)
        if self._token is not None:
            _active_verifier.reset(self._token)
        if errors:
            raise BaseExceptionGroup("bigfoot sandbox deactivation failed", errors)

    def __enter__(self) -> StrictVerifier:
        return self._enter()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._exit()

    async def __aenter__(self) -> StrictVerifier:
        return self._enter()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._exit()


class InAnyOrderContext:
    """Context manager: assertions within block matched in any order."""

    def __init__(self) -> None:
        self._token: contextvars.Token[int] | None = None

    def __enter__(self) -> "InAnyOrderContext":
        current = _any_order_depth.get(0)
        self._token = _any_order_depth.set(current + 1)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._token is not None:
            _any_order_depth.reset(self._token)

    async def __aenter__(self) -> "InAnyOrderContext":
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.__exit__(exc_type, exc_val, tb)
