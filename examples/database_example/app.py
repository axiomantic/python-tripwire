"""Save a user to a SQLite database."""

import sqlite3


def save_user(name: str, email: str) -> None:
    """Insert a user into the users table."""
    conn = sqlite3.connect("app.db")
    conn.execute(
        "INSERT INTO users (name, email) VALUES (?, ?)", (name, email)
    )
    conn.commit()
    conn.close()
