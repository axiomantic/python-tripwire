"""Tests for path resolution mechanism."""

import pytest

from tripwire._path_resolution import resolve_target


def test_resolve_simple_module_attr() -> None:
    """'os.path:sep' resolves to (os.path, 'sep')."""
    import os.path
    parent, attr = resolve_target("os.path:sep")
    assert parent is os.path
    assert attr == "sep"


def test_resolve_dotted_attr() -> None:
    """'os.path:join' resolves to (os.path, 'join')."""
    import os.path
    parent, attr = resolve_target("os.path:join")
    assert parent is os.path
    assert attr == "join"


def test_resolve_class_method() -> None:
    """'collections:OrderedDict.fromkeys' resolves to (OrderedDict, 'fromkeys')."""
    from collections import OrderedDict
    parent, attr = resolve_target("collections:OrderedDict.fromkeys")
    assert parent is OrderedDict
    assert attr == "fromkeys"


def test_resolve_missing_colon_raises_value_error() -> None:
    """Path without colon raises ValueError immediately."""
    with pytest.raises(ValueError, match="must use colon-separated format"):
        resolve_target("os.path.join")


def test_resolve_bad_module_raises_import_error() -> None:
    """Non-existent module raises ImportError."""
    with pytest.raises(ImportError):
        resolve_target("nonexistent_module_xyz:attr")


def test_resolve_bad_attr_raises_attribute_error() -> None:
    """Non-existent attribute raises AttributeError."""
    with pytest.raises(AttributeError):
        resolve_target("os.path:nonexistent_attr_xyz")


def test_resolve_bad_intermediate_attr_raises_attribute_error() -> None:
    """Non-existent intermediate attribute raises AttributeError."""
    with pytest.raises(AttributeError):
        resolve_target("os:nonexistent_xyz.join")


def test_resolve_nested_class_attr() -> None:
    """Deep dotted attr chains work."""
    import collections
    parent, attr = resolve_target("collections:OrderedDict.__init__")
    assert parent is collections.OrderedDict
    assert attr == "__init__"


def test_resolve_returns_correct_parent_for_setattr() -> None:
    """The parent object is the one on which setattr should be called."""
    import os.path
    parent, attr = resolve_target("os.path:sep")
    # Verify we can read the current value through this parent+attr
    assert getattr(parent, attr) == os.path.sep
