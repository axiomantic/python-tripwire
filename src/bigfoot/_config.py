"""Config loading for bigfoot: reads [tool.bigfoot] from pyproject.toml."""

from pathlib import Path
from typing import Any

from bigfoot._compat import tomllib


def load_bigfoot_config(start: Path | None = None) -> dict[str, Any]:
    """Walk up from start (default: Path.cwd()) to find pyproject.toml.

    Returns the [tool.bigfoot] table as a dict, or {} if:
    - no pyproject.toml found in start or any ancestor directory
    - pyproject.toml found but has no [tool.bigfoot] section

    Raises tomllib.TOMLDecodeError if pyproject.toml is malformed.
    This is intentional: a malformed pyproject.toml is a user error that
    must not silently produce empty config.
    """
    search = start or Path.cwd()
    for directory in (search, *search.parents):
        candidate = directory / "pyproject.toml"
        if candidate.is_file():
            with candidate.open("rb") as f:
                data = tomllib.load(f)
            result: dict[str, Any] = data.get("tool", {}).get("bigfoot", {})
            return result
    return {}
