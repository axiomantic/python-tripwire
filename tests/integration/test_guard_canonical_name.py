"""Regression tests: per-protocol guard overrides key on the canonical
plugin registry name, not on the source_id prefix.

Several plugins declare ``guard_prefixes`` that differ from their canonical
registry name. Examples in the registry today:

- ``database`` (registry) / ``db`` (prefix)  -- DatabasePlugin
- ``async_subprocess`` (registry) / ``asyncio`` (prefix)
- ``async_websocket`` (registry) / ``websocket`` (prefix)
- ``sync_websocket``  (registry) / ``websocket`` (prefix)

A user writing ``[tool.tripwire.guard]\\ndatabase = "off"`` and triggering a
``db:query`` source_id MUST see the override applied. Before the canonical
-name fix, dispatch in ``get_verifier_or_raise`` keyed on the source_id
prefix (``"db"``), so the override silently failed and surfaced as an
``UnsafePassthroughError`` / ``GuardedCallError`` reporting ``plugin_name``
``"db"`` instead of ``"database"``.

The marker normalization regression covers a related papercut: feeding a
mixed-case string like ``"Warn"`` or ``"STRICT"`` into
``@pytest.mark.guard(...)`` previously bypassed normalization (the marker
handler did ``GuardLevels(default=arg, overrides={})`` with no validation),
so the misspelled level slipped through to dispatch and failed there with
a less actionable error. Routing through ``_resolve_guard_levels`` gives
the marker the same alias mapping (``"strict"`` -> ``"error"``), case
folding, and validation as the TOML loader.
"""

from __future__ import annotations

import textwrap

import pytest

from tripwire._config import GuardLevels
from tripwire._context import (
    GuardPassThrough,
    _guard_active,
    _guard_levels,
    get_verifier_or_raise,
)
from tripwire._errors import GuardedCallError
from tripwire._firewall_request import NetworkFirewallRequest

pytestmark = pytest.mark.integration

pytest_plugins = ["pytester"]


def test_override_on_canonical_name_applies_to_prefix_source_id() -> None:
    """Setting ``database = "off"`` MUST suppress a ``db:query`` call.

    DatabasePlugin's canonical registry name is ``"database"`` and its
    ``guard_prefixes`` is ``("db",)``. The dispatch in
    ``get_verifier_or_raise`` must look up the per-protocol override by
    canonical name, not by the source_id prefix.

    PATH:  get_verifier_or_raise -> lookup_plugin_class_by_name("db")
           returns (DatabasePlugin, "database") -> Branch 3b -> level =
           overrides["database"] == "off" -> raise GuardPassThrough.
    CHECK: GuardPassThrough is raised (no error, no warning).
    MUTATION: Restoring the bug (overrides.get(prefix, ...)) makes the
              override invisible; DatabasePlugin is passthrough_safe=False
              so the warn-default branch raises UnsafePassthroughError
              and the test fails.
    """
    levels_token = _guard_levels.set(
        GuardLevels(default="warn", overrides={"database": "off"})
    )
    guard_token = _guard_active.set(True)
    try:
        req = NetworkFirewallRequest(protocol="db", host="local", port=0)
        with pytest.raises(GuardPassThrough):
            get_verifier_or_raise("db:query", firewall_request=req)
    finally:
        _guard_active.reset(guard_token)
        _guard_levels.reset(levels_token)


def test_error_reports_canonical_plugin_name_not_prefix() -> None:
    """``GuardedCallError.plugin_name`` reports the canonical registry name.

    A ``db:query`` source_id under ``database = "error"`` raises
    GuardedCallError. Before the fix, ``plugin_name`` would have been
    ``"db"`` (the source_id prefix). After the fix it is ``"database"``
    so users can match it against the same name they wrote in
    ``[tool.tripwire.guard]``.
    """
    levels_token = _guard_levels.set(
        GuardLevels(default="warn", overrides={"database": "error"})
    )
    guard_token = _guard_active.set(True)
    try:
        req = NetworkFirewallRequest(protocol="db", host="local", port=0)
        with pytest.raises(GuardedCallError) as exc_info:
            get_verifier_or_raise("db:query", firewall_request=req)
        assert exc_info.value.plugin_name == "database"
        assert exc_info.value.source_id == "db:query"
    finally:
        _guard_active.reset(guard_token)
        _guard_levels.reset(levels_token)


def test_config_rejects_prefix_as_override_key() -> None:
    """The inverse: writing ``db = "off"`` is rejected by config validation.

    ``[tool.tripwire.guard]`` validates override keys against
    ``VALID_PLUGIN_NAMES`` (the canonical registry names). ``"db"`` is a
    guard_prefix, not a registry name, so feeding it through
    ``_resolve_guard_levels`` raises TripwireConfigError. This guards
    against a "fix" that silently accepts the prefix form and creates a
    second source of truth.
    """
    from tripwire._config import _resolve_guard_levels
    from tripwire._errors import TripwireConfigError

    with pytest.raises(TripwireConfigError, match="Unknown protocol 'db'"):
        _resolve_guard_levels({"guard": {"default": "warn", "db": "off"}})


@pytest.mark.allow("subprocess")
def test_marker_normalizes_mixed_case_warn(pytester: pytest.Pytester) -> None:
    """``@pytest.mark.guard("Warn")`` MUST normalize to ``"warn"``.

    Before the fix, the marker handler ran
    ``GuardLevels(default=arg, overrides={})`` directly; ``"Warn"`` would
    propagate to dispatch as-is and fail later with an opaque error
    (``"Warn"`` does not match any of the dispatch's ``level == "..."``
    arms, so the warn-vs-error decision falls through unpredictably).
    After the fix, the marker is routed through ``_resolve_guard_levels``
    which applies ``.lower()`` and validates against ``_VALID_LEVELS``.

    Observable signal: under guard="warn" with an unsafe plugin like
    subprocess (passthrough_safe=False), dispatch enters the warn branch
    which raises ``UnsafePassthroughError``. Under guard="error" the
    same call would raise ``GuardedCallError``. The error type proves
    the level resolved to "warn" not "error". Project default is "error"
    so any failure to apply the marker would surface as GuardedCallError
    instead.
    """
    pytester.makepyprojecttoml(
        textwrap.dedent(
            """
            [project]
            name = "client"
            version = "0.0.0"

            [tool.tripwire]
            guard = "error"
            """
        )
    )
    pytester.makepyfile(
        test_warn_mixed_case=textwrap.dedent(
            """
            import subprocess

            import pytest

            from tripwire._errors import UnsafePassthroughError


            @pytest.mark.guard("Warn")
            def test_warn_mixed_case():
                with pytest.raises(UnsafePassthroughError):
                    subprocess.run(["true"])
            """
        )
    )
    result = pytester.runpytest_subprocess("-q")
    result.assert_outcomes(passed=1)


@pytest.mark.allow("subprocess")
def test_marker_normalizes_strict_alias(pytester: pytest.Pytester) -> None:
    """``@pytest.mark.guard("STRICT")`` MUST normalize to ``"error"``.

    ``_normalize_level`` lowercases then applies ``_LEVEL_ALIASES``
    (``"strict"`` -> ``"error"``). The marker must use the same path as
    the TOML loader so the alias resolves.

    The inner test asserts that an unmocked ``subprocess.run`` raises
    GuardedCallError under ``"STRICT"``, proving (1) the marker handler
    accepted the mixed-case input, (2) it lowercased to ``"strict"``,
    and (3) it aliased to ``"error"``.
    """
    pytester.makepyprojecttoml(
        textwrap.dedent(
            """
            [project]
            name = "client"
            version = "0.0.0"

            [tool.tripwire]
            guard = "warn"
            """
        )
    )
    pytester.makepyfile(
        test_strict_alias=textwrap.dedent(
            """
            import subprocess

            import pytest

            from tripwire import GuardedCallError


            @pytest.mark.guard("STRICT")
            def test_strict_alias():
                with pytest.raises(GuardedCallError):
                    subprocess.run(["true"])
            """
        )
    )
    result = pytester.runpytest_subprocess("-q")
    result.assert_outcomes(passed=1)
