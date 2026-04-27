"""Shared patching primitives for tripwire plugins.

PatchSet manages a group of monkeypatches with apply/restore semantics.
Used by domain plugins to replace custom activate/deactivate boilerplate.
"""

from dataclasses import dataclass, field
from typing import Any

_ABSENT = object()


@dataclass
class PatchTarget:
    """Describes one monkeypatch to apply.

    Attributes:
        obj: The object to patch (e.g., redis.Redis).
        attr: The attribute name (e.g., "execute_command").
        replacement: The replacement function/value.
        original: Filled in by PatchSet.apply(); restored by PatchSet.restore().
            Uses _ABSENT sentinel to distinguish "not captured" from None.
    """

    _ABSENT = _ABSENT

    obj: Any = field(repr=True)
    attr: str = field(repr=True)
    replacement: Any = field(repr=True)
    original: Any = field(default=_ABSENT)


class PatchSet:
    """Manages a group of monkeypatches with apply/restore semantics.

    Usage:
        ps = PatchSet()
        ps.add(redis.Redis, "execute_command", _patched_execute_command)
        ps.apply()   # saves originals, installs replacements
        ps.restore() # restores originals in reverse order
    """

    def __init__(self) -> None:
        self._patches: list[PatchTarget] = []

    def add(self, obj: Any, attr: str, replacement: Any) -> None:  # noqa: ANN401
        """Register a patch target. Does not apply yet."""
        self._patches.append(PatchTarget(obj=obj, attr=attr, replacement=replacement))

    def apply(self) -> None:
        """Save originals and install replacements via setattr."""
        for p in self._patches:
            p.original = getattr(p.obj, p.attr)
            setattr(p.obj, p.attr, p.replacement)

    def restore(self) -> None:
        """Restore originals in reverse order via setattr.

        Uses _ABSENT sentinel to distinguish "not captured yet" from
        "original was None". Only skips restoration when original is
        _ABSENT (never captured).
        """
        for p in reversed(self._patches):
            if p.original is not _ABSENT:
                setattr(p.obj, p.attr, p.original)
                p.original = _ABSENT
