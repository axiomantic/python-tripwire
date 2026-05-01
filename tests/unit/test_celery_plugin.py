"""Unit tests for CeleryPlugin."""

from __future__ import annotations

import celery
import pytest

from tripwire._context import _current_test_verifier
from tripwire._errors import (
    InteractionMismatchError,
    MissingAssertionFieldsError,
    UnmockedInteractionError,
)
from tripwire._timeline import Interaction
from tripwire._verifier import StrictVerifier
from tripwire.plugins.celery_plugin import (
    _CELERY_AVAILABLE,
    CeleryMockConfig,
    CeleryPlugin,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Create a test Celery app and task for testing
_test_app = celery.Celery("test")
_test_app.config_from_object({"task_always_eager": False, "broker_url": "memory://"})


@_test_app.task(name="myapp.tasks.add")
def add_task(x, y):
    return x + y


@_test_app.task(name="myapp.tasks.send_email")
def send_email_task(to, subject, body):
    pass


def _make_verifier_with_plugin() -> tuple[StrictVerifier, CeleryPlugin]:
    """Return (verifier, plugin) with CeleryPlugin registered but NOT activated."""
    v = StrictVerifier()
    for p in v._plugins:
        if isinstance(p, CeleryPlugin):
            return v, p
    p = CeleryPlugin(v)
    return v, p


def _reset_plugin_count() -> None:
    """Force-reset the class-level install count to 0 and restore patches if leaked."""
    with CeleryPlugin._install_lock:
        CeleryPlugin._install_count = 0
        # Use the plugin's own _restore_patches() to avoid duplicating restoration logic.
        CeleryPlugin.__new__(CeleryPlugin).restore_patches()


@pytest.fixture(autouse=True)
def clean_plugin_counts() -> None:
    """Ensure plugin install count starts and ends at 0 for every test."""
    _reset_plugin_count()
    yield
    _reset_plugin_count()


# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------


def test_celery_available_flag() -> None:
    assert _CELERY_AVAILABLE is True


def test_activate_raises_when_celery_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import tripwire.plugins.celery_plugin as _cp

    v, p = _make_verifier_with_plugin()
    monkeypatch.setattr(_cp, "_CELERY_AVAILABLE", False)
    with pytest.raises(ImportError) as exc_info:
        p.activate()
    assert str(exc_info.value) == (
        "Install pytest-tripwire[celery] to use CeleryPlugin: pip install pytest-tripwire[celery]"
    )


# ---------------------------------------------------------------------------
# CeleryMockConfig dataclass
# ---------------------------------------------------------------------------


def test_celery_mock_config_fields() -> None:
    config = CeleryMockConfig(
        task_name="myapp.tasks.add",
        dispatch_method="delay",
        returns="mock-task-id",
        raises=None,
        required=False,
    )
    assert config.task_name == "myapp.tasks.add"
    assert config.dispatch_method == "delay"
    assert config.returns == "mock-task-id"
    assert config.raises is None
    assert config.required is False
    lines = config.registration_traceback.splitlines()
    assert lines[0].startswith("  File ")


def test_celery_mock_config_defaults() -> None:
    config = CeleryMockConfig(
        task_name="myapp.tasks.add",
        dispatch_method="delay",
        returns="mock-id",
    )
    assert config.raises is None
    assert config.required is True


# ---------------------------------------------------------------------------
# Activation and reference counting
# ---------------------------------------------------------------------------


def test_activate_installs_patch() -> None:
    from celery.app.task import Task

    original_delay = Task.delay
    v, p = _make_verifier_with_plugin()
    p.activate()
    assert Task.delay is not original_delay
    p.deactivate()


def test_deactivate_restores_patch() -> None:
    from celery.app.task import Task

    original_delay = Task.delay
    original_apply_async = Task.apply_async
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.deactivate()
    assert Task.delay is original_delay
    assert Task.apply_async is original_apply_async


def test_reference_counting_nested() -> None:
    from celery.app.task import Task

    original_delay = Task.delay
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.activate()
    assert CeleryPlugin._install_count == 2

    p.deactivate()
    assert CeleryPlugin._install_count == 1
    assert Task.delay is not original_delay

    p.deactivate()
    assert CeleryPlugin._install_count == 0
    assert Task.delay is original_delay


# ---------------------------------------------------------------------------
# Basic interception: delay
# ---------------------------------------------------------------------------


def test_mock_delay_returns_value() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_delay("myapp.tasks.add", returns="mock-result-id")

    with v.sandbox():
        result = add_task.delay(1, 2)

    assert result == "mock-result-id"


# ---------------------------------------------------------------------------
# Basic interception: apply_async
# ---------------------------------------------------------------------------


def test_mock_apply_async_returns_value() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_apply_async("myapp.tasks.add", returns="mock-async-id")

    with v.sandbox():
        result = add_task.apply_async(args=(1, 2), kwargs={"extra": "val"})

    assert result == "mock-async-id"


# ---------------------------------------------------------------------------
# Full assertion certainty
# ---------------------------------------------------------------------------


def test_assert_delay_full_assertion(tripwire_verifier: StrictVerifier) -> None:
    import tripwire

    tripwire.celery.mock_delay("myapp.tasks.add", returns="mock-id")

    with tripwire.sandbox():
        add_task.delay(1, 2)

    tripwire.celery.assert_delay(
        task_name="myapp.tasks.add",
        args=(1, 2),
        kwargs={},
        options={},
    )


def test_assert_apply_async_full_assertion(tripwire_verifier: StrictVerifier) -> None:
    import tripwire

    tripwire.celery.mock_apply_async("myapp.tasks.add", returns="mock-id")

    with tripwire.sandbox():
        add_task.apply_async(args=(1, 2), countdown=10)

    tripwire.celery.assert_apply_async(
        task_name="myapp.tasks.add",
        args=(1, 2),
        kwargs={},
        options={"countdown": 10},
    )


# ---------------------------------------------------------------------------
# FIFO ordering
# ---------------------------------------------------------------------------


def test_mock_delay_fifo() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_delay("myapp.tasks.add", returns="first")
    p.mock_delay("myapp.tasks.add", returns="second")

    with v.sandbox():
        first = add_task.delay(1, 2)
        second = add_task.delay(3, 4)

    assert first == "first"
    assert second == "second"


# ---------------------------------------------------------------------------
# Separate queues for different tasks
# ---------------------------------------------------------------------------


def test_mock_delay_separate_task_queues() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_delay("myapp.tasks.add", returns="add-result")
    p.mock_delay("myapp.tasks.send_email", returns="email-result")

    with v.sandbox():
        add_result = add_task.delay(1, 2)
        email_result = send_email_task.delay("user@test.com", "Hello", "Body")

    assert add_result == "add-result"
    assert email_result == "email-result"


# ---------------------------------------------------------------------------
# Separate queues for delay vs apply_async
# ---------------------------------------------------------------------------


def test_mock_delay_vs_apply_async_separate_queues() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_delay("myapp.tasks.add", returns="delay-result")
    p.mock_apply_async("myapp.tasks.add", returns="async-result")

    with v.sandbox():
        delay_result = add_task.delay(1, 2)
        async_result = add_task.apply_async(args=(3, 4))

    assert delay_result == "delay-result"
    assert async_result == "async-result"


# ---------------------------------------------------------------------------
# Exception propagation
# ---------------------------------------------------------------------------


def test_mock_delay_raises_exception() -> None:
    v, p = _make_verifier_with_plugin()
    err = ConnectionError("Broker unavailable")
    p.mock_delay("myapp.tasks.add", returns=None, raises=err)

    with v.sandbox():
        with pytest.raises(ConnectionError) as exc_info:
            add_task.delay(1, 2)

    assert str(exc_info.value) == "Broker unavailable"


# ---------------------------------------------------------------------------
# Unmocked interaction error
# ---------------------------------------------------------------------------


def test_unmocked_delay_raises() -> None:
    v, p = _make_verifier_with_plugin()

    with v.sandbox():
        with pytest.raises(UnmockedInteractionError) as exc_info:
            add_task.delay(1, 2)

    assert exc_info.value.source_id == "celery:myapp.tasks.add:delay"


def test_unmocked_apply_async_raises() -> None:
    v, p = _make_verifier_with_plugin()

    with v.sandbox():
        with pytest.raises(UnmockedInteractionError) as exc_info:
            add_task.apply_async(args=(1, 2))

    assert exc_info.value.source_id == "celery:myapp.tasks.add:apply_async"


# ---------------------------------------------------------------------------
# Unused mock detection
# ---------------------------------------------------------------------------


def test_get_unused_mocks_returns_unconsumed_required() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_delay("myapp.tasks.add", returns="first")
    p.mock_delay("myapp.tasks.add", returns="second")

    with v.sandbox():
        add_task.delay(1, 2)

    unused = p.get_unused_mocks()
    assert len(unused) == 1
    assert unused[0].task_name == "myapp.tasks.add"
    assert unused[0].returns == "second"


def test_get_unused_mocks_excludes_required_false() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_delay("myapp.tasks.add", returns="value", required=False)

    unused = p.get_unused_mocks()
    assert unused == []


# ---------------------------------------------------------------------------
# Missing assertion fields
# ---------------------------------------------------------------------------


def test_missing_assertion_fields(tripwire_verifier: StrictVerifier) -> None:
    import tripwire
    from tripwire.plugins.celery_plugin import _CelerySentinel

    tripwire.celery.mock_delay("myapp.tasks.add", returns="mock-id")

    with tripwire.sandbox():
        add_task.delay(1, 2)

    sentinel = _CelerySentinel("celery:myapp.tasks.add:delay")
    with pytest.raises(MissingAssertionFieldsError) as exc_info:
        # Only pass task_name, omit others
        tripwire_verifier.assert_interaction(sentinel, task_name="myapp.tasks.add")

    assert "dispatch_method" in exc_info.value.missing_fields
    # Now assert fully so teardown passes
    tripwire.celery.assert_delay(
        task_name="myapp.tasks.add",
        args=(1, 2),
        kwargs={},
        options={},
    )


# ---------------------------------------------------------------------------
# Interactions not auto-asserted
# ---------------------------------------------------------------------------


def test_celery_interactions_not_auto_asserted(tripwire_verifier: StrictVerifier) -> None:
    import tripwire

    tripwire.celery.mock_delay("myapp.tasks.add", returns="mock-id")

    with tripwire.sandbox():
        add_task.delay(1, 2)

    timeline = tripwire_verifier._timeline
    interactions = timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].source_id == "celery:myapp.tasks.add:delay"
    # Assert it so verify_all() at teardown succeeds
    tripwire.celery.assert_delay(
        task_name="myapp.tasks.add",
        args=(1, 2),
        kwargs={},
        options={},
    )


# ---------------------------------------------------------------------------
# Assertable fields
# ---------------------------------------------------------------------------


def test_assertable_fields_delay() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="celery:myapp.tasks.add:delay",
        sequence=0,
        details={
            "task_name": "myapp.tasks.add",
            "dispatch_method": "delay",
            "args": (1, 2),
            "kwargs": {},
            "options": {},
        },
        plugin=p,
    )
    assert p.assertable_fields(interaction) == frozenset(
        {"task_name", "dispatch_method", "args", "kwargs", "options"}
    )


def test_assertable_fields_apply_async() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="celery:myapp.tasks.add:apply_async",
        sequence=0,
        details={
            "task_name": "myapp.tasks.add",
            "dispatch_method": "apply_async",
            "args": (1, 2),
            "kwargs": {},
            "options": {"countdown": 10},
        },
        plugin=p,
    )
    assert p.assertable_fields(interaction) == frozenset(
        {"task_name", "dispatch_method", "args", "kwargs", "options"}
    )


# ---------------------------------------------------------------------------
# format_* methods
# ---------------------------------------------------------------------------


def test_format_interaction() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="celery:myapp.tasks.add:delay",
        sequence=0,
        details={
            "task_name": "myapp.tasks.add",
            "dispatch_method": "delay",
            "args": (1, 2),
            "kwargs": {},
            "options": {},
        },
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[CeleryPlugin] celery.delay('myapp.tasks.add', args=(1, 2))"


def test_format_mock_hint() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="celery:myapp.tasks.add:delay",
        sequence=0,
        details={
            "task_name": "myapp.tasks.add",
            "dispatch_method": "delay",
        },
        plugin=p,
    )
    result = p.format_mock_hint(interaction)
    assert result == "    tripwire.celery.mock_delay('myapp.tasks.add', returns=...)"


def test_format_unmocked_hint() -> None:
    v, p = _make_verifier_with_plugin()
    result = p.format_unmocked_hint("celery:myapp.tasks.add:delay", (), {})
    assert result == (
        "celery.delay('myapp.tasks.add', ...) was called but no mock was registered.\n"
        "Register a mock with:\n"
        "    tripwire.celery.mock_delay('myapp.tasks.add', returns=...)"
    )


def test_format_assert_hint() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="celery:myapp.tasks.add:delay",
        sequence=0,
        details={
            "task_name": "myapp.tasks.add",
            "dispatch_method": "delay",
            "args": (1, 2),
            "kwargs": {},
            "options": {},
        },
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == (
        "    tripwire.celery.assert_delay(\n"
        "        task_name='myapp.tasks.add',\n"
        "        dispatch_method='delay',\n"
        "        args=(1, 2),\n"
        "        kwargs={},\n"
        "        options={},\n"
        "    )"
    )


def test_format_unused_mock_hint() -> None:
    v, p = _make_verifier_with_plugin()
    config = CeleryMockConfig(
        task_name="myapp.tasks.add",
        dispatch_method="delay",
        returns="mock-id",
    )
    result = p.format_unused_mock_hint(config)
    expected_prefix = (
        "celery.delay('myapp.tasks.add') was mocked (required=True) but never called.\n"
        "Registered at:\n"
    )
    assert result == expected_prefix + config.registration_traceback


# ---------------------------------------------------------------------------
# Module-level proxy: tripwire.celery
# ---------------------------------------------------------------------------


def test_celery_mock_proxy_mock_delay(tripwire_verifier: StrictVerifier) -> None:
    import tripwire

    tripwire.celery.mock_delay("myapp.tasks.add", returns="proxy-result")

    with tripwire.sandbox():
        result = add_task.delay(1, 2)

    assert result == "proxy-result"
    tripwire.celery.assert_delay(
        task_name="myapp.tasks.add",
        args=(1, 2),
        kwargs={},
        options={},
    )


def test_celery_mock_proxy_raises_outside_context() -> None:
    import tripwire
    from tripwire._errors import NoActiveVerifierError

    token = _current_test_verifier.set(None)
    try:
        with pytest.raises(NoActiveVerifierError):
            _ = tripwire.celery.mock_delay
    finally:
        _current_test_verifier.reset(token)


# ---------------------------------------------------------------------------
# CeleryPlugin in __all__
# ---------------------------------------------------------------------------


def test_celery_plugin_in_all() -> None:
    import tripwire

    assert "CeleryPlugin" in tripwire.__all__
    assert "celery" in tripwire.__all__
    assert type(tripwire.celery).__name__ == "_CeleryProxy"


# ---------------------------------------------------------------------------
# Typed assertion helpers with wrong args
# ---------------------------------------------------------------------------


def test_assert_delay_wrong_args_raises(tripwire_verifier: StrictVerifier) -> None:
    import tripwire

    tripwire.celery.mock_delay("myapp.tasks.add", returns="mock-id")

    with tripwire.sandbox():
        add_task.delay(1, 2)

    with pytest.raises(InteractionMismatchError):
        tripwire.celery.assert_delay(
            task_name="myapp.tasks.add",
            args=(99, 99),
            kwargs={},
            options={},
        )
    # Assert correctly so teardown passes
    tripwire.celery.assert_delay(
        task_name="myapp.tasks.add",
        args=(1, 2),
        kwargs={},
        options={},
    )
