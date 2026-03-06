"""Async API that fetches data from an external service and stores in PostgreSQL."""

import logging

import aiohttp
import asyncpg

logger = logging.getLogger("sync_api")


async def sync_user_data(user_id: int, db_url: str, api_url: str) -> dict:
    """Fetch user data from API, store in database, return the record."""
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{api_url}/users/{user_id}") as resp:
            data = await resp.json()

    logger.info(f"Fetched user {user_id} from API")

    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute(
            "INSERT INTO users (id, name, email) VALUES ($1, $2, $3)",
            data["id"], data["name"], data["email"],
        )
        row = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
    finally:
        await conn.close()

    return dict(row)
