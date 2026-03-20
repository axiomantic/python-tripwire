"""Tests for _BaseMock, ImportSiteMock, ObjectMock, and _MockDispatchProxy."""

import sys
import types

import pytest

from bigfoot._errors import ConflictError, SandboxNotActiveError, UnmockedInteractionError
from bigfoot._mock_plugin import ImportSiteMock, MockPlugin, ObjectMock
from bigfoot._timeline import Interaction
from bigfoot._verifier import StrictVerifier


# --- Test fixtures ---

class _FakeService:
    """Fake service for patching tests."""
    @staticmethod
    def process(x: int) -> int:
        return x * 2

    @staticmethod
    def fetch(key: str) -> str:
        return f"real_{key}"


def _create_fake_module(name: str, **attrs: object) -> types.ModuleType:
    """Create a fake module with given attributes and register it in sys.modules."""
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


# --- ImportSiteMock tests ---

def test_import_site_mock_validates_path_format() -> None:
    """ImportSiteMock raises ValueError for paths without colon."""
    v = StrictVerifier()
    plugin = MockPlugin(v)
    with pytest.raises(ValueError, match="must use.*colon"):
        ImportSiteMock(path="invalid.path", plugin=plugin)


def test_import_site_mock_stores_path() -> None:
    v = StrictVerifier()
    plugin = MockPlugin(v)
    mock = ImportSiteMock(path="os.path:sep", plugin=plugin)
    assert mock._path == "os.path:sep"


def test_import_site_mock_display_name() -> None:
    v = StrictVerifier()
    plugin = MockPlugin(v)
    mock = ImportSiteMock(path="os.path:sep", plugin=plugin)
    assert mock._display_name == "os.path:sep"


def test_import_site_mock_getattr_returns_method_proxy() -> None:
    from bigfoot._mock_plugin import MethodProxy
    v = StrictVerifier()
    plugin = MockPlugin(v)
    mock = ImportSiteMock(path="os.path:join", plugin=plugin)
    method = mock.some_method
    assert isinstance(method, MethodProxy)
    assert method.source_id == "mock:os.path:join.some_method"


def test_import_site_mock_getattr_cached() -> None:
    v = StrictVerifier()
    plugin = MockPlugin(v)
    mock = ImportSiteMock(path="os.path:join", plugin=plugin)
    m1 = mock.some_method
    m2 = mock.some_method
    assert m1 is m2


def test_import_site_mock_getattr_private_raises() -> None:
    v = StrictVerifier()
    plugin = MockPlugin(v)
    mock = ImportSiteMock(path="os.path:join", plugin=plugin)
    with pytest.raises(AttributeError):
        mock._private


# --- ObjectMock tests ---

def test_object_mock_stores_target_and_attr() -> None:
    v = StrictVerifier()
    plugin = MockPlugin(v)
    target = _FakeService()
    mock = ObjectMock(target=target, attr="process", plugin=plugin)
    assert mock._target is target
    assert mock._attr == "process"


def test_object_mock_display_name() -> None:
    v = StrictVerifier()
    plugin = MockPlugin(v)
    target = _FakeService()
    mock = ObjectMock(target=target, attr="process", plugin=plugin)
    assert mock._display_name == "_FakeService.process"


# --- Activation / Deactivation tests ---

def test_import_site_mock_activate_patches_target(bigfoot_verifier: StrictVerifier) -> None:
    """Activating an ImportSiteMock replaces the target via setattr."""
    mod = _create_fake_module("_test_mod_activate", process=lambda x: x * 2)
    try:
        plugin = MockPlugin(bigfoot_verifier)
        mock = ImportSiteMock(path="_test_mod_activate:process", plugin=plugin)
        mock.returns(42)
        original = mod.process

        mock._activate(enforce=True)
        assert mod.process is not original

        mock._deactivate()
        assert mod.process is original
    finally:
        del sys.modules["_test_mod_activate"]


def test_object_mock_activate_patches_target(bigfoot_verifier: StrictVerifier) -> None:
    """Activating an ObjectMock replaces the attr via setattr."""
    target = _FakeService()
    plugin = MockPlugin(bigfoot_verifier)
    mock = ObjectMock(target=target, attr="process", plugin=plugin)
    mock.returns(42)
    original = target.process

    mock._activate(enforce=True)
    assert target.process is not original

    mock._deactivate()
    assert target.process is original


def test_mock_deactivate_restores_original(bigfoot_verifier: StrictVerifier) -> None:
    """Deactivation restores the original attribute value."""
    mod = _create_fake_module("_test_mod_restore", value="original")
    try:
        plugin = MockPlugin(bigfoot_verifier)
        mock = ImportSiteMock(path="_test_mod_restore:value", plugin=plugin)
        mock.returns("mocked")

        mock._activate(enforce=True)
        assert mod.value != "original"

        mock._deactivate()
        assert mod.value == "original"
    finally:
        del sys.modules["_test_mod_restore"]


# --- Context manager tests ---

def test_mock_context_manager_sets_enforce_false(bigfoot_verifier: StrictVerifier) -> None:
    """Individual context manager (with mock:) sets enforce=False."""
    mod = _create_fake_module("_test_mod_cm", fn=lambda: "real")
    try:
        plugin = MockPlugin(bigfoot_verifier)
        mock = ImportSiteMock(path="_test_mod_cm:fn", plugin=plugin)
        mock.returns("mocked")

        with mock:
            assert mock._enforce is False
    finally:
        del sys.modules["_test_mod_cm"]


# --- Shortcut methods tests ---

def test_base_mock_returns_shortcut() -> None:
    v = StrictVerifier()
    plugin = MockPlugin(v)
    mock = ImportSiteMock(path="os.path:sep", plugin=plugin)
    result = mock.returns("value")
    assert result is mock  # chainable


def test_base_mock_raises_shortcut() -> None:
    v = StrictVerifier()
    plugin = MockPlugin(v)
    mock = ImportSiteMock(path="os.path:sep", plugin=plugin)
    result = mock.raises(ValueError("test"))
    assert result is mock  # chainable


def test_base_mock_calls_shortcut() -> None:
    v = StrictVerifier()
    plugin = MockPlugin(v)
    mock = ImportSiteMock(path="os.path:sep", plugin=plugin)
    result = mock.calls(lambda: None)
    assert result is mock  # chainable


# --- Conflict detection tests ---

def test_conflict_detection_same_target(bigfoot_verifier: StrictVerifier) -> None:
    """Two mocks on the same resolved target raise ConflictError."""
    mod = _create_fake_module("_test_mod_conflict", fn=lambda: "real")
    try:
        plugin = MockPlugin(bigfoot_verifier)
        m1 = ImportSiteMock(path="_test_mod_conflict:fn", plugin=plugin)
        m1.returns("one")
        m2 = ImportSiteMock(path="_test_mod_conflict:fn", plugin=plugin)
        m2.returns("two")

        m1._activate(enforce=True)
        with pytest.raises(ConflictError):
            m2._activate(enforce=True)
        m1._deactivate()
    finally:
        del sys.modules["_test_mod_conflict"]
