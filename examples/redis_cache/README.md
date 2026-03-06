# Redis Cache Example

Demonstrates bigfoot's Redis plugin for mocking Redis commands.

The application module (`app.py`) reads from a Redis cache. The test
(`test_app.py`) uses `bigfoot.redis_mock` to mock `GET` commands and
verify the exact key lookups, covering both cache hit and cache miss
scenarios.

Run: `python -m pytest examples/redis_cache/ -v`
