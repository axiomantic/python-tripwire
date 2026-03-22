"""Unit tests for RedisPlugin."""

from __future__ import annotations

import pytest

from bigfoot._context import _current_test_verifier
from bigfoot._errors import InteractionMismatchError, UnmockedInteractionError
from bigfoot._verifier import StrictVerifier

redis = pytest.importorskip("redis")

from bigfoot.plugins.redis_plugin import (  # noqa: E402
    _REDIS_AVAILABLE,
    RedisMockConfig,
    RedisPlugin,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_verifier_with_plugin() -> tuple[StrictVerifier, RedisPlugin]:
    """Return (verifier, plugin) with RedisPlugin registered but NOT activated.

    The verifier auto-instantiates plugins, so we retrieve the existing
    RedisPlugin rather than creating a duplicate.
    """
    v = StrictVerifier()
    for p in v._plugins:
        if isinstance(p, RedisPlugin):
            return v, p
    p = RedisPlugin(v)
    return v, p


def _reset_plugin_count() -> None:
    """Force-reset the class-level install count to 0 and restore patches if leaked."""
    with RedisPlugin._install_lock:
        RedisPlugin._install_count = 0
        # Use the plugin's own _restore_patches() to avoid duplicating restoration logic.
        RedisPlugin.__new__(RedisPlugin).restore_patches()


@pytest.fixture(autouse=True)
def clean_plugin_counts() -> None:
    """Ensure plugin install count starts and ends at 0 for every test."""
    _reset_plugin_count()
    yield
    _reset_plugin_count()


# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------


# ESCAPE: test_redis_available_flag
#   CLAIM: _REDIS_AVAILABLE is True when redis is importable.
#   PATH:  Module-level try/except import guard in redis_plugin.py.
#   CHECK: _REDIS_AVAILABLE is True (since pytest.importorskip ensured it).
#   MUTATION: Setting it to False when redis IS importable fails the equality check.
#   ESCAPE: Nothing reasonable -- exact boolean equality.
def test_redis_available_flag() -> None:
    assert _REDIS_AVAILABLE is True


# ESCAPE: test_activate_raises_when_redis_unavailable
#   CLAIM: If _REDIS_AVAILABLE is False, calling activate() raises ImportError
#          with the exact installation hint message.
#   PATH:  activate() -> check _REDIS_AVAILABLE -> False -> raise ImportError.
#   CHECK: ImportError raised; str(exc) == exact message string.
#   MUTATION: Not checking the flag and proceeding normally would not raise.
#   ESCAPE: Raising ImportError with a different message fails the exact string check.
def test_activate_raises_when_redis_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import bigfoot.plugins.redis_plugin as _rp

    v, p = _make_verifier_with_plugin()
    monkeypatch.setattr(_rp, "_REDIS_AVAILABLE", False)
    with pytest.raises(ImportError) as exc_info:
        p.activate()
    assert str(exc_info.value) == (
        "Install bigfoot[redis] to use RedisPlugin: pip install bigfoot[redis]"
    )


# ---------------------------------------------------------------------------
# RedisMockConfig dataclass
# ---------------------------------------------------------------------------


# ESCAPE: test_redis_mock_config_fields
#   CLAIM: RedisMockConfig stores command, returns, raises, required correctly.
#   PATH:  Dataclass construction.
#   CHECK: All fields equal their expected values.
#   MUTATION: Wrong field name or default value fails equality check.
#   ESCAPE: Nothing reasonable -- exact equality on all fields.
def test_redis_mock_config_fields() -> None:
    err = redis.exceptions.ResponseError("WRONGTYPE")
    config = RedisMockConfig(command="GET", returns="value", raises=err, required=False)
    assert config.command == "GET"
    assert config.returns == "value"
    assert config.raises is err
    assert config.required is False
    lines = config.registration_traceback.splitlines()
    assert lines[0].startswith("  File ")


# ESCAPE: test_redis_mock_config_defaults
#   CLAIM: RedisMockConfig defaults: raises=None, required=True.
#   PATH:  Dataclass construction with minimal arguments.
#   CHECK: raises is None; required is True.
#   MUTATION: Wrong default for required fails equality check.
#   ESCAPE: Nothing reasonable -- exact equality.
def test_redis_mock_config_defaults() -> None:
    config = RedisMockConfig(command="SET", returns=True)
    assert config.raises is None
    assert config.required is True


# ---------------------------------------------------------------------------
# Activation and reference counting
# ---------------------------------------------------------------------------


# ESCAPE: test_activate_installs_patch
#   CLAIM: After activate(), redis.Redis.execute_command is replaced with bigfoot interceptor.
#   PATH:  activate() -> _install_count == 0 -> store original -> install interceptor.
#   CHECK: redis.Redis.execute_command is not the original after activate().
#   MUTATION: Skipping patch installation leaves original in place; identity check fails.
#   ESCAPE: Nothing reasonable -- identity comparison proves replacement.
def test_activate_installs_patch() -> None:
    original = redis.Redis.execute_command
    v, p = _make_verifier_with_plugin()
    p.activate()
    assert redis.Redis.execute_command is not original
    p.deactivate()


# ESCAPE: test_deactivate_restores_patch
#   CLAIM: After activate() then deactivate(), redis.Redis.execute_command is restored.
#   PATH:  deactivate() -> _install_count reaches 0 -> restore original.
#   CHECK: redis.Redis.execute_command is the original after deactivate().
#   MUTATION: Not restoring in deactivate() leaves bigfoot's interceptor in place.
#   ESCAPE: Nothing reasonable -- identity comparison against saved original.
def test_deactivate_restores_patch() -> None:
    original = redis.Redis.execute_command
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.deactivate()
    assert redis.Redis.execute_command is original


# ESCAPE: test_reference_counting_nested
#   CLAIM: Two activate() calls require two deactivate() calls before patch is removed.
#   PATH:  First activate -> _install_count=1; second -> _install_count=2 (no reinstall).
#          First deactivate -> _install_count=1 (patch remains).
#          Second deactivate -> _install_count=0 (original restored).
#   CHECK: After first deactivate, execute_command is still patched.
#          After second deactivate, it is the original.
#   MUTATION: Restoring on first deactivate fails the mid-point identity check.
#   ESCAPE: Nothing reasonable -- sequential identity checks prove count-controlled restoration.
def test_reference_counting_nested() -> None:
    original = redis.Redis.execute_command
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.activate()
    assert RedisPlugin._install_count == 2

    p.deactivate()
    assert RedisPlugin._install_count == 1
    assert redis.Redis.execute_command is not original

    p.deactivate()
    assert RedisPlugin._install_count == 0
    assert redis.Redis.execute_command is original


# ---------------------------------------------------------------------------
# mock_command: basic GET returns value
# ---------------------------------------------------------------------------


# ESCAPE: test_mock_command_get_returns_value
#   CLAIM: mock_command("GET", returns="value") -> execute_command("GET", "mykey") returns "value".
#   PATH:  mock_command -> appends RedisMockConfig to _queues["GET"] ->
#          execute_command("GET", ...) -> interceptor pops from queue -> returns "value".
#   CHECK: result == "value".
#   MUTATION: Returning wrong value from config fails the equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_mock_command_get_returns_value() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_command("GET", returns="value")

    with v.sandbox():
        r = redis.Redis()
        result = r.execute_command("GET", "mykey")

    assert result == "value"


# ---------------------------------------------------------------------------
# Case insensitivity: mock_command("get", ...) matched by execute_command("GET", ...)
# ---------------------------------------------------------------------------


# ESCAPE: test_mock_command_case_insensitive
#   CLAIM: mock_command("get", ...) is matched by execute_command("GET", ...) via uppercase normalization.
#   PATH:  mock_command normalizes "get" -> "GET"; execute_command normalizes "GET" -> "GET";
#          queue lookup by "GET" succeeds.
#   CHECK: result == "case_value".
#   MUTATION: No normalization means "get" queue != "GET" lookup; UnmockedInteractionError raised.
#   ESCAPE: Nothing reasonable -- exact equality proves the lookup succeeded.
def test_mock_command_case_insensitive() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_command("get", returns="case_value")

    with v.sandbox():
        r = redis.Redis()
        result = r.execute_command("GET", "mykey")

    assert result == "case_value"


# ---------------------------------------------------------------------------
# FIFO ordering: multiple mock_command("GET", ...) calls consumed in order
# ---------------------------------------------------------------------------


# ESCAPE: test_mock_command_fifo_same_command
#   CLAIM: Two mock_command("GET", ...) calls are consumed in FIFO order.
#   PATH:  mock_command x2 -> two RedisMockConfig in deque for "GET".
#          First execute_command("GET", ...) -> popleft -> returns "first".
#          Second execute_command("GET", ...) -> popleft -> returns "second".
#   CHECK: first_result == "first"; second_result == "second".
#   MUTATION: Reversing FIFO order (LIFO) swaps the returned values; both checks fail.
#   ESCAPE: Nothing reasonable -- exact string equality on distinct values.
def test_mock_command_fifo_same_command() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_command("GET", returns="first")
    p.mock_command("GET", returns="second")

    with v.sandbox():
        r = redis.Redis()
        first_result = r.execute_command("GET", "key1")
        second_result = r.execute_command("GET", "key2")

    assert first_result == "first"
    assert second_result == "second"


# ---------------------------------------------------------------------------
# Different commands have separate queues
# ---------------------------------------------------------------------------


# ESCAPE: test_mock_command_separate_queues
#   CLAIM: mock_command("GET", ...) and mock_command("SET", ...) use separate queues.
#   PATH:  "GET" and "SET" are different keys in _queues dict.
#          execute_command("SET", ...) -> pops from "SET" queue only.
#          execute_command("GET", ...) -> pops from "GET" queue only.
#   CHECK: set_result == True; get_result == "myval".
#   MUTATION: Single shared queue would fail the ordering/value checks.
#   ESCAPE: Nothing reasonable -- exact equality on distinct values from distinct queues.
def test_mock_command_separate_queues() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_command("SET", returns=True)
    p.mock_command("GET", returns="myval")

    with v.sandbox():
        r = redis.Redis()
        set_result = r.execute_command("SET", "mykey", "myval")
        get_result = r.execute_command("GET", "mykey")

    assert set_result is True
    assert get_result == "myval"


# ---------------------------------------------------------------------------
# raises parameter
# ---------------------------------------------------------------------------


# ESCAPE: test_mock_command_raises_exception
#   CLAIM: mock_command("GET", returns=None, raises=ResponseError("WRONGTYPE")) raises on execute.
#   PATH:  interceptor pops config with raises set -> raises config.raises.
#   CHECK: ResponseError raised; str(exc) == "WRONGTYPE".
#   MUTATION: Not raising when config.raises is set returns None instead of raising.
#   ESCAPE: Raising a different exception type passes type check but fails the str check.
def test_mock_command_raises_exception() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_command("GET", returns=None, raises=redis.exceptions.ResponseError("WRONGTYPE"))

    with v.sandbox():
        r = redis.Redis()
        with pytest.raises(redis.exceptions.ResponseError) as exc_info:
            r.execute_command("GET", "mykey")

    assert str(exc_info.value) == "WRONGTYPE"


# ---------------------------------------------------------------------------
# get_unused_mocks
# ---------------------------------------------------------------------------


# ESCAPE: test_get_unused_mocks_returns_unconsumed_required
#   CLAIM: get_unused_mocks() returns all RedisMockConfig with required=True still in queues.
#   PATH:  Two mock_command("GET", ...) registered; only first consumed.
#          get_unused_mocks() scans _queues and returns remaining required configs.
#   CHECK: len(unused) == 1; unused[0].command == "GET"; unused[0].returns == "second".
#   MUTATION: Returning all configs (including consumed) fails the length check.
#   ESCAPE: Returning unconsumed but required=False configs also fails (would still be 1 item).
def test_get_unused_mocks_returns_unconsumed_required() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_command("GET", returns="first")
    p.mock_command("GET", returns="second")

    with v.sandbox():
        r = redis.Redis()
        r.execute_command("GET", "key1")

    unused = p.get_unused_mocks()
    assert len(unused) == 1
    assert unused[0].command == "GET"
    assert unused[0].returns == "second"


# ESCAPE: test_get_unused_mocks_excludes_required_false
#   CLAIM: get_unused_mocks() excludes configs with required=False even if unconsumed.
#   PATH:  mock_command("GET", ..., required=False) registered but never consumed.
#          get_unused_mocks() filters out required=False configs.
#   CHECK: get_unused_mocks() == [].
#   MUTATION: Not filtering by required=False returns the config; list length fails.
#   ESCAPE: Nothing reasonable -- exact equality with empty list.
def test_get_unused_mocks_excludes_required_false() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_command("GET", returns="value", required=False)

    unused = p.get_unused_mocks()
    assert unused == []


# ---------------------------------------------------------------------------
# UnmockedInteractionError when command queue is empty
# ---------------------------------------------------------------------------


# ESCAPE: test_unmocked_error_when_queue_empty
#   CLAIM: When execute_command fires with no mock registered, UnmockedInteractionError is raised
#          with source_id == "redis:get".
#   PATH:  interceptor -> _queues.get("GET") is empty -> raise UnmockedInteractionError.
#   CHECK: UnmockedInteractionError raised; exc.source_id == "redis:get".
#   MUTATION: Silently returning None instead of raising; no exception raised.
#   ESCAPE: Raising with source_id == "redis:GET" (wrong case) fails the equality check.
def test_unmocked_error_when_queue_empty() -> None:
    v, p = _make_verifier_with_plugin()
    # No mocks registered

    with v.sandbox():
        r = redis.Redis()
        with pytest.raises(UnmockedInteractionError) as exc_info:
            r.execute_command("GET", "mykey")

    assert exc_info.value.source_id == "redis:get"


# ESCAPE: test_unmocked_error_after_queue_exhausted
#   CLAIM: After the only queued mock is consumed, a second call raises UnmockedInteractionError.
#   PATH:  First execute_command pops the single mock; second call finds empty queue -> raises.
#   CHECK: UnmockedInteractionError raised on second call; source_id == "redis:get".
#   MUTATION: Silently returning None or reusing exhausted mock fails either the value or the raise check.
#   ESCAPE: Nothing reasonable -- exact exception type and source_id.
def test_unmocked_error_after_queue_exhausted() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_command("GET", returns="value")

    with v.sandbox():
        r = redis.Redis()
        first_result = r.execute_command("GET", "key1")

        with pytest.raises(UnmockedInteractionError) as exc_info:
            r.execute_command("GET", "key2")

    assert first_result == "value"
    assert exc_info.value.source_id == "redis:get"


# ---------------------------------------------------------------------------
# matches() and assertable_fields()
# ---------------------------------------------------------------------------


# ESCAPE: test_matches_field_comparison
#   CLAIM: matches() does field-by-field comparison; returns True when fields match, False otherwise.
#   PATH:  matches(interaction, expected) -> compare each expected key against details.
#   CHECK: Empty expected matches anything; non-matching field returns False; matching field True.
#   MUTATION: Returning True always fails the non-matching field check.
#   ESCAPE: Nothing reasonable -- exact boolean equality on distinct cases.
def test_matches_field_comparison() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="redis:get",
        sequence=0,
        details={"command": "GET", "args": ("mykey",), "kwargs": {}},
        plugin=p,
    )
    # Empty expected matches any interaction
    assert p.matches(interaction, {}) is True
    # Field that matches returns True
    assert p.matches(interaction, {"command": "GET"}) is True
    # Field that does not match returns False
    assert p.matches(interaction, {"command": "SET"}) is False
    # Field not present in details returns False
    assert p.matches(interaction, {"foo": "bar"}) is False


# ESCAPE: test_assertable_fields_all_three
#   CLAIM: assertable_fields() returns frozenset({"command", "args", "kwargs"}).
#   PATH:  assertable_fields(interaction) -> frozenset({"command", "args", "kwargs"}).
#   CHECK: result == frozenset({"command", "args", "kwargs"}).
#   MUTATION: Returning frozenset() skips completeness enforcement entirely.
#   ESCAPE: Nothing reasonable -- exact equality.
def test_assertable_fields_all_three() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(source_id="redis:get", sequence=0, details={}, plugin=p)
    assert p.assertable_fields(interaction) == frozenset({"command", "args", "kwargs"})


# ---------------------------------------------------------------------------
# format_* methods
# ---------------------------------------------------------------------------


# ESCAPE: test_format_interaction
#   CLAIM: format_interaction returns a human-readable string for the given interaction.
#   PATH:  format_interaction(interaction) -> string.
#   CHECK: result == exact expected string.
#   MUTATION: Returning wrong format string fails equality check.
#   ESCAPE: Different order or missing fields in format string fails the equality check.
def test_format_interaction() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="redis:get",
        sequence=0,
        details={"command": "GET", "args": ("mykey",)},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[RedisPlugin] redis.GET('mykey')"


# ESCAPE: test_format_interaction_no_args
#   CLAIM: format_interaction with no args returns string without argument list details.
#   PATH:  format_interaction(interaction) -> string.
#   CHECK: result == exact expected string for a command with no extra args.
#   MUTATION: Crashing on empty args fails the test.
#   ESCAPE: Returning wrong format fails equality.
def test_format_interaction_no_args() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="redis:ping",
        sequence=0,
        details={"command": "PING", "args": ()},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[RedisPlugin] redis.PING()"


# ESCAPE: test_format_mock_hint
#   CLAIM: format_mock_hint returns copy-pasteable code to mock the interaction.
#   PATH:  format_mock_hint(interaction) -> string.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Different format fails the equality check.
def test_format_mock_hint() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="redis:get",
        sequence=0,
        details={"command": "GET"},
        plugin=p,
    )
    result = p.format_mock_hint(interaction)
    assert result == "    bigfoot.redis_mock.mock_command('GET', returns=...)"


# ESCAPE: test_format_unmocked_hint
#   CLAIM: format_unmocked_hint returns copy-pasteable code for an unmocked call.
#   PATH:  format_unmocked_hint(source_id, args, kwargs) -> string.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Different format fails the equality check.
def test_format_unmocked_hint() -> None:
    v, p = _make_verifier_with_plugin()
    result = p.format_unmocked_hint("redis:get", ("mykey",), {})
    assert result == (
        "redis.GET(...) was called but no mock was registered.\n"
        "Register a mock with:\n"
        "    bigfoot.redis_mock.mock_command('GET', returns=...)"
    )


# ESCAPE: test_format_assert_hint
#   CLAIM: format_assert_hint returns assert_command() syntax with all three fields.
#   PATH:  format_assert_hint(interaction) -> string with assert_command syntax.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Different format fails the equality check.
def test_format_assert_hint() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="redis:get",
        sequence=0,
        details={"command": "GET", "args": ("mykey",), "kwargs": {}},
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == (
        "    bigfoot.redis_mock.assert_command(\n"
        "        command='GET',\n"
        "        args=('mykey',),\n"
        "        kwargs={},\n"
        "    )"
    )


# ESCAPE: test_format_unused_mock_hint
#   CLAIM: format_unused_mock_hint returns hint containing command name and traceback.
#   PATH:  format_unused_mock_hint(mock_config) -> string.
#   CHECK: result starts with exact prefix; registration_traceback is in result.
#   MUTATION: Wrong prefix text fails the startswith check.
#   ESCAPE: Not including registration_traceback at all would fail the endswith/in check.
def test_format_unused_mock_hint() -> None:
    v, p = _make_verifier_with_plugin()
    config = RedisMockConfig(command="GET", returns="value")
    result = p.format_unused_mock_hint(config)
    expected_prefix = (
        "redis.GET(...) was mocked (required=True) but never called.\nRegistered at:\n"
    )
    assert result == expected_prefix + config.registration_traceback


# ---------------------------------------------------------------------------
# Module-level proxy: bigfoot.redis_mock
# ---------------------------------------------------------------------------


# ESCAPE: test_redis_mock_proxy_mock_command
#   CLAIM: bigfoot.redis_mock.mock_command("GET", returns="v") works when verifier is active.
#   PATH:  _RedisProxy.__getattr__("mock_command") -> get verifier ->
#          find/create RedisPlugin -> return plugin.mock_command.
#   CHECK: The proxy call does not raise and the mock is registered.
#   MUTATION: Returning None instead of the plugin fails with AttributeError on mock_command.
#   ESCAPE: Nothing reasonable -- call succeeds or raises.
def test_redis_mock_proxy_mock_command(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.redis_mock.mock_command("GET", returns="proxy_value", required=True)

    with bigfoot.sandbox():
        r = redis.Redis()
        result = r.execute_command("GET", "somekey")

    assert result == "proxy_value"
    bigfoot.redis_mock.assert_command("GET", args=("somekey",), kwargs={})


# ESCAPE: test_redis_mock_proxy_raises_outside_context
#   CLAIM: Accessing bigfoot.redis_mock outside a test context raises NoActiveVerifierError.
#   PATH:  _RedisProxy.__getattr__ -> _get_test_verifier_or_raise -> NoActiveVerifierError.
#   CHECK: NoActiveVerifierError raised.
#   MUTATION: Silently returning None would not raise.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_redis_mock_proxy_raises_outside_context() -> None:
    import bigfoot
    from bigfoot._errors import NoActiveVerifierError

    token = _current_test_verifier.set(None)
    try:
        with pytest.raises(NoActiveVerifierError):
            _ = bigfoot.redis_mock.mock_command
    finally:
        _current_test_verifier.reset(token)


# ---------------------------------------------------------------------------
# RedisPlugin in __all__
# ---------------------------------------------------------------------------


# ESCAPE: test_redis_plugin_in_all
#   CLAIM: RedisPlugin and redis_mock are exported from bigfoot.__all__.
#   PATH:  bigfoot.__all__ contains "RedisPlugin" and "redis_mock".
#   CHECK: "RedisPlugin" in bigfoot.__all__; "redis_mock" in bigfoot.__all__.
#   MUTATION: Omitting either from __all__ fails the membership check.
#   ESCAPE: Nothing reasonable -- exact membership check.
def test_redis_plugin_in_all() -> None:
    import bigfoot
    from bigfoot.plugins.redis_plugin import RedisPlugin as _RedisPlugin

    assert bigfoot.RedisPlugin is _RedisPlugin
    assert type(bigfoot.redis_mock).__name__ == "_RedisProxy"


# ---------------------------------------------------------------------------
# New tests: no auto-assert, assert_command() typed helper
# ---------------------------------------------------------------------------


def test_redis_interactions_not_auto_asserted(bigfoot_verifier: StrictVerifier) -> None:
    """Redis interactions are NOT auto-asserted — they land on the timeline unasserted."""
    import bigfoot

    bigfoot.redis_mock.mock_command("GET", returns=b"value")
    with bigfoot.sandbox():
        client = redis.Redis()
        client.execute_command("GET", "key")
    # At this point the interaction is on the timeline but NOT asserted
    timeline = bigfoot_verifier._timeline
    interactions = timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].source_id == "redis:get"
    # Assert it so verify_all() at teardown succeeds
    bigfoot.redis_mock.assert_command("GET", args=("key",), kwargs={})


def test_assert_command_typed_helper(bigfoot_verifier: StrictVerifier) -> None:
    """assert_command() asserts the next Redis interaction."""
    import bigfoot

    bigfoot.redis_mock.mock_command("SET", returns=True)
    with bigfoot.sandbox():
        client = redis.Redis()
        client.execute_command("SET", "key", "value")
    bigfoot.redis_mock.assert_command("SET", args=("key", "value"), kwargs={})


def test_assert_command_wrong_args_raises(bigfoot_verifier: StrictVerifier) -> None:
    """assert_command() with wrong args raises InteractionMismatchError."""
    import bigfoot

    bigfoot.redis_mock.mock_command("GET", returns=b"val")
    with bigfoot.sandbox():
        client = redis.Redis()
        client.execute_command("GET", "key")
    with pytest.raises(InteractionMismatchError):
        bigfoot.redis_mock.assert_command("GET", args=("wrong_key",), kwargs={})
    # Now assert correctly so teardown passes
    bigfoot.redis_mock.assert_command("GET", args=("key",), kwargs={})
