"""Unit tests for DnsPlugin."""

from __future__ import annotations

import socket

import pytest

from bigfoot._context import _current_test_verifier
from bigfoot._errors import (
    InteractionMismatchError,
    MissingAssertionFieldsError,
    UnmockedInteractionError,
)
from bigfoot._timeline import Interaction
from bigfoot._verifier import StrictVerifier
from bigfoot.plugins.dns_plugin import (
    DnsMockConfig,
    DnsPlugin,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_verifier_with_plugin() -> tuple[StrictVerifier, DnsPlugin]:
    """Return (verifier, plugin) with DnsPlugin registered but NOT activated."""
    v = StrictVerifier()
    for p in v._plugins:
        if isinstance(p, DnsPlugin):
            return v, p
    p = DnsPlugin(v)
    return v, p


def _reset_plugin_count() -> None:
    """Force-reset the class-level install count to 0 and restore patches if leaked."""
    with DnsPlugin._install_lock:
        DnsPlugin._install_count = 0
        # Use the plugin's own _restore_patches() to avoid duplicating restoration logic.
        DnsPlugin.__new__(DnsPlugin).restore_patches()


@pytest.fixture(autouse=True)
def clean_plugin_counts() -> None:
    """Ensure plugin install count starts and ends at 0 for every test."""
    _reset_plugin_count()
    yield
    _reset_plugin_count()


# ---------------------------------------------------------------------------
# DnsMockConfig dataclass
# ---------------------------------------------------------------------------


def test_dns_mock_config_fields() -> None:
    """DnsMockConfig stores operation, hostname, returns, raises, required correctly."""
    err = socket.gaierror("Name or service not known")
    config = DnsMockConfig(
        operation="getaddrinfo",
        hostname="example.com",
        returns=[],
        raises=err,
        required=False,
    )
    assert config.operation == "getaddrinfo"
    assert config.hostname == "example.com"
    assert config.returns == []
    assert config.raises is err
    assert config.required is False
    lines = config.registration_traceback.splitlines()
    assert lines[0].startswith("  File ")


def test_dns_mock_config_defaults() -> None:
    """DnsMockConfig defaults: raises=None, required=True."""
    config = DnsMockConfig(operation="gethostbyname", hostname="example.com", returns="1.2.3.4")
    assert config.raises is None
    assert config.required is True


# ---------------------------------------------------------------------------
# Activation and reference counting
# ---------------------------------------------------------------------------


def test_activate_installs_getaddrinfo_patch() -> None:
    """After activate(), socket.getaddrinfo is replaced with bigfoot interceptor."""
    original = socket.getaddrinfo
    v, p = _make_verifier_with_plugin()
    p.activate()
    assert socket.getaddrinfo is not original
    p.deactivate()


def test_activate_installs_gethostbyname_patch() -> None:
    """After activate(), socket.gethostbyname is replaced with bigfoot interceptor."""
    original = socket.gethostbyname
    v, p = _make_verifier_with_plugin()
    p.activate()
    assert socket.gethostbyname is not original
    p.deactivate()


def test_deactivate_restores_patches() -> None:
    """After activate() then deactivate(), socket functions are restored."""
    original_getaddrinfo = socket.getaddrinfo
    original_gethostbyname = socket.gethostbyname
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.deactivate()
    assert socket.getaddrinfo is original_getaddrinfo
    assert socket.gethostbyname is original_gethostbyname


def test_reference_counting_nested() -> None:
    """Two activate() calls require two deactivate() calls before patches are removed."""
    original_getaddrinfo = socket.getaddrinfo
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.activate()
    assert DnsPlugin._install_count == 2

    p.deactivate()
    assert DnsPlugin._install_count == 1
    assert socket.getaddrinfo is not original_getaddrinfo

    p.deactivate()
    assert DnsPlugin._install_count == 0
    assert socket.getaddrinfo is original_getaddrinfo


# ---------------------------------------------------------------------------
# mock_getaddrinfo: basic interception
# ---------------------------------------------------------------------------


def test_mock_getaddrinfo_returns_value() -> None:
    """mock_getaddrinfo returns the configured result when getaddrinfo is called."""
    v, p = _make_verifier_with_plugin()
    expected_result = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))
    ]
    p.mock_getaddrinfo("example.com", returns=expected_result)

    with v.sandbox():
        result = socket.getaddrinfo("example.com", 80)

    assert result == expected_result


def test_mock_getaddrinfo_full_assertion(bigfoot_verifier: StrictVerifier) -> None:
    """assert_getaddrinfo asserts all fields: host, port, family, type, proto."""
    import bigfoot

    expected_result = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))
    ]
    bigfoot.dns_mock.mock_getaddrinfo("example.com", returns=expected_result)

    with bigfoot.sandbox():
        socket.getaddrinfo("example.com", 80, socket.AF_INET, socket.SOCK_STREAM, 6)

    bigfoot.dns_mock.assert_getaddrinfo(
        host="example.com",
        port=80,
        family=socket.AF_INET,
        type=socket.SOCK_STREAM,
        proto=6,
    )


def test_mock_getaddrinfo_default_args(bigfoot_verifier: StrictVerifier) -> None:
    """getaddrinfo with default family/type/proto records 0 for each."""
    import bigfoot

    expected_result = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))
    ]
    bigfoot.dns_mock.mock_getaddrinfo("example.com", returns=expected_result)

    with bigfoot.sandbox():
        socket.getaddrinfo("example.com", 80)

    bigfoot.dns_mock.assert_getaddrinfo(
        host="example.com",
        port=80,
        family=0,
        type=0,
        proto=0,
    )


# ---------------------------------------------------------------------------
# mock_gethostbyname: basic interception
# ---------------------------------------------------------------------------


def test_mock_gethostbyname_returns_value() -> None:
    """mock_gethostbyname returns the configured result."""
    v, p = _make_verifier_with_plugin()
    p.mock_gethostbyname("example.com", returns="93.184.216.34")

    with v.sandbox():
        result = socket.gethostbyname("example.com")

    assert result == "93.184.216.34"


def test_mock_gethostbyname_full_assertion(bigfoot_verifier: StrictVerifier) -> None:
    """assert_gethostbyname asserts hostname field."""
    import bigfoot

    bigfoot.dns_mock.mock_gethostbyname("example.com", returns="93.184.216.34")

    with bigfoot.sandbox():
        socket.gethostbyname("example.com")

    bigfoot.dns_mock.assert_gethostbyname(hostname="example.com")


# ---------------------------------------------------------------------------
# Unmocked interaction error
# ---------------------------------------------------------------------------


def test_unmocked_getaddrinfo_raises() -> None:
    """getaddrinfo without mock raises UnmockedInteractionError."""
    v, p = _make_verifier_with_plugin()

    with v.sandbox():
        with pytest.raises(UnmockedInteractionError) as exc_info:
            socket.getaddrinfo("example.com", 80)

    assert exc_info.value.source_id == "dns:getaddrinfo:example.com"


def test_unmocked_gethostbyname_raises() -> None:
    """gethostbyname without mock raises UnmockedInteractionError."""
    v, p = _make_verifier_with_plugin()

    with v.sandbox():
        with pytest.raises(UnmockedInteractionError) as exc_info:
            socket.gethostbyname("example.com")

    assert exc_info.value.source_id == "dns:gethostbyname:example.com"


# ---------------------------------------------------------------------------
# Unused mock detection
# ---------------------------------------------------------------------------


def test_get_unused_mocks_returns_unconsumed_required() -> None:
    """get_unused_mocks() returns unconsumed required mocks."""
    v, p = _make_verifier_with_plugin()
    p.mock_getaddrinfo("example.com", returns=[])
    p.mock_getaddrinfo("other.com", returns=[])

    with v.sandbox():
        socket.getaddrinfo("example.com", 80)

    unused = p.get_unused_mocks()
    assert len(unused) == 1
    assert unused[0].hostname == "other.com"


def test_get_unused_mocks_excludes_required_false() -> None:
    """get_unused_mocks() excludes configs with required=False."""
    v, p = _make_verifier_with_plugin()
    p.mock_getaddrinfo("example.com", returns=[], required=False)

    unused = p.get_unused_mocks()
    assert unused == []


# ---------------------------------------------------------------------------
# Missing assertion fields
# ---------------------------------------------------------------------------


def test_missing_assertion_fields_getaddrinfo(bigfoot_verifier: StrictVerifier) -> None:
    """Asserting getaddrinfo with incomplete fields raises MissingAssertionFieldsError."""
    import bigfoot
    from bigfoot.plugins.dns_plugin import _DnsSentinel

    bigfoot.dns_mock.mock_getaddrinfo("example.com", returns=[])

    with bigfoot.sandbox():
        socket.getaddrinfo("example.com", 80)

    sentinel = _DnsSentinel("dns:getaddrinfo:example.com")
    with pytest.raises(MissingAssertionFieldsError) as exc_info:
        # Only pass host, omit port/family/type/proto
        bigfoot_verifier.assert_interaction(sentinel, host="example.com")

    assert "port" in exc_info.value.missing_fields
    # Now assert fully so teardown passes
    bigfoot.dns_mock.assert_getaddrinfo(
        host="example.com", port=80, family=0, type=0, proto=0,
    )


# ---------------------------------------------------------------------------
# Exception propagation
# ---------------------------------------------------------------------------


def test_mock_getaddrinfo_raises_exception() -> None:
    """mock_getaddrinfo with raises parameter propagates the exception."""
    v, p = _make_verifier_with_plugin()
    err = socket.gaierror("Name or service not known")
    p.mock_getaddrinfo("example.com", returns=None, raises=err)

    with v.sandbox():
        with pytest.raises(socket.gaierror) as exc_info:
            socket.getaddrinfo("example.com", 80)

    assert str(exc_info.value) == "Name or service not known"


def test_mock_gethostbyname_raises_exception() -> None:
    """mock_gethostbyname with raises parameter propagates the exception."""
    v, p = _make_verifier_with_plugin()
    err = socket.gaierror("Name or service not known")
    p.mock_gethostbyname("example.com", returns=None, raises=err)

    with v.sandbox():
        with pytest.raises(socket.gaierror) as exc_info:
            socket.gethostbyname("example.com")

    assert str(exc_info.value) == "Name or service not known"


# ---------------------------------------------------------------------------
# FIFO ordering
# ---------------------------------------------------------------------------


def test_mock_getaddrinfo_fifo() -> None:
    """Multiple mocks for same hostname are consumed in FIFO order."""
    v, p = _make_verifier_with_plugin()
    p.mock_getaddrinfo("example.com", returns="first")
    p.mock_getaddrinfo("example.com", returns="second")

    with v.sandbox():
        first = socket.getaddrinfo("example.com", 80)
        second = socket.getaddrinfo("example.com", 443)

    assert first == "first"
    assert second == "second"


# ---------------------------------------------------------------------------
# Interactions not auto-asserted
# ---------------------------------------------------------------------------


def test_dns_interactions_not_auto_asserted(bigfoot_verifier: StrictVerifier) -> None:
    """DNS interactions are NOT auto-asserted -- they land on the timeline unasserted."""
    import bigfoot

    bigfoot.dns_mock.mock_gethostbyname("example.com", returns="1.2.3.4")
    with bigfoot.sandbox():
        socket.gethostbyname("example.com")

    timeline = bigfoot_verifier._timeline
    interactions = timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].source_id == "dns:gethostbyname:example.com"
    # Assert it so verify_all() at teardown succeeds
    bigfoot.dns_mock.assert_gethostbyname(hostname="example.com")


# ---------------------------------------------------------------------------
# Assertable fields
# ---------------------------------------------------------------------------


def test_assertable_fields_getaddrinfo() -> None:
    """assertable_fields for getaddrinfo returns all detail keys."""
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="dns:getaddrinfo:example.com",
        sequence=0,
        details={"host": "example.com", "port": 80, "family": 0, "type": 0, "proto": 0},
        plugin=p,
    )
    assert p.assertable_fields(interaction) == frozenset(
        {"host", "port", "family", "type", "proto"}
    )


def test_assertable_fields_gethostbyname() -> None:
    """assertable_fields for gethostbyname returns all detail keys."""
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="dns:gethostbyname:example.com",
        sequence=0,
        details={"hostname": "example.com"},
        plugin=p,
    )
    assert p.assertable_fields(interaction) == frozenset({"hostname"})


# ---------------------------------------------------------------------------
# format_* methods
# ---------------------------------------------------------------------------


def test_format_interaction_getaddrinfo() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="dns:getaddrinfo:example.com",
        sequence=0,
        details={"host": "example.com", "port": 80, "family": 0, "type": 0, "proto": 0},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[DnsPlugin] dns.getaddrinfo('example.com', 80)"


def test_format_interaction_gethostbyname() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="dns:gethostbyname:example.com",
        sequence=0,
        details={"hostname": "example.com"},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[DnsPlugin] dns.gethostbyname('example.com')"


def test_format_mock_hint_getaddrinfo() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="dns:getaddrinfo:example.com",
        sequence=0,
        details={"host": "example.com", "port": 80, "family": 0, "type": 0, "proto": 0},
        plugin=p,
    )
    result = p.format_mock_hint(interaction)
    assert result == "    bigfoot.dns_mock.mock_getaddrinfo('example.com', returns=...)"


def test_format_unmocked_hint() -> None:
    v, p = _make_verifier_with_plugin()
    result = p.format_unmocked_hint("dns:getaddrinfo:example.com", (), {})
    assert result == (
        "socket.getaddrinfo('example.com', ...) was called but no mock was registered.\n"
        "Register a mock with:\n"
        "    bigfoot.dns_mock.mock_getaddrinfo('example.com', returns=...)"
    )


def test_format_assert_hint_getaddrinfo() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="dns:getaddrinfo:example.com",
        sequence=0,
        details={"host": "example.com", "port": 80, "family": 0, "type": 0, "proto": 0},
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == (
        "    bigfoot.dns_mock.assert_getaddrinfo(\n"
        "        host='example.com',\n"
        "        port=80,\n"
        "        family=0,\n"
        "        type=0,\n"
        "        proto=0,\n"
        "    )"
    )


def test_format_unused_mock_hint() -> None:
    v, p = _make_verifier_with_plugin()
    config = DnsMockConfig(operation="getaddrinfo", hostname="example.com", returns=[])
    result = p.format_unused_mock_hint(config)
    expected_prefix = (
        "dns.getaddrinfo('example.com') was mocked (required=True) but never called.\n"
        "Registered at:\n"
    )
    assert result == expected_prefix + config.registration_traceback


# ---------------------------------------------------------------------------
# Module-level proxy: bigfoot.dns_mock
# ---------------------------------------------------------------------------


def test_dns_mock_proxy_mock_getaddrinfo(bigfoot_verifier: StrictVerifier) -> None:
    """bigfoot.dns_mock.mock_getaddrinfo works via the proxy."""
    import bigfoot

    expected_result = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))
    ]
    bigfoot.dns_mock.mock_getaddrinfo("example.com", returns=expected_result)

    with bigfoot.sandbox():
        result = socket.getaddrinfo("example.com", 80)

    assert result == expected_result
    bigfoot.dns_mock.assert_getaddrinfo(
        host="example.com",
        port=80,
        family=0,
        type=0,
        proto=0,
    )


def test_dns_mock_proxy_raises_outside_context() -> None:
    """Accessing bigfoot.dns_mock outside a test context raises NoActiveVerifierError."""
    import bigfoot
    from bigfoot._errors import NoActiveVerifierError

    token = _current_test_verifier.set(None)
    try:
        with pytest.raises(NoActiveVerifierError):
            _ = bigfoot.dns_mock.mock_getaddrinfo
    finally:
        _current_test_verifier.reset(token)


# ---------------------------------------------------------------------------
# DnsPlugin in __all__
# ---------------------------------------------------------------------------


def test_dns_plugin_in_all() -> None:
    """DnsPlugin and dns_mock are exported from bigfoot."""
    import bigfoot

    assert "DnsPlugin" in bigfoot.__all__
    assert "dns_mock" in bigfoot.__all__
    assert type(bigfoot.dns_mock).__name__ == "_DnsProxy"


# ---------------------------------------------------------------------------
# assert_command typed helpers with wrong args
# ---------------------------------------------------------------------------


def test_assert_getaddrinfo_wrong_args_raises(bigfoot_verifier: StrictVerifier) -> None:
    """assert_getaddrinfo with wrong values raises InteractionMismatchError."""
    import bigfoot

    bigfoot.dns_mock.mock_getaddrinfo("example.com", returns=[])

    with bigfoot.sandbox():
        socket.getaddrinfo("example.com", 80)

    with pytest.raises(InteractionMismatchError):
        bigfoot.dns_mock.assert_getaddrinfo(
            host="wrong.com",
            port=80,
            family=0,
            type=0,
            proto=0,
        )
    # Assert correctly so teardown passes
    bigfoot.dns_mock.assert_getaddrinfo(
        host="example.com",
        port=80,
        family=0,
        type=0,
        proto=0,
    )


# ---------------------------------------------------------------------------
# dnspython optional: resolve
# ---------------------------------------------------------------------------


class TestDnsResolve:
    """Tests for dns.resolver.resolve interception (requires dnspython)."""

    @pytest.fixture(autouse=True)
    def _require_dnspython(self) -> None:
        import dns.resolver  # noqa: F401

    def test_mock_resolve_returns_value(self) -> None:
        """mock_resolve returns the configured result."""
        v, p = _make_verifier_with_plugin()
        p.mock_resolve("example.com", "A", returns=["93.184.216.34"])

        with v.sandbox():
            import dns.resolver

            result = dns.resolver.resolve("example.com", "A")

        assert result == ["93.184.216.34"]

    def test_mock_resolve_full_assertion(self, bigfoot_verifier: StrictVerifier) -> None:
        """assert_resolve asserts qname and rdtype fields."""
        import bigfoot

        bigfoot.dns_mock.mock_resolve("example.com", "A", returns=["93.184.216.34"])

        with bigfoot.sandbox():
            import dns.resolver

            dns.resolver.resolve("example.com", "A")

        bigfoot.dns_mock.assert_resolve(qname="example.com", rdtype="A")

    def test_unmocked_resolve_raises(self) -> None:
        """resolve without mock raises UnmockedInteractionError."""
        v, p = _make_verifier_with_plugin()

        with v.sandbox():
            import dns.resolver

            with pytest.raises(UnmockedInteractionError) as exc_info:
                dns.resolver.resolve("example.com", "A")

        assert exc_info.value.source_id == "dns:resolve:example.com"

    def test_mock_resolve_raises_exception(self) -> None:
        """mock_resolve with raises parameter propagates the exception."""
        import dns.resolver

        v, p = _make_verifier_with_plugin()
        err = dns.resolver.NXDOMAIN()
        p.mock_resolve("example.com", "A", returns=None, raises=err)

        with v.sandbox():
            with pytest.raises(dns.resolver.NXDOMAIN):
                dns.resolver.resolve("example.com", "A")

    def test_assertable_fields_resolve(self) -> None:
        """assertable_fields for resolve returns qname and rdtype."""
        v, p = _make_verifier_with_plugin()
        interaction = Interaction(
            source_id="dns:resolve:example.com",
            sequence=0,
            details={"qname": "example.com", "rdtype": "A"},
            plugin=p,
        )
        assert p.assertable_fields(interaction) == frozenset({"qname", "rdtype"})

    def test_resolve_patches_resolver_instance(self) -> None:
        """Resolve patches both dns.resolver.resolve and Resolver.resolve."""
        import dns.resolver

        v, p = _make_verifier_with_plugin()
        p.mock_resolve("example.com", "A", returns=["93.184.216.34"])

        with v.sandbox():
            resolver = dns.resolver.Resolver()
            result = resolver.resolve("example.com", "A")

        assert result == ["93.184.216.34"]
