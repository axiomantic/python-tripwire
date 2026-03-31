"""Test async API using bigfoot HTTP, asyncpg, and logging plugins."""

import pytest

import bigfoot

from .app import sync_user_data


@pytest.mark.asyncio
async def test_sync_user_data_fetches_and_stores():
    # Mock the external API response
    bigfoot.http.mock_response(
        "GET",
        "https://api.example.com/users/1",
        json={"id": 1, "name": "Alice", "email": "alice@example.com"},
    )

    # Script the asyncpg session
    bigfoot.asyncpg_mock.new_session() \
        .expect("connect", returns=None) \
        .expect("execute", returns="INSERT 0 1") \
        .expect("fetchrow", returns={"id": 1, "name": "Alice", "email": "alice@example.com"}) \
        .expect("close", returns=None)

    async with bigfoot:
        result = await sync_user_data(
            user_id=1,
            db_url="postgresql://localhost/app",
            api_url="https://api.example.com",
        )

    assert result == {"id": 1, "name": "Alice", "email": "alice@example.com"}

    # Assert the HTTP request and response
    bigfoot.http.assert_request(
        method="GET",
        url="https://api.example.com/users/1",
    ).assert_response(
        status=200,
        headers={"content-type": "application/json"},
        body='{"id": 1, "name": "Alice", "email": "alice@example.com"}',
    )

    # Assert the log message
    bigfoot.log_mock.assert_info("Fetched user 1 from API", "sync_api")

    # Assert the database interactions
    bigfoot.asyncpg_mock.assert_connect(dsn="postgresql://localhost/app")
    bigfoot.asyncpg_mock.assert_execute(
        query="INSERT INTO users (id, name, email) VALUES ($1, $2, $3)",
        args=[1, "Alice", "alice@example.com"],
    )
    bigfoot.asyncpg_mock.assert_fetchrow(
        query="SELECT * FROM users WHERE id = $1",
        args=[1],
    )
    bigfoot.asyncpg_mock.assert_close()
