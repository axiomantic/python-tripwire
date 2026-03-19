"""Fetch user count from a PostgreSQL database via asyncpg."""

import asyncpg


async def get_user_count() -> int:
    """Return the number of users in the database."""
    conn = await asyncpg.connect(host="localhost", database="app", user="app")
    count = await conn.fetchval("SELECT count(*) FROM users")
    await conn.close()
    return count
