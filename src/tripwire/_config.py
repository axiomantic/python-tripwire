"""Config loading for tripwire: reads [tool.tripwire] from pyproject.toml."""

from pathlib import Path
from typing import Any

from tripwire._compat import tomllib


def load_tripwire_config(start: Path | None = None) -> dict[str, Any]:
    """Walk up from start (default: Path.cwd()) to find pyproject.toml.

    Returns the [tool.tripwire] table as a dict, or {} if:
    - no pyproject.toml found in start or any ancestor directory
    - pyproject.toml found but has no [tool.tripwire] section

    Raises tomllib.TOMLDecodeError if pyproject.toml is malformed.
    This is intentional: a malformed pyproject.toml is a user error that
    must not silently produce empty config.
    """
    from tripwire._errors import ConfigMigrationError  # noqa: PLC0415

    search = start or Path.cwd()
    for directory in (search, *search.parents):
        candidate = directory / "pyproject.toml"
        if candidate.is_file():
            with candidate.open("rb") as f:
                data = tomllib.load(f)
            if "bigfoot" in data.get("tool", {}):
                raise ConfigMigrationError(
                    "bigfoot was renamed to tripwire in 0.20.0; "
                    "rename the table to [tool.tripwire]"
                )
            result: dict[str, Any] = data.get("tool", {}).get("tripwire", {})
            return result
    return {}
