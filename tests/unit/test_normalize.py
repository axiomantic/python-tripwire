"""Tests for tripwire._normalize -- URL/host/path normalization."""

from tripwire._normalize import normalize_host, normalize_path, normalize_url


class TestNormalizeHost:
    def test_lowercase(self) -> None:
        assert normalize_host("API.Example.COM") == "api.example.com"

    def test_strip_ipv6_brackets(self) -> None:
        assert normalize_host("[::1]") == "localhost"

    def test_localhost_aliases_127(self) -> None:
        assert normalize_host("127.0.0.1") == "localhost"

    def test_localhost_aliases_ipv6(self) -> None:
        assert normalize_host("::1") == "localhost"

    def test_localhost_aliases_zero(self) -> None:
        assert normalize_host("0.0.0.0") == "localhost"

    def test_localhost_literal(self) -> None:
        assert normalize_host("localhost") == "localhost"

    def test_regular_host(self) -> None:
        assert normalize_host("api.stripe.com") == "api.stripe.com"

    def test_ip_normalization(self) -> None:
        assert normalize_host("10.0.0.1") == "10.0.0.1"

    def test_whitespace_strip(self) -> None:
        assert normalize_host("  api.example.com  ") == "api.example.com"


class TestNormalizePath:
    def test_decode_percent_encoding(self) -> None:
        assert normalize_path("/api/%2e%2e/secret") == "/secret"

    def test_resolve_dotdot(self) -> None:
        assert normalize_path("/api/../secret") == "/secret"

    def test_resolve_dot(self) -> None:
        assert normalize_path("/api/./v1") == "/api/v1"

    def test_collapse_double_slashes(self) -> None:
        assert normalize_path("/api//v1") == "/api/v1"

    def test_strip_trailing_slash(self) -> None:
        assert normalize_path("/api/v1/") == "/api/v1"

    def test_root_preserved(self) -> None:
        assert normalize_path("/") == "/"

    def test_add_leading_slash(self) -> None:
        assert normalize_path("api/v1") == "/api/v1"


class TestNormalizeUrl:
    def test_http_default_port(self) -> None:
        scheme, host, port, path = normalize_url("http://example.com/api")
        assert scheme == "http"
        assert host == "example.com"
        assert port == 80
        assert path == "/api"

    def test_https_default_port(self) -> None:
        scheme, host, port, path = normalize_url("https://api.stripe.com/v1/charges")
        assert scheme == "https"
        assert host == "api.stripe.com"
        assert port == 443
        assert path == "/v1/charges"

    def test_explicit_port(self) -> None:
        scheme, host, port, path = normalize_url("http://localhost:8080/api")
        assert scheme == "http"
        assert host == "localhost"
        assert port == 8080
        assert path == "/api"

    def test_redis_url(self) -> None:
        scheme, host, port, path = normalize_url("redis://localhost:6379/0")
        assert scheme == "redis"
        assert host == "localhost"
        assert port == 6379
        assert path == "/0"

    def test_localhost_normalization_in_url(self) -> None:
        scheme, host, port, path = normalize_url("http://127.0.0.1:8080/api")
        assert scheme == "http"
        assert host == "localhost"
        assert port == 8080
        assert path == "/api"
