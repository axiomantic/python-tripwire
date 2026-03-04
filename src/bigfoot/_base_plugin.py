"""BasePlugin abstract base class for all bigfoot plugins."""
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bigfoot._timeline import Interaction
    from bigfoot._verifier import StrictVerifier


class BasePlugin(ABC):
    """Abstract base for all bigfoot plugins.

    Subclasses must implement all abstract methods and maintain class-level
    _install_count and _install_lock for reference-counted activation.
    """

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

    @abstractmethod
    def get_unused_mocks(self) -> list[Any]:
        """Return MockConfig/HttpMockConfig objects never triggered. Exclude required=False."""

    @abstractmethod
    def format_unused_mock_hint(self, mock_config: object) -> str:
        """Hint for each unused mock: show 'remove this mock' OR 'mark required=False'."""

    def record(self, interaction: "Interaction") -> None:
        """Concrete method: append interaction to the verifier's shared timeline.

        This is NOT abstract -- all plugins share the same implementation.
        Calls self.verifier._timeline.append(interaction), which assigns the
        sequence number atomically under the timeline lock.
        """
        self.verifier._timeline.append(interaction)
