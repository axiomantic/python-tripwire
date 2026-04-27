"""Test memcache user profile caching using tripwire memcache_mock."""

import tripwire

from .app import cache_user_profile, get_user_profile


def test_cache_hit():
    tripwire.memcache_mock.mock_command("GET", returns=b'{"name": "Alice"}')

    with tripwire:
        from pymemcache.client.base import Client
        client = Client(("localhost", 11211))
        result = get_user_profile(client, "42")

    assert result == '{"name": "Alice"}'

    tripwire.memcache_mock.assert_get(command="GET", key="profile:42")


def test_cache_write():
    tripwire.memcache_mock.mock_command("SET", returns=True)

    with tripwire:
        from pymemcache.client.base import Client
        client = Client(("localhost", 11211))
        cache_user_profile(client, "42", '{"name": "Alice"}', ttl=600)

    tripwire.memcache_mock.assert_set(
        command="SET",
        key="profile:42",
        value=b'{"name": "Alice"}',
        expire=600,
    )
