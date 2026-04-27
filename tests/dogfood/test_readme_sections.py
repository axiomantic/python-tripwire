"""Dogfood tests asserting README documentation sections exist.

These tests guard against accidental removal of documentation that
downstream users (and the design plan) depend on.
"""

from __future__ import annotations

import re
from pathlib import Path

README_PATH = Path(__file__).resolve().parents[2] / "README.md"


def test_readme_has_pick_default_section() -> None:
    """README contains a "Picking the right guard default" section heading.

    Guards against the C8 README addition being silently removed in a
    future README rewrite.
    """
    text = README_PATH.read_text(encoding="utf-8")
    pattern = re.compile(r"^##\s+Picking the right guard default\s*$", re.MULTILINE)
    assert pattern.search(text) is not None, (
        "README.md missing required section header "
        '"## Picking the right guard default"'
    )
