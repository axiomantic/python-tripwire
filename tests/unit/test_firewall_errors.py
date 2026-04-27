"""Tests for GuardedCallError message generation with firewall requests."""

from tripwire._errors import GuardedCallError
from tripwire._firewall_request import (
    HttpFirewallRequest,
    RedisFirewallRequest,
    SubprocessFirewallRequest,
)


class TestGuardedCallErrorMessages:
    def test_http_error_message(self) -> None:
        req = HttpFirewallRequest(
            host="api.stripe.com", port=443, scheme="https",
            path="/v1/charges", method="GET",
        )
        err = GuardedCallError("http:request", "http", firewall_request=req)
        msg = str(err)
        assert "GET https://api.stripe.com:443/v1/charges" in msg
        assert "@pytest.mark.allow" in msg
        assert "tripwire.allow" in msg
        assert "[tool.tripwire.firewall]" in msg

    def test_redis_error_message(self) -> None:
        req = RedisFirewallRequest(host="localhost", port=6379, db=0, command="FLUSHALL")
        err = GuardedCallError("redis:command", "redis", firewall_request=req)
        msg = str(err)
        assert "redis://localhost:6379/0 FLUSHALL" in msg

    def test_subprocess_error_message(self) -> None:
        req = SubprocessFirewallRequest(command="rm -rf /tmp/important", binary="rm")
        err = GuardedCallError("subprocess:run", "subprocess", firewall_request=req)
        msg = str(err)
        assert "subprocess: rm -rf /tmp/important" in msg

    def test_no_request_fallback(self) -> None:
        err = GuardedCallError("http:request", "http", firewall_request=None)
        msg = str(err)
        assert "@pytest.mark.allow" in msg
        assert '"http"' in msg

    def test_old_signature_still_works(self) -> None:
        """Backward compat: firewall_request defaults to None."""
        err = GuardedCallError("http:request", "http")
        msg = str(err)
        assert "blocked by tripwire firewall" in msg
