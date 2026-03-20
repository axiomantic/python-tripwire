"""BasePlugin abstract base class for all bigfoot plugins."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

from bigfoot._recording import _recording_in_progress

if TYPE_CHECKING:
    from bigfoot._timeline import Interaction
    from bigfoot._verifier import StrictVerifier


class BasePlugin(ABC):
    """Abstract base for all bigfoot plugins.

    Subclasses must implement all abstract methods and maintain class-level
    _install_count and _install_lock for reference-counted activation.
    """

    supports_guard: ClassVar[bool] = True

    def __init__(self, verifier: "StrictVerifier") -> None:
        self.verifier = verifier
        verifier._register_plugin(self)

    @abstractmethod
    def activate(self) -> None:
        """Install interceptors. Reference-counted: only install if _install_count == 0.
        Must be thread-safe. Check for conflicts before installing."""

    @abstractmethod
    def deactivate(self) -> None:
        """Remove interceptors. Decrement _install_count. Only restore if count reaches 0.
        Must not raise (collect errors for caller to raise after ContextVar reset)."""

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
