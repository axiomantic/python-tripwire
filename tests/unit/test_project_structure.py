"""Tests for Task 1: project skeleton structure.

Verifies that the required directories, files, and pyproject.toml
content are present and correct.
"""

import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib
from pathlib import Path

# Project root is two levels up from this test file:
# tests/unit/test_project_structure.py -> tests/ -> project root
PROJECT_ROOT = Path(__file__).parent.parent.parent


def test_tripwire_init_exists() -> None:
    init = PROJECT_ROOT / "src" / "tripwire" / "__init__.py"
    assert init.exists(), f"Expected {init} to exist"


def test_tripwire_plugins_init_exists() -> None:
    init = PROJECT_ROOT / "src" / "tripwire" / "plugins" / "__init__.py"
    assert init.exists(), f"Expected {init} to exist"


def test_tests_unit_init_exists() -> None:
    init = PROJECT_ROOT / "tests" / "unit" / "__init__.py"
    assert init.exists(), f"Expected {init} to exist"


def test_tests_integration_init_exists() -> None:
    init = PROJECT_ROOT / "tests" / "integration" / "__init__.py"
    assert init.exists(), f"Expected {init} to exist"


def test_tests_dogfood_init_exists() -> None:
    init = PROJECT_ROOT / "tests" / "dogfood" / "__init__.py"
    assert init.exists(), f"Expected {init} to exist"


def test_pyproject_toml_exists() -> None:
    pyproject = PROJECT_ROOT / "pyproject.toml"
    assert pyproject.exists(), f"Expected {pyproject} to exist"


def test_pyproject_toml_is_valid_toml() -> None:
    pyproject = PROJECT_ROOT / "pyproject.toml"
    content = pyproject.read_bytes()
    # tomllib.loads requires str but tomllib.load/loads needs bytes for load
    data = tomllib.loads(content.decode())
    assert isinstance(data, dict), "pyproject.toml must parse to a dict"


def test_pyproject_toml_has_pytest11_entry_point() -> None:
    pyproject = PROJECT_ROOT / "pyproject.toml"
    data = tomllib.loads(pyproject.read_bytes().decode())
    entry_points = data.get("project", {}).get("entry-points", {})
    pytest11 = entry_points.get("pytest11", {})
    assert pytest11 == {"tripwire": "tripwire.pytest_plugin"}, (
        f"[project.entry-points.pytest11] must be {{'tripwire': 'tripwire.pytest_plugin'}}, got {pytest11!r}"
    )


def test_pyproject_toml_package_name_is_tripwire() -> None:
    pyproject = PROJECT_ROOT / "pyproject.toml"
    data = tomllib.loads(pyproject.read_bytes().decode())
    name = data.get("project", {}).get("name")
    assert name == "pytest-tripwire", f"[project].name must be 'pytest-tripwire', got {name!r}"


def test_pyproject_toml_python_requirement() -> None:
    pyproject = PROJECT_ROOT / "pyproject.toml"
    data = tomllib.loads(pyproject.read_bytes().decode())
    requires_python = data.get("project", {}).get("requires-python")
    assert requires_python == ">=3.10", (
        f"[project].requires-python must be '>=3.10', got {requires_python!r}"
    )
