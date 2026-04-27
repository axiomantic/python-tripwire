# Async API Example

Demonstrates tripwire with async code, combining HTTP, asyncpg, and
logging plugins in a single test.

The application module (`app.py`) fetches user data from an external API
via aiohttp, stores it in PostgreSQL via asyncpg, and logs the operation.
The test (`test_app.py`) mocks all three interaction types and verifies
the complete sequence.

Run: `python -m pytest examples/async_api/ -v`
