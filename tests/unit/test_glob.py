"""Tests for tripwire._glob -- custom glob matching."""

from tripwire._glob import tripwire_match


class TestHostGlob:
    def test_subdomain_match(self) -> None:
        assert tripwire_match("*.example.com", "sub.example.com") is True

    def test_deep_subdomain_match(self) -> None:
        assert tripwire_match("*.example.com", "deep.sub.example.com") is True

    def test_bare_domain_no_match(self) -> None:
        assert tripwire_match("*.example.com", "example.com") is False

    def test_evil_hyphen_no_match(self) -> None:
        """Security-critical: *.example.com must NOT match evil-example.com."""
        assert tripwire_match("*.example.com", "evil-example.com") is False

    def test_case_insensitive_host(self) -> None:
        assert tripwire_match("*.Example.COM", "sub.example.com", case_sensitive=False) is True


class TestPathGlob:
    def test_double_star_deep_match(self) -> None:
        assert tripwire_match("/api/**", "/api/v1/users") is True

    def test_double_star_deeper(self) -> None:
        assert tripwire_match("/api/**", "/api/v2/items/123") is True

    def test_single_star_no_deep(self) -> None:
        assert tripwire_match("/api/*", "/api/v1/users") is False

    def test_single_star_one_segment(self) -> None:
        assert tripwire_match("/api/*", "/api/v1") is True


class TestExactMatch:
    def test_no_wildcards_exact(self) -> None:
        assert tripwire_match("hello", "hello") is True

    def test_no_wildcards_mismatch(self) -> None:
        assert tripwire_match("hello", "world") is False
