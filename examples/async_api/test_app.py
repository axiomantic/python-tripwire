"""Test async API using tripwire HTTP, asyncpg, and logging plugins."""

import pytest

import tripwire

from .app import sync_user_data


@pytest.mark.asyncio
async def test_sync_user_data_fetches_and_stores():
    # Mock the external API response
    tripwire.http.mock_response(
        "GET",
        "https://api.example.com/users/1",
        json={"id": 1, "name": "Alice", "email": "alice@example.com"},
    )

    # Script the asyncpg session
    tripwire.asyncpg_mock.new_session() \
        .expect("connect", returns=None) \
        .expect("execute", returns="INSERT 0 1") \
        .expect("fetchrow", returns={"id": 1, "name": "Alice", "email": "alice@example.com"}) \
        .expect("close", returns=None)

    async with tripwire:
        result = await sync_user_data(
            user_id=1,
            db_url="postgresql://localhost/app",
            api_url="https://api.example.com",
        )

    assert result == {"id": 1, "name": "Alice", "email": "alice@example.com"}

    # Assert the HTTP request and response
    tripwire.http.assert_request(
        method="GET",
        url="https://api.example.com/users/1",
    ).assert_response(
        status=200,
        headers={"content-type": "application/json"},
        body='{"id": 1, "name": "Alice", "email": "alice@example.com"}',
    )

    # Assert the log message
    tripwire.log_mock.assert_info("Fetched user 1 from API", "sync_api")

    # Assert the database interactions
    tripwire.asyncpg_mock.assert_connect(dsn="postgresql://localhost/app")
    tripwire.asyncpg_mock.assert_execute(
        query="INSERT INTO users (id, name, email) VALUES ($1, $2, $3)",
        args=[1, "Alice", "alice@example.com"],
    )
    tripwire.asyncpg_mock.assert_fetchrow(
        query="SELECT * FROM users WHERE id = $1",
        args=[1],
    )
    tripwire.asyncpg_mock.assert_close()
