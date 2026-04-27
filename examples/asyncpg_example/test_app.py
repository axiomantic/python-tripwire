"""Test get_user_count using tripwire asyncpg_mock."""

import tripwire

from .app import get_user_count


async def test_get_user_count():
    (tripwire.asyncpg_mock
        .new_session()
        .expect("connect",  returns=None)
        .expect("fetchval", returns=42)
        .expect("close",    returns=None))

    with tripwire:
        result = await get_user_count()

    assert result == 42

    tripwire.asyncpg_mock.assert_connect(host="localhost", database="app", user="app")
    tripwire.asyncpg_mock.assert_fetchval(query="SELECT count(*) FROM users", args=[])
    tripwire.asyncpg_mock.assert_close()
