"""User profile caching with memcache."""



def get_user_profile(client, user_id):
    """Fetch user profile from memcache, return None on miss."""
    cached = client.get(f"profile:{user_id}")
    if cached is not None:
        return cached.decode("utf-8")
    return None


def cache_user_profile(client, user_id, profile_json, ttl=300):
    """Store user profile in memcache with TTL."""
    client.set(f"profile:{user_id}", profile_json.encode("utf-8"), expire=ttl)
