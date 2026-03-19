"""Tests for explicit-enable error behavior in resolve_enabled_plugins."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from bigfoot._errors import BigfootConfigError
from bigfoot._registry import PluginEntry, resolve_enabled_plugins


def _fake_entry(name: str, avail: str) -> PluginEntry:
    return PluginEntry(
        name=name,
        import_path=f"bigfoot.plugins.{name}_plugin",
        class_name=f"{name.title()}Plugin",
        availability_check=avail,
    )


class TestExplicitEnableError:
    """Explicit enable + missing dep raises BigfootConfigError."""

    def test_explicit_enable_missing_dep_raises(self) -> None:
        entry = _fake_entry("fakeplugin", "nonexistent_module_xyz")
        with patch("bigfoot._registry.PLUGIN_REGISTRY", (entry,)):
            with patch("bigfoot._registry.VALID_PLUGIN_NAMES", frozenset({"fakeplugin"})):
                with pytest.raises(BigfootConfigError, match="fakeplugin"):
                    resolve_enabled_plugins({"enabled_plugins": ["fakeplugin"]})

    def test_explicit_enable_missing_dep_error_message_contains_install_hint(self) -> None:
        entry = _fake_entry("fakeplugin", "nonexistent_module_xyz")
        with patch("bigfoot._registry.PLUGIN_REGISTRY", (entry,)):
            with patch("bigfoot._registry.VALID_PLUGIN_NAMES", frozenset({"fakeplugin"})):
                with pytest.raises(BigfootConfigError, match=r"pip install bigfoot\[fakeplugin\]"):
                    resolve_enabled_plugins({"enabled_plugins": ["fakeplugin"]})

    def test_default_enable_missing_dep_silent_skip(self) -> None:
        entry = _fake_entry("fakeplugin", "nonexistent_module_xyz")
        with patch("bigfoot._registry.PLUGIN_REGISTRY", (entry,)):
            with patch("bigfoot._registry.VALID_PLUGIN_NAMES", frozenset({"fakeplugin"})):
                result = resolve_enabled_plugins({})
                assert not any(e.name == "fakeplugin" for e in result)

    def test_disabled_plugins_not_affected(self) -> None:
        entry = _fake_entry("fakeplugin", "nonexistent_module_xyz")
        with patch("bigfoot._registry.PLUGIN_REGISTRY", (entry,)):
            with patch("bigfoot._registry.VALID_PLUGIN_NAMES", frozenset({"fakeplugin"})):
                result = resolve_enabled_plugins({"disabled_plugins": ["fakeplugin"]})
                assert not any(e.name == "fakeplugin" for e in result)


class TestAutoInstantiateExplicitEnable:
    """_auto_instantiate_plugins raises for explicitly enabled plugins that fail import."""

    def test_explicit_enable_import_failure_raises(self) -> None:
        entry = _fake_entry("fakeplugin", "always")
        with patch("bigfoot._registry.PLUGIN_REGISTRY", (entry,)):
            with patch("bigfoot._registry.VALID_PLUGIN_NAMES", frozenset({"fakeplugin"})):
                with patch("bigfoot._registry.get_plugin_class", side_effect=ImportError("no module")):
                    from bigfoot._verifier import StrictVerifier
                    with pytest.raises(BigfootConfigError, match="fakeplugin"):
                        v = StrictVerifier.__new__(StrictVerifier)
                        v._plugins = []
                        v._timeline = None  # type: ignore[assignment]
                        v._bigfoot_config = {"enabled_plugins": ["fakeplugin"]}
                        v._auto_instantiate_plugins()
