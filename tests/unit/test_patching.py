"""Tests for PatchSet and PatchTarget."""

from bigfoot._patching import PatchSet, PatchTarget


class _DummyTarget:
    """Target object for patching tests."""
    value = "original"
    other = "original_other"


def test_patch_target_stores_obj_attr_replacement() -> None:
    target = _DummyTarget()
    pt = PatchTarget(obj=target, attr="value", replacement="new")
    assert pt.obj is target
    assert pt.attr == "value"
    assert pt.replacement == "new"
    assert pt.original is PatchTarget._ABSENT


def test_patch_set_add_stores_patch_target() -> None:
    ps = PatchSet()
    target = _DummyTarget()
    ps.add(target, "value", "new")
    assert len(ps._patches) == 1
    assert ps._patches[0].obj is target
    assert ps._patches[0].attr == "value"
    assert ps._patches[0].replacement == "new"


def test_patch_set_apply_replaces_attributes() -> None:
    target = _DummyTarget()
    ps = PatchSet()
    ps.add(target, "value", "patched")
    ps.apply()
    assert target.value == "patched"


def test_patch_set_apply_saves_original() -> None:
    target = _DummyTarget()
    ps = PatchSet()
    ps.add(target, "value", "patched")
    ps.apply()
    assert ps._patches[0].original == "original"


def test_patch_set_restore_restores_original() -> None:
    target = _DummyTarget()
    ps = PatchSet()
    ps.add(target, "value", "patched")
    ps.apply()
    ps.restore()
    assert target.value == "original"


def test_patch_set_restore_resets_to_absent() -> None:
    target = _DummyTarget()
    ps = PatchSet()
    ps.add(target, "value", "patched")
    ps.apply()
    ps.restore()
    assert ps._patches[0].original is PatchTarget._ABSENT


def test_patch_set_multiple_patches() -> None:
    target = _DummyTarget()
    ps = PatchSet()
    ps.add(target, "value", "patched_value")
    ps.add(target, "other", "patched_other")
    ps.apply()
    assert target.value == "patched_value"
    assert target.other == "patched_other"
    ps.restore()
    assert target.value == "original"
    assert target.other == "original_other"


def test_patch_set_restore_reverses_order() -> None:
    """Patches are restored in reverse application order."""
    target = _DummyTarget()
    ps = PatchSet()
    ps.add(target, "value", "first")
    ps.add(target, "other", "second")
    ps.apply()
    ps.restore()
    assert target.value == "original"
    assert target.other == "original_other"


def test_patch_set_restore_handles_none_original() -> None:
    """When original value was None, restore correctly sets it back to None."""

    class _NoneTarget:
        value = None

    target = _NoneTarget()
    ps = PatchSet()
    ps.add(target, "value", "replaced")
    ps.apply()
    assert target.value == "replaced"
    assert ps._patches[0].original is None
    ps.restore()
    assert target.value is None
