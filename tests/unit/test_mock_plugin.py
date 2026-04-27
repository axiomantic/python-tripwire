"""Tests for Task 7: _mock_plugin.py — MockPlugin, MockProxy, MethodProxy, MockConfig."""

from typing import Any

import pytest

from tripwire._context import _active_verifier
from tripwire._errors import SandboxNotActiveError, UnmockedInteractionError
from tripwire._mock_plugin import (
    _ABSENT,
    MethodProxy,
    MockConfig,
    MockPlugin,
    MockProxy,
)
from tripwire._timeline import Interaction
from tripwire._verifier import StrictVerifier

# ---------------------------------------------------------------------------
# _ABSENT sentinel
# ---------------------------------------------------------------------------


def test_absent_sentinel_is_unique_object() -> None:
    """_ABSENT is a unique object sentinel distinct from None, True, False, and _SENTINEL."""
    # ESCAPE: test_absent_sentinel_is_unique_object
    #   CLAIM: _ABSENT is a module-level sentinel that is distinct from common values.
    #   PATH: Module-level `_ABSENT = object()` creates a unique identity.
    #   CHECK: `_ABSENT is not None`, `_ABSENT is not True`, `_ABSENT is not False`,
    #          `isinstance(_ABSENT, object)`, and identity comparison with _SENTINEL.
    #   MUTATION: Setting `_ABSENT = None` would fail `_ABSENT is not None`.
    #            Setting `_ABSENT = _SENTINEL` would fail the identity check vs _SENTINEL.
    #   ESCAPE: Nothing reasonable -- exact identity checks against all common confusions.
    #   IMPACT: assert_call() could not distinguish "parameter not passed" from None.
    from tripwire._mock_plugin import _SENTINEL

    assert _ABSENT is not None
    assert _ABSENT is not True
    assert _ABSENT is not False
    assert isinstance(_ABSENT, object)
    assert _ABSENT is not _SENTINEL


# ---------------------------------------------------------------------------
# MockPlugin registration
# ---------------------------------------------------------------------------


def test_mock_plugin_registers_on_verifier() -> None:
    """MockPlugin registers itself with the verifier on construction."""
    # ESCAPE:
    # CLAIM: MockPlugin.__init__ registers the plugin with the verifier.
    # PATH: MockPlugin.__init__ -> BasePlugin.__init__ -> verifier._register_plugin(self).
    # CHECK: 'p in v._plugins' confirms the exact plugin instance appears in the list.
    # MUTATION: Removing _register_plugin call in BasePlugin.__init__ leaves _plugins empty.
    # ESCAPE: A plugin that registers a dummy sentinel instead of self would fail 'p in v._plugins'.
    # IMPACT: Teardown verify_all() would skip this plugin's unused mock checks.
    v = StrictVerifier()
    p = MockPlugin(v)
    assert p in v._plugins


def test_mock_plugin_duplicate_is_idempotent() -> None:
    """Registering a second MockPlugin on the same verifier silently skips it."""
    # ESCAPE:
    # CLAIM: StrictVerifier._register_plugin silently skips duplicate plugin types.
    # PATH: MockPlugin.__init__ -> BasePlugin.__init__ -> verifier._register_plugin -> type match -> return.
    # CHECK: Plugin count unchanged after attempting duplicate registration.
    # MUTATION: Allowing duplicate registration would produce two MockPlugins.
    # ESCAPE: Nothing reasonable -- exact count comparison.
    # IMPACT: Multiple MockPlugins would interfere with each other's proxy tracking.
    v = StrictVerifier()
    MockPlugin(v)
    mock_count = sum(1 for p in v._plugins if isinstance(p, MockPlugin))
    assert mock_count == 1
    MockPlugin(v)  # Should silently skip
    mock_count_after = sum(1 for p in v._plugins if isinstance(p, MockPlugin))
    assert mock_count_after == 1


# ---------------------------------------------------------------------------
# MockProxy creation and caching
# ---------------------------------------------------------------------------


def test_get_or_create_proxy_returns_mock_proxy_instance() -> None:
    """get_or_create_proxy returns a MockProxy instance."""
    # ESCAPE:
    # CLAIM: get_or_create_proxy returns a MockProxy.
    # PATH: MockPlugin.get_or_create_proxy creates MockProxy if not present.
    # CHECK: isinstance(proxy, MockProxy) validates the type exactly.
    # MUTATION: Returning a raw object instead of MockProxy would fail isinstance check.
    # ESCAPE: A subclass of MockProxy would pass isinstance; that's acceptable.
    # IMPACT: proxy.charge would not return a MethodProxy; attribute access would break.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    assert isinstance(proxy, MockProxy)


def test_get_or_create_proxy_returns_same_instance() -> None:
    """get_or_create_proxy returns the same MockProxy for the same name."""
    # ESCAPE:
    # CLAIM: Calling get_or_create_proxy twice with the same name returns the same object.
    # PATH: MockPlugin._proxies dict caches proxies by name.
    # CHECK: 'proxy1 is proxy2' is identity equality, not just value equality.
    # MUTATION: Creating a new MockProxy each call would fail the 'is' check.
    # ESCAPE: Nothing reasonable passes 'is' without being the same object.
    # IMPACT: Method configurations (.returns(), .raises()) would be lost between calls.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy1 = p.get_or_create_proxy("Service")
    proxy2 = p.get_or_create_proxy("Service")
    assert proxy1 is proxy2


def test_get_or_create_proxy_different_names_returns_different_instances() -> None:
    """get_or_create_proxy returns distinct instances for different names."""
    # ESCAPE:
    # CLAIM: Different names yield different MockProxy instances.
    # PATH: MockPlugin._proxies dict keys by name; distinct names produce distinct objects.
    # CHECK: 'proxy1 is not proxy2' via identity.
    # MUTATION: Returning the same singleton for all names would fail this check.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Two services would share method configurations, causing cross-contamination.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy1 = p.get_or_create_proxy("ServiceA")
    proxy2 = p.get_or_create_proxy("ServiceB")
    assert proxy1 is not proxy2


# ---------------------------------------------------------------------------
# MockProxy attribute access
# ---------------------------------------------------------------------------


def test_mock_proxy_attribute_access_returns_method_proxy() -> None:
    """MockProxy.__getattr__ returns a MethodProxy instance."""
    # ESCAPE:
    # CLAIM: Accessing any attribute on MockProxy returns a MethodProxy.
    # PATH: MockProxy.__getattr__ creates and returns MethodProxy for the method name.
    # CHECK: isinstance(method, MethodProxy) validates the exact type.
    # MUTATION: Returning a raw callable would fail isinstance check.
    # ESCAPE: A MethodProxy subclass would pass; acceptable.
    # IMPACT: .returns(), .raises(), .calls() would not be available on the result.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    method = proxy.charge
    assert isinstance(method, MethodProxy)


def test_mock_proxy_attribute_access_cached() -> None:
    """MockProxy returns the same MethodProxy instance for repeated attribute access."""
    # ESCAPE:
    # CLAIM: proxy.charge returns the same MethodProxy object each time.
    # PATH: MockProxy._methods dict caches MethodProxy by method_name.
    # CHECK: 'proxy.charge is proxy.charge' via identity.
    # MUTATION: Creating a new MethodProxy each attribute access would fail 'is' check.
    # ESCAPE: Nothing reasonable passes 'is' without being the same object.
    # IMPACT: .returns() configured on one access would be lost on the next access.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    assert proxy.charge is proxy.charge


# ---------------------------------------------------------------------------
# MethodProxy source_id
# ---------------------------------------------------------------------------


def test_method_proxy_source_id() -> None:
    """MethodProxy.source_id is 'mock:<ProxyName>.<MethodName>'."""
    # ESCAPE:
    # CLAIM: source_id follows the exact pattern 'mock:PaymentService.charge'.
    # PATH: MethodProxy.__init__ sets self.source_id = f"mock:{mock_name}.{method_name}".
    # CHECK: Exact string equality.
    # MUTATION: Wrong separator, missing 'mock:' prefix, or wrong case would fail.
    # ESCAPE: Nothing reasonable produces this exact string incorrectly.
    # IMPACT: Timeline entries would have wrong source_id; assertions would never match.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("PaymentService")
    assert proxy.charge.source_id == "mock:PaymentService.charge"


# ---------------------------------------------------------------------------
# MethodProxy configuration queue
# ---------------------------------------------------------------------------


def test_method_proxy_returns_configures_queue() -> None:
    """Calling .returns() twice adds two entries to the config queue."""
    # ESCAPE:
    # CLAIM: Each .returns() call appends exactly one MockConfig to _config_queue.
    # PATH: MethodProxy.returns() appends MockConfig to self._config_queue.
    # CHECK: len(_config_queue) == 2 with exact count.
    # MUTATION: Appending twice per call would give len == 4; no-op would give len == 0.
    # ESCAPE: A queue with 2 wrong entries would pass length check but fail FIFO test.
    # IMPACT: FIFO consumption would behave incorrectly; too many or too few side effects.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    proxy.charge.returns("ok").returns("ok2")
    assert len(proxy.charge._config_queue) == 2


def test_method_proxy_returns_method_is_chainable() -> None:
    """MethodProxy.returns() returns the same MethodProxy for chaining."""
    # ESCAPE:
    # CLAIM: .returns() returns self (the MethodProxy).
    # PATH: MethodProxy.returns() ends with 'return self'.
    # CHECK: result is proxy.charge (exact identity).
    # MUTATION: Returning None or a new MethodProxy would fail identity check.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Chaining .returns("a").returns("b") would fail with AttributeError on None.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    method = proxy.charge
    result = method.returns("x")
    assert result is method


def test_method_proxy_raises_method_is_chainable() -> None:
    """MethodProxy.raises() returns the same MethodProxy for chaining."""
    # ESCAPE:
    # CLAIM: .raises() returns self.
    # PATH: MethodProxy.raises() ends with 'return self'.
    # CHECK: result is proxy.charge.
    # MUTATION: Returning None would fail.
    # ESCAPE: Nothing reasonable.
    # IMPACT: .raises(exc).required(False) would fail with AttributeError.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    method = proxy.charge
    result = method.raises(ValueError("err"))
    assert result is method


def test_method_proxy_calls_method_is_chainable() -> None:
    """MethodProxy.calls() returns the same MethodProxy for chaining."""
    # ESCAPE:
    # CLAIM: .calls() returns self.
    # PATH: MethodProxy.calls() ends with 'return self'.
    # CHECK: result is the same MethodProxy.
    # MUTATION: Returning None would fail.
    # ESCAPE: Nothing reasonable.
    # IMPACT: .calls(fn).required(False) would fail.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    method = proxy.charge
    result = method.calls(lambda: None)
    assert result is method


def test_method_proxy_required_method_is_chainable() -> None:
    """MethodProxy.required() returns the same MethodProxy for chaining."""
    # ESCAPE:
    # CLAIM: .required() returns self.
    # PATH: MethodProxy.required() ends with 'return self'.
    # CHECK: result is method.
    # MUTATION: Returning None would fail.
    # ESCAPE: Nothing reasonable.
    # IMPACT: proxy.charge.required(False).returns("x") would fail.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    method = proxy.charge
    result = method.required(False)
    assert result is method


# ---------------------------------------------------------------------------
# MethodProxy __call__ — sandbox not active
# ---------------------------------------------------------------------------


def test_method_proxy_raises_sandbox_not_active_outside_sandbox() -> None:
    """Calling a MethodProxy outside a sandbox raises SandboxNotActiveError."""
    # ESCAPE:
    # CLAIM: Without an active sandbox (ContextVar unset), SandboxNotActiveError is raised.
    # PATH: MethodProxy.__call__ -> get_verifier_or_raise(source_id) -> raises SandboxNotActiveError.
    # CHECK: pytest.raises(SandboxNotActiveError) catches the exact type.
    # MUTATION: Skipping get_verifier_or_raise call means it proceeds to UnmockedInteractionError.
    # ESCAPE: A different exception type would not be caught by SandboxNotActiveError.
    # IMPACT: Mocks could silently fire without a sandbox, ignoring interaction recording.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    proxy.charge.returns("ok")

    with pytest.raises(SandboxNotActiveError):
        proxy.charge()


def test_method_proxy_sandbox_not_active_error_has_source_id() -> None:
    """SandboxNotActiveError raised outside sandbox contains the correct source_id."""
    # ESCAPE:
    # CLAIM: The SandboxNotActiveError carries the method's source_id.
    # PATH: get_verifier_or_raise(source_id) -> SandboxNotActiveError(source_id=source_id).
    # CHECK: exc.source_id == "mock:Service.charge" (exact equality).
    # MUTATION: Passing wrong source_id (e.g., "") to SandboxNotActiveError would fail.
    # ESCAPE: Nothing reasonable passes exact equality with wrong value.
    # IMPACT: Error messages would show wrong source, confusing debugging.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    proxy.charge.returns("ok")

    with pytest.raises(SandboxNotActiveError) as exc_info:
        proxy.charge()

    assert exc_info.value.source_id == "mock:Service.charge"


# ---------------------------------------------------------------------------
# MethodProxy __call__ — returns side effect
# ---------------------------------------------------------------------------


def test_method_proxy_call_returns_configured_value() -> None:
    """Calling a mock with .returns('success') returns 'success'."""
    # ESCAPE:
    # CLAIM: .returns("success") causes the mock call to return "success".
    # PATH: MethodProxy.__call__ -> pops MockConfig -> _ReturnValue -> returns value.
    # CHECK: result == "success" (exact equality).
    # MUTATION: Returning None or a different value would fail.
    # ESCAPE: A broken impl that always returns the first value but pops nothing
    #         would still pass this test, but fail the FIFO test below.
    # IMPACT: Mock calls would return wrong values to production code.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    proxy.charge.returns("success")

    token = _active_verifier.set(v)
    try:
        result = proxy.charge("arg1")
    finally:
        _active_verifier.reset(token)

    assert result == "success"


def test_method_proxy_call_returns_none_value() -> None:
    """MethodProxy.returns(None) causes the mock to return None explicitly."""
    # ESCAPE:
    # CLAIM: .returns(None) is valid and returns None (not treated as 'no config').
    # PATH: MethodProxy.returns(None) appends MockConfig(_ReturnValue(None)).
    # CHECK: result is None (exact identity for None).
    # MUTATION: Treating None as "no config" would raise UnmockedInteractionError.
    # ESCAPE: Nothing reasonable fails 'is None' for an explicit None return.
    # IMPACT: Mocks returning None would incorrectly raise UnmockedInteractionError.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    proxy.charge.returns(None)

    token = _active_verifier.set(v)
    try:
        result = proxy.charge()
    finally:
        _active_verifier.reset(token)

    assert result is None


# ---------------------------------------------------------------------------
# MethodProxy __call__ — raises side effect
# ---------------------------------------------------------------------------


def test_method_proxy_call_raises_configured_exception() -> None:
    """Calling a mock with .raises(ValueError(...)) raises that exception."""
    # ESCAPE:
    # CLAIM: .raises(ValueError("payment failed")) causes the mock call to raise that exception.
    # PATH: MethodProxy.__call__ -> pops MockConfig -> _RaiseException -> raise exc.
    # CHECK: pytest.raises(ValueError) and str(exc_info.value) == "payment failed".
    # MUTATION: Returning the exception instead of raising would fail pytest.raises.
    # ESCAPE: Raising a different exception type would not be caught by pytest.raises(ValueError).
    # IMPACT: Tests expecting exceptions from mocks would silently receive return values.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    proxy.charge.raises(ValueError("payment failed"))

    token = _active_verifier.set(v)
    try:
        with pytest.raises(ValueError) as exc_info:
            proxy.charge()
    finally:
        _active_verifier.reset(token)

    assert str(exc_info.value) == "payment failed"


def test_method_proxy_raises_exception_class() -> None:
    """MethodProxy.raises() works with an exception class (type) as well as instance."""
    # ESCAPE:
    # CLAIM: .raises(ValueError) (a class, not instance) causes ValueError to be raised.
    # PATH: MethodProxy.__call__ -> raise exc (where exc is the class).
    # CHECK: pytest.raises(ValueError) catches it.
    # MUTATION: Only handling instances and not types would fail.
    # ESCAPE: If both class and instance are treated identically this passes.
    #         The task brief says BaseException | type[BaseException] for the parameter.
    # IMPACT: raise SomeExceptionClass works the same as raise SomeExceptionClass().
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    proxy.charge.raises(ValueError)

    token = _active_verifier.set(v)
    try:
        with pytest.raises(ValueError):
            proxy.charge()
    finally:
        _active_verifier.reset(token)


# ---------------------------------------------------------------------------
# MethodProxy __call__ — calls side effect
# ---------------------------------------------------------------------------


def test_method_proxy_calls_delegates_to_fn() -> None:
    """Calling a mock with .calls(fn) invokes fn with the same args and returns its result."""
    # ESCAPE:
    # CLAIM: .calls(lambda x: x * 2) causes proxy.charge(5) to return 10.
    # PATH: MethodProxy.__call__ -> pops MockConfig -> _CallFn -> fn(*args, **kwargs).
    # CHECK: result == 10 (exact equality).
    # MUTATION: Calling fn() with no args would raise TypeError (missing positional arg).
    # ESCAPE: A fn that ignores its args and always returns 10 would pass; acceptable for
    #         this test since we verify args passing in the kwarg test below.
    # IMPACT: Mock callables that need call args would receive wrong arguments.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    proxy.charge.calls(lambda x: x * 2)

    token = _active_verifier.set(v)
    try:
        result = proxy.charge(5)
    finally:
        _active_verifier.reset(token)

    assert result == 10


def test_method_proxy_calls_passes_kwargs() -> None:
    """MethodProxy.calls() passes keyword arguments to the callable."""
    # ESCAPE:
    # CLAIM: kwargs are forwarded to the callable.
    # PATH: MethodProxy.__call__ -> fn(*args, **kwargs).
    # CHECK: captured_kwargs == {"amount": 42} (exact dict equality).
    # MUTATION: Passing only *args would leave kwargs empty; captured dict would be {}.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Mock callables that depend on kwargs would receive empty kwargs.
    captured_kwargs: dict[str, Any] = {}

    def capture_fn(**kw: Any) -> str:
        captured_kwargs.update(kw)
        return "captured"

    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    proxy.charge.calls(capture_fn)

    token = _active_verifier.set(v)
    try:
        proxy.charge(amount=42)
    finally:
        _active_verifier.reset(token)

    assert captured_kwargs == {"amount": 42}


# ---------------------------------------------------------------------------
# MethodProxy __call__ — interaction recording
# ---------------------------------------------------------------------------


def test_method_proxy_call_records_interaction() -> None:
    """Calling a mock records an Interaction on the verifier's timeline."""
    # ESCAPE:
    # CLAIM: Calling proxy.charge() appends exactly one Interaction to the timeline.
    # PATH: MethodProxy.__call__ -> plugin.record(interaction) -> timeline.append(interaction).
    # CHECK: len(interactions) == 1 and interactions[0].source_id == "mock:Service.charge".
    # MUTATION: Forgetting to call record() would leave timeline empty; len check fails.
    # ESCAPE: A broken impl that records a different source_id fails the source_id check.
    # IMPACT: Interactions would be invisible to assert_interaction(); assertions silently pass.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    proxy.charge.returns("ok")

    token = _active_verifier.set(v)
    try:
        proxy.charge()
    finally:
        _active_verifier.reset(token)

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].source_id == "mock:Service.charge"


def test_method_proxy_raises_records_before_raising() -> None:
    """Raising mocks record the interaction before raising the exception."""
    # ESCAPE:
    # CLAIM: Even when .raises() is configured, the interaction is recorded first.
    # PATH: MethodProxy.__call__ -> record(interaction) -> raise exc.
    # CHECK: After pytest.raises catches the exception, timeline has 1 interaction.
    # MUTATION: Recording after raising (wrong order) would still pass this test, but
    #           a raises() that forgets to record entirely would fail it.
    # ESCAPE: If record() is skipped, len(interactions) == 0 fails.
    # IMPACT: Raised-exception mocks would be invisible to assert_interaction().
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    proxy.charge.raises(ValueError("err"))

    token = _active_verifier.set(v)
    try:
        with pytest.raises(ValueError):
            proxy.charge()
    finally:
        _active_verifier.reset(token)

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].source_id == "mock:Service.charge"


def test_method_proxy_recorded_interaction_has_correct_plugin() -> None:
    """The recorded Interaction has the MockPlugin instance as its plugin."""
    # ESCAPE:
    # CLAIM: interaction.plugin is the MockPlugin instance.
    # PATH: MethodProxy.__call__ creates Interaction(plugin=self._plugin).
    # CHECK: interactions[0].plugin is p (exact identity).
    # MUTATION: Using a different plugin reference would fail identity check.
    # ESCAPE: Nothing reasonable.
    # IMPACT: format_interaction(), format_mock_hint() etc. would dispatch to wrong plugin.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    proxy.charge.returns("ok")

    token = _active_verifier.set(v)
    try:
        proxy.charge()
    finally:
        _active_verifier.reset(token)

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].plugin is p


# ---------------------------------------------------------------------------
# MethodProxy __call__ — no config raises UnmockedInteractionError
# ---------------------------------------------------------------------------


def test_method_proxy_call_raises_when_no_config() -> None:
    """Calling a mock with no side effects raises UnmockedInteractionError."""
    # ESCAPE:
    # CLAIM: Without any .returns()/.raises()/.calls() config, calling the mock raises.
    # PATH: MethodProxy.__call__ -> no config in queue -> raise UnmockedInteractionError.
    # CHECK: pytest.raises(UnmockedInteractionError) catches it.
    # MUTATION: Returning None instead of raising would fail pytest.raises.
    # ESCAPE: Raising a different error type would not be caught by UnmockedInteractionError.
    # IMPACT: Unconfigured mocks would silently return None instead of alerting the developer.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")

    token = _active_verifier.set(v)
    try:
        with pytest.raises(UnmockedInteractionError):
            proxy.charge()
    finally:
        _active_verifier.reset(token)


def test_method_proxy_unmocked_error_has_source_id() -> None:
    """UnmockedInteractionError raised when no config has the correct source_id."""
    # ESCAPE:
    # CLAIM: exc.source_id == "mock:Service.charge".
    # PATH: UnmockedInteractionError(source_id=self.source_id, ...).
    # CHECK: Exact equality on source_id.
    # MUTATION: Using wrong source_id string would fail.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Error messages would name the wrong source, confusing debugging.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")

    token = _active_verifier.set(v)
    try:
        with pytest.raises(UnmockedInteractionError) as exc_info:
            proxy.charge()
    finally:
        _active_verifier.reset(token)

    assert exc_info.value.source_id == "mock:Service.charge"


# ---------------------------------------------------------------------------
# MethodProxy __call__ — FIFO queue consumption
# ---------------------------------------------------------------------------


def test_method_proxy_queue_is_fifo() -> None:
    """Side effects are consumed in FIFO order; exhaustion raises UnmockedInteractionError."""
    # ESCAPE:
    # CLAIM: .returns("first").returns("second") makes the first call return "first",
    #        the second return "second", and the third raise UnmockedInteractionError.
    # PATH: MethodProxy.__call__ -> deque.popleft() each call -> empty -> raises.
    # CHECK: Three separate assertions, one per call.
    # MUTATION: LIFO (stack) order would return "second" then "first"; first assert fails.
    #           Infinite repeat of last value would never exhaust; UnmockedInteractionError never raised.
    # ESCAPE: Nothing reasonable produces "first", "second", UnmockedInteractionError in that order incorrectly.
    # IMPACT: Sequential mocks would return in wrong order; exhaustion would silently return last value.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    proxy.charge.returns("first").returns("second")

    token = _active_verifier.set(v)
    try:
        assert proxy.charge() == "first"
        assert proxy.charge() == "second"
        with pytest.raises(UnmockedInteractionError):
            proxy.charge()
    finally:
        _active_verifier.reset(token)


def test_method_proxy_mixed_fifo_order() -> None:
    """Mixed side effect types (.returns, .raises, .calls) are consumed in FIFO order."""
    # ESCAPE:
    # CLAIM: Queue order is preserved across different side effect types.
    # PATH: Each configuration method appends to the same deque; popleft is FIFO.
    # CHECK: First call returns value, second raises, third calls fn.
    # MUTATION: Wrong ordering of side effect dispatch logic could skip or reorder.
    # ESCAPE: All three distinct outcomes in sequence are verified; reordering changes at least one.
    # IMPACT: Complex mock sequences with mixed effects would behave unpredictably.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    proxy.charge.returns("first_return")
    proxy.charge.raises(RuntimeError("boom"))
    proxy.charge.calls(lambda: "from_fn")

    token = _active_verifier.set(v)
    try:
        assert proxy.charge() == "first_return"
        with pytest.raises(RuntimeError) as exc_info:
            proxy.charge()
        assert str(exc_info.value) == "boom"
        assert proxy.charge() == "from_fn"
    finally:
        _active_verifier.reset(token)


# ---------------------------------------------------------------------------
# get_unused_mocks
# ---------------------------------------------------------------------------


def test_get_unused_mocks_returns_unconsumed_required() -> None:
    """get_unused_mocks returns MockConfig items that are required and not yet consumed."""
    # ESCAPE:
    # CLAIM: A configured mock that was never called appears in get_unused_mocks().
    # PATH: MockPlugin.get_unused_mocks -> iterates _config_queue for required items.
    # CHECK: len(unused) == 1 AND unused[0].required is True (both conditions).
    # MUTATION: Returning empty list always would fail len check.
    #           Returning item with required=False would fail the required check.
    # ESCAPE: Nothing reasonable fails both checks simultaneously.
    # IMPACT: Unused required mocks would silently pass teardown verification.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    proxy.charge.returns("ok")
    unused = p.get_unused_mocks()
    assert len(unused) == 1
    assert unused[0].required is True


def test_get_unused_mocks_excludes_not_required() -> None:
    """get_unused_mocks excludes MockConfig items with required=False."""
    # ESCAPE:
    # CLAIM: .required(False) before .returns() marks the config as not required.
    # PATH: MethodProxy.required(False) sets sticky flag; next .returns() captures it.
    # CHECK: len(unused) == 0 (exact count).
    # MUTATION: Ignoring required=False and always including would give len == 1.
    # ESCAPE: Nothing reasonable returns empty list if required=False items are included.
    # IMPACT: Tests with optional mocks would spuriously fail at teardown.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    proxy.charge.required(False).returns("ok")
    unused = p.get_unused_mocks()
    assert len(unused) == 0


def test_get_unused_mocks_excludes_consumed() -> None:
    """get_unused_mocks excludes configs that have been consumed by a call."""
    # ESCAPE:
    # CLAIM: After a mock is called once, it is removed from the unused list.
    # PATH: MethodProxy.__call__ -> popleft() removes config from queue -> get_unused_mocks sees empty queue.
    # CHECK: len(unused) == 0.
    # MUTATION: Not popping from queue means config remains; len == 1.
    # ESCAPE: Nothing reasonable leaves len == 0 if popleft is broken.
    # IMPACT: Consumed mocks would appear as unused, causing spurious UnusedMocksError.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    proxy.charge.returns("ok")

    token = _active_verifier.set(v)
    try:
        proxy.charge()
    finally:
        _active_verifier.reset(token)

    unused = p.get_unused_mocks()
    assert len(unused) == 0


def test_get_unused_mocks_returns_empty_when_no_config() -> None:
    """get_unused_mocks returns empty list when no mocks are configured."""
    # ESCAPE:
    # CLAIM: No configuration = no unused mocks.
    # PATH: No proxies or empty _config_queue.
    # CHECK: unused == [] (exact equality including empty).
    # MUTATION: Returning a non-empty list when nothing configured would fail.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Spurious UnusedMocksError at teardown for tests with no mocks.
    v = StrictVerifier()
    p = MockPlugin(v)
    unused = p.get_unused_mocks()
    assert unused == []


def test_get_unused_mocks_returns_mock_config_instances() -> None:
    """get_unused_mocks returns MockConfig instances."""
    # ESCAPE:
    # CLAIM: Items in the returned list are MockConfig instances.
    # PATH: get_unused_mocks() builds list from _config_queue; each item is MockConfig.
    # CHECK: isinstance(unused[0], MockConfig).
    # MUTATION: Returning a dict or string instead of MockConfig would fail isinstance.
    # ESCAPE: Nothing reasonable.
    # IMPACT: format_unused_mock_hint(mock_config) would fail attribute access.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    proxy.charge.returns("ok")
    unused = p.get_unused_mocks()
    assert len(unused) == 1
    assert isinstance(unused[0], MockConfig)


# ---------------------------------------------------------------------------
# MockPlugin.activate / deactivate (reference counting)
# ---------------------------------------------------------------------------


def test_mock_plugin_activate_increments_count() -> None:
    """activate() increments the class-level _install_count."""
    # ESCAPE:
    # CLAIM: activate() increments MockPlugin._install_count by 1.
    # PATH: MockPlugin.activate() -> MockPlugin._install_count += 1.
    # CHECK: count after == initial + 1 (relative comparison).
    # MUTATION: Not incrementing, or incrementing by 2, would fail.
    # ESCAPE: A broken impl that sets count=999 would make initial+1 fail.
    # IMPACT: Reference counting broken; double-install or double-uninstall possible.
    v = StrictVerifier()
    p = MockPlugin(v)
    initial = MockPlugin._install_count
    p.activate()
    assert MockPlugin._install_count == initial + 1
    p.deactivate()
    assert MockPlugin._install_count == initial


def test_mock_plugin_deactivate_decrements_count() -> None:
    """deactivate() decrements the class-level _install_count."""
    # ESCAPE:
    # CLAIM: deactivate() decrements MockPlugin._install_count by 1.
    # PATH: MockPlugin.deactivate() -> MockPlugin._install_count -= 1 (floored at 0).
    # CHECK: count after deactivate == initial (round-trip).
    # MUTATION: Not decrementing would leave count == initial + 1.
    # ESCAPE: Setting count=0 always would make initial+1 fail (caught by activate test).
    # IMPACT: Interceptors could be uninstalled prematurely when multiple sandboxes nest.
    v = StrictVerifier()
    p = MockPlugin(v)
    initial = MockPlugin._install_count
    p.activate()
    p.activate()
    assert MockPlugin._install_count == initial + 2
    p.deactivate()
    p.deactivate()
    assert MockPlugin._install_count == initial


# ---------------------------------------------------------------------------
# MockPlugin.matches
# ---------------------------------------------------------------------------


def test_matches_returns_true_for_empty_expected() -> None:
    """matches() returns True when expected dict is empty (no constraints)."""
    # ESCAPE:
    # CLAIM: An empty expected dict matches any interaction.
    # PATH: matches() iterates over expected.items() — empty loop always returns True.
    # CHECK: return value is True (exact boolean).
    # MUTATION: Returning False for empty expected would block all assertions.
    # ESCAPE: Nothing reasonable.
    # IMPACT: assert_interaction() with no extra constraints would never match anything.
    v = StrictVerifier()
    p = MockPlugin(v)
    interaction = Interaction(
        source_id="mock:Svc.method",
        sequence=0,
        details={"args": "()", "kwargs": "{}"},
        plugin=p,
    )
    assert p.matches(interaction, {}) is True


def test_matches_returns_true_for_matching_fields() -> None:
    """matches() returns True when all expected fields match the interaction's details."""
    # ESCAPE:
    # CLAIM: Expected fields that match details return True.
    # PATH: matches() compares expected_val == interaction.details.get(key) for each key.
    # CHECK: return value is True.
    # MUTATION: Inverting the comparison would return False for matching fields.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Correct assertions would be treated as mismatches.
    v = StrictVerifier()
    p = MockPlugin(v)
    interaction = Interaction(
        source_id="mock:Svc.method",
        sequence=0,
        details={"args": "('x',)", "kwargs": "{}"},
        plugin=p,
    )
    assert p.matches(interaction, {"args": "('x',)"}) is True


def test_matches_returns_false_for_mismatched_fields() -> None:
    """matches() returns False when any expected field does not match."""
    # ESCAPE:
    # CLAIM: A field mismatch causes matches() to return False.
    # PATH: matches() finds expected_val != actual_val -> returns False.
    # CHECK: return value is False (exact boolean).
    # MUTATION: Ignoring mismatches and always returning True would fail.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Wrong interactions would be matched by assert_interaction().
    v = StrictVerifier()
    p = MockPlugin(v)
    interaction = Interaction(
        source_id="mock:Svc.method",
        sequence=0,
        details={"args": "('x',)", "kwargs": "{}"},
        plugin=p,
    )
    assert p.matches(interaction, {"args": "('y',)"}) is False


# ---------------------------------------------------------------------------
# MockPlugin.format_interaction
# ---------------------------------------------------------------------------


def test_format_interaction_returns_string() -> None:
    """format_interaction() returns a non-empty string describing the interaction."""
    # ESCAPE:
    # CLAIM: format_interaction() returns "[MockPlugin] Svc.method" for source_id "mock:Svc.method".
    # PATH: MockPlugin.format_interaction(interaction) -> str replacing "mock:" with "[MockPlugin] ".
    # CHECK: Exact string equality.
    # MUTATION: Wrong prefix or separator produces a different string.
    # ESCAPE: Nothing reasonable produces this exact string incorrectly.
    # IMPACT: Mismatch error messages would have missing/broken interaction descriptions.
    v = StrictVerifier()
    p = MockPlugin(v)
    interaction = Interaction(
        source_id="mock:Svc.method",
        sequence=0,
        details={},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[MockPlugin] Svc.method()"


def test_format_interaction_includes_source_id_components() -> None:
    """format_interaction() includes the mock name and method name from source_id."""
    # ESCAPE:
    # CLAIM: The formatted string contains 'PaymentService' and 'charge'.
    # PATH: format_interaction() transforms 'mock:PaymentService.charge' into a readable string.
    # CHECK: Both substrings appear in result.
    # MUTATION: Truncating source_id before the dot would omit 'charge'.
    # ESCAPE: A broken impl that returns "mock:PaymentService.charge" verbatim would pass;
    #         that is an acceptable format.
    # IMPACT: Error messages would be unreadable; developers couldn't identify the interaction.
    v = StrictVerifier()
    p = MockPlugin(v)
    interaction = Interaction(
        source_id="mock:PaymentService.charge",
        sequence=0,
        details={},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert "PaymentService" in result
    assert "charge" in result


# ---------------------------------------------------------------------------
# MockPlugin.format_mock_hint
# ---------------------------------------------------------------------------


def test_format_mock_hint_returns_string() -> None:
    """format_mock_hint() returns a copy-pasteable mock configuration snippet."""
    # ESCAPE:
    # CLAIM: format_mock_hint returns the exact snippet for the given mock_name and method_name.
    # PATH: MockPlugin.format_mock_hint(interaction) uses details["mock_name"] and details["method_name"].
    # CHECK: Exact string equality.
    # MUTATION: Using wrong field names or format produces a different string.
    # ESCAPE: Nothing reasonable produces this exact string incorrectly.
    # IMPACT: UnmockedInteractionError hints would be None/broken.
    v = StrictVerifier()
    p = MockPlugin(v)
    interaction = Interaction(
        source_id="mock:Svc.method",
        sequence=0,
        details={"mock_name": "Svc", "method_name": "method"},
        plugin=p,
    )
    result = p.format_mock_hint(interaction)
    assert result == 'tripwire.mock("Svc").method.returns(<value>)'


# ---------------------------------------------------------------------------
# MockPlugin.format_unmocked_hint
# ---------------------------------------------------------------------------


def test_format_unmocked_hint_contains_source_components() -> None:
    """format_unmocked_hint() contains the mock name and method name."""
    # ESCAPE:
    # CLAIM: The unmocked hint mentions the mock and method names for identification.
    # PATH: format_unmocked_hint parses source_id to extract names.
    # CHECK: Both names appear in result.
    # MUTATION: Using wrong parse logic (e.g., split on wrong char) would omit method name.
    # ESCAPE: If entire source_id is included verbatim, both names still appear.
    # IMPACT: Developers couldn't tell which mock to add from the error hint.
    v = StrictVerifier()
    p = MockPlugin(v)
    result = p.format_unmocked_hint(
        "mock:PaymentService.charge",
        args=("arg1",),
        kwargs={"amount": 100},
    )
    assert "PaymentService" in result
    assert "charge" in result


def test_format_unmocked_hint_returns_string() -> None:
    """format_unmocked_hint() returns the exact copy-pasteable hint for an unconfigured mock."""
    # ESCAPE:
    # CLAIM: format_unmocked_hint returns the exact multiline hint string for the given source_id/args/kwargs.
    # PATH: format_unmocked_hint parses source_id into mock_name/method_name and formats the hint.
    # CHECK: Exact string equality.
    # MUTATION: Wrong parse logic, wrong field order, or missing section produces a different string.
    # ESCAPE: Nothing reasonable produces this exact string incorrectly.
    # IMPACT: UnmockedInteractionError hint field would be None or malformed.
    v = StrictVerifier()
    p = MockPlugin(v)
    result = p.format_unmocked_hint("mock:Svc.method", args=(), kwargs={})
    expected = (
        "Unexpected call to Svc.method\n\n"
        "  Called with: args=(), kwargs={}\n\n"
        "  To mock this interaction, add before your sandbox:\n"
        '    tripwire.mock("Svc").method.returns(<value>)\n\n'
        "  Or to mark it optional:\n"
        '    tripwire.mock("Svc").method.required(False).returns(<value>)'
    )
    assert result == expected


# ---------------------------------------------------------------------------
# MockPlugin.format_assert_hint
# ---------------------------------------------------------------------------


def test_format_assert_hint_returns_string() -> None:
    """format_assert_hint() returns the exact assert_interaction snippet."""
    # ESCAPE:
    # CLAIM: format_assert_hint returns the exact copy-pasteable assertion for the given interaction.
    # PATH: MockPlugin.format_assert_hint(interaction) uses details["mock_name"] and details["method_name"].
    # CHECK: Exact string equality.
    # MUTATION: Wrong field names or format produces a different snippet.
    # ESCAPE: Nothing reasonable produces this exact string incorrectly.
    # IMPACT: UnassertedInteractionsError hints would be None/broken.
    v = StrictVerifier()
    p = MockPlugin(v)
    interaction = Interaction(
        source_id="mock:Svc.method",
        sequence=0,
        details={"mock_name": "Svc", "method_name": "method"},
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == (
        'tripwire.mock("Svc").method.assert_call(\n'
        "    args=(),\n"
        "    kwargs={},\n"
        ")"
    )


# ---------------------------------------------------------------------------
# MethodProxy.assert_call convenience method
# ---------------------------------------------------------------------------


def test_method_proxy_assert_call_convenience_method() -> None:
    """MethodProxy.assert_call() is a convenience wrapper for assert_interaction."""
    # ESCAPE:
    # CLAIM: assert_call() delegates to verifier.assert_interaction() with correct source and fields.
    # PATH: MethodProxy.assert_call -> _get_test_verifier_or_raise().assert_interaction(self, args=..., kwargs=...).
    # CHECK: No assertion error when args/kwargs match the recorded interaction.
    # MUTATION: Passing wrong source or swapping args/kwargs would cause InteractionMismatchError.
    # ESCAPE: If assert_call silently did nothing, this test would still pass; covered by next test.
    # IMPACT: Users have no convenience wrapper and must use raw verifier.assert_interaction().
    from tripwire._context import _current_test_verifier

    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Stripe")
    proxy.create_charge.returns("ch_123")

    token = _current_test_verifier.set(v)
    try:
        with v.sandbox():
            result = proxy.create_charge(5000, "usd", source="tok_123")

        assert result == "ch_123"

        proxy.create_charge.assert_call(
            args=(5000, "usd"),
            kwargs={"source": "tok_123"},
        )
    finally:
        _current_test_verifier.reset(token)


def test_method_proxy_assert_call_raises_on_mismatch() -> None:
    """MethodProxy.assert_call() raises InteractionMismatchError on wrong args."""
    # ESCAPE:
    # CLAIM: assert_call() actually validates args/kwargs, not just marking interactions as asserted.
    # PATH: assert_call -> assert_interaction -> matches() -> field comparison -> InteractionMismatchError.
    # CHECK: pytest.raises(InteractionMismatchError) confirms assert_call is not a no-op.
    # MUTATION: A no-op assert_call would not raise, failing this test.
    # IMPACT: assert_call would silently pass even with wrong assertions, defeating certainty.
    from tripwire._context import _current_test_verifier
    from tripwire._errors import InteractionMismatchError

    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Stripe")
    proxy.create_charge.returns("ch_123")

    token = _current_test_verifier.set(v)
    try:
        with v.sandbox():
            proxy.create_charge(5000, "usd")

        with pytest.raises(InteractionMismatchError):
            proxy.create_charge.assert_call(
                args=(9999, "eur"),
                kwargs={},
            )
    finally:
        _current_test_verifier.reset(token)


def test_method_proxy_assert_call_defaults_kwargs_to_empty_dict() -> None:
    """MethodProxy.assert_call() defaults kwargs to {} when not provided."""
    # ESCAPE:
    # CLAIM: Omitting kwargs argument defaults to empty dict, matching calls with no kwargs.
    # PATH: assert_call(kwargs=None default) -> kwargs if kwargs is not None else {} -> {}.
    # CHECK: No error when calling assert_call with only args= for a call that had no kwargs.
    # MUTATION: If default were None instead of {}, MissingAssertionFieldsError or mismatch.
    # IMPACT: Users would always have to pass kwargs={} explicitly, reducing convenience.
    from tripwire._context import _current_test_verifier

    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Svc")
    proxy.ping.returns("pong")

    token = _current_test_verifier.set(v)
    try:
        with v.sandbox():
            proxy.ping()

        proxy.ping.assert_call()
    finally:
        _current_test_verifier.reset(token)


# ---------------------------------------------------------------------------
# MockPlugin.format_unused_mock_hint
# ---------------------------------------------------------------------------


def test_format_unused_mock_hint_includes_registration_traceback() -> None:
    """format_unused_mock_hint() includes the registration_traceback in its output."""
    # ESCAPE:
    # CLAIM: The hint output contains registration_traceback so developers can find
    #        the line that registered the unused mock.
    # PATH: format_unused_mock_hint(mock_config) -> str containing mock_config.registration_traceback.
    # CHECK: mock_config.registration_traceback in result (the exact traceback string appears).
    # MUTATION: Omitting registration_traceback from the output fails this check.
    # ESCAPE: A broken impl that includes traceback twice would still pass; acceptable.
    # IMPACT: Developers cannot find which test line registered an unused mock without this.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("MyService")
    proxy.my_method.returns("hello")

    unused = p.get_unused_mocks()
    assert len(unused) == 1

    mock_config = unused[0]
    result = p.format_unused_mock_hint(mock_config)

    assert isinstance(result, str)
    assert mock_config.registration_traceback in result


def test_format_unused_mock_hint_includes_mock_name_and_method() -> None:
    """format_unused_mock_hint() identifies the unused mock by name and method."""
    # ESCAPE:
    # CLAIM: The hint mentions 'MyService' and 'my_method' so developers know what to remove.
    # PATH: format_unused_mock_hint uses mock_config.mock_name and mock_config.method_name.
    # CHECK: Both strings appear in result.
    # MUTATION: Using wrong field names or omitting them would fail these checks.
    # ESCAPE: Nothing reasonable mentions both names without them being in the output.
    # IMPACT: Developers couldn't identify which mock to remove or mark optional.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("MyService")
    proxy.my_method.returns("hello")

    unused = p.get_unused_mocks()
    assert len(unused) == 1

    result = p.format_unused_mock_hint(unused[0])
    assert "MyService" in result
    assert "my_method" in result


# ---------------------------------------------------------------------------
# MockConfig dataclass fields
# ---------------------------------------------------------------------------


def test_mock_config_registration_traceback_captured_at_registration() -> None:
    """MockConfig.registration_traceback is a non-empty string captured at registration time."""
    # ESCAPE:
    # CLAIM: registration_traceback is a non-empty string (the stack at .returns() call time).
    # PATH: MockConfig.__init__ captures traceback.format_stack() at construction.
    # CHECK: isinstance(tb, str) AND len(tb) > 0.
    # MUTATION: Setting registration_traceback="" would fail len check.
    # ESCAPE: A single space " " passes len check; but test_format_unused_mock_hint_includes_registration_traceback
    #         ensures the actual value appears in formatted output.
    # IMPACT: Unused mock hints would show empty traceback; developer can't find registration site.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    proxy.charge.returns("ok")

    unused = p.get_unused_mocks()
    assert len(unused) == 1

    tb = unused[0].registration_traceback
    assert isinstance(tb, str)
    assert "File " in tb


def test_mock_config_required_defaults_to_true() -> None:
    """MockConfig items created by .returns() have required=True by default."""
    # ESCAPE:
    # CLAIM: Without calling .required(), mocks are required by default.
    # PATH: MethodProxy._next_required defaults to True; MockConfig(required=self._next_required).
    # CHECK: unused[0].required is True.
    # MUTATION: Defaulting _next_required to False would make required False.
    # ESCAPE: Nothing reasonable.
    # IMPACT: All mocks would be optional by default; teardown checks would never catch unused mocks.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    proxy.charge.returns("ok")

    unused = p.get_unused_mocks()
    assert len(unused) == 1
    assert unused[0].required is True


def test_mock_config_required_false_when_set() -> None:
    """MockConfig items created after .required(False) have required=False."""
    # ESCAPE:
    # CLAIM: .required(False) sets the sticky flag so next MockConfig has required=False.
    # PATH: MethodProxy.required(False) sets self._next_required = False;
    #       next .returns() captures it as MockConfig(required=False).
    # CHECK: config.required is False.
    # MUTATION: Not storing the sticky flag would leave required=True.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Mocks marked optional would still appear in get_unused_mocks() at teardown.
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    proxy.charge.required(False).returns("ok")

    # Access the config directly from the queue (without calling)
    method = proxy.charge
    assert len(method._config_queue) == 1
    config = method._config_queue[0]
    assert config.required is False


# ---------------------------------------------------------------------------
# MockPlugin is a concrete subclass of BasePlugin
# ---------------------------------------------------------------------------


def test_mock_plugin_implements_all_abstract_methods() -> None:
    """MockPlugin implements all 9 abstract methods from BasePlugin."""
    # ESCAPE:
    # CLAIM: MockPlugin has no remaining abstract methods.
    # PATH: Python ABC enforcement; if any method is abstract, instantiation raises TypeError.
    # CHECK: MockPlugin can be instantiated (no TypeError).
    # MUTATION: Removing any method implementation from MockPlugin would raise TypeError.
    # ESCAPE: If the test itself instantiates MockPlugin, a TypeError would prevent the test from running.
    # IMPACT: MockPlugin would be unusable if any abstract method is missing.
    from tripwire._base_plugin import BasePlugin

    v = StrictVerifier()
    p = MockPlugin(v)  # Would raise TypeError if any abstract method unimplemented
    assert isinstance(p, BasePlugin)


# ---------------------------------------------------------------------------
# Coverage gap: MethodProxy.__call__ unknown side-effect RuntimeError
# ---------------------------------------------------------------------------


def test_method_proxy_call_raises_runtime_error_on_unknown_side_effect() -> None:
    """MethodProxy.__call__ raises RuntimeError if an unknown side-effect type is queued.

    This defends against future internal state corruption: if a MockConfig is
    somehow constructed with a side_effect that isn't _ReturnValue, _RaiseException,
    or _CallFn, the error surface is explicit rather than silent.
    """
    from tripwire._mock_plugin import MockConfig

    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Svc")
    method = proxy.do_thing

    # Inject a MockConfig with a bogus side-effect type directly into the queue
    class _Bogus:
        pass

    bad_config = MockConfig(
        mock_name="Svc",
        method_name="do_thing",
        side_effect=_Bogus(),  # type: ignore[arg-type]
    )
    method._config_queue.append(bad_config)

    with v.sandbox():
        with pytest.raises(RuntimeError, match="Unknown side effect type"):
            method()


# ---------------------------------------------------------------------------
# Coverage gap: MockProxy.__getattr__ raises AttributeError for private names
# ---------------------------------------------------------------------------


def test_mock_proxy_getattr_raises_attribute_error_for_private_names() -> None:
    """MockProxy.__getattr__ raises AttributeError when accessing names starting with '_'."""
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Svc")

    with pytest.raises(AttributeError):
        _ = proxy._private_method  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Coverage gap: MockPlugin.matches() returns False on exception
# ---------------------------------------------------------------------------


def test_mock_plugin_matches_returns_false_on_exception() -> None:
    """MockPlugin.matches() catches exceptions from comparison and returns False."""
    v = StrictVerifier()
    p = MockPlugin(v)

    class _RaisesOnEq:
        def __eq__(self, other: object) -> bool:
            raise RuntimeError("comparison exploded")

    interaction = Interaction(
        source_id="mock:Svc.method",
        sequence=0,
        details={"args": "()", "kwargs": "{}"},
        plugin=p,
    )
    result = p.matches(interaction, {"args": _RaisesOnEq()})
    assert result is False


# ---------------------------------------------------------------------------
# wraps delegation tests
# ---------------------------------------------------------------------------


def test_method_proxy_wraps_delegation_calls_real_method() -> None:
    """When wraps is set and queue is empty, real method is called and result returned."""

    class _Real:
        def compute(self, x: int, y: int) -> int:
            return x + y

    v = StrictVerifier()
    p = MockPlugin(v)
    real = _Real()
    proxy = p.get_or_create_proxy("Svc", wraps=real)

    with v.sandbox():
        result = proxy.compute(3, 4)

    assert result == 7


def test_method_proxy_wraps_delegation_records_interaction() -> None:
    """When wraps delegates to real, interaction is recorded on the timeline."""

    class _Real:
        def compute(self, x: int, y: int) -> int:
            return x + y

    v = StrictVerifier()
    p = MockPlugin(v)
    real = _Real()
    proxy = p.get_or_create_proxy("Svc", wraps=real)

    with v.sandbox():
        proxy.compute(3, 4)

    unasserted = v._timeline.all_unasserted()
    assert len(unasserted) == 1
    assert unasserted[0].source_id == "mock:Svc.compute"
    assert unasserted[0].details["args"] == (3, 4)
    assert unasserted[0].details["kwargs"] == {}


def test_method_proxy_wraps_delegation_records_even_when_real_raises() -> None:
    """When wraps real method raises, interaction is still recorded before re-raise."""

    class _Real:
        def fail(self) -> None:
            raise ValueError("real error")

    v = StrictVerifier()
    p = MockPlugin(v)
    real = _Real()
    proxy = p.get_or_create_proxy("Svc", wraps=real)

    with v.sandbox():
        with pytest.raises(ValueError, match="real error"):
            proxy.fail()

    unasserted = v._timeline.all_unasserted()
    assert len(unasserted) == 1
    assert unasserted[0].source_id == "mock:Svc.fail"


def test_method_proxy_queue_takes_priority_over_wraps() -> None:
    """If queue has entries, they are consumed even when wraps is set."""

    class _Real:
        def compute(self) -> str:
            return "real"

    v = StrictVerifier()
    p = MockPlugin(v)
    real = _Real()
    proxy = p.get_or_create_proxy("Svc", wraps=real)
    proxy.compute.returns("mocked")

    with v.sandbox():
        result = proxy.compute()

    assert result == "mocked"


def test_mock_proxy_wraps_property() -> None:
    """MockProxy.wraps returns the real object set at construction."""

    class _Real:
        pass

    v = StrictVerifier()
    p = MockPlugin(v)
    real = _Real()
    proxy = p.get_or_create_proxy("Svc", wraps=real)
    assert proxy.wraps is real


def test_mock_proxy_wraps_none_when_not_set() -> None:
    """MockProxy.wraps returns None when no wraps was provided."""
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Svc")
    assert proxy.wraps is None


def test_get_or_create_proxy_updates_wraps_on_existing_proxy() -> None:
    """If proxy already exists and wraps is passed, wraps is updated on the existing proxy."""

    class _Real:
        pass

    v = StrictVerifier()
    p = MockPlugin(v)
    proxy1 = p.get_or_create_proxy("Svc")
    assert proxy1.wraps is None

    real = _Real()
    proxy2 = p.get_or_create_proxy("Svc", wraps=real)
    assert proxy2 is proxy1  # same object
    assert proxy2.wraps is real


# ---------------------------------------------------------------------------
# assertable_fields tests
# ---------------------------------------------------------------------------


def test_mock_plugin_assertable_fields_returns_args_kwargs() -> None:
    """assertable_fields() always returns frozenset({'args', 'kwargs'})."""
    v = StrictVerifier()
    p = MockPlugin(v)

    interaction = Interaction(
        source_id="mock:Svc.method",
        sequence=0,
        details={"mock_name": "Svc", "method_name": "method", "args": (), "kwargs": {}},
        plugin=p,
    )
    result = p.assertable_fields(interaction)
    assert result == frozenset({"args", "kwargs"})


# ---------------------------------------------------------------------------
# format_assert_hint multiline tests
# ---------------------------------------------------------------------------


def test_mock_plugin_format_assert_hint_includes_args_and_kwargs() -> None:
    """format_assert_hint produces multiline output with args and kwargs included."""
    v = StrictVerifier()
    p = MockPlugin(v)

    interaction = Interaction(
        source_id="mock:Logger.log",
        sequence=0,
        details={
            "mock_name": "Logger",
            "method_name": "log",
            "args": ("event",),
            "kwargs": {"level": "info"},
        },
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == (
        'tripwire.mock("Logger").log.assert_call(\n'
        "    args=('event',),\n"
        "    kwargs={'level': 'info'},\n"
        ")"
    )


# ---------------------------------------------------------------------------
# MethodProxy __call__ -- raised stored in details
# ---------------------------------------------------------------------------


def test_method_proxy_raises_stores_raised_in_details() -> None:
    """When .raises() is configured, the exception instance is stored in details['raised']."""
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    exc = ValueError("payment failed")
    proxy.charge.raises(exc)

    token = _active_verifier.set(v)
    try:
        with pytest.raises(ValueError):
            proxy.charge("arg1")
    finally:
        _active_verifier.reset(token)

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details["raised"] is exc


def test_method_proxy_returns_does_not_store_raised_in_details() -> None:
    """When .returns() is configured, no 'raised' key appears in details."""
    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Service")
    proxy.charge.returns("ok")

    token = _active_verifier.set(v)
    try:
        proxy.charge()
    finally:
        _active_verifier.reset(token)

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert "raised" not in interactions[0].details


# ---------------------------------------------------------------------------
# Spy observability: returned and raised in details
# ---------------------------------------------------------------------------


def test_wraps_delegation_stores_returned_in_details() -> None:
    """When wraps delegates to real and it returns, details['returned'] is set."""

    class _Real:
        def compute(self, x: int) -> int:
            return x * 2

    v = StrictVerifier()
    p = MockPlugin(v)
    real = _Real()
    proxy = p.get_or_create_proxy("Svc", wraps=real)

    with v.sandbox():
        result = proxy.compute(5)

    assert result == 10
    unasserted = v._timeline.all_unasserted()
    assert len(unasserted) == 1
    assert unasserted[0].details["returned"] == 10


def test_wraps_delegation_stores_raised_in_details() -> None:
    """When wraps delegates to real and it raises, details['raised'] is set."""

    class _Real:
        def fail(self) -> None:
            raise ValueError("real error")

    v = StrictVerifier()
    p = MockPlugin(v)
    real = _Real()
    proxy = p.get_or_create_proxy("Svc", wraps=real)

    with v.sandbox():
        with pytest.raises(ValueError, match="real error"):
            proxy.fail()

    unasserted = v._timeline.all_unasserted()
    assert len(unasserted) == 1
    assert isinstance(unasserted[0].details["raised"], ValueError)
    assert str(unasserted[0].details["raised"]) == "real error"


def test_wraps_delegation_returned_none_is_distinct_from_no_return() -> None:
    """When wraps real method returns None explicitly, details['returned'] is None."""

    class _Real:
        def do_nothing(self) -> None:
            return None

    v = StrictVerifier()
    p = MockPlugin(v)
    real = _Real()
    proxy = p.get_or_create_proxy("Svc", wraps=real)

    with v.sandbox():
        result = proxy.do_nothing()

    assert result is None
    unasserted = v._timeline.all_unasserted()
    assert len(unasserted) == 1
    assert "returned" in unasserted[0].details
    assert unasserted[0].details["returned"] is None


def test_wraps_delegation_returned_and_raised_mutually_exclusive() -> None:
    """A wraps interaction never has both 'returned' and 'raised' in details."""

    class _Real:
        def compute(self, x: int) -> int:
            return x * 2

        def fail(self) -> None:
            raise ValueError("boom")

    v = StrictVerifier()
    p = MockPlugin(v)
    real = _Real()
    proxy = p.get_or_create_proxy("Svc", wraps=real)

    with v.sandbox():
        proxy.compute(3)
        with pytest.raises(ValueError):
            proxy.fail()

    unasserted = v._timeline.all_unasserted()
    assert len(unasserted) == 2
    # First: returned, no raised
    assert "returned" in unasserted[0].details
    assert "raised" not in unasserted[0].details
    # Second: raised, no returned
    assert "raised" in unasserted[1].details
    assert "returned" not in unasserted[1].details


# ---------------------------------------------------------------------------
# assertable_fields adapts to raised/returned
# ---------------------------------------------------------------------------


def test_mock_plugin_assertable_fields_includes_raised_when_present() -> None:
    """assertable_fields includes 'raised' when interaction.details has 'raised'."""
    v = StrictVerifier()
    p = MockPlugin(v)

    interaction = Interaction(
        source_id="mock:Svc.method",
        sequence=0,
        details={
            "mock_name": "Svc",
            "method_name": "method",
            "args": (),
            "kwargs": {},
            "raised": ValueError("err"),
        },
        plugin=p,
    )
    result = p.assertable_fields(interaction)
    assert result == frozenset({"args", "kwargs", "raised"})


def test_mock_plugin_assertable_fields_includes_returned_when_present() -> None:
    """assertable_fields includes 'returned' when interaction.details has 'returned'."""
    v = StrictVerifier()
    p = MockPlugin(v)

    interaction = Interaction(
        source_id="mock:Svc.method",
        sequence=0,
        details={
            "mock_name": "Svc",
            "method_name": "method",
            "args": (),
            "kwargs": {},
            "returned": 42,
        },
        plugin=p,
    )
    result = p.assertable_fields(interaction)
    assert result == frozenset({"args", "kwargs", "returned"})


def test_mock_plugin_assertable_fields_plain_call_unchanged() -> None:
    """assertable_fields for a plain mock call (no raised/returned) is still {args, kwargs}."""
    v = StrictVerifier()
    p = MockPlugin(v)

    interaction = Interaction(
        source_id="mock:Svc.method",
        sequence=0,
        details={
            "mock_name": "Svc",
            "method_name": "method",
            "args": (),
            "kwargs": {},
        },
        plugin=p,
    )
    result = p.assertable_fields(interaction)
    assert result == frozenset({"args", "kwargs"})


# ---------------------------------------------------------------------------
# assert_call with raised and returned
# ---------------------------------------------------------------------------


def test_assert_call_with_raised_parameter() -> None:
    """assert_call(raised=...) passes raised to assert_interaction."""
    from tripwire._context import _current_test_verifier

    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Svc")
    exc = ValueError("boom")
    proxy.do_thing.raises(exc)

    token = _current_test_verifier.set(v)
    try:
        with v.sandbox():
            with pytest.raises(ValueError):
                proxy.do_thing("arg1")

        proxy.do_thing.assert_call(
            args=("arg1",),
            kwargs={},
            raised=exc,
        )
    finally:
        _current_test_verifier.reset(token)


def test_assert_call_with_returned_parameter() -> None:
    """assert_call(returned=...) passes returned to assert_interaction for spy mode."""
    from tripwire._context import _current_test_verifier

    class _Real:
        def compute(self, x: int) -> int:
            return x * 2

    v = StrictVerifier()
    p = MockPlugin(v)
    real = _Real()
    proxy = p.get_or_create_proxy("Svc", wraps=real)

    token = _current_test_verifier.set(v)
    try:
        with v.sandbox():
            result = proxy.compute(5)

        assert result == 10
        proxy.compute.assert_call(
            args=(5,),
            kwargs={},
            returned=10,
        )
    finally:
        _current_test_verifier.reset(token)


def test_assert_call_missing_raised_raises_missing_fields_error() -> None:
    """Omitting raised= when details has 'raised' triggers MissingAssertionFieldsError.

    NOTE: This test passes even before Task 5's assert_call implementation because
    the enforcement comes from assertable_fields (Task 4), not from assert_call itself.
    assertable_fields requires 'raised' when it is present in interaction.details, and
    the existing assert_call (without the raised= parameter) cannot pass it, so
    assert_interaction raises MissingAssertionFieldsError. This is by design: the
    certainty contract enforcement is in assertable_fields, and assert_call is just a
    convenience wrapper that constructs the expected dict.
    """
    from tripwire._context import _current_test_verifier
    from tripwire._errors import MissingAssertionFieldsError

    v = StrictVerifier()
    p = MockPlugin(v)
    proxy = p.get_or_create_proxy("Svc")
    proxy.do_thing.raises(ValueError("boom"))

    token = _current_test_verifier.set(v)
    try:
        with v.sandbox():
            with pytest.raises(ValueError):
                proxy.do_thing()

        with pytest.raises(MissingAssertionFieldsError) as exc_info:
            proxy.do_thing.assert_call(
                args=(),
                kwargs={},
                # raised= intentionally omitted
            )
        assert "raised" in exc_info.value.missing_fields
    finally:
        _current_test_verifier.reset(token)


# ---------------------------------------------------------------------------
# format_assert_hint with raised/returned
# ---------------------------------------------------------------------------


def test_format_assert_hint_includes_raised_when_present() -> None:
    """format_assert_hint includes raised= line when details has 'raised'."""
    v = StrictVerifier()
    p = MockPlugin(v)

    exc = ValueError("boom")
    interaction = Interaction(
        source_id="mock:Svc.method",
        sequence=0,
        details={
            "mock_name": "Svc",
            "method_name": "method",
            "args": ("a",),
            "kwargs": {},
            "raised": exc,
        },
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == (
        'tripwire.mock("Svc").method.assert_call(\n'
        "    args=('a',),\n"
        "    kwargs={},\n"
        f"    raised={exc!r},\n"
        ")"
    )


def test_format_assert_hint_includes_returned_when_present() -> None:
    """format_assert_hint includes returned= line when details has 'returned'."""
    v = StrictVerifier()
    p = MockPlugin(v)

    interaction = Interaction(
        source_id="mock:Svc.method",
        sequence=0,
        details={
            "mock_name": "Svc",
            "method_name": "method",
            "args": (),
            "kwargs": {},
            "returned": {"data": "value"},
        },
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == (
        'tripwire.mock("Svc").method.assert_call(\n'
        "    args=(),\n"
        "    kwargs={},\n"
        "    returned={'data': 'value'},\n"
        ")"
    )


def test_format_assert_hint_plain_call_unchanged() -> None:
    """format_assert_hint for a plain call (no raised/returned) is unchanged."""
    v = StrictVerifier()
    p = MockPlugin(v)

    interaction = Interaction(
        source_id="mock:Svc.method",
        sequence=0,
        details={
            "mock_name": "Svc",
            "method_name": "method",
            "args": (),
            "kwargs": {},
        },
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == (
        'tripwire.mock("Svc").method.assert_call(\n'
        "    args=(),\n"
        "    kwargs={},\n"
        ")"
    )


def test_format_mock_hint_includes_raises_when_raised_in_details() -> None:
    """format_mock_hint suggests .raises() when interaction has 'raised'."""
    v = StrictVerifier()
    p = MockPlugin(v)

    exc = ValueError("boom")
    interaction = Interaction(
        source_id="mock:Svc.method",
        sequence=0,
        details={
            "mock_name": "Svc",
            "method_name": "method",
            "args": (),
            "kwargs": {},
            "raised": exc,
        },
        plugin=p,
    )
    result = p.format_mock_hint(interaction)
    assert result == f'tripwire.mock("Svc").method.raises({exc!r})'
