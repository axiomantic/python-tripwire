"""Unit tests for NativePlugin (ctypes/cffi native function interception)."""

from __future__ import annotations

import ctypes
import ctypes.util

import pytest

from bigfoot._context import _current_test_verifier
from bigfoot._errors import (
    InteractionMismatchError,
    MissingAssertionFieldsError,
    UnmockedInteractionError,
)
from bigfoot._timeline import Interaction
from bigfoot._verifier import StrictVerifier
from bigfoot.plugins.native_plugin import (
    CdllProxy,
    NativeMockConfig,
    NativePlugin,
    _FuncProxy,
    _NativeSentinel,
    _serialize_arg,
    _serialize_struct,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_verifier_with_plugin() -> tuple[StrictVerifier, NativePlugin]:
    """Return (verifier, plugin) with NativePlugin registered."""
    v = StrictVerifier()
    for p in v._plugins:
        if isinstance(p, NativePlugin):
            return v, p
    p = NativePlugin(v)
    return v, p


def _reset_plugin_count() -> None:
    """Force-reset the class-level install count to 0 and restore patches if leaked."""
    with NativePlugin._install_lock:
        NativePlugin._install_count = 0
        # Use the plugin's own _restore_patches() to avoid duplicating restoration logic.
        NativePlugin.__new__(NativePlugin).restore_patches()


@pytest.fixture(autouse=True)
def clean_plugin_counts() -> None:
    """Ensure plugin install count starts and ends at 0 for every test."""
    _reset_plugin_count()
    yield
    _reset_plugin_count()


# ---------------------------------------------------------------------------
# 1. Basic interception (CDLL load + function call through proxy)
# ---------------------------------------------------------------------------


# ESCAPE: test_basic_interception_cdll_load_and_call
#   CLAIM: Loading a CDLL and calling a function through the proxy returns the mocked value.
#   PATH:  mock_call -> enqueue -> activate patches CDLL.__init__ -> CDLL("libm") returns
#          CdllProxy -> proxy.sqrt(42) pops from queue -> returns mocked value.
#   CHECK: result == 6.48 (exact equality on mocked return value).
#   MUTATION: Returning wrong value from queue fails equality. Not patching CDLL.__init__
#             means real library loads (no CdllProxy), AttributeError on _FuncProxy.
#   ESCAPE: Nothing reasonable -- exact equality on return value.
def test_basic_interception_cdll_load_and_call() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_call("libm", "sqrt", returns=6.48)

    with v.sandbox():
        lib = ctypes.CDLL("libm")
        assert isinstance(lib, CdllProxy)
        result = lib.sqrt(42)

    assert result == 6.48


# ---------------------------------------------------------------------------
# 2. Full assertion certainty (assertable_fields)
# ---------------------------------------------------------------------------


# ESCAPE: test_assertable_fields_returns_correct_frozenset
#   CLAIM: assertable_fields() returns frozenset({"library", "function", "args"}).
#   PATH:  assertable_fields(interaction) -> frozenset(interaction.details.keys()).
#   CHECK: result == frozenset({"library", "function", "args"}).
#   MUTATION: Returning frozenset() skips completeness enforcement entirely.
#   ESCAPE: Nothing reasonable -- exact frozenset equality.
def test_assertable_fields_returns_correct_frozenset() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="native:libm:sqrt",
        sequence=0,
        details={"library": "libm", "function": "sqrt", "args": (42,)},
        plugin=p,
    )
    assert p.assertable_fields(interaction) == frozenset({"library", "function", "args"})


# ---------------------------------------------------------------------------
# 3. Unmocked interaction error
# ---------------------------------------------------------------------------


# ESCAPE: test_unmocked_interaction_error
#   CLAIM: Calling a native function with no mock registered raises UnmockedInteractionError.
#   PATH:  proxy.sqrt(42) -> _FuncProxy.__call__ -> no queue entry -> raises.
#   CHECK: UnmockedInteractionError raised; exc.source_id == "native:libm:sqrt".
#   MUTATION: Silently returning None instead of raising; no exception raised.
#   ESCAPE: Raising with wrong source_id fails equality check.
def test_unmocked_interaction_error() -> None:
    v, p = _make_verifier_with_plugin()

    with v.sandbox():
        lib = ctypes.CDLL("libm")
        with pytest.raises(UnmockedInteractionError) as exc_info:
            lib.sqrt(42)

    assert exc_info.value.source_id == "native:libm:sqrt"


# ---------------------------------------------------------------------------
# 4. Unused mock warning
# ---------------------------------------------------------------------------


# ESCAPE: test_unused_mock_returns_unconsumed_required
#   CLAIM: get_unused_mocks() returns mocks with required=True that were never consumed.
#   PATH:  mock_call x2 -> only first consumed -> get_unused_mocks scans queues.
#   CHECK: len(unused) == 1; unused[0].library == "libm"; unused[0].function == "cos".
#   MUTATION: Returning all configs (including consumed) fails length. Not filtering
#             by required returns optional mocks too.
#   ESCAPE: Nothing reasonable -- exact equality on remaining mock fields.
def test_unused_mock_returns_unconsumed_required() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_call("libm", "sqrt", returns=6.48)
    p.mock_call("libm", "cos", returns=0.5)

    with v.sandbox():
        lib = ctypes.CDLL("libm")
        lib.sqrt(42)

    unused = p.get_unused_mocks()
    assert len(unused) == 1
    assert unused[0].library == "libm"
    assert unused[0].function == "cos"
    assert unused[0].returns == 0.5
    assert unused[0].raises is None
    assert unused[0].required is True


# ESCAPE: test_unused_mock_excludes_required_false
#   CLAIM: get_unused_mocks() excludes configs with required=False.
#   PATH:  mock_call with required=False -> get_unused_mocks filters them out.
#   CHECK: get_unused_mocks() == [].
#   MUTATION: Not filtering by required=False returns the config.
#   ESCAPE: Nothing reasonable -- exact equality with empty list.
def test_unused_mock_excludes_required_false() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_call("libm", "sqrt", returns=6.48, required=False)

    unused = p.get_unused_mocks()
    assert unused == []


# ---------------------------------------------------------------------------
# 5. Missing fields error
# ---------------------------------------------------------------------------


# ESCAPE: test_missing_fields_error
#   CLAIM: assert_interaction without all required fields raises MissingAssertionFieldsError.
#   PATH:  assert_interaction with only library+function (no args) ->
#          verifier checks assertable_fields -> missing "args" -> raises.
#   CHECK: MissingAssertionFieldsError raised; missing_fields == frozenset({"args"}).
#   MUTATION: Returning frozenset() from assertable_fields skips the check entirely.
#   ESCAPE: Nothing reasonable -- exact frozenset equality on missing_fields.
def test_missing_fields_error(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.native_mock.mock_call("libm", "sqrt", returns=6.48)
    with bigfoot.sandbox():
        lib = ctypes.CDLL("libm")
        lib.sqrt(42)

    # Use assert_interaction directly without 'args' to trigger missing fields
    sentinel = _NativeSentinel("native:libm:sqrt")
    with pytest.raises(MissingAssertionFieldsError) as exc_info:
        bigfoot_verifier.assert_interaction(
            sentinel,
            library="libm",
            function="sqrt",
        )

    assert exc_info.value.missing_fields == frozenset({"args"})

    # Assert correctly for teardown
    bigfoot.native_mock.assert_call("libm", "sqrt", args=(42,))


# ---------------------------------------------------------------------------
# 6. Typed assertion helpers (positive and NEGATIVE tests)
# ---------------------------------------------------------------------------


# ESCAPE: test_assert_call_typed_helper_positive
#   CLAIM: assert_call() asserts the next native interaction successfully.
#   PATH:  mock_call -> sandbox -> call -> assert_call matches -> no error.
#   CHECK: No exception raised (test passes cleanly).
#   MUTATION: Wrong source_id generation in assert_call would cause InteractionMismatchError.
#   ESCAPE: Nothing reasonable -- test either passes or raises.
def test_assert_call_typed_helper_positive(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.native_mock.mock_call("libm", "sqrt", returns=6.48)
    with bigfoot.sandbox():
        lib = ctypes.CDLL("libm")
        lib.sqrt(42)

    bigfoot.native_mock.assert_call("libm", "sqrt", args=(42,))


# ESCAPE: test_assert_call_typed_helper_negative_wrong_args
#   CLAIM: assert_call() with wrong args raises InteractionMismatchError.
#   PATH:  assert_call with wrong args -> verifier.assert_interaction -> mismatch.
#   CHECK: InteractionMismatchError raised.
#   MUTATION: Skipping field comparison would pass with wrong args.
#   ESCAPE: Nothing reasonable -- exact exception type check.
def test_assert_call_typed_helper_negative_wrong_args(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.native_mock.mock_call("libm", "sqrt", returns=6.48)
    with bigfoot.sandbox():
        lib = ctypes.CDLL("libm")
        lib.sqrt(42)

    with pytest.raises(InteractionMismatchError):
        bigfoot.native_mock.assert_call("libm", "sqrt", args=(999,))

    # Assert correctly for teardown
    bigfoot.native_mock.assert_call("libm", "sqrt", args=(42,))


# ESCAPE: test_assert_call_typed_helper_negative_wrong_function
#   CLAIM: assert_call() with wrong function name raises InteractionMismatchError.
#   PATH:  assert_call with wrong function -> source_id mismatch.
#   CHECK: InteractionMismatchError raised.
#   MUTATION: Not comparing source_id would pass with wrong function.
#   ESCAPE: Nothing reasonable -- exact exception type check.
def test_assert_call_typed_helper_negative_wrong_function(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.native_mock.mock_call("libm", "sqrt", returns=6.48)
    with bigfoot.sandbox():
        lib = ctypes.CDLL("libm")
        lib.sqrt(42)

    with pytest.raises(InteractionMismatchError):
        bigfoot.native_mock.assert_call("libm", "cos", args=(42,))

    # Assert correctly for teardown
    bigfoot.native_mock.assert_call("libm", "sqrt", args=(42,))


# ESCAPE: test_assert_call_typed_helper_negative_wrong_library
#   CLAIM: assert_call() with wrong library raises InteractionMismatchError.
#   PATH:  assert_call with wrong library -> field mismatch.
#   CHECK: InteractionMismatchError raised.
#   MUTATION: Not comparing library field would pass with wrong library.
#   ESCAPE: Nothing reasonable -- exact exception type check.
def test_assert_call_typed_helper_negative_wrong_library(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.native_mock.mock_call("libm", "sqrt", returns=6.48)
    with bigfoot.sandbox():
        lib = ctypes.CDLL("libm")
        lib.sqrt(42)

    with pytest.raises(InteractionMismatchError):
        bigfoot.native_mock.assert_call("libz", "sqrt", args=(42,))

    # Assert correctly for teardown
    bigfoot.native_mock.assert_call("libm", "sqrt", args=(42,))


# ---------------------------------------------------------------------------
# 7. Conflict detection
# ---------------------------------------------------------------------------


# ESCAPE: test_activate_detects_conflict
#   CLAIM: If ctypes.CDLL.__init__ is already monkey-patched by another library,
#          activate() raises ConflictError.
#   PATH:  activate() -> check if CDLL.__init__ is not the expected original -> raise.
#   CHECK: ConflictError raised.
#   MUTATION: Skipping conflict check allows double-patching silently.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_activate_detects_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    from bigfoot._errors import ConflictError

    v, p = _make_verifier_with_plugin()

    # Simulate another library patching CDLL.__init__
    def fake_init(self, name, *args: object, **kwargs: object) -> None:
        pass

    monkeypatch.setattr(ctypes.CDLL, "__init__", fake_init)

    # First activate sets up the original
    # Since the method is already patched by fake_init, activate should detect conflict
    with pytest.raises(ConflictError) as exc_info:
        p.activate()

    assert exc_info.value.target == "ctypes.CDLL.__init__"


# ---------------------------------------------------------------------------
# 8. Exception propagation
# ---------------------------------------------------------------------------


# ESCAPE: test_exception_propagation
#   CLAIM: mock_call with raises= propagates the exception when the function is called.
#   PATH:  mock_call(raises=OSError("lib not found")) -> proxy.func() -> raises OSError.
#   CHECK: OSError raised; str(exc) == "lib not found".
#   MUTATION: Not checking raises in the interceptor returns the value instead.
#   ESCAPE: Raising a different exception type fails the type check.
def test_exception_propagation() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_call("libm", "sqrt", returns=None, raises=OSError("lib not found"))

    with v.sandbox():
        lib = ctypes.CDLL("libm")
        with pytest.raises(OSError) as exc_info:
            lib.sqrt(42)

    assert str(exc_info.value) == "lib not found"


# ---------------------------------------------------------------------------
# 9. Graceful degradation (cffi not installed)
# ---------------------------------------------------------------------------


# ESCAPE: test_graceful_degradation_cffi_not_installed
#   CLAIM: When cffi is not installed, NativePlugin still activates successfully
#          for ctypes interception.
#   PATH:  activate() -> patches ctypes.CDLL.__init__ -> skips cffi patching.
#   CHECK: Plugin activates without error; ctypes interception works.
#   MUTATION: Requiring cffi unconditionally would raise ImportError.
#   ESCAPE: Nothing reasonable -- test passes or raises ImportError.
def test_graceful_degradation_cffi_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    import bigfoot.plugins.native_plugin as np_mod

    monkeypatch.setattr(np_mod, "_CFFI_AVAILABLE", False)

    v, p = _make_verifier_with_plugin()
    p.mock_call("libm", "sqrt", returns=6.48)

    # activate should succeed without cffi
    p.activate()
    try:
        # Verify ctypes still works
        with v.sandbox():
            lib = ctypes.CDLL("libm")
            result = lib.sqrt(42)
        assert result == 6.48
    finally:
        p.deactivate()


# ---------------------------------------------------------------------------
# 10. CdllProxy attribute access creates _FuncProxy
# ---------------------------------------------------------------------------


# ESCAPE: test_cdll_proxy_attribute_access_creates_func_proxy
#   CLAIM: Accessing an attribute on CdllProxy creates a _FuncProxy instance.
#   PATH:  CdllProxy.__getattr__("sqrt") -> _FuncProxy(plugin, "libm", "sqrt").
#   CHECK: type(func) is _FuncProxy; func._library_name == "libm"; func._function_name == "sqrt".
#   MUTATION: Returning a raw string or None fails the type check.
#   ESCAPE: Nothing reasonable -- exact type and attribute checks.
def test_cdll_proxy_attribute_access_creates_func_proxy() -> None:
    v, p = _make_verifier_with_plugin()
    proxy = CdllProxy("libm", p)
    func = proxy.sqrt
    assert type(func) is _FuncProxy
    assert func._library_name == "libm"
    assert func._function_name == "sqrt"


# ---------------------------------------------------------------------------
# 11. Closed library raises on subsequent calls
# ---------------------------------------------------------------------------


# ESCAPE: test_closed_library_raises_on_access
#   CLAIM: After close(), accessing attributes on CdllProxy raises OSError.
#   PATH:  proxy.close() -> _closed = True -> __getattr__ checks _closed -> raises OSError.
#   CHECK: OSError raised.
#   MUTATION: Not checking _closed in __getattr__ allows access after close.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_closed_library_raises_on_access() -> None:
    v, p = _make_verifier_with_plugin()
    proxy = CdllProxy("libm", p)
    proxy.close()

    with pytest.raises(OSError):
        _ = proxy.sqrt


# ---------------------------------------------------------------------------
# 12. Struct serialization (ctypes.Structure -> dict)
# ---------------------------------------------------------------------------


# ESCAPE: test_struct_serialization
#   CLAIM: _serialize_struct converts ctypes.Structure to dict of field_name -> value.
#   PATH:  _serialize_struct(struct_instance) -> iterate _fields_ -> getattr.
#   CHECK: result == {"x": 10, "y": 20}.
#   MUTATION: Returning empty dict fails equality. Wrong field name fails.
#   ESCAPE: Nothing reasonable -- exact dict equality.
def test_struct_serialization() -> None:
    class Point(ctypes.Structure):
        _fields_ = [("x", ctypes.c_int), ("y", ctypes.c_int)]

    pt = Point(x=10, y=20)
    result = _serialize_struct(pt)
    assert result == {"x": 10, "y": 20}


# ESCAPE: test_serialize_arg_struct
#   CLAIM: _serialize_arg on a ctypes.Structure delegates to _serialize_struct.
#   PATH:  _serialize_arg(struct_instance) -> isinstance check -> _serialize_struct.
#   CHECK: result == {"x": 10, "y": 20}.
#   MUTATION: Not detecting Structure type passes it through unchanged.
#   ESCAPE: Nothing reasonable -- exact dict equality.
def test_serialize_arg_struct() -> None:
    class Point(ctypes.Structure):
        _fields_ = [("x", ctypes.c_int), ("y", ctypes.c_int)]

    pt = Point(x=10, y=20)
    result = _serialize_arg(pt)
    assert result == {"x": 10, "y": 20}


# ---------------------------------------------------------------------------
# 13. Callback pass-through (CFUNCTYPE recorded as "<callback>")
# ---------------------------------------------------------------------------


# ESCAPE: test_callback_serialized_as_callback_string
#   CLAIM: _serialize_arg on a CFUNCTYPE callback returns "<callback>".
#   PATH:  _serialize_arg(callback) -> callable check + _type_ attr -> "<callback>".
#   CHECK: result == "<callback>".
#   MUTATION: Returning the raw callback object fails equality.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_callback_serialized_as_callback_string() -> None:
    callback_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int)

    @callback_type
    def my_callback(x):
        return x * 2

    result = _serialize_arg(my_callback)
    assert result == "<callback>"


# ESCAPE: test_serialize_arg_simple_cdata
#   CLAIM: _serialize_arg on c_int(42) returns 42.
#   PATH:  _serialize_arg(c_int(42)) -> isinstance SimpleCData -> value attr.
#   CHECK: result == 42.
#   MUTATION: Returning the ctypes object itself fails equality.
#   ESCAPE: Nothing reasonable -- exact int equality.
def test_serialize_arg_simple_cdata() -> None:
    result = _serialize_arg(ctypes.c_int(42))
    assert result == 42


# ESCAPE: test_serialize_arg_c_float
#   CLAIM: _serialize_arg on c_float(1.5) returns 1.5.
#   PATH:  _serialize_arg(c_float(1.5)) -> isinstance SimpleCData -> value attr.
#   CHECK: result == 1.5 (within float tolerance, but c_float(1.5).value is exactly
#          representable, so == works).
#   MUTATION: Not extracting .value returns the ctypes wrapper object.
#   ESCAPE: Nothing reasonable -- exact float equality for 1.5.
def test_serialize_arg_c_float() -> None:
    result = _serialize_arg(ctypes.c_float(1.5))
    # c_float(1.5).value is a Python float; 1.5 is exactly representable in IEEE 754
    assert result == 1.5


# ESCAPE: test_serialize_arg_plain_python_passthrough
#   CLAIM: _serialize_arg on plain Python objects returns them unchanged.
#   PATH:  _serialize_arg(42) -> no isinstance matches -> return value.
#   CHECK: result == 42.
#   MUTATION: Converting plain types to something else fails equality.
#   ESCAPE: Nothing reasonable -- exact equality.
def test_serialize_arg_plain_python_passthrough() -> None:
    assert _serialize_arg(42) == 42
    assert _serialize_arg("hello") == "hello"
    assert _serialize_arg([1, 2, 3]) == [1, 2, 3]


# ---------------------------------------------------------------------------
# 14. cffi ABI mode (FFI.dlopen returns proxy)
# ---------------------------------------------------------------------------


# ESCAPE: test_cffi_abi_mode_dlopen_returns_proxy
#   CLAIM: When cffi is installed, FFI.dlopen returns CffiProxy after activate().
#   PATH:  activate() patches FFI.dlopen -> dlopen("libm") returns CffiProxy.
#   CHECK: isinstance(lib, CffiProxy).
#   MUTATION: Not patching dlopen returns real library object, not CffiProxy.
#   ESCAPE: Nothing reasonable -- exact type check.
def test_cffi_abi_mode_dlopen_returns_proxy() -> None:
    import cffi  # noqa: I001

    from bigfoot.plugins.native_plugin import CffiProxy

    v, p = _make_verifier_with_plugin()
    p.mock_call("libm", "sqrt", returns=6.48)

    p.activate()
    try:
        with v.sandbox():
            ffi = cffi.FFI()
            lib = ffi.dlopen("libm")
            assert isinstance(lib, CffiProxy)
            result = lib.sqrt(42)
        assert result == 6.48
    finally:
        p.deactivate()


# ---------------------------------------------------------------------------
# 15. Not default enabled
# ---------------------------------------------------------------------------


# ESCAPE: test_not_default_enabled
#   CLAIM: NativePlugin has default_enabled=False in the registry.
#   PATH:  PLUGIN_REGISTRY entry for "native" has default_enabled=False.
#   CHECK: entry.default_enabled is False.
#   MUTATION: Setting True would include it in default set; is False check fails.
#   ESCAPE: Nothing reasonable -- exact boolean equality.
def test_not_default_enabled() -> None:
    from bigfoot._registry import PLUGIN_REGISTRY

    native_entry = None
    for entry in PLUGIN_REGISTRY:
        if entry.name == "native":
            native_entry = entry
            break

    assert native_entry is not None
    assert native_entry.default_enabled is False
    assert native_entry.import_path == "bigfoot.plugins.native_plugin"
    assert native_entry.class_name == "NativePlugin"
    assert native_entry.availability_check == "always"


# ---------------------------------------------------------------------------
# Flow tests: assert_interaction after sandbox
# ---------------------------------------------------------------------------


# ESCAPE: test_flow_assert_interaction_records_details
#   CLAIM: After sandbox, recorded interaction has correct details for assert_interaction.
#   PATH:  mock_call -> sandbox -> call -> record interaction -> assert_interaction checks details.
#   CHECK: Interaction details match exact expected values.
#   MUTATION: Recording wrong library/function/args fails assertion.
#   ESCAPE: Nothing reasonable -- exact field equality via assert_interaction.
def test_flow_assert_interaction_records_details(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.native_mock.mock_call("libm", "sqrt", returns=6.48)
    with bigfoot.sandbox():
        lib = ctypes.CDLL("libm")
        lib.sqrt(42)

    # Check the recorded interaction details
    timeline = bigfoot_verifier._timeline
    interactions = timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].source_id == "native:libm:sqrt"
    assert interactions[0].details == {
        "library": "libm",
        "function": "sqrt",
        "args": (42,),
    }

    # Assert to satisfy teardown
    bigfoot.native_mock.assert_call("libm", "sqrt", args=(42,))


# ESCAPE: test_flow_interactions_not_auto_asserted
#   CLAIM: Native interactions are NOT auto-asserted -- they land unasserted on timeline.
#   PATH:  mock_call -> sandbox -> call -> record -> interaction._asserted == False.
#   CHECK: timeline.all_unasserted() returns 1 interaction.
#   MUTATION: Auto-asserting in record() would return 0 unasserted.
#   ESCAPE: Nothing reasonable -- exact count check.
def test_flow_interactions_not_auto_asserted(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.native_mock.mock_call("libm", "sqrt", returns=6.48)
    with bigfoot.sandbox():
        lib = ctypes.CDLL("libm")
        lib.sqrt(42)

    interactions = bigfoot_verifier._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].source_id == "native:libm:sqrt"

    # Assert to satisfy teardown
    bigfoot.native_mock.assert_call("libm", "sqrt", args=(42,))


# ---------------------------------------------------------------------------
# format_* methods (exact string equality)
# ---------------------------------------------------------------------------


# ESCAPE: test_format_interaction
#   CLAIM: format_interaction returns "[NativePlugin] libm.sqrt(42)".
#   PATH:  format_interaction(interaction) -> format string.
#   CHECK: result == "[NativePlugin] libm.sqrt(42)".
#   MUTATION: Wrong format string fails exact equality.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_interaction() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="native:libm:sqrt",
        sequence=0,
        details={"library": "libm", "function": "sqrt", "args": (42,)},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[NativePlugin] libm.sqrt(42)"


# ESCAPE: test_format_interaction_multiple_args
#   CLAIM: format_interaction with multiple args shows all args.
#   PATH:  format_interaction(interaction) -> format string.
#   CHECK: result == "[NativePlugin] libm.pow(2.0, 3.0)".
#   MUTATION: Showing only first arg fails exact equality.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_interaction_multiple_args() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="native:libm:pow",
        sequence=0,
        details={"library": "libm", "function": "pow", "args": (2.0, 3.0)},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[NativePlugin] libm.pow(2.0, 3.0)"


# ESCAPE: test_format_interaction_no_args
#   CLAIM: format_interaction with no args shows empty parens.
#   PATH:  format_interaction(interaction) -> format string.
#   CHECK: result == "[NativePlugin] libm.rand()".
#   MUTATION: Showing "()" differently fails exact equality.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_interaction_no_args() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="native:libm:rand",
        sequence=0,
        details={"library": "libm", "function": "rand", "args": ()},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[NativePlugin] libm.rand()"


# ESCAPE: test_format_mock_hint
#   CLAIM: format_mock_hint returns copy-pasteable mock_call code.
#   PATH:  format_mock_hint(interaction) -> format string.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails exact equality.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_mock_hint() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="native:libm:sqrt",
        sequence=0,
        details={"library": "libm", "function": "sqrt", "args": (42,)},
        plugin=p,
    )
    result = p.format_mock_hint(interaction)
    assert result == "    bigfoot.native_mock.mock_call('libm', 'sqrt', returns=...)"


# ESCAPE: test_format_unmocked_hint
#   CLAIM: format_unmocked_hint returns hint for unmocked native call.
#   PATH:  format_unmocked_hint(source_id, args, kwargs) -> format string.
#   CHECK: result == exact expected multiline string.
#   MUTATION: Wrong format fails exact equality.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_unmocked_hint() -> None:
    v, p = _make_verifier_with_plugin()
    result = p.format_unmocked_hint("native:libm:sqrt", (42,), {})
    assert result == (
        "libm.sqrt(...) was called but no mock was registered.\n"
        "Register a mock with:\n"
        "    bigfoot.native_mock.mock_call('libm', 'sqrt', returns=...)"
    )


# ESCAPE: test_format_assert_hint
#   CLAIM: format_assert_hint returns copy-pasteable assert_call code.
#   PATH:  format_assert_hint(interaction) -> format string.
#   CHECK: result == exact expected multiline string.
#   MUTATION: Wrong format fails exact equality.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_assert_hint() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="native:libm:sqrt",
        sequence=0,
        details={"library": "libm", "function": "sqrt", "args": (42,)},
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == (
        "    bigfoot.native_mock.assert_call(\n"
        "        library='libm',\n"
        "        function='sqrt',\n"
        "        args=(42,),\n"
        "    )"
    )


# ESCAPE: test_format_unused_mock_hint
#   CLAIM: format_unused_mock_hint returns hint for unused mock with traceback.
#   PATH:  format_unused_mock_hint(config) -> format string.
#   CHECK: result == expected prefix + config.registration_traceback.
#   MUTATION: Wrong prefix fails startswith. Missing traceback fails the concat check.
#   ESCAPE: Nothing reasonable -- exact string equality with dynamic traceback.
def test_format_unused_mock_hint() -> None:
    v, p = _make_verifier_with_plugin()
    config = NativeMockConfig(library="libm", function="sqrt", returns=6.48)
    result = p.format_unused_mock_hint(config)
    expected_prefix = (
        "libm.sqrt(...) was mocked (required=True) but never called.\nRegistered at:\n"
    )
    assert result == expected_prefix + config.registration_traceback


# ---------------------------------------------------------------------------
# NativeMockConfig dataclass
# ---------------------------------------------------------------------------


# ESCAPE: test_native_mock_config_fields
#   CLAIM: NativeMockConfig stores library, function, returns, raises, required correctly.
#   PATH:  Dataclass construction.
#   CHECK: All fields equal their expected values.
#   MUTATION: Wrong field name or default value fails equality check.
#   ESCAPE: Nothing reasonable -- exact equality on all fields.
def test_native_mock_config_fields() -> None:
    config = NativeMockConfig(
        library="libm", function="sqrt", returns=6.48, raises=OSError("err"), required=False
    )
    assert config.library == "libm"
    assert config.function == "sqrt"
    assert config.returns == 6.48
    assert isinstance(config.raises, OSError)
    assert str(config.raises) == "err"
    assert config.required is False
    lines = config.registration_traceback.splitlines()
    assert lines[0].startswith("  File ")


# ESCAPE: test_native_mock_config_defaults
#   CLAIM: NativeMockConfig defaults: raises=None, required=True.
#   PATH:  Dataclass construction with minimal arguments.
#   CHECK: raises is None; required is True.
#   MUTATION: Wrong default for required fails equality check.
#   ESCAPE: Nothing reasonable -- exact equality.
def test_native_mock_config_defaults() -> None:
    config = NativeMockConfig(library="libm", function="sqrt", returns=6.48)
    assert config.raises is None
    assert config.required is True


# ---------------------------------------------------------------------------
# Sentinel
# ---------------------------------------------------------------------------


# ESCAPE: test_native_sentinel_source_id
#   CLAIM: _NativeSentinel stores source_id correctly.
#   PATH:  _NativeSentinel("native:libm:sqrt").source_id.
#   CHECK: sentinel.source_id == "native:libm:sqrt".
#   MUTATION: Wrong source_id fails equality.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_native_sentinel_source_id() -> None:
    sentinel = _NativeSentinel("native:libm:sqrt")
    assert sentinel.source_id == "native:libm:sqrt"


# ---------------------------------------------------------------------------
# matches() method
# ---------------------------------------------------------------------------


# ESCAPE: test_matches_field_comparison
#   CLAIM: matches() does field-by-field comparison.
#   PATH:  matches(interaction, expected) -> compare each expected key.
#   CHECK: Empty expected matches; matching fields match; non-matching fails.
#   MUTATION: Always returning True fails the non-matching check.
#   ESCAPE: Nothing reasonable -- exact boolean equality on distinct cases.
def test_matches_field_comparison() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="native:libm:sqrt",
        sequence=0,
        details={"library": "libm", "function": "sqrt", "args": (42,)},
        plugin=p,
    )
    assert p.matches(interaction, {}) is True
    assert p.matches(interaction, {"library": "libm"}) is True
    assert p.matches(interaction, {"library": "libm", "function": "sqrt"}) is True
    assert p.matches(interaction, {"library": "libz"}) is False
    assert p.matches(interaction, {"nonexistent": "field"}) is False


# ---------------------------------------------------------------------------
# FIFO queue ordering
# ---------------------------------------------------------------------------


# ESCAPE: test_fifo_ordering_same_function
#   CLAIM: Two mock_call for same function are consumed in FIFO order.
#   PATH:  mock_call x2 -> first call pops first, second pops second.
#   CHECK: first_result == 1.0; second_result == 2.0.
#   MUTATION: LIFO ordering swaps the values; both checks fail.
#   ESCAPE: Nothing reasonable -- exact equality on distinct values.
def test_fifo_ordering_same_function() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_call("libm", "sqrt", returns=1.0)
    p.mock_call("libm", "sqrt", returns=2.0)

    with v.sandbox():
        lib = ctypes.CDLL("libm")
        first = lib.sqrt(1)
        second = lib.sqrt(4)

    assert first == 1.0
    assert second == 2.0


# ---------------------------------------------------------------------------
# Activation / deactivation reference counting
# ---------------------------------------------------------------------------


# ESCAPE: test_activate_deactivate_reference_counting
#   CLAIM: Two activate() calls require two deactivate() to fully restore.
#   PATH:  activate x2 -> count=2; deactivate x1 -> count=1 (still patched);
#          deactivate x2 -> count=0 (restored).
#   CHECK: After first deactivate, count==1. After second, count==0.
#   MUTATION: Restoring on first deactivate fails mid-point check.
#   ESCAPE: Nothing reasonable -- exact count equality.
def test_activate_deactivate_reference_counting() -> None:
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.activate()
    assert NativePlugin._install_count == 2

    p.deactivate()
    assert NativePlugin._install_count == 1

    p.deactivate()
    assert NativePlugin._install_count == 0


# ---------------------------------------------------------------------------
# __init__.py integration
# ---------------------------------------------------------------------------


# ESCAPE: test_native_plugin_in_all
#   CLAIM: NativePlugin and native_mock are exported from bigfoot.__all__.
#   PATH:  bigfoot.__all__ includes "NativePlugin" and "native_mock".
#   CHECK: Both names present in __all__.
#   MUTATION: Omitting either from __all__ fails membership.
#   ESCAPE: Nothing reasonable -- exact membership check.
def test_native_plugin_in_all() -> None:
    import bigfoot

    assert "NativePlugin" in bigfoot.__all__
    assert "native_mock" in bigfoot.__all__


# ESCAPE: test_native_plugin_importable_from_bigfoot
#   CLAIM: NativePlugin is importable from bigfoot and is the correct class.
#   PATH:  bigfoot.NativePlugin is bigfoot.plugins.native_plugin.NativePlugin.
#   CHECK: Identity check passes.
#   MUTATION: Wrong class or missing import fails identity.
#   ESCAPE: Nothing reasonable -- identity check.
def test_native_plugin_importable_from_bigfoot() -> None:
    import bigfoot
    from bigfoot.plugins.native_plugin import NativePlugin as _NativePlugin

    assert bigfoot.NativePlugin is _NativePlugin


# ESCAPE: test_native_mock_proxy_type
#   CLAIM: bigfoot.native_mock is a _NativeProxy instance.
#   PATH:  bigfoot.native_mock is a module-level proxy.
#   CHECK: type(bigfoot.native_mock).__name__ == "_NativeProxy".
#   MUTATION: Wrong proxy type fails name check.
#   ESCAPE: Nothing reasonable -- exact string equality on type name.
def test_native_mock_proxy_type() -> None:
    import bigfoot

    assert type(bigfoot.native_mock).__name__ == "_NativeProxy"


# ESCAPE: test_native_mock_proxy_raises_outside_context
#   CLAIM: Accessing bigfoot.native_mock outside test context raises NoActiveVerifierError.
#   PATH:  _NativeProxy.__getattr__ -> _get_test_verifier_or_raise -> raises.
#   CHECK: NoActiveVerifierError raised.
#   MUTATION: Not raising allows silent use outside tests.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_native_mock_proxy_raises_outside_context() -> None:
    import bigfoot
    from bigfoot._errors import NoActiveVerifierError

    token = _current_test_verifier.set(None)
    try:
        with pytest.raises(NoActiveVerifierError):
            _ = bigfoot.native_mock.mock_call
    finally:
        _current_test_verifier.reset(token)


# ---------------------------------------------------------------------------
# Serialization of args in recorded interaction
# ---------------------------------------------------------------------------


# ESCAPE: test_args_serialized_in_interaction
#   CLAIM: ctypes args are serialized (c_int(42) -> 42) in recorded interaction details.
#   PATH:  _FuncProxy.__call__ -> _serialize_arg on each arg -> record.
#   CHECK: interaction details args == (42,) not (c_int(42),).
#   MUTATION: Not serializing leaves ctypes objects in details; equality fails.
#   ESCAPE: Nothing reasonable -- exact tuple equality.
def test_args_serialized_in_interaction() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_call("libm", "sqrt", returns=6.48)

    with v.sandbox():
        lib = ctypes.CDLL("libm")
        lib.sqrt(ctypes.c_int(42))

    timeline = v._timeline
    interactions = timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details["args"] == (42,)


# ESCAPE: test_callback_arg_serialized_in_interaction
#   CLAIM: CFUNCTYPE callback args are serialized as "<callback>" in interaction details.
#   PATH:  _FuncProxy.__call__ -> _serialize_arg on callback -> "<callback>" -> record.
#   CHECK: interaction details args contains "<callback>".
#   MUTATION: Not detecting callbacks leaves function object; equality fails.
#   ESCAPE: Nothing reasonable -- exact tuple equality.
def test_callback_arg_serialized_in_interaction() -> None:
    v, p = _make_verifier_with_plugin()
    callback_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int)

    @callback_type
    def my_cb(x):
        return x * 2

    p.mock_call("mylib", "register_callback", returns=0)

    with v.sandbox():
        lib = ctypes.CDLL("mylib")
        lib.register_callback(my_cb)

    timeline = v._timeline
    interactions = timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details["args"] == ("<callback>",)


# ---------------------------------------------------------------------------
# Fix 4: _serialize_arg Pointer path
# ---------------------------------------------------------------------------


# ESCAPE: test_serialize_arg_pointer_with_value
#   CLAIM: _serialize_arg on a ctypes pointer returns contents.
#   PATH:  _serialize_arg(pointer) -> isinstance _Pointer -> value.contents.
#   CHECK: result.value == 42 (the pointed-to c_int).
#   MUTATION: Returning raw pointer object fails type check.
#   ESCAPE: Nothing reasonable -- exact equality on contents value.
def test_serialize_arg_pointer_with_value() -> None:
    val = ctypes.c_int(42)
    ptr = ctypes.pointer(val)
    result = _serialize_arg(ptr)
    assert result.value == 42


# ESCAPE: test_serialize_arg_null_pointer
#   CLAIM: _serialize_arg on a null pointer returns None.
#   PATH:  _serialize_arg(null_ptr) -> isinstance _Pointer -> ValueError caught -> None.
#   CHECK: result is None.
#   MUTATION: Not catching ValueError raises instead of returning None.
#   ESCAPE: Nothing reasonable -- exact None check via is.
def test_serialize_arg_null_pointer() -> None:
    null_ptr_type = ctypes.POINTER(ctypes.c_int)
    null = null_ptr_type()
    result = _serialize_arg(null)
    assert result is None


# ---------------------------------------------------------------------------
# Fix 5: CffiProxy.close()
# ---------------------------------------------------------------------------


# ESCAPE: test_cffi_proxy_close_blocks_access
#   CLAIM: CffiProxy.close() sets _closed=True; subsequent attr access raises OSError.
#   PATH:  proxy.close() -> _closed=True -> __getattr__ checks _closed -> raises OSError.
#   CHECK: OSError raised after close.
#   MUTATION: Not checking _closed in __getattr__ allows access after close.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_cffi_proxy_close_blocks_access() -> None:
    from bigfoot.plugins.native_plugin import CffiProxy

    v, p = _make_verifier_with_plugin()
    proxy = CffiProxy("libm", p)

    # Before close, attribute access works (returns _FuncProxy)
    func = proxy.sqrt
    assert type(func) is _FuncProxy

    proxy.close()
    assert object.__getattribute__(proxy, "_closed") is True

    with pytest.raises(OSError):
        _ = proxy.sqrt


# ---------------------------------------------------------------------------
# Fix 6: matches() exception catch branch
# ---------------------------------------------------------------------------


# ESCAPE: test_matches_returns_false_on_comparison_exception
#   CLAIM: matches() returns False when __eq__ raises an exception.
#   PATH:  matches(interaction, expected) -> expected_val != actual_val raises -> except -> False.
#   CHECK: result is False.
#   MUTATION: Not catching the exception propagates it as unhandled.
#   ESCAPE: Nothing reasonable -- exact boolean equality.
def test_matches_returns_false_on_comparison_exception() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="native:libm:sqrt",
        sequence=0,
        details={"library": "libm", "function": "sqrt", "args": (42,)},
        plugin=p,
    )

    class ExplodingEq:
        def __eq__(self, other: object) -> bool:
            raise TypeError("comparison not supported")

    result = p.matches(interaction, {"library": ExplodingEq()})
    assert result is False


# ---------------------------------------------------------------------------
# Fix 7: _get_native_plugin() RuntimeError
# ---------------------------------------------------------------------------


# ESCAPE: test_get_native_plugin_raises_without_native_plugin
#   CLAIM: _get_native_plugin() raises RuntimeError when no NativePlugin is registered.
#   PATH:  _get_native_plugin() -> iterate verifier._plugins -> no NativePlugin -> raise.
#   CHECK: RuntimeError raised with expected message.
#   MUTATION: Not raising allows silent failure.
#   ESCAPE: Nothing reasonable -- exact exception type and message.
def test_get_native_plugin_raises_without_native_plugin() -> None:
    from bigfoot._context import _active_verifier
    from bigfoot.plugins.native_plugin import _get_native_plugin

    v = StrictVerifier()
    # Remove any NativePlugin from the verifier's plugins
    v._plugins = [p for p in v._plugins if not isinstance(p, NativePlugin)]

    token = _active_verifier.set(v)
    try:
        with pytest.raises(RuntimeError) as exc_info:
            _get_native_plugin()
        assert str(exc_info.value) == (
            "BUG: bigfoot NativePlugin interceptor is active but no "
            "NativePlugin is registered on the current verifier."
        )
    finally:
        _active_verifier.reset(token)


# ---------------------------------------------------------------------------
# Fix 8: Missing assertions in test_unused_mock_returns_unconsumed_required
# ---------------------------------------------------------------------------
# (Assertions for raises and required added inline above in the existing test.
#  Adding a dedicated test here to cover the gap explicitly.)


# ESCAPE: test_unused_mock_has_expected_config_fields
#   CLAIM: Unconsumed mock config has raises=None and required=True.
#   PATH:  mock_call with defaults -> not consumed -> get_unused_mocks -> check fields.
#   CHECK: config.raises is None; config.required is True.
#   MUTATION: Wrong default values fail equality checks.
#   ESCAPE: Nothing reasonable -- exact equality.
def test_unused_mock_has_expected_config_fields() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_call("libm", "cos", returns=0.5)

    unused = p.get_unused_mocks()
    assert len(unused) == 1
    config = unused[0]
    assert config.library == "libm"
    assert config.function == "cos"
    assert config.returns == 0.5
    assert config.raises is None
    assert config.required is True
