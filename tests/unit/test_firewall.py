"""Tests for bigfoot._firewall -- FirewallStack evaluation."""

from bigfoot._firewall import Disposition, FirewallRule, FirewallStack, RestrictFrame
from bigfoot._firewall_request import (
    HttpFirewallRequest,
    RedisFirewallRequest,
)
from bigfoot._match import M


class TestFirewallStackEvaluate:
    def test_empty_stack_denies(self) -> None:
        stack = FirewallStack()
        req = HttpFirewallRequest(host="example.com", port=80)
        assert stack.evaluate(req) == Disposition.DENY

    def test_single_allow_matches(self) -> None:
        rule = FirewallRule(pattern=M(protocol="http"), disposition=Disposition.ALLOW)
        stack = FirewallStack((rule,))
        req = HttpFirewallRequest(host="example.com", port=80)
        assert stack.evaluate(req) == Disposition.ALLOW

    def test_single_allow_no_match(self) -> None:
        rule = FirewallRule(pattern=M(protocol="redis"), disposition=Disposition.ALLOW)
        stack = FirewallStack((rule,))
        req = HttpFirewallRequest(host="example.com", port=80)
        assert stack.evaluate(req) == Disposition.DENY

    def test_single_deny_matches(self) -> None:
        rule = FirewallRule(pattern=M(protocol="http"), disposition=Disposition.DENY)
        stack = FirewallStack((rule,))
        req = HttpFirewallRequest(host="example.com", port=80)
        assert stack.evaluate(req) == Disposition.DENY

    def test_innermost_rule_wins(self) -> None:
        outer = FirewallRule(pattern=M(protocol="http"), disposition=Disposition.DENY)
        inner = FirewallRule(pattern=M(protocol="http", host="*.example.com"), disposition=Disposition.ALLOW)
        stack = FirewallStack((outer, inner))
        req = HttpFirewallRequest(host="sub.example.com", port=80)
        assert stack.evaluate(req) == Disposition.ALLOW

    def test_push_creates_new_stack(self) -> None:
        base = FirewallStack()
        rule = FirewallRule(pattern=M(protocol="http"), disposition=Disposition.ALLOW)
        new_stack = base.push(rule)
        assert new_stack.frames == (rule,)
        assert base.frames == ()  # immutable

    def test_restrict_blocks_non_matching(self) -> None:
        restrict = RestrictFrame(pattern=M(protocol="http"))
        allow_redis = FirewallRule(pattern=M(protocol="redis"), disposition=Disposition.ALLOW)
        stack = FirewallStack((restrict, allow_redis))
        req = RedisFirewallRequest(host="localhost", port=6379)
        assert stack.evaluate(req) == Disposition.DENY

    def test_restrict_allows_matching_to_continue(self) -> None:
        restrict = RestrictFrame(pattern=M(protocol="http"))
        allow_http = FirewallRule(pattern=M(protocol="http"), disposition=Disposition.ALLOW)
        stack = FirewallStack((restrict, allow_http))
        req = HttpFirewallRequest(host="example.com", port=80)
        assert stack.evaluate(req) == Disposition.ALLOW

    def test_nested_restrict_intersection(self) -> None:
        restrict1 = RestrictFrame(pattern=M(protocol="http"))
        restrict2 = RestrictFrame(pattern=M(host="*.example.com"))
        allow_all_http = FirewallRule(pattern=M(protocol="http"), disposition=Disposition.ALLOW)
        stack = FirewallStack((restrict1, restrict2, allow_all_http))
        # HTTP to example.com: passes both restricts
        req_ok = HttpFirewallRequest(host="sub.example.com", port=80)
        assert stack.evaluate(req_ok) == Disposition.ALLOW
        # HTTP to other.com: fails restrict2
        req_bad = HttpFirewallRequest(host="other.com", port=80)
        assert stack.evaluate(req_bad) == Disposition.DENY

    def test_restrict_cannot_be_bypassed_by_inner_allow(self) -> None:
        """Two-phase algorithm: restrict checked BEFORE rule scan."""
        restrict = RestrictFrame(pattern=M(protocol="http"))
        inner_allow = FirewallRule(pattern=M(protocol="redis"), disposition=Disposition.ALLOW)
        stack = FirewallStack((restrict, inner_allow))
        req = RedisFirewallRequest(host="localhost", port=6379)
        # Inner allow("redis") should NOT override restrict(http)
        assert stack.evaluate(req) == Disposition.DENY
