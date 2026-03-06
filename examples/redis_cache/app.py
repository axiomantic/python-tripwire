"""Simple Redis-backed cache."""

import json

import redis


def get_user(user_id: int, client: redis.Redis | None = None) -> dict | None:
    """Get user from cache or return None."""
    if client is None:
        client = redis.Redis()
    cached = client.get(f"user:{user_id}")
    if cached is not None:
        return json.loads(cached)
    return None
