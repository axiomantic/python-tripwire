"""Unit tests for bigfoot LoggingPlugin."""

import logging
from unittest.mock import MagicMock

import pytest

import bigfoot
from bigfoot._context import _current_test_verifier
from bigfoot._errors import (
    ConflictError,
    InteractionMismatchError,
    MissingAssertionFieldsError,
    UnassertedInteractionsError,
    UnusedMocksError,
)
from bigfoot._timeline import Interaction
from bigfoot._verifier import StrictVerifier
from bigfoot.plugins.logging_plugin import (
    _LOGGER_LOG_ORIGINAL,
    LoggingPlugin,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_verifier_with_plugin() -> tuple[StrictVerifier, LoggingPlugin]:
    """Return (verifier, plugin) with plugin registered but not activated.

    The verifier auto-instantiates plugins, so we retrieve the existing
    LoggingPlugin rather than creating a duplicate.
    """
    v = StrictVerifier()
    for p in v._plugins:
        if isinstance(p, LoggingPlugin):
            return v, p
    # Fallback: create one if not auto-instantiated (shouldn't happen in practice)
    p = LoggingPlugin(v)
    return v, p


def _reset_install_count() -> None:
    """Force-reset the class-level install count to 0 and restore patches if leaked."""
    with LoggingPlugin._install_lock:
        LoggingPlugin._install_count = 0
        # Use the plugin's own _restore_patches() to avoid duplicating restoration logic.
        LoggingPlugin.__new__(LoggingPlugin).restore_patches()


@pytest.fixture(autouse=True)
def clean_install_count():
    """Ensure LoggingPlugin install count starts and ends at 0 for every test."""
    _reset_install_count()
    yield
    _reset_install_count()


@pytest.fixture(autouse=True)
def _set_root_logger_to_debug():
    """Set root logger to DEBUG so all log levels reach Logger._log during tests."""
    root = logging.getLogger()
    original_level = root.level
    root.setLevel(logging.DEBUG)
    yield
    root.setLevel(original_level)


# ---------------------------------------------------------------------------
# Activation and reference counting
# ---------------------------------------------------------------------------


def test_activate_installs_patches() -> None:
    v, p = _make_verifier_with_plugin()
    assert logging.Logger._log is _LOGGER_LOG_ORIGINAL
    p.activate()
    assert logging.Logger._log is not _LOGGER_LOG_ORIGINAL


def test_deactivate_restores_patches() -> None:
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.deactivate()
    assert logging.Logger._log is _LOGGER_LOG_ORIGINAL


def test_reference_counting_nested() -> None:
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.activate()
    assert LoggingPlugin._install_count == 2

    p.deactivate()
    assert LoggingPlugin._install_count == 1
    # Patches must still be active after first deactivate
    assert logging.Logger._log is not _LOGGER_LOG_ORIGINAL

    p.deactivate()
    assert LoggingPlugin._install_count == 0
    assert logging.Logger._log is _LOGGER_LOG_ORIGINAL


def test_install_noop() -> None:
    v, p = _make_verifier_with_plugin()
    p.install()  # Must not raise


# ---------------------------------------------------------------------------
# Basic log interception
# ---------------------------------------------------------------------------


def test_intercept_info_log() -> None:
    v, p = _make_verifier_with_plugin()
    logger = logging.getLogger("test.info")

    with v.sandbox():
        logger.info("hello world")

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].source_id == "logging:log"
    assert interactions[0].details == {
        "level": "INFO",
        "message": "hello world",
        "logger_name": "test.info",
    }


def test_intercept_debug_log() -> None:
    v, p = _make_verifier_with_plugin()
    logger = logging.getLogger("test.debug")

    with v.sandbox():
        logger.debug("debug message")

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details["level"] == "DEBUG"
    assert interactions[0].details["message"] == "debug message"


def test_intercept_warning_log() -> None:
    v, p = _make_verifier_with_plugin()
    logger = logging.getLogger("test.warning")

    with v.sandbox():
        logger.warning("watch out")

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details["level"] == "WARNING"
    assert interactions[0].details["message"] == "watch out"


def test_intercept_error_log() -> None:
    v, p = _make_verifier_with_plugin()
    logger = logging.getLogger("test.error")

    with v.sandbox():
        logger.error("something failed")

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details["level"] == "ERROR"
    assert interactions[0].details["message"] == "something failed"


def test_intercept_critical_log() -> None:
    v, p = _make_verifier_with_plugin()
    logger = logging.getLogger("test.critical")

    with v.sandbox():
        logger.critical("system down")

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details["level"] == "CRITICAL"
    assert interactions[0].details["message"] == "system down"


def test_intercept_log_with_args_formatting() -> None:
    """Log messages with %s args are formatted before recording."""
    v, p = _make_verifier_with_plugin()
    logger = logging.getLogger("test.args")

    with v.sandbox():
        logger.info("User %s logged in from %s", "alice", "192.168.1.1")

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details["message"] == "User alice logged in from 192.168.1.1"


# ---------------------------------------------------------------------------
# assert_log with all 3 fields
# ---------------------------------------------------------------------------


def test_assert_log_all_fields() -> None:
    v, p = _make_verifier_with_plugin()
    logger = logging.getLogger("myapp.auth")

    with v.sandbox():
        logger.info("login successful")

    p.assert_log("INFO", "login successful", "myapp.auth")
    v.verify_all()  # Should not raise


def test_assert_log_wrong_level_raises() -> None:
    v, p = _make_verifier_with_plugin()
    logger = logging.getLogger("myapp")

    with v.sandbox():
        logger.info("hello")

    with pytest.raises(InteractionMismatchError):
        p.assert_log("ERROR", "hello", "myapp")


def test_assert_log_wrong_message_raises() -> None:
    v, p = _make_verifier_with_plugin()
    logger = logging.getLogger("myapp")

    with v.sandbox():
        logger.info("hello")

    with pytest.raises(InteractionMismatchError):
        p.assert_log("INFO", "goodbye", "myapp")


def test_assert_log_wrong_logger_name_raises() -> None:
    v, p = _make_verifier_with_plugin()
    logger = logging.getLogger("myapp.real")

    with v.sandbox():
        logger.info("hello")

    with pytest.raises(InteractionMismatchError):
        p.assert_log("INFO", "hello", "myapp.fake")


# ---------------------------------------------------------------------------
# Per-level convenience helpers
# ---------------------------------------------------------------------------


def test_assert_debug_helper() -> None:
    v, p = _make_verifier_with_plugin()
    logger = logging.getLogger("test.helpers")

    with v.sandbox():
        logger.debug("trace")

    p.assert_debug("trace", "test.helpers")
    v.verify_all()


def test_assert_info_helper() -> None:
    v, p = _make_verifier_with_plugin()
    logger = logging.getLogger("test.helpers")

    with v.sandbox():
        logger.info("started")

    p.assert_info("started", "test.helpers")
    v.verify_all()


def test_assert_warning_helper() -> None:
    v, p = _make_verifier_with_plugin()
    logger = logging.getLogger("test.helpers")

    with v.sandbox():
        logger.warning("careful")

    p.assert_warning("careful", "test.helpers")
    v.verify_all()


def test_assert_error_helper() -> None:
    v, p = _make_verifier_with_plugin()
    logger = logging.getLogger("test.helpers")

    with v.sandbox():
        logger.error("broken")

    p.assert_error("broken", "test.helpers")
    v.verify_all()


def test_assert_critical_helper() -> None:
    v, p = _make_verifier_with_plugin()
    logger = logging.getLogger("test.helpers")

    with v.sandbox():
        logger.critical("fatal")

    p.assert_critical("fatal", "test.helpers")
    v.verify_all()


# ---------------------------------------------------------------------------
# mock_log with expected values
# ---------------------------------------------------------------------------


def test_mock_log_consumes_matching_entry() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_log("INFO", "expected message", logger_name="myapp")
    logger = logging.getLogger("myapp")

    with v.sandbox():
        logger.info("expected message")

    # Mock was consumed; no unused mocks error
    p.assert_log("INFO", "expected message", "myapp")
    v.verify_all()


def test_mock_log_with_none_logger_name_matches_any() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_log("INFO", "expected message", logger_name=None)
    logger = logging.getLogger("any.logger.name")

    with v.sandbox():
        logger.info("expected message")

    p.assert_log("INFO", "expected message", "any.logger.name")
    v.verify_all()


def test_mock_log_fifo_order() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_log("INFO", "first")
    p.mock_log("ERROR", "second")
    logger = logging.getLogger("test.fifo")

    with v.sandbox():
        logger.info("first")
        logger.error("second")

    p.assert_info("first", "test.fifo")
    p.assert_error("second", "test.fifo")
    v.verify_all()


# ---------------------------------------------------------------------------
# Fire-and-forget behavior
# ---------------------------------------------------------------------------


def test_unmocked_log_is_swallowed_and_recorded() -> None:
    """Unmocked log calls are swallowed (not actually logged) and recorded."""
    v, p = _make_verifier_with_plugin()
    logger = logging.getLogger("test.swallow")

    with v.sandbox():
        logger.info("unmocked message")

    # The interaction was recorded on the timeline
    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details["message"] == "unmocked message"


def test_unasserted_log_raises_at_teardown() -> None:
    """Unmocked log call recorded on timeline fails verify_all if not asserted."""
    v, p = _make_verifier_with_plugin()
    logger = logging.getLogger("test.unasserted")

    with v.sandbox():
        logger.info("not asserted")

    with pytest.raises(UnassertedInteractionsError) as exc_info:
        v.verify_all()

    assert len(exc_info.value.interactions) == 1
    assert exc_info.value.interactions[0].details["message"] == "not asserted"


def test_asserted_log_passes_verify_all() -> None:
    """Log call properly asserted passes verify_all."""
    v, p = _make_verifier_with_plugin()
    logger = logging.getLogger("test.asserted")

    with v.sandbox():
        logger.info("asserted")

    p.assert_info("asserted", "test.asserted")
    v.verify_all()  # Should not raise


# ---------------------------------------------------------------------------
# Unused mocks
# ---------------------------------------------------------------------------


def test_unused_required_mock_raises() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_log("INFO", "never called", required=True)

    with v.sandbox():
        pass  # No log calls

    with pytest.raises(UnusedMocksError) as exc_info:
        v.verify_all()

    assert len(exc_info.value.mocks) == 1
    source_id, details, _tb = exc_info.value.mocks[0]
    assert source_id == "logging:log"
    assert details == {"level": "INFO", "message": "never called"}


def test_unused_optional_mock_does_not_raise() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_log("INFO", "optional", required=False)

    with v.sandbox():
        pass  # No log calls

    v.verify_all()  # Should not raise


# ---------------------------------------------------------------------------
# assertable_fields
# ---------------------------------------------------------------------------


def test_assertable_fields_returns_correct_frozenset() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="logging:log",
        sequence=0,
        details={"level": "INFO", "message": "test", "logger_name": "root"},
        plugin=p,
    )
    result = p.assertable_fields(interaction)
    assert result == frozenset({"level", "message", "logger_name"})


def test_missing_assertion_fields_raises() -> None:
    """Omitting a required field from assert_interaction raises MissingAssertionFieldsError."""
    v, p = _make_verifier_with_plugin()
    logger = logging.getLogger("test.missing")

    with v.sandbox():
        logger.info("hello")

    with pytest.raises(MissingAssertionFieldsError) as exc_info:
        v.assert_interaction(p.log, level="INFO", message="hello")
        # Missing logger_name

    assert "logger_name" in exc_info.value.missing_fields


# ---------------------------------------------------------------------------
# Multiple loggers
# ---------------------------------------------------------------------------


def test_multiple_loggers_different_names() -> None:
    v, p = _make_verifier_with_plugin()
    logger_a = logging.getLogger("service.auth")
    logger_b = logging.getLogger("service.payment")

    with v.sandbox():
        logger_a.info("authenticated")
        logger_b.warning("rate limited")

    p.assert_info("authenticated", "service.auth")
    p.assert_warning("rate limited", "service.payment")
    v.verify_all()


# ---------------------------------------------------------------------------
# format_interaction, format_assert_hint, format_mock_hint
# ---------------------------------------------------------------------------


def test_format_interaction() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="logging:log",
        sequence=0,
        details={"level": "ERROR", "message": "disk full", "logger_name": "storage"},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[LoggingPlugin] ERROR storage: disk full"


def test_format_assert_hint() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="logging:log",
        sequence=0,
        details={"level": "INFO", "message": "started", "logger_name": "myapp"},
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert "bigfoot.log_mock.assert_log" in result
    assert "'INFO'" in result
    assert "'started'" in result
    assert "'myapp'" in result


def test_format_mock_hint() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="logging:log",
        sequence=0,
        details={"level": "WARNING", "message": "low memory", "logger_name": "system"},
        plugin=p,
    )
    result = p.format_mock_hint(interaction)
    assert "bigfoot.log_mock.mock_log" in result
    assert "'WARNING'" in result
    assert "'low memory'" in result
    assert "'system'" in result


def test_format_unused_mock_hint() -> None:
    v, p = _make_verifier_with_plugin()
    mock_config = ("logging:log", {"level": "INFO", "message": "test"}, "traceback here")
    result = p.format_unused_mock_hint(mock_config)
    assert "mocked but never called" in result
    assert "traceback here" in result


def test_format_unmocked_hint() -> None:
    v, p = _make_verifier_with_plugin()
    result = p.format_unmocked_hint("logging:log", ("INFO", "hello"), {})
    assert "bigfoot.log_mock.mock_log" in result


# ---------------------------------------------------------------------------
# ConflictError detection
# ---------------------------------------------------------------------------


def test_conflict_error_logger_log_already_patched() -> None:
    v, p = _make_verifier_with_plugin()
    foreign_patch = MagicMock()
    original = logging.Logger._log
    try:
        logging.Logger._log = foreign_patch  # type: ignore[assignment]
        with pytest.raises(ConflictError):
            p.check_conflicts()
    finally:
        logging.Logger._log = original  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Module-level API via bigfoot.log_mock proxy
# ---------------------------------------------------------------------------


def test_log_mock_proxy_in_sandbox(bigfoot_verifier: StrictVerifier) -> None:
    logger = logging.getLogger("test.proxy")

    with bigfoot.sandbox():
        logger.info("via proxy")

    bigfoot.log_mock.assert_info("via proxy", "test.proxy")


def test_log_mock_proxy_raises_outside_context() -> None:
    from bigfoot._errors import NoActiveVerifierError

    token = _current_test_verifier.set(None)
    try:
        with pytest.raises(NoActiveVerifierError):
            _ = bigfoot.log_mock.mock_log
    finally:
        _current_test_verifier.reset(token)
