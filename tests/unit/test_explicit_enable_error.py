"""Tests for explicit-enable error behavior in resolve_enabled_plugins."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tripwire._errors import TripwireConfigError
from tripwire._registry import PluginEntry, resolve_enabled_plugins


def _fake_entry(name: str, avail: str) -> PluginEntry:
    return PluginEntry(
        name=name,
        import_path=f"tripwire.plugins.{name}_plugin",
        class_name=f"{name.title()}Plugin",
        availability_check=avail,
    )


class TestExplicitEnableError:
    """Explicit enable + missing dep raises TripwireConfigError."""

    def test_explicit_enable_missing_dep_raises(self) -> None:
        entry = _fake_entry("fakeplugin", "nonexistent_module_xyz")
        with patch("tripwire._registry.PLUGIN_REGISTRY", (entry,)):
            with patch("tripwire._registry.VALID_PLUGIN_NAMES", frozenset({"fakeplugin"})):
                with pytest.raises(TripwireConfigError, match="fakeplugin"):
                    resolve_enabled_plugins({"enabled_plugins": ["fakeplugin"]})

    def test_explicit_enable_missing_dep_error_message_contains_install_hint(self) -> None:
        entry = _fake_entry("fakeplugin", "nonexistent_module_xyz")
        with patch("tripwire._registry.PLUGIN_REGISTRY", (entry,)):
            with patch("tripwire._registry.VALID_PLUGIN_NAMES", frozenset({"fakeplugin"})):
                with pytest.raises(TripwireConfigError, match=r"pip install pytest-tripwire\[fakeplugin\]"):
                    resolve_enabled_plugins({"enabled_plugins": ["fakeplugin"]})

    def test_default_enable_missing_dep_silent_skip(self) -> None:
        entry = _fake_entry("fakeplugin", "nonexistent_module_xyz")
        with patch("tripwire._registry.PLUGIN_REGISTRY", (entry,)):
            with patch("tripwire._registry.VALID_PLUGIN_NAMES", frozenset({"fakeplugin"})):
                result = resolve_enabled_plugins({})
                assert not any(e.name == "fakeplugin" for e in result)

    def test_disabled_plugins_not_affected(self) -> None:
        entry = _fake_entry("fakeplugin", "nonexistent_module_xyz")
        with patch("tripwire._registry.PLUGIN_REGISTRY", (entry,)):
            with patch("tripwire._registry.VALID_PLUGIN_NAMES", frozenset({"fakeplugin"})):
                result = resolve_enabled_plugins({"disabled_plugins": ["fakeplugin"]})
                assert not any(e.name == "fakeplugin" for e in result)


class TestAutoInstantiateExplicitEnable:
    """_auto_instantiate_plugins raises for explicitly enabled plugins that fail import."""

    def test_explicit_enable_import_failure_raises(self) -> None:
        entry = _fake_entry("fakeplugin", "always")
        with patch("tripwire._registry.PLUGIN_REGISTRY", (entry,)):
            with patch("tripwire._registry.VALID_PLUGIN_NAMES", frozenset({"fakeplugin"})):
                with patch("tripwire._registry.get_plugin_class", side_effect=ImportError("no module")):
                    from tripwire._verifier import StrictVerifier
                    with pytest.raises(TripwireConfigError, match="fakeplugin"):
                        v = StrictVerifier.__new__(StrictVerifier)
                        v._plugins = []
                        v._timeline = None  # type: ignore[assignment]
                        v._tripwire_config = {"enabled_plugins": ["fakeplugin"]}
                        v._auto_instantiate_plugins()
