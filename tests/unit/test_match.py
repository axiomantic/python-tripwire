"""Tests for tripwire._match -- M() pattern matching."""

from tripwire._firewall_request import (
    DnsFirewallRequest,
    HttpFirewallRequest,
    RedisFirewallRequest,
    SubprocessFirewallRequest,
)
from tripwire._match import M


class TestProtocolMatch:
    def test_match_protocol(self) -> None:
        m = M(protocol="http")
        req = HttpFirewallRequest(host="example.com", port=80, scheme="https", path="/api", method="GET")
        assert m.matches(req) is True

    def test_mismatch_protocol(self) -> None:
        m = M(protocol="redis")
        req = HttpFirewallRequest(host="example.com", port=80)
        assert m.matches(req) is False

    def test_no_protocol_matches_any(self) -> None:
        m = M(host="example.com")
        req = HttpFirewallRequest(host="example.com", port=80)
        assert m.matches(req) is True


class TestFieldMatch:
    def test_exact_match_method(self) -> None:
        m = M(protocol="http", method="GET")
        req = HttpFirewallRequest(host="example.com", port=80, method="GET")
        assert m.matches(req) is True

    def test_exact_mismatch(self) -> None:
        m = M(protocol="http", method="POST")
        req = HttpFirewallRequest(host="example.com", port=80, method="GET")
        assert m.matches(req) is False

    def test_glob_host(self) -> None:
        m = M(protocol="http", host="*.example.com")
        req = HttpFirewallRequest(host="sub.example.com", port=80)
        assert m.matches(req) is True

    def test_glob_host_no_evil(self) -> None:
        m = M(protocol="http", host="*.example.com")
        req = HttpFirewallRequest(host="evil-example.com", port=80)
        assert m.matches(req) is False

    def test_cidr_match(self) -> None:
        m = M(host__cidr="10.0.0.0/8")
        req = HttpFirewallRequest(host="10.1.2.3", port=80)
        assert m.matches(req) is True

    def test_cidr_no_match(self) -> None:
        m = M(host__cidr="10.0.0.0/8")
        req = HttpFirewallRequest(host="192.168.1.1", port=80)
        assert m.matches(req) is False

    def test_regex_match(self) -> None:
        m = M(protocol="http", path__regex=r"/api/v\d+/.*")
        req = HttpFirewallRequest(host="example.com", port=80, path="/api/v2/users")
        assert m.matches(req) is True

    def test_callable_match(self) -> None:
        m = M(protocol="subprocess", binary=lambda b: b in {"git", "curl"})
        req = SubprocessFirewallRequest(command="git status", binary="git")
        assert m.matches(req) is True

    def test_callable_no_match(self) -> None:
        m = M(protocol="subprocess", binary=lambda b: b in {"git", "curl"})
        req = SubprocessFirewallRequest(command="rm -rf /", binary="rm")
        assert m.matches(req) is False

    def test_missing_field_no_match(self) -> None:
        m = M(protocol="http", nonexistent="value")
        req = HttpFirewallRequest(host="example.com", port=80)
        assert m.matches(req) is False


class TestComposition:
    def test_or_pattern(self) -> None:
        m = M(protocol="http") | M(protocol="dns")
        http_req = HttpFirewallRequest(host="example.com", port=80)
        dns_req = DnsFirewallRequest(hostname="example.com")
        redis_req = RedisFirewallRequest(host="localhost", port=6379)
        assert m.matches(http_req) is True
        assert m.matches(dns_req) is True
        assert m.matches(redis_req) is False

    def test_and_pattern(self) -> None:
        m = M(protocol="http") & M(host="*.example.com")
        req_match = HttpFirewallRequest(host="sub.example.com", port=80)
        req_no = HttpFirewallRequest(host="other.com", port=80)
        assert m.matches(req_match) is True
        assert m.matches(req_no) is False

    def test_not_pattern(self) -> None:
        m = ~M(protocol="redis", command="FLUSHALL")
        req_flush = RedisFirewallRequest(host="localhost", port=6379, command="FLUSHALL")
        req_get = RedisFirewallRequest(host="localhost", port=6379, command="GET")
        assert m.matches(req_flush) is False
        assert m.matches(req_get) is True
