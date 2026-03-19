"""Unit tests for FileIoPlugin."""

from __future__ import annotations

import builtins
import io
import os
import pathlib
import shutil

import pytest

from bigfoot._errors import (
    ConflictError,
    MissingAssertionFieldsError,
    UnmockedInteractionError,
)
from bigfoot._timeline import Interaction
from bigfoot._verifier import StrictVerifier
from bigfoot.plugins.file_io_plugin import (
    FileIoMockConfig,
    FileIoPlugin,
    _file_io_bypass,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_verifier_with_plugin() -> tuple[StrictVerifier, FileIoPlugin]:
    """Return (verifier, plugin) with FileIoPlugin registered but NOT activated.

    FileIoPlugin is not default-enabled, so the auto-verifier won't have one.
    We create a fresh StrictVerifier and then add the plugin manually.
    """
    v = StrictVerifier()
    # FileIoPlugin is not auto-instantiated (default_enabled=False),
    # so we always need to create it manually.
    p = FileIoPlugin(v)
    return v, p


def _reset_plugin_count() -> None:
    """Force-reset the class-level install count to 0 and restore patches if leaked."""
    with FileIoPlugin._install_lock:
        FileIoPlugin._install_count = 0
        if FileIoPlugin._original_open is not None:
            builtins.open = FileIoPlugin._original_open
            FileIoPlugin._original_open = None
        if FileIoPlugin._original_read_text is not None:
            pathlib.Path.read_text = FileIoPlugin._original_read_text
            FileIoPlugin._original_read_text = None
        if FileIoPlugin._original_read_bytes is not None:
            pathlib.Path.read_bytes = FileIoPlugin._original_read_bytes
            FileIoPlugin._original_read_bytes = None
        if FileIoPlugin._original_write_text is not None:
            pathlib.Path.write_text = FileIoPlugin._original_write_text
            FileIoPlugin._original_write_text = None
        if FileIoPlugin._original_write_bytes is not None:
            pathlib.Path.write_bytes = FileIoPlugin._original_write_bytes
            FileIoPlugin._original_write_bytes = None
        if FileIoPlugin._original_remove is not None:
            os.remove = FileIoPlugin._original_remove
            FileIoPlugin._original_remove = None
        if FileIoPlugin._original_unlink is not None:
            os.unlink = FileIoPlugin._original_unlink
            FileIoPlugin._original_unlink = None
        if FileIoPlugin._original_rename is not None:
            os.rename = FileIoPlugin._original_rename
            FileIoPlugin._original_rename = None
        if FileIoPlugin._original_replace is not None:
            os.replace = FileIoPlugin._original_replace
            FileIoPlugin._original_replace = None
        if FileIoPlugin._original_makedirs is not None:
            os.makedirs = FileIoPlugin._original_makedirs
            FileIoPlugin._original_makedirs = None
        if FileIoPlugin._original_mkdir is not None:
            os.mkdir = FileIoPlugin._original_mkdir
            FileIoPlugin._original_mkdir = None
        if FileIoPlugin._original_copy is not None:
            shutil.copy = FileIoPlugin._original_copy
            FileIoPlugin._original_copy = None
        if FileIoPlugin._original_copy2 is not None:
            shutil.copy2 = FileIoPlugin._original_copy2
            FileIoPlugin._original_copy2 = None
        if FileIoPlugin._original_copytree is not None:
            shutil.copytree = FileIoPlugin._original_copytree
            FileIoPlugin._original_copytree = None
        if FileIoPlugin._original_rmtree is not None:
            shutil.rmtree = FileIoPlugin._original_rmtree
            FileIoPlugin._original_rmtree = None


@pytest.fixture(autouse=True)
def clean_plugin_counts():
    """Ensure plugin install count starts and ends at 0 for every test."""
    _reset_plugin_count()
    yield
    _reset_plugin_count()


# ---------------------------------------------------------------------------
# 1. Basic interception for each operation type
# ---------------------------------------------------------------------------


# ESCAPE: test_mock_open_read_returns_string_io
#   CLAIM: mock_operation("open", "/tmp/test.txt", returns="hello") intercepts
#          builtins.open("/tmp/test.txt", "r") and returns a StringIO with "hello".
#   PATH:  mock_operation -> queue entry -> intercepted open -> match -> return StringIO("hello").
#   CHECK: result.read() == "hello".
#   MUTATION: Not intercepting open returns a real file handle (which fails or returns different content).
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_mock_open_read_returns_string_io() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("open", "/tmp/test.txt", returns="hello")

    with v.sandbox():
        f = builtins.open("/tmp/test.txt")
        content = f.read()
        f.close()

    assert content == "hello"


# ESCAPE: test_mock_open_write_returns_string_io
#   CLAIM: mock_operation("open", "/tmp/out.txt") for write mode intercepts and absorbs data.
#   PATH:  mock_operation -> queue entry -> intercepted open("w") -> return empty StringIO.
#   CHECK: Writing does not raise; the returned handle is a StringIO.
#   MUTATION: Not intercepting would attempt real file write which may fail or write to disk.
#   ESCAPE: Nothing reasonable -- isinstance check and write success.
def test_mock_open_write_returns_string_io() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("open", "/tmp/out.txt")

    with v.sandbox():
        f = builtins.open("/tmp/out.txt", "w")
        f.write("data")
        f.close()

    assert isinstance(f, io.StringIO)
    # Verify the interaction was actually recorded on the timeline
    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details == {"path": os.path.normpath("/tmp/out.txt"), "mode": "w", "encoding": "utf-8"}


# ESCAPE: test_mock_open_binary_read_returns_bytes_io
#   CLAIM: mock_operation("open", "/tmp/bin.dat", returns=b"binary") intercepts
#          open("/tmp/bin.dat", "rb") and returns a BytesIO with b"binary".
#   PATH:  mock_operation -> queue entry -> intercepted open("rb") -> return BytesIO(b"binary").
#   CHECK: result.read() == b"binary".
#   MUTATION: Returning StringIO instead of BytesIO would fail type check on read result.
#   ESCAPE: Nothing reasonable -- exact bytes equality.
def test_mock_open_binary_read_returns_bytes_io() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("open", "/tmp/bin.dat", returns=b"binary")

    with v.sandbox():
        f = builtins.open("/tmp/bin.dat", "rb")
        content = f.read()
        f.close()

    assert content == b"binary"


# ESCAPE: test_mock_read_text
#   CLAIM: mock_operation("read_text", "/tmp/r.txt", returns="text_content") intercepts
#          pathlib.Path("/tmp/r.txt").read_text().
#   PATH:  mock_operation -> queue entry -> intercepted read_text -> return "text_content".
#   CHECK: result == "text_content".
#   MUTATION: Not intercepting calls real read_text which would fail (no such file).
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_mock_read_text() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("read_text", "/tmp/r.txt", returns="text_content")

    with v.sandbox():
        result = pathlib.Path("/tmp/r.txt").read_text()

    assert result == "text_content"


# ESCAPE: test_mock_read_bytes
#   CLAIM: mock_operation("read_bytes", "/tmp/r.bin", returns=b"bytes_content") intercepts
#          pathlib.Path("/tmp/r.bin").read_bytes().
#   PATH:  mock_operation -> queue entry -> intercepted read_bytes -> return b"bytes_content".
#   CHECK: result == b"bytes_content".
#   MUTATION: Not intercepting calls real read_bytes which would fail.
#   ESCAPE: Nothing reasonable -- exact bytes equality.
def test_mock_read_bytes() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("read_bytes", "/tmp/r.bin", returns=b"bytes_content")

    with v.sandbox():
        result = pathlib.Path("/tmp/r.bin").read_bytes()

    assert result == b"bytes_content"


# ESCAPE: test_mock_write_text
#   CLAIM: mock_operation("write_text", "/tmp/w.txt") intercepts
#          pathlib.Path("/tmp/w.txt").write_text("some data").
#   PATH:  mock_operation -> queue entry -> intercepted write_text -> records data.
#   CHECK: No exception raised; interaction recorded.
#   MUTATION: Not intercepting calls real write_text which may fail.
#   ESCAPE: Nothing reasonable -- no exception and interaction recorded.
def test_mock_write_text() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("write_text", "/tmp/w.txt")

    with v.sandbox():
        pathlib.Path("/tmp/w.txt").write_text("some data")

    # Verify interaction was recorded with correct details
    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details == {"path": os.path.normpath("/tmp/w.txt"), "data": "some data"}


# ESCAPE: test_mock_write_bytes
#   CLAIM: mock_operation("write_bytes", "/tmp/w.bin") intercepts
#          pathlib.Path("/tmp/w.bin").write_bytes(b"binary data").
#   PATH:  mock_operation -> queue entry -> intercepted write_bytes -> records data.
#   CHECK: Interaction details match exactly.
#   MUTATION: Not recording data or recording wrong value fails equality.
#   ESCAPE: Nothing reasonable -- exact dict equality.
def test_mock_write_bytes() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("write_bytes", "/tmp/w.bin")

    with v.sandbox():
        pathlib.Path("/tmp/w.bin").write_bytes(b"binary data")

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details == {"path": os.path.normpath("/tmp/w.bin"), "data": b"binary data"}


# ESCAPE: test_mock_remove
#   CLAIM: mock_operation("remove", "/tmp/del.txt") intercepts os.remove("/tmp/del.txt").
#   PATH:  mock_operation -> queue entry -> intercepted os.remove -> records interaction.
#   CHECK: No exception; interaction details match exactly.
#   MUTATION: Not intercepting calls real os.remove which fails (no such file).
#   ESCAPE: Nothing reasonable -- exact dict equality.
def test_mock_remove() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("remove", "/tmp/del.txt")

    with v.sandbox():
        os.remove("/tmp/del.txt")

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details == {"path": os.path.normpath("/tmp/del.txt")}


# ESCAPE: test_mock_unlink
#   CLAIM: mock_operation("unlink", "/tmp/ul.txt") intercepts os.unlink("/tmp/ul.txt").
#   PATH:  mock_operation -> queue entry -> intercepted os.unlink -> records interaction.
#   CHECK: No exception; interaction details match exactly.
#   MUTATION: Not intercepting calls real os.unlink which fails.
#   ESCAPE: Nothing reasonable -- exact dict equality.
def test_mock_unlink() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("unlink", "/tmp/ul.txt")

    with v.sandbox():
        os.unlink("/tmp/ul.txt")

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details == {"path": os.path.normpath("/tmp/ul.txt")}


# ESCAPE: test_mock_rename
#   CLAIM: mock_operation("rename", "/tmp/old.txt") intercepts os.rename("/tmp/old.txt", "/tmp/new.txt").
#   PATH:  mock_operation -> queue entry -> intercepted os.rename -> records src, dst.
#   CHECK: Interaction details match exactly with src and dst.
#   MUTATION: Recording wrong fields fails equality.
#   ESCAPE: Nothing reasonable -- exact dict equality.
def test_mock_rename() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("rename", "/tmp/old.txt")

    with v.sandbox():
        os.rename("/tmp/old.txt", "/tmp/new.txt")

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details == {"src": os.path.normpath("/tmp/old.txt"), "dst": os.path.normpath("/tmp/new.txt")}


# ESCAPE: test_mock_replace
#   CLAIM: mock_operation("replace", "/tmp/src.txt") intercepts os.replace.
#   PATH:  mock_operation -> queue entry -> intercepted os.replace -> records src, dst.
#   CHECK: Interaction details match exactly.
#   MUTATION: Not intercepting os.replace separately from os.rename fails.
#   ESCAPE: Nothing reasonable -- exact dict equality.
def test_mock_replace() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("replace", "/tmp/src.txt")

    with v.sandbox():
        os.replace("/tmp/src.txt", "/tmp/dst.txt")

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details == {"src": os.path.normpath("/tmp/src.txt"), "dst": os.path.normpath("/tmp/dst.txt")}


# ESCAPE: test_mock_makedirs
#   CLAIM: mock_operation("makedirs", "/tmp/newdir") intercepts os.makedirs.
#   PATH:  mock_operation -> queue entry -> intercepted os.makedirs -> records path, exist_ok.
#   CHECK: Interaction details match exactly.
#   MUTATION: Not recording exist_ok fails equality.
#   ESCAPE: Nothing reasonable -- exact dict equality.
def test_mock_makedirs() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("makedirs", "/tmp/newdir")

    with v.sandbox():
        os.makedirs("/tmp/newdir", exist_ok=True)

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details == {"path": os.path.normpath("/tmp/newdir"), "exist_ok": True}


# ESCAPE: test_mock_mkdir
#   CLAIM: mock_operation("mkdir", "/tmp/singledir") intercepts os.mkdir.
#   PATH:  mock_operation -> queue entry -> intercepted os.mkdir -> records path.
#   CHECK: Interaction details match exactly.
#   MUTATION: Missing path field fails equality.
#   ESCAPE: Nothing reasonable -- exact dict equality.
def test_mock_mkdir() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("mkdir", "/tmp/singledir")

    with v.sandbox():
        os.mkdir("/tmp/singledir")

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details == {"path": os.path.normpath("/tmp/singledir")}


# ESCAPE: test_mock_copy
#   CLAIM: mock_operation("copy", "/tmp/src.txt") intercepts shutil.copy.
#   PATH:  mock_operation -> queue entry -> intercepted shutil.copy -> records src, dst.
#   CHECK: Interaction details match exactly.
#   MUTATION: Not intercepting shutil.copy calls real function.
#   ESCAPE: Nothing reasonable -- exact dict equality.
def test_mock_copy() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("copy", "/tmp/src.txt")

    with v.sandbox():
        shutil.copy("/tmp/src.txt", "/tmp/dst.txt")

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details == {"src": os.path.normpath("/tmp/src.txt"), "dst": os.path.normpath("/tmp/dst.txt")}


# ESCAPE: test_mock_copy2
#   CLAIM: mock_operation("copy2", "/tmp/s2.txt") intercepts shutil.copy2.
#   PATH:  mock_operation -> queue entry -> intercepted shutil.copy2 -> records src, dst.
#   CHECK: Interaction details match exactly.
#   MUTATION: Not intercepting shutil.copy2 calls real function.
#   ESCAPE: Nothing reasonable -- exact dict equality.
def test_mock_copy2() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("copy2", "/tmp/s2.txt")

    with v.sandbox():
        shutil.copy2("/tmp/s2.txt", "/tmp/d2.txt")

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details == {"src": os.path.normpath("/tmp/s2.txt"), "dst": os.path.normpath("/tmp/d2.txt")}


# ESCAPE: test_mock_copytree
#   CLAIM: mock_operation("copytree", "/tmp/srcdir") intercepts shutil.copytree.
#   PATH:  mock_operation -> queue entry -> intercepted shutil.copytree -> records src, dst.
#   CHECK: Interaction details match exactly.
#   MUTATION: Not intercepting shutil.copytree calls real function.
#   ESCAPE: Nothing reasonable -- exact dict equality.
def test_mock_copytree() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("copytree", "/tmp/srcdir")

    with v.sandbox():
        shutil.copytree("/tmp/srcdir", "/tmp/dstdir")

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details == {"src": os.path.normpath("/tmp/srcdir"), "dst": os.path.normpath("/tmp/dstdir")}


# ESCAPE: test_mock_rmtree
#   CLAIM: mock_operation("rmtree", "/tmp/rmdir") intercepts shutil.rmtree.
#   PATH:  mock_operation -> queue entry -> intercepted shutil.rmtree -> records path.
#   CHECK: Interaction details match exactly.
#   MUTATION: Not intercepting shutil.rmtree calls real function.
#   ESCAPE: Nothing reasonable -- exact dict equality.
def test_mock_rmtree() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("rmtree", "/tmp/rmdir")

    with v.sandbox():
        shutil.rmtree("/tmp/rmdir")

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details == {"path": os.path.normpath("/tmp/rmdir")}


# ---------------------------------------------------------------------------
# 2. Full assertion certainty
# ---------------------------------------------------------------------------


# ESCAPE: test_assertable_fields_returns_all_detail_keys_for_open
#   CLAIM: assertable_fields() returns frozenset(interaction.details.keys()) for open.
#   PATH:  assertable_fields(interaction) -> frozenset of all detail keys.
#   CHECK: result == frozenset({"path", "mode", "encoding"}).
#   MUTATION: Returning empty frozenset skips completeness enforcement.
#   ESCAPE: Nothing reasonable -- exact frozenset equality.
def test_assertable_fields_returns_all_detail_keys_for_open() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="file_io:open",
        sequence=0,
        details={"path": "/tmp/f.txt", "mode": "r", "encoding": "utf-8"},
        plugin=p,
    )
    assert p.assertable_fields(interaction) == frozenset({"path", "mode", "encoding"})


# ESCAPE: test_assertable_fields_returns_all_detail_keys_for_read_text
#   CLAIM: assertable_fields() returns frozenset(interaction.details.keys()) for read_text.
#   PATH:  assertable_fields(interaction) -> frozenset({"path"}).
#   CHECK: result == frozenset({"path"}).
#   MUTATION: Returning empty frozenset skips completeness enforcement.
#   ESCAPE: Nothing reasonable -- exact frozenset equality.
def test_assertable_fields_returns_all_detail_keys_for_read_text() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="file_io:read_text",
        sequence=0,
        details={"path": "/tmp/r.txt"},
        plugin=p,
    )
    assert p.assertable_fields(interaction) == frozenset({"path"})


# ESCAPE: test_assertable_fields_returns_all_detail_keys_for_rename
#   CLAIM: assertable_fields() returns frozenset({"src", "dst"}) for rename.
#   PATH:  assertable_fields(interaction) -> frozenset({"src", "dst"}).
#   CHECK: result == frozenset({"src", "dst"}).
#   MUTATION: Returning only one field skips completeness check on the other.
#   ESCAPE: Nothing reasonable -- exact frozenset equality.
def test_assertable_fields_returns_all_detail_keys_for_rename() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="file_io:rename",
        sequence=0,
        details={"src": "/a", "dst": "/b"},
        plugin=p,
    )
    assert p.assertable_fields(interaction) == frozenset({"src", "dst"})


# ESCAPE: test_assertable_fields_returns_all_detail_keys_for_makedirs
#   CLAIM: assertable_fields() returns frozenset({"path", "exist_ok"}) for makedirs.
#   PATH:  assertable_fields(interaction) -> frozenset({"path", "exist_ok"}).
#   CHECK: result == frozenset({"path", "exist_ok"}).
#   MUTATION: Omitting exist_ok skips completeness check on that field.
#   ESCAPE: Nothing reasonable -- exact frozenset equality.
def test_assertable_fields_returns_all_detail_keys_for_makedirs() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="file_io:makedirs",
        sequence=0,
        details={"path": "/tmp/d", "exist_ok": True},
        plugin=p,
    )
    assert p.assertable_fields(interaction) == frozenset({"path", "exist_ok"})


# ESCAPE: test_assertable_fields_returns_all_detail_keys_for_write_text
#   CLAIM: assertable_fields() returns frozenset({"path", "data"}) for write_text.
#   PATH:  assertable_fields(interaction) -> frozenset({"path", "data"}).
#   CHECK: result == frozenset({"path", "data"}).
#   MUTATION: Omitting data skips completeness check on that field.
#   ESCAPE: Nothing reasonable -- exact frozenset equality.
def test_assertable_fields_returns_all_detail_keys_for_write_text() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="file_io:write_text",
        sequence=0,
        details={"path": "/tmp/w.txt", "data": "hello"},
        plugin=p,
    )
    assert p.assertable_fields(interaction) == frozenset({"path", "data"})


# ---------------------------------------------------------------------------
# 3. Unmocked error
# ---------------------------------------------------------------------------


# ESCAPE: test_unmocked_error_when_no_mock_registered
#   CLAIM: Calling builtins.open() with no mock raises UnmockedInteractionError.
#   PATH:  intercepted open -> no queue entry -> raise UnmockedInteractionError.
#   CHECK: UnmockedInteractionError raised; source_id == "file_io:open".
#   MUTATION: Silently falling through to real open would not raise.
#   ESCAPE: Nothing reasonable -- exact exception type and source_id.
def test_unmocked_error_when_no_mock_registered() -> None:
    v, p = _make_verifier_with_plugin()

    with v.sandbox():
        with pytest.raises(UnmockedInteractionError) as exc_info:
            builtins.open("/tmp/nonexistent.txt")

    assert exc_info.value.source_id == "file_io:open"


# ESCAPE: test_unmocked_error_read_text
#   CLAIM: Calling Path.read_text() with no mock raises UnmockedInteractionError.
#   PATH:  intercepted read_text -> no queue entry -> raise UnmockedInteractionError.
#   CHECK: UnmockedInteractionError raised; source_id == "file_io:read_text".
#   MUTATION: Silently calling real read_text would not raise (or raise FileNotFoundError).
#   ESCAPE: Nothing reasonable -- exact exception type and source_id.
def test_unmocked_error_read_text() -> None:
    v, p = _make_verifier_with_plugin()

    with v.sandbox():
        with pytest.raises(UnmockedInteractionError) as exc_info:
            pathlib.Path("/tmp/nonexistent.txt").read_text()

    assert exc_info.value.source_id == "file_io:read_text"


# ---------------------------------------------------------------------------
# 4. Unused mock
# ---------------------------------------------------------------------------


# ESCAPE: test_unused_mock_reported_by_get_unused_mocks
#   CLAIM: Registering a mock but never consuming it returns it from get_unused_mocks().
#   PATH:  mock_operation -> queue entry stays unconsumed -> get_unused_mocks scans queues.
#   CHECK: len(unused) == 1; unused[0].operation == "open"; unused[0].path_pattern == "/tmp/f.txt".
#   MUTATION: Not tracking unconsumed mocks returns empty list.
#   ESCAPE: Nothing reasonable -- exact equality on all fields.
def test_unused_mock_reported_by_get_unused_mocks() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("open", "/tmp/f.txt", returns="data")

    unused = p.get_unused_mocks()
    assert len(unused) == 1
    assert unused[0].operation == "open"
    assert unused[0].path_pattern == os.path.normpath("/tmp/f.txt")
    assert unused[0].returns == "data"


# ESCAPE: test_unused_mock_excluded_when_required_false
#   CLAIM: Mocks with required=False are excluded from get_unused_mocks().
#   PATH:  mock_operation(required=False) -> get_unused_mocks filters by required.
#   CHECK: get_unused_mocks() == [].
#   MUTATION: Not filtering by required returns the mock; list is non-empty.
#   ESCAPE: Nothing reasonable -- exact equality with empty list.
def test_unused_mock_excluded_when_required_false() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("open", "/tmp/f.txt", returns="data", required=False)

    unused = p.get_unused_mocks()
    assert unused == []


# ---------------------------------------------------------------------------
# 5. Missing fields
# ---------------------------------------------------------------------------


# ESCAPE: test_missing_fields_error_when_field_omitted
#   CLAIM: Calling assert_open() without encoding raises MissingAssertionFieldsError.
#   PATH:  assert_open(path=..., mode=...) -> assert_interaction -> missing "encoding" field.
#   CHECK: MissingAssertionFieldsError raised.
#   MUTATION: Not enforcing field completeness passes silently.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_missing_fields_error_when_field_omitted(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    p = FileIoPlugin(bigfoot_verifier)
    p.mock_operation("open", "/tmp/f.txt", returns="data")

    with bigfoot.sandbox():
        f = builtins.open("/tmp/f.txt")
        f.close()

    with pytest.raises(MissingAssertionFieldsError):
        # Omitting encoding should raise
        p.assert_open(path="/tmp/f.txt", mode="r")

    # Now assert properly so verify_all() at teardown succeeds
    p.assert_open(path="/tmp/f.txt", mode="r", encoding="utf-8")


# ---------------------------------------------------------------------------
# 6. Typed helpers
# ---------------------------------------------------------------------------


# ESCAPE: test_assert_open_typed_helper
#   CLAIM: assert_open() asserts the next file_io:open interaction with all required fields.
#   PATH:  assert_open -> assert_interaction with path, mode, encoding.
#   CHECK: No exception raised (all fields provided, values match).
#   MUTATION: If assert_open passes wrong sentinel, InteractionMismatchError raised.
#   ESCAPE: Nothing reasonable -- exact field matching.
def test_assert_open_typed_helper(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    p = FileIoPlugin(bigfoot_verifier)
    p.mock_operation("open", "/tmp/f.txt", returns="data")

    with bigfoot.sandbox():
        f = builtins.open("/tmp/f.txt")
        f.close()

    p.assert_open(path="/tmp/f.txt", mode="r", encoding="utf-8")


# ESCAPE: test_assert_read_text_typed_helper
#   CLAIM: assert_read_text() asserts the next file_io:read_text interaction.
#   PATH:  assert_read_text -> assert_interaction with path.
#   CHECK: No exception raised.
#   MUTATION: Wrong path value causes InteractionMismatchError.
#   ESCAPE: Nothing reasonable -- exact field matching.
def test_assert_read_text_typed_helper(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    p = FileIoPlugin(bigfoot_verifier)
    p.mock_operation("read_text", "/tmp/r.txt", returns="content")

    with bigfoot.sandbox():
        pathlib.Path("/tmp/r.txt").read_text()

    p.assert_read_text(path="/tmp/r.txt")


# ESCAPE: test_assert_write_text_typed_helper
#   CLAIM: assert_write_text() asserts the next file_io:write_text interaction.
#   PATH:  assert_write_text -> assert_interaction with path, data.
#   CHECK: No exception raised.
#   MUTATION: Wrong data causes InteractionMismatchError.
#   ESCAPE: Nothing reasonable -- exact field matching.
def test_assert_write_text_typed_helper(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    p = FileIoPlugin(bigfoot_verifier)
    p.mock_operation("write_text", "/tmp/w.txt")

    with bigfoot.sandbox():
        pathlib.Path("/tmp/w.txt").write_text("hello")

    p.assert_write_text(path="/tmp/w.txt", data="hello")


# ESCAPE: test_assert_remove_typed_helper
#   CLAIM: assert_remove() asserts the next file_io:remove interaction.
#   PATH:  assert_remove -> assert_interaction with path.
#   CHECK: No exception raised.
#   MUTATION: Wrong path causes InteractionMismatchError.
#   ESCAPE: Nothing reasonable -- exact field matching.
def test_assert_remove_typed_helper(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    p = FileIoPlugin(bigfoot_verifier)
    p.mock_operation("remove", "/tmp/del.txt")

    with bigfoot.sandbox():
        os.remove("/tmp/del.txt")

    p.assert_remove(path="/tmp/del.txt")


# ESCAPE: test_assert_rename_typed_helper
#   CLAIM: assert_rename() asserts the next file_io:rename interaction.
#   PATH:  assert_rename -> assert_interaction with src, dst.
#   CHECK: No exception raised.
#   MUTATION: Wrong src or dst causes InteractionMismatchError.
#   ESCAPE: Nothing reasonable -- exact field matching.
def test_assert_rename_typed_helper(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    p = FileIoPlugin(bigfoot_verifier)
    p.mock_operation("rename", "/tmp/old.txt")

    with bigfoot.sandbox():
        os.rename("/tmp/old.txt", "/tmp/new.txt")

    p.assert_rename(src="/tmp/old.txt", dst="/tmp/new.txt")


# ---------------------------------------------------------------------------
# 7. Conflict detection
# ---------------------------------------------------------------------------


# ESCAPE: test_conflict_error_if_already_patched
#   CLAIM: If builtins.open is already patched by another library, activate() raises ConflictError.
#   PATH:  activate() -> check if builtins.open is already patched -> raise ConflictError.
#   CHECK: ConflictError raised.
#   MUTATION: Not checking for existing patches silently overwrites them.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_conflict_error_if_already_patched(monkeypatch: pytest.MonkeyPatch) -> None:
    v, p = _make_verifier_with_plugin()

    # Simulate another library having patched builtins.open
    original_open = builtins.open
    fake_open = lambda *a, **k: None  # noqa: E731
    fake_open.__module__ = "unittest.mock"
    monkeypatch.setattr(builtins, "open", fake_open)

    with pytest.raises(ConflictError):
        p.activate()


# ---------------------------------------------------------------------------
# 8. Exception propagation
# ---------------------------------------------------------------------------


# ESCAPE: test_mock_operation_raises_os_error
#   CLAIM: mock_operation("open", "/tmp/f.txt", raises=OSError("perm denied")) raises on intercept.
#   PATH:  interceptor pops config with raises set -> raises config.raises.
#   CHECK: OSError raised; str(exc) == "perm denied".
#   MUTATION: Not raising when config.raises is set returns mock value instead.
#   ESCAPE: Raising different exception type fails type check.
def test_mock_operation_raises_os_error() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("open", "/tmp/f.txt", raises=OSError("perm denied"))

    with v.sandbox():
        with pytest.raises(OSError) as exc_info:
            builtins.open("/tmp/f.txt")

    assert str(exc_info.value) == "perm denied"


# ESCAPE: test_mock_remove_raises_file_not_found
#   CLAIM: mock_operation("remove", "/tmp/f.txt", raises=FileNotFoundError("gone")) raises.
#   PATH:  interceptor pops config with raises -> raises FileNotFoundError.
#   CHECK: FileNotFoundError raised; str(exc) == "gone".
#   MUTATION: Not raising returns None instead.
#   ESCAPE: Nothing reasonable -- exact exception type and message.
def test_mock_remove_raises_file_not_found() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("remove", "/tmp/f.txt", raises=FileNotFoundError("gone"))

    with v.sandbox():
        with pytest.raises(FileNotFoundError) as exc_info:
            os.remove("/tmp/f.txt")

    assert str(exc_info.value) == "gone"


# ---------------------------------------------------------------------------
# 9. Graceful degradation (always available)
# ---------------------------------------------------------------------------


# ESCAPE: test_file_io_plugin_always_importable
#   CLAIM: FileIoPlugin is always importable (no optional dependencies).
#   PATH:  import bigfoot.plugins.file_io_plugin -> no ImportError.
#   CHECK: FileIoPlugin is a class; no exception on import.
#   MUTATION: Adding a try/except guard around a missing dep would change this.
#   ESCAPE: Nothing reasonable -- import succeeds or fails.
def test_file_io_plugin_always_importable() -> None:
    from bigfoot.plugins.file_io_plugin import (
        FileIoPlugin as FileIoPluginDirect,
    )

    assert FileIoPluginDirect is FileIoPlugin


# ---------------------------------------------------------------------------
# 10. Reentrancy guard
# ---------------------------------------------------------------------------


# ESCAPE: test_reentrancy_guard_bypasses_when_set
#   CLAIM: When _file_io_bypass is True, intercepted open falls through to real builtins.open.
#   PATH:  _file_io_bypass.set(True) -> intercepted_open checks bypass -> calls original.
#   CHECK: Real builtins.open is called (reading a file that actually exists).
#   MUTATION: Not checking bypass would intercept bigfoot's own I/O and break the framework.
#   ESCAPE: Nothing reasonable -- if bypass fails, the mock queue is checked and
#           UnmockedInteractionError is raised (or wrong data returned).
def test_reentrancy_guard_bypasses_when_set(tmp_path: pathlib.Path) -> None:
    v, p = _make_verifier_with_plugin()
    real_file = tmp_path / "real.txt"
    real_file.write_text("real content")

    with v.sandbox():
        # With bypass set, open should fall through to real builtins.open
        token = _file_io_bypass.set(True)
        try:
            with builtins.open(str(real_file)) as f:
                content = f.read()
        finally:
            _file_io_bypass.reset(token)

    assert content == "real content"


# ESCAPE: test_reentrancy_guard_not_active_without_bypass
#   CLAIM: Without bypass, intercepted open goes through interception logic.
#   PATH:  _file_io_bypass default False -> interceptor proceeds -> checks queue.
#   CHECK: UnmockedInteractionError raised (no mock registered).
#   MUTATION: Always bypassing would never intercept.
#   ESCAPE: Nothing reasonable -- exception raised proves interception is active.
def test_reentrancy_guard_not_active_without_bypass() -> None:
    v, p = _make_verifier_with_plugin()

    with v.sandbox():
        with pytest.raises(UnmockedInteractionError):
            builtins.open("/tmp/no_bypass.txt")


# ESCAPE: test_reentrancy_guard_no_verifier_falls_through
#   CLAIM: When no verifier is active (outside sandbox), intercepted open falls through to real.
#   PATH:  intercepted_open -> get_active_verifier() returns None -> call original.
#   CHECK: Real open works normally outside sandbox.
#   MUTATION: Not checking for verifier would raise an error or return wrong result.
#   ESCAPE: Nothing reasonable -- reading a real file succeeds only if fallthrough works.
def test_reentrancy_guard_no_verifier_falls_through(tmp_path: pathlib.Path) -> None:
    v, p = _make_verifier_with_plugin()
    p.activate()
    try:
        real_file = tmp_path / "outside.txt"
        real_file.write_text("outside content")
        # Outside any sandbox, open should fall through
        with builtins.open(str(real_file)) as f:
            content = f.read()
        assert content == "outside content"
    finally:
        p.deactivate()


# ---------------------------------------------------------------------------
# 11. Not default enabled
# ---------------------------------------------------------------------------


# ESCAPE: test_file_io_not_default_enabled
#   CLAIM: FileIoPlugin is NOT included in default resolve_enabled_plugins({}).
#   PATH:  PLUGIN_REGISTRY has file_io with default_enabled=False.
#          resolve_enabled_plugins({}) filters out default_enabled=False entries.
#   CHECK: "file_io" not in default resolved names.
#   MUTATION: Setting default_enabled=True would include it in defaults.
#   ESCAPE: Nothing reasonable -- exact membership check.
def test_file_io_not_default_enabled() -> None:
    from bigfoot._registry import resolve_enabled_plugins

    result = resolve_enabled_plugins({})
    names = {e.name for e in result}
    assert "file_io" not in names


# ESCAPE: test_file_io_included_when_explicitly_enabled
#   CLAIM: FileIoPlugin IS included when enabled_plugins=["file_io"].
#   PATH:  resolve_enabled_plugins({"enabled_plugins": ["file_io"]}) includes file_io.
#   CHECK: "file_io" in resolved names.
#   MUTATION: Not registering the plugin in PLUGIN_REGISTRY raises BigfootConfigError.
#   ESCAPE: Nothing reasonable -- exact membership check.
def test_file_io_included_when_explicitly_enabled() -> None:
    from bigfoot._registry import resolve_enabled_plugins

    result = resolve_enabled_plugins({"enabled_plugins": ["file_io"]})
    names = {e.name for e in result}
    assert "file_io" in names


# ---------------------------------------------------------------------------
# 12. Format methods with EXACT string equality
# ---------------------------------------------------------------------------


# ESCAPE: test_format_interaction_open
#   CLAIM: format_interaction returns exact string for open interaction.
#   PATH:  format_interaction(interaction) -> formatted string.
#   CHECK: result == exact expected string.
#   MUTATION: Different format string fails exact equality.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_interaction_open() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="file_io:open",
        sequence=0,
        details={"path": os.path.normpath("/tmp/f.txt"), "mode": "r", "encoding": "utf-8"},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == f"[FileIoPlugin] open('{os.path.normpath('/tmp/f.txt')}', mode='r')"


# ESCAPE: test_format_interaction_remove
#   CLAIM: format_interaction returns exact string for remove interaction.
#   PATH:  format_interaction(interaction) -> formatted string.
#   CHECK: result == exact expected string.
#   MUTATION: Different format string fails exact equality.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_interaction_remove() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="file_io:remove",
        sequence=0,
        details={"path": os.path.normpath("/tmp/del.txt")},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == f"[FileIoPlugin] os.remove('{os.path.normpath('/tmp/del.txt')}')"


# ESCAPE: test_format_interaction_rename
#   CLAIM: format_interaction returns exact string for rename interaction.
#   PATH:  format_interaction(interaction) -> formatted string.
#   CHECK: result == exact expected string.
#   MUTATION: Different format string fails exact equality.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_interaction_rename() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="file_io:rename",
        sequence=0,
        details={"src": os.path.normpath("/tmp/old.txt"), "dst": os.path.normpath("/tmp/new.txt")},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == f"[FileIoPlugin] os.rename('{os.path.normpath('/tmp/old.txt')}', '{os.path.normpath('/tmp/new.txt')}')"


# ESCAPE: test_format_interaction_makedirs
#   CLAIM: format_interaction returns exact string for makedirs interaction.
#   PATH:  format_interaction(interaction) -> formatted string.
#   CHECK: result == exact expected string.
#   MUTATION: Different format string fails exact equality.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_interaction_makedirs() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="file_io:makedirs",
        sequence=0,
        details={"path": os.path.normpath("/tmp/newdir"), "exist_ok": True},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == f"[FileIoPlugin] os.makedirs('{os.path.normpath('/tmp/newdir')}', exist_ok=True)"


# ESCAPE: test_format_interaction_copy
#   CLAIM: format_interaction returns exact string for copy interaction.
#   PATH:  format_interaction(interaction) -> formatted string.
#   CHECK: result == exact expected string.
#   MUTATION: Different format string fails exact equality.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_interaction_copy() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="file_io:copy",
        sequence=0,
        details={"src": os.path.normpath("/tmp/s.txt"), "dst": os.path.normpath("/tmp/d.txt")},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == f"[FileIoPlugin] shutil.copy('{os.path.normpath('/tmp/s.txt')}', '{os.path.normpath('/tmp/d.txt')}')"


# ESCAPE: test_format_interaction_rmtree
#   CLAIM: format_interaction returns exact string for rmtree interaction.
#   PATH:  format_interaction(interaction) -> formatted string.
#   CHECK: result == exact expected string.
#   MUTATION: Different format string fails exact equality.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_interaction_rmtree() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="file_io:rmtree",
        sequence=0,
        details={"path": os.path.normpath("/tmp/rmdir")},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == f"[FileIoPlugin] shutil.rmtree('{os.path.normpath('/tmp/rmdir')}')"


# ESCAPE: test_format_mock_hint
#   CLAIM: format_mock_hint returns copy-pasteable code to mock the interaction.
#   PATH:  format_mock_hint(interaction) -> string.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Different format fails the equality check.
def test_format_mock_hint() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="file_io:open",
        sequence=0,
        details={"path": os.path.normpath("/tmp/f.txt"), "mode": "r", "encoding": "utf-8"},
        plugin=p,
    )
    result = p.format_mock_hint(interaction)
    assert result == f"    bigfoot.file_io_mock.mock_operation('open', '{os.path.normpath('/tmp/f.txt')}', returns=...)"


# ESCAPE: test_format_unmocked_hint
#   CLAIM: format_unmocked_hint returns copy-pasteable code for an unmocked call.
#   PATH:  format_unmocked_hint(source_id, args, kwargs) -> string.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Different format fails the equality check.
def test_format_unmocked_hint() -> None:
    v, p = _make_verifier_with_plugin()
    result = p.format_unmocked_hint("file_io:open", (os.path.normpath("/tmp/f.txt"), "r"), {})
    np = os.path.normpath
    assert result == (
        f"open('{np('/tmp/f.txt')}', ...) was called but no mock was registered.\n"
        "Register a mock with:\n"
        f"    bigfoot.file_io_mock.mock_operation('open', '{np('/tmp/f.txt')}', returns=...)"
    )


# ESCAPE: test_format_assert_hint
#   CLAIM: format_assert_hint returns assert helper syntax.
#   PATH:  format_assert_hint(interaction) -> string with assert_open syntax.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Different format fails the equality check.
def test_format_assert_hint() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="file_io:open",
        sequence=0,
        details={"path": os.path.normpath("/tmp/f.txt"), "mode": "r", "encoding": "utf-8"},
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    npath = os.path.normpath("/tmp/f.txt")
    assert result == (
        "    bigfoot.file_io_mock.assert_open(\n"
        f"        path={npath!r},\n"
        "        mode='r',\n"
        "        encoding='utf-8',\n"
        "    )"
    )


# ESCAPE: test_format_assert_hint_remove
#   CLAIM: format_assert_hint for remove returns assert_remove syntax.
#   PATH:  format_assert_hint(interaction) -> string with assert_remove syntax.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Different format fails the equality check.
def test_format_assert_hint_remove() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="file_io:remove",
        sequence=0,
        details={"path": os.path.normpath("/tmp/del.txt")},
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    npath = os.path.normpath("/tmp/del.txt")
    assert result == (
        "    bigfoot.file_io_mock.assert_remove(\n"
        f"        path={npath!r},\n"
        "    )"
    )


# ESCAPE: test_format_unused_mock_hint
#   CLAIM: format_unused_mock_hint returns hint with operation and traceback.
#   PATH:  format_unused_mock_hint(mock_config) -> string.
#   CHECK: result == exact expected prefix + traceback.
#   MUTATION: Wrong prefix fails equality.
#   ESCAPE: Nothing reasonable -- exact string equality on prefix + traceback.
def test_format_unused_mock_hint() -> None:
    v, p = _make_verifier_with_plugin()
    config = FileIoMockConfig(operation="open", path_pattern=os.path.normpath("/tmp/f.txt"), returns="data")
    result = p.format_unused_mock_hint(config)
    np = os.path.normpath
    expected_prefix = (
        f"file_io:open('{np('/tmp/f.txt')}') was mocked (required=True) but never called.\n"
        "Registered at:\n"
    )
    assert result == expected_prefix + config.registration_traceback


# ---------------------------------------------------------------------------
# FIFO ordering
# ---------------------------------------------------------------------------


# ESCAPE: test_fifo_ordering_same_key
#   CLAIM: Multiple mock_operation calls for same key are consumed in FIFO order.
#   PATH:  Two mock_operation("open", "/tmp/f.txt") -> two configs in deque.
#          First open -> popleft -> returns "first".
#          Second open -> popleft -> returns "second".
#   CHECK: first result == "first"; second result == "second".
#   MUTATION: LIFO ordering swaps results; both checks fail.
#   ESCAPE: Nothing reasonable -- exact string equality on distinct values.
def test_fifo_ordering_same_key() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("open", "/tmp/f.txt", returns="first")
    p.mock_operation("open", "/tmp/f.txt", returns="second")

    with v.sandbox():
        f1 = builtins.open("/tmp/f.txt")
        content1 = f1.read()
        f1.close()
        f2 = builtins.open("/tmp/f.txt")
        content2 = f2.read()
        f2.close()

    assert content1 == "first"
    assert content2 == "second"


# ---------------------------------------------------------------------------
# FileIoMockConfig dataclass
# ---------------------------------------------------------------------------


# ESCAPE: test_file_io_mock_config_fields
#   CLAIM: FileIoMockConfig stores all fields correctly with defaults.
#   PATH:  Dataclass construction.
#   CHECK: All fields equal their expected values.
#   MUTATION: Wrong default value fails equality check.
#   ESCAPE: Nothing reasonable -- exact equality on all fields.
def test_file_io_mock_config_fields() -> None:
    config = FileIoMockConfig(
        operation="open",
        path_pattern="/tmp/f.txt",
        returns="data",
        raises=OSError("fail"),
        required=False,
    )
    assert config.operation == "open"
    assert config.path_pattern == "/tmp/f.txt"
    assert config.returns == "data"
    assert config.raises is not None
    assert type(config.raises) is OSError
    assert str(config.raises) == "fail"
    assert config.required is False
    assert "test_file_io_plugin.py" in config.registration_traceback


# ESCAPE: test_file_io_mock_config_defaults
#   CLAIM: FileIoMockConfig defaults: returns=None, raises=None, required=True.
#   PATH:  Dataclass construction with minimal arguments.
#   CHECK: returns is None; raises is None; required is True.
#   MUTATION: Wrong default fails equality check.
#   ESCAPE: Nothing reasonable -- exact equality.
def test_file_io_mock_config_defaults() -> None:
    config = FileIoMockConfig(operation="open", path_pattern="/tmp/f.txt")
    assert config.returns is None
    assert config.raises is None
    assert config.required is True


# ---------------------------------------------------------------------------
# Activation / deactivation
# ---------------------------------------------------------------------------


# ESCAPE: test_activate_installs_patch
#   CLAIM: After activate(), builtins.open is replaced with bigfoot interceptor.
#   PATH:  activate() -> _install_count == 0 -> store original -> install interceptor.
#   CHECK: builtins.open is not the original after activate().
#   MUTATION: Skipping patch installation leaves original in place; identity check fails.
#   ESCAPE: Nothing reasonable -- identity comparison proves replacement.
def test_activate_installs_patch() -> None:
    original = builtins.open
    v, p = _make_verifier_with_plugin()
    p.activate()
    assert builtins.open is not original
    p.deactivate()


# ESCAPE: test_deactivate_restores_patch
#   CLAIM: After activate() then deactivate(), builtins.open is restored.
#   PATH:  deactivate() -> _install_count reaches 0 -> restore original.
#   CHECK: builtins.open is the original after deactivate().
#   MUTATION: Not restoring leaves interceptor in place.
#   ESCAPE: Nothing reasonable -- identity comparison against saved original.
def test_deactivate_restores_patch() -> None:
    original = builtins.open
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.deactivate()
    assert builtins.open is original


# ESCAPE: test_reference_counting_nested
#   CLAIM: Two activate() calls require two deactivate() calls before patch is removed.
#   PATH:  First activate -> _install_count=1; second -> _install_count=2.
#          First deactivate -> _install_count=1 (patch remains).
#          Second deactivate -> _install_count=0 (original restored).
#   CHECK: After first deactivate, open is still patched. After second, it is restored.
#   MUTATION: Restoring on first deactivate fails mid-point check.
#   ESCAPE: Nothing reasonable -- sequential identity checks prove counting.
def test_reference_counting_nested() -> None:
    original = builtins.open
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.activate()
    assert FileIoPlugin._install_count == 2

    p.deactivate()
    assert FileIoPlugin._install_count == 1
    assert builtins.open is not original

    p.deactivate()
    assert FileIoPlugin._install_count == 0
    assert builtins.open is original


# ---------------------------------------------------------------------------
# __init__.py integration
# ---------------------------------------------------------------------------


# ESCAPE: test_file_io_plugin_in_all
#   CLAIM: FileIoPlugin and file_io_mock are exported from bigfoot.__all__.
#   PATH:  bigfoot.__all__ contains "FileIoPlugin" and "file_io_mock".
#   CHECK: Both names in __all__; bigfoot.FileIoPlugin is the real class.
#   MUTATION: Omitting either from __all__ fails membership check.
#   ESCAPE: Nothing reasonable -- exact membership check.
def test_file_io_plugin_in_all() -> None:
    import bigfoot

    assert "FileIoPlugin" in bigfoot.__all__
    assert "file_io_mock" in bigfoot.__all__
    assert bigfoot.FileIoPlugin is FileIoPlugin


# ESCAPE: test_file_io_mock_proxy
#   CLAIM: bigfoot.file_io_mock proxies to FileIoPlugin on the active verifier.
#   PATH:  _FileIoProxy.__getattr__ -> get verifier -> find/create FileIoPlugin.
#   CHECK: Proxy attribute access does not raise when verifier is active.
#   MUTATION: Wrong proxy class or missing registration fails with AttributeError.
#   ESCAPE: Nothing reasonable -- attribute access succeeds or fails.
def test_file_io_mock_proxy(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    # End-to-end: register mock through proxy, trigger it, verify interaction
    bigfoot.file_io_mock.mock_operation("open", "/tmp/proxy-test.txt", returns="proxied")
    with bigfoot.sandbox():
        f = builtins.open("/tmp/proxy-test.txt")
        result = f.read()
    assert result == "proxied"
    bigfoot.file_io_mock.assert_open(path="/tmp/proxy-test.txt", mode="r", encoding="utf-8")


# ---------------------------------------------------------------------------
# matches() method
# ---------------------------------------------------------------------------


# ESCAPE: test_matches_field_comparison
#   CLAIM: matches() does field-by-field comparison; returns True when fields match.
#   PATH:  matches(interaction, expected) -> compare each key.
#   CHECK: Empty expected matches any; non-matching returns False; matching returns True.
#   MUTATION: Always returning True fails the non-matching check.
#   ESCAPE: Nothing reasonable -- exact boolean equality on distinct cases.
def test_matches_field_comparison() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="file_io:open",
        sequence=0,
        details={"path": os.path.normpath("/tmp/f.txt"), "mode": "r", "encoding": "utf-8"},
        plugin=p,
    )
    assert p.matches(interaction, {}) is True
    assert p.matches(interaction, {"path": os.path.normpath("/tmp/f.txt")}) is True
    assert p.matches(interaction, {"path": os.path.normpath("/tmp/wrong.txt")}) is False
    assert p.matches(interaction, {"foo": "bar"}) is False


# ---------------------------------------------------------------------------
# Registry entry
# ---------------------------------------------------------------------------


# ESCAPE: test_file_io_in_registry
#   CLAIM: "file_io" is a valid plugin name in the registry.
#   PATH:  PLUGIN_REGISTRY contains a PluginEntry with name="file_io".
#   CHECK: "file_io" in VALID_PLUGIN_NAMES.
#   MUTATION: Not adding to registry fails membership check.
#   ESCAPE: Nothing reasonable -- exact membership check.
def test_file_io_in_registry() -> None:
    from bigfoot._registry import PLUGIN_REGISTRY, VALID_PLUGIN_NAMES

    assert "file_io" in VALID_PLUGIN_NAMES
    entry = next(e for e in PLUGIN_REGISTRY if e.name == "file_io")
    assert entry.import_path == "bigfoot.plugins.file_io_plugin"
    assert entry.class_name == "FileIoPlugin"
    assert entry.availability_check == "always"
    assert entry.default_enabled is False


# ---------------------------------------------------------------------------
# Interactions NOT auto-asserted
# ---------------------------------------------------------------------------


# ESCAPE: test_interactions_not_auto_asserted
#   CLAIM: File I/O interactions are NOT auto-asserted; they land unasserted on timeline.
#   PATH:  interceptor records interaction without calling mark_asserted.
#   CHECK: all_unasserted() returns the interaction.
#   MUTATION: Auto-asserting would return empty list from all_unasserted().
#   ESCAPE: Nothing reasonable -- exact length and source_id check.
def test_interactions_not_auto_asserted(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    p = FileIoPlugin(bigfoot_verifier)
    p.mock_operation("open", "/tmp/f.txt", returns="data")

    with bigfoot.sandbox():
        f = builtins.open("/tmp/f.txt")
        f.close()

    timeline = bigfoot_verifier._timeline
    interactions = timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].source_id == "file_io:open"
    # Assert it so verify_all() at teardown succeeds
    p.assert_open(path="/tmp/f.txt", mode="r", encoding="utf-8")
