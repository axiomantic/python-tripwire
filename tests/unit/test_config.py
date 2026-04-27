"""Unit tests for tripwire._config and plugin config integration."""

import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib
from pathlib import Path
from typing import Any

import pytest

from tripwire._config import load_tripwire_config

httpx = pytest.importorskip("httpx")
requests = pytest.importorskip("requests")

from tripwire._verifier import StrictVerifier  # noqa: E402
from tripwire.plugins.http import HttpPlugin  # noqa: E402

# ---------------------------------------------------------------------------
# Stub verifier for unit-testing load_config() in isolation
# ---------------------------------------------------------------------------


class _StubVerifier:
    """Minimal stub for StrictVerifier — only attributes that HttpPlugin touches."""

    def __init__(self, tripwire_config: dict[str, Any] | None = None) -> None:
        self._tripwire_config: dict[str, Any] = tripwire_config if tripwire_config is not None else {}
        self.registered_plugins: list[Any] = []

    def _register_plugin(self, plugin: Any) -> None:
        self.registered_plugins.append(plugin)


# ---------------------------------------------------------------------------
# Tests for load_tripwire_config()
# ---------------------------------------------------------------------------


def test_no_pyproject_returns_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No pyproject.toml anywhere in the walk → returns {}."""
    monkeypatch.chdir(tmp_path)
    result = load_tripwire_config(start=tmp_path)
    assert result == {}


def test_pyproject_without_tripwire_section_returns_empty(tmp_path: Path) -> None:
    """pyproject.toml present but no [tool.tripwire] → returns {}."""
    (tmp_path / "pyproject.toml").write_text("[tool.other]\nkey = 1\n")
    result = load_tripwire_config(start=tmp_path)
    assert result == {}


def test_pyproject_with_tripwire_http_section(tmp_path: Path) -> None:
    """pyproject.toml with [tool.tripwire.http] → returns correct nested dict."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.tripwire.http]\nrequire_response = true\n"
    )
    result = load_tripwire_config(start=tmp_path)
    assert result == {"http": {"require_response": True}}


def test_malformed_toml_propagates_error(tmp_path: Path) -> None:
    """Malformed pyproject.toml → tomllib.TOMLDecodeError propagates."""
    (tmp_path / "pyproject.toml").write_text("this is not valid toml ===\n")
    with pytest.raises(tomllib.TOMLDecodeError):
        load_tripwire_config(start=tmp_path)


def test_start_param_used_instead_of_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """start= kwarg targets a different directory than cwd."""
    # cwd has no pyproject.toml
    other = tmp_path / "other"
    other.mkdir()
    (other / "pyproject.toml").write_text("[tool.tripwire.http]\nrequire_response = false\n")
    # Search from 'other', not cwd
    result = load_tripwire_config(start=other)
    assert result == {"http": {"require_response": False}}


def test_walks_up_to_parent(tmp_path: Path) -> None:
    """pyproject.toml in parent dir, child has none → finds parent file."""
    (tmp_path / "pyproject.toml").write_text("[tool.tripwire.http]\nrequire_response = true\n")
    child = tmp_path / "child"
    child.mkdir()
    result = load_tripwire_config(start=child)
    assert result == {"http": {"require_response": True}}


def test_first_pyproject_wins(tmp_path: Path) -> None:
    """Stops at the first pyproject.toml found (nearest ancestor)."""
    (tmp_path / "pyproject.toml").write_text("[tool.tripwire.http]\nrequire_response = true\n")
    child = tmp_path / "child"
    child.mkdir()
    (child / "pyproject.toml").write_text("[tool.other]\nkey = 1\n")
    # child has pyproject.toml without [tool.tripwire], so result is {}
    result = load_tripwire_config(start=child)
    assert result == {}


# ---------------------------------------------------------------------------
# Tests for HttpPlugin.load_config() in isolation
# ---------------------------------------------------------------------------


def test_load_config_require_response_true() -> None:
    """load_config with require_response=True sets _require_response to True."""
    stub = _StubVerifier(tripwire_config={})
    plugin = HttpPlugin(stub)  # type: ignore[arg-type]
    # Default is True
    assert plugin._require_response is True
    plugin.load_config({"require_response": True})
    assert plugin._require_response is True


def test_load_config_require_response_false() -> None:
    """load_config with require_response=False sets _require_response to False."""
    stub = _StubVerifier(tripwire_config={})
    plugin = HttpPlugin(stub)  # type: ignore[arg-type]
    # Start at True via constructor override
    plugin._require_response = True
    plugin.load_config({"require_response": False})
    assert plugin._require_response is False


def test_load_config_wrong_type_raises_type_error() -> None:
    """load_config with a non-bool require_response raises TypeError."""
    stub = _StubVerifier(tripwire_config={})
    plugin = HttpPlugin(stub)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="require_response") as exc_info:
        plugin.load_config({"require_response": "yes"})
    assert "bool" in str(exc_info.value)
    assert "str" in str(exc_info.value)


def test_load_config_int_type_raises_type_error() -> None:
    """TOML integer (not bool) for require_response raises TypeError."""
    stub = _StubVerifier(tripwire_config={})
    plugin = HttpPlugin(stub)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="require_response") as exc_info:
        plugin.load_config({"require_response": 1})
    assert "bool" in str(exc_info.value)
    assert "int" in str(exc_info.value)


def test_load_config_missing_key_no_op() -> None:
    """load_config with empty dict is a no-op; _require_response unchanged."""
    stub = _StubVerifier(tripwire_config={})
    plugin = HttpPlugin(stub)  # type: ignore[arg-type]
    plugin._require_response = True
    plugin.load_config({})
    assert plugin._require_response is True


def test_load_config_unknown_keys_ignored() -> None:
    """Unknown keys in config dict are silently ignored (forward-compat)."""
    stub = _StubVerifier(tripwire_config={})
    plugin = HttpPlugin(stub)  # type: ignore[arg-type]
    # Should not raise
    plugin.load_config({"require_response": True, "unknown_key": 42, "future_option": "value"})
    assert plugin._require_response is True


# ---------------------------------------------------------------------------
# Integration tests: StrictVerifier picks up config from pyproject.toml
# ---------------------------------------------------------------------------


def test_http_plugin_reads_require_response_from_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full integration: require_response=true in TOML → HttpPlugin._require_response is True."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.tripwire.http]\nrequire_response = true\n"
    )
    monkeypatch.chdir(tmp_path)
    verifier = StrictVerifier()
    # Retrieve the auto-created HttpPlugin
    plugin = next(p for p in verifier._plugins if isinstance(p, HttpPlugin))
    assert plugin._require_response is True


def test_config_absent_preserves_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No [tool.tripwire.http] in pyproject.toml → _require_response remains True (the default)."""
    (tmp_path / "pyproject.toml").write_text("[tool.other]\nkey = 1\n")
    monkeypatch.chdir(tmp_path)
    verifier = StrictVerifier()
    # Retrieve the auto-created HttpPlugin
    plugin = next(p for p in verifier._plugins if isinstance(p, HttpPlugin))
    assert plugin._require_response is True


def test_no_pyproject_preserves_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No pyproject.toml at all → _require_response remains True (the default)."""
    monkeypatch.chdir(tmp_path)
    verifier = StrictVerifier()
    # Retrieve the auto-created HttpPlugin
    plugin = next(p for p in verifier._plugins if isinstance(p, HttpPlugin))
    assert plugin._require_response is True


def test_require_response_wrong_type_raises_on_plugin_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wrong type in TOML raises TypeError at StrictVerifier construction time.

    With auto-instantiation, HttpPlugin is created during StrictVerifier.__init__,
    so the TypeError propagates from the verifier constructor.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.tripwire.http]\nrequire_response = "yes"\n'
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(TypeError, match="require_response"):
        StrictVerifier()
