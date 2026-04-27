"""Integration tests for firewall layering: TOML + marks + context managers."""

from tripwire._firewall import Disposition, FirewallRule, FirewallStack
from tripwire._firewall_request import (
    DnsFirewallRequest,
    HttpFirewallRequest,
    RedisFirewallRequest,
)
from tripwire._match import M


class TestAppendixAWalkthrough:
    """Recreate the Appendix A walkthrough from the design doc."""

    def _build_stack(self) -> FirewallStack:
        """Build the stack from the Appendix A example."""
        frames = (
            # Index 0 (outermost): TOML allow dns
            FirewallRule(pattern=M(protocol="dns"), disposition=Disposition.ALLOW),
            # Index 1: TOML allow redis://localhost:6379
            FirewallRule(
                pattern=M(protocol="redis", host="localhost", port=6379),
                disposition=Disposition.ALLOW,
            ),
            # Index 2: mark allow http *.example.com
            FirewallRule(
                pattern=M(protocol="http", host="*.example.com"),
                disposition=Disposition.ALLOW,
            ),
            # Index 3: deny cm *.internal.example.com
            FirewallRule(
                pattern=M(protocol="http", host="*.internal.example.com"),
                disposition=Disposition.DENY,
            ),
            # Index 4 (innermost): allow cm api.internal.example.com
            FirewallRule(
                pattern=M(protocol="http", host="api.internal.example.com"),
                disposition=Disposition.ALLOW,
            ),
        )
        return FirewallStack(frames)

    def test_specific_internal_allowed(self) -> None:
        stack = self._build_stack()
        req = HttpFirewallRequest(
            host="api.internal.example.com", port=443,
            scheme="https", path="/charge", method="GET",
        )
        assert stack.evaluate(req) == Disposition.ALLOW

    def test_other_internal_denied(self) -> None:
        stack = self._build_stack()
        req = HttpFirewallRequest(
            host="secret.internal.example.com", port=443,
        )
        assert stack.evaluate(req) == Disposition.DENY

    def test_example_com_subdomain_allowed(self) -> None:
        stack = self._build_stack()
        req = HttpFirewallRequest(host="api.example.com", port=443)
        assert stack.evaluate(req) == Disposition.ALLOW

    def test_unknown_host_denied(self) -> None:
        stack = self._build_stack()
        req = HttpFirewallRequest(host="evil.com", port=80)
        assert stack.evaluate(req) == Disposition.DENY

    def test_dns_allowed(self) -> None:
        stack = self._build_stack()
        req = DnsFirewallRequest(hostname="anything.com")
        assert stack.evaluate(req) == Disposition.ALLOW

    def test_redis_localhost_allowed(self) -> None:
        stack = self._build_stack()
        req = RedisFirewallRequest(host="localhost", port=6379)
        assert stack.evaluate(req) == Disposition.ALLOW

    def test_redis_remote_denied(self) -> None:
        stack = self._build_stack()
        req = RedisFirewallRequest(host="redis.prod.internal", port=6379)
        assert stack.evaluate(req) == Disposition.DENY
