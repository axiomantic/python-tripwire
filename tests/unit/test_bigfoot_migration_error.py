"""C1-T7: a deprecated `[tool.<old>]` section in pyproject.toml raises
ConfigMigrationError.

The migration check fires at the TOP of `load_tripwire_config`, BEFORE any
other validation (so it triggers even when the rest of the table would
otherwise be invalid). The companion test confirms `[tool.tripwire]` alone
parses cleanly without raising.

Note on string construction: the legacy package name is built without
writing the literal substring, so a future re-run of the rename sed pass
cannot accidentally rewrite this test's fixtures or assertions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tripwire._config import load_tripwire_config
from tripwire._errors import ConfigMigrationError, TripwireError

# Reconstruct the legacy package name without writing the literal substring.
_OLD_NAME = "b" + "igfoot"


def _write_pyproject(path: Path, body: str) -> None:
    (path / "pyproject.toml").write_text(body, encoding="utf-8")


def test_old_table_raises_migration(tmp_path: Path) -> None:
    """A pyproject.toml containing `[tool.<old>]` raises ConfigMigrationError
    with the expected migration hint message, and the error is a
    TripwireError subclass.
    """
    _write_pyproject(
        tmp_path,
        f"[tool.{_OLD_NAME}]\nguard = \"warn\"\n",
    )

    with pytest.raises(ConfigMigrationError) as exc_info:
        load_tripwire_config(tmp_path)

    expected_message = (
        f"{_OLD_NAME} was renamed to tripwire in 0.20.0; "
        "rename the table to [tool.tripwire]"
    )
    assert str(exc_info.value) == expected_message
    assert isinstance(exc_info.value, TripwireError)


def test_tripwire_table_does_not_trigger_migration_error(tmp_path: Path) -> None:
    """A pyproject.toml with only `[tool.tripwire]` parses cleanly:
    load_tripwire_config returns the tripwire sub-table verbatim.
    """
    _write_pyproject(tmp_path, "[tool.tripwire]\nguard = \"error\"\n")
    assert load_tripwire_config(tmp_path) == {"guard": "error"}


def test_migration_check_fires_before_other_validation(tmp_path: Path) -> None:
    """Even when the rest of `[tool.<old>]` would be otherwise invalid, the
    migration error fires first because the check is at the top of the loader.
    """
    _write_pyproject(
        tmp_path,
        (
            f"[tool.{_OLD_NAME}]\n"
            "guard = \"definitely-not-a-valid-value\"\n"
            "enabled_plugins = \"not-a-list\"\n"
        ),
    )
    with pytest.raises(ConfigMigrationError):
        load_tripwire_config(tmp_path)
