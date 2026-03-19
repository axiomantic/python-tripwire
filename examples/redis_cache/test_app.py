"""Test Redis cache using bigfoot redis_mock."""

import bigfoot

from .app import get_user


def test_get_user_cache_hit():
    bigfoot.redis_mock.mock_command(
        "GET", returns=b'{"id": 1, "name": "Alice"}'
    )

    with bigfoot:
        result = get_user(1)

    assert result == {"id": 1, "name": "Alice"}
    bigfoot.redis_mock.assert_command("GET", args=("user:1",), kwargs={"keys": ["user:1"]})


def test_get_user_cache_miss():
    bigfoot.redis_mock.mock_command("GET", returns=None)

    with bigfoot:
        result = get_user(42)

    assert result is None
    bigfoot.redis_mock.assert_command("GET", args=("user:42",), kwargs={"keys": ["user:42"]})
