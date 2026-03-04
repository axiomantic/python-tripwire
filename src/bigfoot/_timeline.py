"""Interaction dataclass and Timeline class."""
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bigfoot._base_plugin import BasePlugin


@dataclass
class Interaction:
    """A single recorded event in the bigfoot timeline."""

    source_id: str
    # sequence=0 is a placeholder; Timeline.append() assigns the real number atomically.
    sequence: int
    details: dict[str, Any]
    plugin: "BasePlugin"
    _asserted: bool = field(default=False, init=False, repr=False)


class Timeline:
    """Thread-safe ordered list of Interactions."""

    def __init__(self) -> None:
        self._interactions: list[Interaction] = []
        self._lock: threading.Lock = threading.Lock()
        self._sequence: int = 0

    def append(self, interaction: Interaction) -> None:
        with self._lock:
            interaction.sequence = self._sequence
            self._sequence += 1
            self._interactions.append(interaction)

    def peek_next_unasserted(self) -> Interaction | None:
        with self._lock:
            for i in self._interactions:
                if not i._asserted:
                    return i
            return None

    def find_any_unasserted(
        self,
        predicate: Callable[[Interaction], bool],
    ) -> Interaction | None:
        """Used by in_any_order() assertions. Returns first matching unasserted interaction."""
        with self._lock:
            for i in self._interactions:
                if not i._asserted and predicate(i):
                    return i
            return None

    def mark_asserted(self, interaction: Interaction) -> None:
        with self._lock:
            interaction._asserted = True

    def all_unasserted(self) -> list[Interaction]:
        with self._lock:
            return [i for i in self._interactions if not i._asserted]
