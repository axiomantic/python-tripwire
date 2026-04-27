"""Test Redis cache using tripwire redis_mock."""

import tripwire
from dirty_equals import IsInstance

from .app import get_user


def test_get_user_cache_hit():
    tripwire.redis_mock.mock_command(
        "GET", returns=b'{"id": 1, "name": "Alice"}'
    )

    with tripwire:
        result = get_user(1)

    assert result == {"id": 1, "name": "Alice"}
    tripwire.redis_mock.assert_command("GET", args=("user:1",), kwargs=IsInstance(dict))


def test_get_user_cache_miss():
    tripwire.redis_mock.mock_command("GET", returns=None)

    with tripwire:
        result = get_user(42)

    assert result is None
    tripwire.redis_mock.assert_command("GET", args=("user:42",), kwargs=IsInstance(dict))
