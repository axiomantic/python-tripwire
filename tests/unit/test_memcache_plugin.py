"""Unit tests for MemcachePlugin."""

from __future__ import annotations

import pymemcache  # noqa: F401
import pytest

from bigfoot._context import _current_test_verifier
from bigfoot._errors import (
    InteractionMismatchError,
    MissingAssertionFieldsError,
    UnmockedInteractionError,
)
from bigfoot._timeline import Interaction
from bigfoot._verifier import StrictVerifier
from bigfoot.plugins.memcache_plugin import (
    _PYMEMCACHE_AVAILABLE,
    MemcacheMockConfig,
    MemcachePlugin,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_verifier_with_plugin() -> tuple[StrictVerifier, MemcachePlugin]:
    """Return (verifier, plugin) with MemcachePlugin registered but NOT activated."""
    v = StrictVerifier()
    for p in v._plugins:
        if isinstance(p, MemcachePlugin):
            return v, p
    p = MemcachePlugin(v)
    return v, p


def _reset_plugin_count() -> None:
    """Force-reset the class-level install count to 0 and restore patches if leaked."""
    with MemcachePlugin._install_lock:
        MemcachePlugin._install_count = 0
        # Use the plugin's own _restore_patches() to avoid duplicating restoration logic.
        MemcachePlugin.__new__(MemcachePlugin).restore_patches()


@pytest.fixture(autouse=True)
def clean_plugin_counts() -> None:
    """Ensure plugin install count starts and ends at 0 for every test."""
    _reset_plugin_count()
    yield
    _reset_plugin_count()


# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------


def test_pymemcache_available_flag() -> None:
    assert _PYMEMCACHE_AVAILABLE is True


def test_activate_raises_when_pymemcache_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import bigfoot.plugins.memcache_plugin as _mp

    v, p = _make_verifier_with_plugin()
    monkeypatch.setattr(_mp, "_PYMEMCACHE_AVAILABLE", False)
    with pytest.raises(ImportError) as exc_info:
        p.activate()
    assert str(exc_info.value) == (
        "Install bigfoot[pymemcache] to use MemcachePlugin: pip install bigfoot[pymemcache]"
    )


# ---------------------------------------------------------------------------
# MemcacheMockConfig dataclass
# ---------------------------------------------------------------------------


def test_memcache_mock_config_fields() -> None:
    config = MemcacheMockConfig(command="GET", returns=b"value", raises=None, required=False)
    assert config.command == "GET"
    assert config.returns == b"value"
    assert config.raises is None
    assert config.required is False
    lines = config.registration_traceback.splitlines()
    assert lines[0].startswith("  File ")


def test_memcache_mock_config_defaults() -> None:
    config = MemcacheMockConfig(command="SET", returns=True)
    assert config.raises is None
    assert config.required is True


# ---------------------------------------------------------------------------
# Activation and reference counting
# ---------------------------------------------------------------------------


def test_activate_installs_patch() -> None:
    from pymemcache.client.base import Client

    original_get = Client.get
    v, p = _make_verifier_with_plugin()
    p.activate()
    assert Client.get is not original_get
    p.deactivate()


def test_deactivate_restores_patch() -> None:
    from pymemcache.client.base import Client

    original_get = Client.get
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.deactivate()
    assert Client.get is original_get


def test_reference_counting_nested() -> None:
    from pymemcache.client.base import Client

    original_get = Client.get
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.activate()
    assert MemcachePlugin._install_count == 2

    p.deactivate()
    assert MemcachePlugin._install_count == 1
    assert Client.get is not original_get

    p.deactivate()
    assert MemcachePlugin._install_count == 0
    assert Client.get is original_get


# ---------------------------------------------------------------------------
# Basic interception: get
# ---------------------------------------------------------------------------


def test_mock_command_get_returns_value() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_command("GET", returns=b"value")

    with v.sandbox():
        from pymemcache.client.base import Client

        client = Client(("localhost", 11211))
        result = client.get("mykey")

    assert result == b"value"


# ---------------------------------------------------------------------------
# Basic interception: set
# ---------------------------------------------------------------------------


def test_mock_command_set_returns_value() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_command("SET", returns=True)

    with v.sandbox():
        from pymemcache.client.base import Client

        client = Client(("localhost", 11211))
        result = client.set("mykey", b"myvalue", expire=300)

    assert result is True


# ---------------------------------------------------------------------------
# Full assertion certainty
# ---------------------------------------------------------------------------


def test_assert_get_full_assertion(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.memcache_mock.mock_command("GET", returns=b"value")

    with bigfoot.sandbox():
        from pymemcache.client.base import Client

        client = Client(("localhost", 11211))
        client.get("mykey")

    bigfoot.memcache_mock.assert_get(command="GET", key="mykey")


def test_assert_set_full_assertion(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.memcache_mock.mock_command("SET", returns=True)

    with bigfoot.sandbox():
        from pymemcache.client.base import Client

        client = Client(("localhost", 11211))
        client.set("mykey", b"myvalue", expire=300)

    bigfoot.memcache_mock.assert_set(
        command="SET", key="mykey", value=b"myvalue", expire=300,
    )


# ---------------------------------------------------------------------------
# Case insensitivity
# ---------------------------------------------------------------------------


def test_mock_command_case_insensitive() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_command("get", returns=b"case_value")

    with v.sandbox():
        from pymemcache.client.base import Client

        client = Client(("localhost", 11211))
        result = client.get("mykey")

    assert result == b"case_value"


# ---------------------------------------------------------------------------
# FIFO ordering
# ---------------------------------------------------------------------------


def test_mock_command_fifo() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_command("GET", returns=b"first")
    p.mock_command("GET", returns=b"second")

    with v.sandbox():
        from pymemcache.client.base import Client

        client = Client(("localhost", 11211))
        first = client.get("key1")
        second = client.get("key2")

    assert first == b"first"
    assert second == b"second"


# ---------------------------------------------------------------------------
# Separate queues
# ---------------------------------------------------------------------------


def test_mock_command_separate_queues() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_command("SET", returns=True)
    p.mock_command("GET", returns=b"myval")

    with v.sandbox():
        from pymemcache.client.base import Client

        client = Client(("localhost", 11211))
        set_result = client.set("mykey", b"myval")
        get_result = client.get("mykey")

    assert set_result is True
    assert get_result == b"myval"


# ---------------------------------------------------------------------------
# Exception propagation
# ---------------------------------------------------------------------------


def test_mock_command_raises_exception() -> None:
    v, p = _make_verifier_with_plugin()
    err = ConnectionError("Connection refused")
    p.mock_command("GET", returns=None, raises=err)

    with v.sandbox():
        from pymemcache.client.base import Client

        client = Client(("localhost", 11211))
        with pytest.raises(ConnectionError) as exc_info:
            client.get("mykey")

    assert str(exc_info.value) == "Connection refused"


# ---------------------------------------------------------------------------
# Unmocked interaction error
# ---------------------------------------------------------------------------


def test_unmocked_error_when_queue_empty() -> None:
    v, p = _make_verifier_with_plugin()

    with v.sandbox():
        from pymemcache.client.base import Client

        client = Client(("localhost", 11211))
        with pytest.raises(UnmockedInteractionError) as exc_info:
            client.get("mykey")

    assert exc_info.value.source_id == "memcache:get"


# ---------------------------------------------------------------------------
# Unused mock detection
# ---------------------------------------------------------------------------


def test_get_unused_mocks_returns_unconsumed_required() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_command("GET", returns=b"first")
    p.mock_command("GET", returns=b"second")

    with v.sandbox():
        from pymemcache.client.base import Client

        client = Client(("localhost", 11211))
        client.get("key1")

    unused = p.get_unused_mocks()
    assert len(unused) == 1
    assert unused[0].command == "GET"
    assert unused[0].returns == b"second"


def test_get_unused_mocks_excludes_required_false() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_command("GET", returns=b"value", required=False)

    unused = p.get_unused_mocks()
    assert unused == []


# ---------------------------------------------------------------------------
# Missing assertion fields
# ---------------------------------------------------------------------------


def test_missing_assertion_fields(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot
    from bigfoot.plugins.memcache_plugin import _MemcacheSentinel

    bigfoot.memcache_mock.mock_command("SET", returns=True)

    with bigfoot.sandbox():
        from pymemcache.client.base import Client

        client = Client(("localhost", 11211))
        client.set("mykey", b"myvalue", expire=300)

    sentinel = _MemcacheSentinel("memcache:set")
    with pytest.raises(MissingAssertionFieldsError) as exc_info:
        # Only pass command, omit key/value/expire
        bigfoot_verifier.assert_interaction(sentinel, command="SET")

    assert "key" in exc_info.value.missing_fields
    # Now assert fully so teardown passes
    bigfoot.memcache_mock.assert_set(
        command="SET", key="mykey", value=b"myvalue", expire=300,
    )


# ---------------------------------------------------------------------------
# Interactions not auto-asserted
# ---------------------------------------------------------------------------


def test_memcache_interactions_not_auto_asserted(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.memcache_mock.mock_command("GET", returns=b"value")

    with bigfoot.sandbox():
        from pymemcache.client.base import Client

        client = Client(("localhost", 11211))
        client.get("mykey")

    timeline = bigfoot_verifier._timeline
    interactions = timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].source_id == "memcache:get"
    # Assert it so verify_all() at teardown succeeds
    bigfoot.memcache_mock.assert_get(command="GET", key="mykey")


# ---------------------------------------------------------------------------
# Assertable fields
# ---------------------------------------------------------------------------


def test_assertable_fields_get() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="memcache:get",
        sequence=0,
        details={"command": "GET", "key": "mykey"},
        plugin=p,
    )
    assert p.assertable_fields(interaction) == frozenset({"command", "key"})


def test_assertable_fields_set() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="memcache:set",
        sequence=0,
        details={"command": "SET", "key": "mykey", "value": b"val", "expire": 300},
        plugin=p,
    )
    assert p.assertable_fields(interaction) == frozenset(
        {"command", "key", "value", "expire"}
    )


# ---------------------------------------------------------------------------
# format_* methods
# ---------------------------------------------------------------------------


def test_format_interaction() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="memcache:get",
        sequence=0,
        details={"command": "GET", "key": "mykey"},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[MemcachePlugin] memcache.GET('mykey')"


def test_format_mock_hint() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="memcache:get",
        sequence=0,
        details={"command": "GET"},
        plugin=p,
    )
    result = p.format_mock_hint(interaction)
    assert result == "    bigfoot.memcache_mock.mock_command('GET', returns=...)"


def test_format_unmocked_hint() -> None:
    v, p = _make_verifier_with_plugin()
    result = p.format_unmocked_hint("memcache:get", ("mykey",), {})
    assert result == (
        "memcache.GET(...) was called but no mock was registered.\n"
        "Register a mock with:\n"
        "    bigfoot.memcache_mock.mock_command('GET', returns=...)"
    )


def test_format_assert_hint() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="memcache:get",
        sequence=0,
        details={"command": "GET", "key": "mykey"},
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == (
        "    bigfoot.memcache_mock.assert_get(\n"
        "        command='GET',\n"
        "        key='mykey',\n"
        "    )"
    )


def test_format_unused_mock_hint() -> None:
    v, p = _make_verifier_with_plugin()
    config = MemcacheMockConfig(command="GET", returns=b"value")
    result = p.format_unused_mock_hint(config)
    expected_prefix = (
        "memcache.GET(...) was mocked (required=True) but never called.\n"
        "Registered at:\n"
    )
    assert result == expected_prefix + config.registration_traceback


# ---------------------------------------------------------------------------
# Module-level proxy: bigfoot.memcache_mock
# ---------------------------------------------------------------------------


def test_memcache_mock_proxy_mock_command(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.memcache_mock.mock_command("GET", returns=b"proxy_value")

    with bigfoot.sandbox():
        from pymemcache.client.base import Client

        client = Client(("localhost", 11211))
        result = client.get("somekey")

    assert result == b"proxy_value"
    bigfoot.memcache_mock.assert_get(command="GET", key="somekey")


def test_memcache_mock_proxy_raises_outside_context() -> None:
    import bigfoot
    from bigfoot._errors import NoActiveVerifierError

    token = _current_test_verifier.set(None)
    try:
        with pytest.raises(NoActiveVerifierError):
            _ = bigfoot.memcache_mock.mock_command
    finally:
        _current_test_verifier.reset(token)


# ---------------------------------------------------------------------------
# MemcachePlugin in __all__
# ---------------------------------------------------------------------------


def test_memcache_plugin_in_all() -> None:
    import bigfoot

    assert "MemcachePlugin" in bigfoot.__all__
    assert "memcache_mock" in bigfoot.__all__
    assert type(bigfoot.memcache_mock).__name__ == "_MemcacheProxy"


# ---------------------------------------------------------------------------
# Typed assertion helpers
# ---------------------------------------------------------------------------


def test_assert_delete(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.memcache_mock.mock_command("DELETE", returns=True)

    with bigfoot.sandbox():
        from pymemcache.client.base import Client

        client = Client(("localhost", 11211))
        client.delete("mykey")

    bigfoot.memcache_mock.assert_delete(command="DELETE", key="mykey")


def test_assert_incr(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.memcache_mock.mock_command("INCR", returns=42)

    with bigfoot.sandbox():
        from pymemcache.client.base import Client

        client = Client(("localhost", 11211))
        client.incr("counter", 1)

    bigfoot.memcache_mock.assert_incr(command="INCR", key="counter", value=1)


def test_assert_get_wrong_args_raises(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.memcache_mock.mock_command("GET", returns=b"val")

    with bigfoot.sandbox():
        from pymemcache.client.base import Client

        client = Client(("localhost", 11211))
        client.get("mykey")

    with pytest.raises(InteractionMismatchError):
        bigfoot.memcache_mock.assert_get(command="GET", key="wrongkey")
    # Assert correctly so teardown passes
    bigfoot.memcache_mock.assert_get(command="GET", key="mykey")
