"""Tests for TOML firewall rule parsing."""

from bigfoot._firewall_request import (
    HttpFirewallRequest,
    RedisFirewallRequest,
    SubprocessFirewallRequest,
)
from bigfoot.pytest_plugin import _parse_toml_rule


class TestParseTomlRule:
    def test_protocol_wildcard(self) -> None:
        m = _parse_toml_rule("dns:*")
        from bigfoot._firewall_request import DnsFirewallRequest
        req = DnsFirewallRequest(hostname="example.com")
        assert m.matches(req) is True

    def test_subprocess_binary(self) -> None:
        m = _parse_toml_rule("subprocess:git")
        req = SubprocessFirewallRequest(command="git status", binary="git")
        assert m.matches(req) is True

    def test_http_url(self) -> None:
        m = _parse_toml_rule("http://*.example.com")
        req = HttpFirewallRequest(host="sub.example.com", port=80)
        assert m.matches(req) is True

    def test_https_url(self) -> None:
        m = _parse_toml_rule("https://api.stripe.com/v1/charges")
        req = HttpFirewallRequest(host="api.stripe.com", port=443, path="/v1/charges", scheme="https")
        assert m.matches(req) is True

    def test_redis_url_with_db(self) -> None:
        m = _parse_toml_rule("redis://localhost:6379/0")
        req = RedisFirewallRequest(host="localhost", port=6379, db=0)
        assert m.matches(req) is True

    def test_boto3_service_operation(self) -> None:
        m = _parse_toml_rule("boto3:s3:GetObject")
        from bigfoot._firewall_request import Boto3FirewallRequest
        req = Boto3FirewallRequest(service="s3", operation="GetObject")
        assert m.matches(req) is True

    def test_memcache_command(self) -> None:
        m = _parse_toml_rule("memcache:get")
        from bigfoot._firewall_request import MemcacheFirewallRequest
        req = MemcacheFirewallRequest(host="localhost", port=11211, command="get")
        assert m.matches(req) is True

    def test_file_io_path(self) -> None:
        m = _parse_toml_rule("file_io:/tmp/**")
        from bigfoot._firewall_request import FileIoFirewallRequest
        req = FileIoFirewallRequest(path="/tmp/foo/bar")
        assert m.matches(req) is True
