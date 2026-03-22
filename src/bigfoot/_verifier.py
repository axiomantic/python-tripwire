# src/bigfoot/_verifier.py
"""StrictVerifier, SandboxContext, and InAnyOrderContext."""

import warnings
from importlib.metadata import entry_points
from types import TracebackType
from typing import TYPE_CHECKING, Any, Protocol

from bigfoot._config import load_bigfoot_config
from bigfoot._context import _active_verifier, _any_order_depth
from bigfoot._errors import (
    AllWildcardAssertionError,
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
    from bigfoot._mock_plugin import ImportSiteMock, MockPlugin


class _HasSourceId(Protocol):
    """Structural type for objects that can be used as interaction sources."""

    source_id: str


class StrictVerifier:
    """Manages plugin lifecycle, sandbox context, and assertion verification.

    Do NOT instantiate directly. Use bigfoot's pytest integration:

        # In your test:
        bigfoot.http.mock_response("GET", "/api", json={"ok": True})
        with bigfoot:
            response = requests.get("/api")
        bigfoot.http.assert_request("GET", "/api", status=200)

    Direct instantiation bypasses forced assertion checking and will
    silently produce tests that pass without verifying anything.

    To access the verifier in a fixture or helper:
        verifier = bigfoot.current_verifier()
    """

    _suppress_direct_warning: bool = False

    def __init__(self) -> None:
        # Detect direct instantiation outside pytest
        if not StrictVerifier._suppress_direct_warning:
            from bigfoot._context import _current_test_verifier  # noqa: PLC0415

            if _current_test_verifier.get(None) is None:
                warnings.warn(
                    "StrictVerifier instantiated directly. "
                    "Use `with bigfoot:` for proper assertion enforcement. "
                    "Direct instantiation bypasses the pytest fixture that "
                    "enforces verify_all() at teardown.",
                    stacklevel=2,
                )
        self._plugins: list[BasePlugin] = []
        self._timeline: Timeline = Timeline()
        self._bigfoot_config: dict[str, Any] = load_bigfoot_config()
        self._auto_instantiate_plugins()

    def _auto_instantiate_plugins(self) -> None:
        """Create instances of all enabled plugins on this verifier.

        Checks config for enabled_plugins/disabled_plugins.
        Silently skips plugins whose optional deps are not installed.

        After built-in plugins, discovers 3rd-party plugins registered via
        the ``bigfoot.plugins`` entry point group. Entry point plugins are
        instantiated unconditionally (if installed, they should work).

        Constructor bugs are intentionally NOT caught: if a plugin's __init__
        raises a non-ImportError exception, it propagates. This is correct
        because a broken plugin constructor is a bug that should be fixed, not
        silently ignored.
        """
        from bigfoot._registry import get_plugin_class, resolve_enabled_plugins

        entries = resolve_enabled_plugins(self._bigfoot_config)
        for entry in entries:
            try:
                plugin_cls = get_plugin_class(entry)
                plugin_cls(self)  # BasePlugin.__init__ calls _register_plugin
            except ImportError:
                explicitly_enabled = set(self._bigfoot_config.get("enabled_plugins", []))
                if entry.name in explicitly_enabled:
                    from bigfoot._errors import BigfootConfigError
                    raise BigfootConfigError(
                        f"Plugin '{entry.name}' is in enabled_plugins but failed "
                        f"to import. Ensure its dependencies are installed: "
                        f"pip install bigfoot[{entry.name}]"
                    )
                # Silent skip only for default-enabled (not explicitly listed) plugins

        self._load_entrypoint_plugins()

    def _load_entrypoint_plugins(self) -> None:
        """Discover and instantiate 3rd-party plugins from entry points.

        Looks for plugins registered under the ``bigfoot.plugins`` entry point
        group. Each entry point should resolve to a BasePlugin subclass.
        Duplicate types (already registered by built-in registry) are silently
        skipped by _register_plugin.
        """
        for ep in entry_points(group="bigfoot.plugins"):
            try:
                plugin_cls = ep.load()
                plugin_cls(self)
            except ImportError:
                pass  # Optional dependency not installed; expected.
            except Exception as exc:
                warnings.warn(
                    f"bigfoot: entry point plugin {ep.name!r} failed to load: {exc}",
                    stacklevel=1,
                )

    def _register_plugin(self, plugin: "BasePlugin") -> None:
        for existing in self._plugins:
            if type(existing) is type(plugin):
                # Silently skip: plugin of this type already registered.
                # This happens when a test author manually creates a plugin
                # that was already auto-instantiated, or vice versa.
                return
        self._plugins.append(plugin)

    def mock(self, path: str) -> "ImportSiteMock":
        """Create an import-site mock. Path format: 'module:attr'.

        Lazily creates MockPlugin if not already registered.
        """

        mock_plugin = self._get_or_create_mock_plugin()
        return mock_plugin.create_import_site_mock(path, spy=False)

    def spy(self, path: str) -> "ImportSiteMock":
        """Create an import-site spy. Path format: 'module:attr'.

        Lazily creates MockPlugin if not already registered.
        """
        mock_plugin = self._get_or_create_mock_plugin()
        return mock_plugin.create_import_site_mock(path, spy=True)

    def _get_or_create_mock_plugin(self) -> "MockPlugin":
        """Return the existing MockPlugin or create one."""
        from bigfoot._mock_plugin import MockPlugin  # noqa: PLC0415

        for plugin in self._plugins:
            if isinstance(plugin, MockPlugin):
                return plugin
        return MockPlugin(self)

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
            # Detect all-wildcard assertions
            if expected and self._all_wildcards(expected):
                hint = candidate.plugin.format_assert_hint(candidate)
                raise AllWildcardAssertionError(interaction=candidate, hint=hint)
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
            # Detect all-wildcard assertions
            if expected and self._all_wildcards(expected):
                hint = interaction.plugin.format_assert_hint(interaction)
                raise AllWildcardAssertionError(interaction=interaction, hint=hint)
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
        """Run Enforcement 2 and 3. Called at teardown.

        Only interactions with enforce=True are checked. Interactions recorded
        during individual mock activation (enforce=False) are not required to
        be asserted.
        """
        self._assert_no_active_sandbox()
        unasserted = [i for i in self._timeline.all_unasserted() if i.enforce]
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

    @staticmethod
    def _all_wildcards(expected: dict[str, object]) -> bool:
        """Return True if ALL expected values are always-true matchers."""
        try:
            from dirty_equals import AnyThing  # noqa: PLC0415
        except ImportError:
            return False
        return all(isinstance(v, AnyThing) for v in expected.values())

    def _format_unasserted_error(self, unasserted: list[Interaction]) -> str:
        lines = [f"{len(unasserted)} interaction(s) were not asserted", ""]
        for i in unasserted:
            lines.append(f"  [sequence={i.sequence}] {i.plugin.format_interaction(i)}")
            lines.append("")
            lines.append("    Copy this assertion into your test:")
            lines.append("")
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
    """Activates all plugins and mocks. Supports both sync (with) and async (async with)."""

    def __init__(self, verifier: StrictVerifier) -> None:
        self._verifier = verifier
        self._token: contextvars.Token[Any] | None = None
        self._activated_mocks: list[Any] = []  # list[_BaseMock]

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

        # Activate all registered mocks with enforce=True
        if not errors:
            from bigfoot._mock_plugin import MockPlugin as _MP  # noqa: PLC0415, N814

            mock_plugin = next(
                (p for p in self._verifier._plugins if isinstance(p, _MP)), None
            )
            if mock_plugin is not None:
                for mock_obj in mock_plugin._mocks:
                    try:
                        mock_obj._activate(enforce=True)
                        self._activated_mocks.append(mock_obj)
                    except Exception as e:
                        errors.append(e)
                        break

        if errors:
            # Deactivate mocks in reverse order first
            for mock_obj in reversed(self._activated_mocks):
                try:
                    mock_obj._deactivate()
                except Exception as cleanup_e:
                    errors.append(cleanup_e)
            self._activated_mocks.clear()
            # Then deactivate plugins
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

        # Deactivate mocks in reverse order FIRST (before plugins)
        for mock_obj in reversed(self._activated_mocks):
            try:
                mock_obj._deactivate()
            except Exception as e:
                errors.append(e)
        self._activated_mocks.clear()

        # Then deactivate plugins (existing behavior)
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
