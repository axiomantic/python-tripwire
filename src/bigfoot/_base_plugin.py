"""BasePlugin abstract base class for all bigfoot plugins."""

import threading
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

from bigfoot._recording import _recording_in_progress

if TYPE_CHECKING:
    from bigfoot._timeline import Interaction
    from bigfoot._verifier import StrictVerifier


class BasePlugin(ABC):
    """Abstract base for all bigfoot plugins.

    To write a custom plugin:

    1. Subclass BasePlugin
    2. Implement all abstract methods (matches, format_interaction,
       format_mock_hint, format_unmocked_hint, format_assert_hint,
       get_unused_mocks, format_unused_mock_hint)
    3. Override install_patches() and restore_patches() for monkeypatching
    4. Register via entry_points in pyproject.toml or [tool.bigfoot]
    5. Use ``with bigfoot:`` in tests (never verifier.sandbox() directly)

    Import from bigfoot directly:
        from bigfoot import BasePlugin, Interaction, Timeline

    Subclasses get per-class _install_count and _install_lock automatically
    via __init_subclass__. The default activate()/deactivate() implementations
    provide reference-counted patching: override install_patches() and
    restore_patches() instead of activate()/deactivate() for standard
    ref-counting behavior.
    """

    supports_guard: ClassVar[bool] = True

    # Shared patching infrastructure -- each subclass gets its own via __init_subclass__
    _install_count: ClassVar[int] = 0
    _install_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init_subclass__(cls, **kwargs: Any) -> None:  # noqa: ANN401
        """Give each plugin subclass its own lock and counter."""
        super().__init_subclass__(**kwargs)
        cls._install_count = 0
        cls._install_lock = threading.Lock()

    def __init__(self, verifier: "StrictVerifier") -> None:
        self.verifier = verifier
        verifier._register_plugin(self)

    def activate(self) -> None:
        """Reference-counted activation. Calls check_conflicts() and
        install_patches() on first activation.

        Plugins that need custom activation logic can override this method
        directly. Plugins that use standard ref-counting should override
        install_patches() and restore_patches() instead.
        """
        with type(self)._install_lock:
            if type(self)._install_count == 0:
                self.check_conflicts()
                self.install_patches()
            type(self)._install_count += 1

    def deactivate(self) -> None:
        """Reference-counted deactivation. Calls restore_patches() when
        count reaches 0.

        Plugins that need custom deactivation logic can override this method
        directly. Plugins that use standard ref-counting should override
        install_patches() and restore_patches() instead.
        """
        with type(self)._install_lock:
            type(self)._install_count = max(0, type(self)._install_count - 1)
            if type(self)._install_count == 0:
                self.restore_patches()

    def check_conflicts(self) -> None:
        """Check for conflicting patches before installing.

        Default: no-op. Domain plugins that need conflict detection override
        this to raise ConflictError when foreign patches are detected.

        Called by activate() when _install_count goes 0 -> 1, before
        install_patches().
        """

    def install_patches(self) -> None:
        """Install monkeypatches. Called once when install_count goes 0 -> 1.

        Default: no-op. Plugins that do import-site patching override this.
        """

    def restore_patches(self) -> None:
        """Restore original functions. Called once when install_count goes 1 -> 0.

        Default: no-op. Plugins that do import-site patching override this.
        """

    @abstractmethod
    def matches(self, interaction: "Interaction", expected: dict[str, Any]) -> bool:
        """Return True if interaction matches expected fields. Never raise."""

    @abstractmethod
    def format_interaction(self, interaction: "Interaction") -> str:
        """One-line human-readable description.

        Example: '[HttpPlugin] POST https://api.example.com/v1'
        """

    @abstractmethod
    def format_mock_hint(self, interaction: "Interaction") -> str:
        """Copy-pasteable code to mock this interaction (resolves UnmockedInteractionError)."""

    @abstractmethod
    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        """Copy-pasteable code snippet for mocking a call that failed before reaching the
        timeline."""

    @abstractmethod
    def format_assert_hint(self, interaction: "Interaction") -> str:
        """Copy-pasteable code to assert this interaction (resolves UnassertedInteractionsError)."""

    def assertable_fields(self, interaction: "Interaction") -> frozenset[str]:
        """Return the set of field names that must appear in **expected when asserting
        this interaction.

        Default implementation: return all keys in interaction.details as assertable
        fields. This is correct for any plugin that stores only user-assertable data
        in details. Plugins with no-data steps (close, commit, etc.) must override
        this to return frozenset() for those steps.

        The verifier calls this after matching by source_id to enforce completeness:
        any field in the returned set that is absent from **expected causes
        MissingAssertionFieldsError to be raised.
        """
        return frozenset(interaction.details.keys())

    @abstractmethod
    def get_unused_mocks(self) -> list[Any]:
        """Return MockConfig/HttpMockConfig objects never triggered. Exclude required=False."""

    @abstractmethod
    def format_unused_mock_hint(self, mock_config: object) -> str:
        """Hint for each unused mock: show 'remove this mock' OR 'mark required=False'."""

    @classmethod
    def config_key(cls) -> str | None:
        """Return the [tool.bigfoot.<key>] section name for this plugin.

        Return None to opt out of configuration entirely. Plugins that return
        None receive no load_config() call from concrete subclass __init__.

        Example: HttpPlugin returns "http", mapping to [tool.bigfoot.http].
        """
        return None

    def load_config(self, config: dict[str, Any]) -> None:
        """Apply configuration from the plugin's [tool.bigfoot.<key>] sub-table.

        Called as the last line of each concrete plugin's __init__, after all
        instance attributes have been set. The default implementation is a no-op.
        Plugins override this to read and validate their options.

        Args:
            config: The parsed sub-table dict. Empty dict ({}) when no config
                    section is present for this plugin.
        """

    def record(self, interaction: "Interaction") -> None:
        """Concrete method: append interaction to the verifier's shared timeline.

        Sets _recording_in_progress for the duration of the append so that
        Timeline.mark_asserted() can detect the auto-assert anti-pattern and
        raise AutoAssertError immediately.
        """
        token = _recording_in_progress.set(True)
        try:
            self.verifier._timeline.append(interaction)
        finally:
            _recording_in_progress.reset(token)
