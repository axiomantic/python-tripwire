"""Smoke tests for the package rename and pytest plugin registration (C1).

These tests verify the rename's structural preconditions:
- The package imports under its new name and resolves to the on-disk source.
- The pytest entry-point registers under the new name and version.
- No source/test file still references the old name.

Note on string construction: the forbidden-name needle in C1-T5 is built
character-by-character so a future re-run of the rename sed pass cannot
accidentally rewrite the test's own assertion target.
"""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

# Rebuild "bigfoot" without writing the literal substring, so any future
# global rename sed pass cannot silently rewrite this assertion target.
_FORBIDDEN_NAME = "b" + "igfoot"


def _expected_version() -> str:
    """Source-of-truth version from pyproject.toml.

    Reading from pyproject avoids a hardcoded version literal that has to be
    updated alongside every release bump (the previous literal "0.20.0" broke
    the suite on the 0.20.1 release-trigger bump).
    """
    repo_root = Path(__file__).resolve().parents[2]
    with (repo_root / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


# C1-T1
def test_import_tripwire_resolves() -> None:
    """`import tripwire` resolves under src/tripwire/ and metadata version
    matches pyproject.toml.

    The codebase audit confirmed no `tripwire.__version__` symbol is exposed,
    so version is sourced exclusively from package metadata (pyproject.toml).
    """
    import tripwire

    module_path = Path(tripwire.__file__).resolve()
    repo_root = Path(__file__).resolve().parents[2]
    expected_pkg_dir = (repo_root / "src" / "tripwire").resolve()
    assert module_path.parent == expected_pkg_dir, (
        f"tripwire imported from {module_path}, expected under {expected_pkg_dir}"
    )

    assert importlib.metadata.version("pytest-tripwire") == _expected_version()


# C1-T2
@pytest.mark.allow("subprocess")
def test_pytest_entrypoint_registered() -> None:
    """The `tripwire` pytest11 entry-point is registered against the version
    declared in pyproject.toml AND `pytest --trace-config` actually loads
    `tripwire.pytest_plugin`.

    The two halves together guard against:
    - A stale legacy entry-point (e.g., `bigfoot`) still being registered.
    - The entry-point existing in metadata but failing to load at pytest start.
    """
    # Half 1: the pytest11 entry-point is registered against pytest-tripwire
    # at the version declared in pyproject.toml.
    dist = importlib.metadata.distribution("pytest-tripwire")
    pytest11_eps = [ep for ep in dist.entry_points if ep.group == "pytest11"]
    assert pytest11_eps == [
        importlib.metadata.EntryPoint(
            name="tripwire",
            value="tripwire.pytest_plugin",
            group="pytest11",
        )
    ], f"unexpected pytest11 entry-points: {pytest11_eps!r}"
    assert dist.version == _expected_version()

    # Half 2: `pytest --trace-config` actually loads tripwire.pytest_plugin.
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--trace-config", "--collect-only", "-q"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
        check=False,
    )
    combined = result.stdout + result.stderr
    assert "tripwire.pytest_plugin" in combined, (
        "tripwire.pytest_plugin not loaded in pytest --trace-config output:\n"
        f"{combined}"
    )


# C1-T5
def test_no_old_package_name_remains_in_source() -> None:
    """No .py file in src/ or tests/ contains the lowercased forbidden name,
    except for files that legitimately document the migration path.

    The forbidden name is the pre-0.20.0 package name (see _FORBIDDEN_NAME at
    module top). CHANGELOG.md and the proposal file are intentionally out of
    scope: this scan covers only Python source under src/ and tests/.

    Allowlist (files that MUST reference the old name to do their job):
    - src/tripwire/_config.py: implements the `[tool.<old>]` migration check.
    - src/tripwire/_errors.py: defines/documents `ConfigMigrationError`.
    - tests/unit/test_smoke_rename.py: this file (documents the rename).
    - tests/unit/test_bigfoot_migration_error.py: exercises the migration check.
    """
    repo_root = Path(__file__).resolve().parents[2]
    allowlist = frozenset(
        {
            "src/tripwire/_config.py",
            "src/tripwire/_errors.py",
            "tests/unit/test_smoke_rename.py",
            "tests/unit/test_bigfoot_migration_error.py",
        }
    )
    offenders: list[str] = []
    for base in ("src", "tests"):
        for py_file in (repo_root / base).rglob("*.py"):
            rel_posix = py_file.relative_to(repo_root).as_posix()
            if rel_posix in allowlist:
                continue
            text = py_file.read_text(encoding="utf-8")
            if _FORBIDDEN_NAME in text.lower():
                offenders.append(rel_posix)
    assert offenders == [], (
        f"Files still reference {_FORBIDDEN_NAME!r} after rename:\n"
        + "\n".join(offenders)
    )
