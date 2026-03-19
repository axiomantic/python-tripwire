"""Save a user to a PostgreSQL database via psycopg2."""

import psycopg2


def save_user(name: str, email: str) -> None:
    """Insert a user into the users table."""
    conn = psycopg2.connect(host="localhost", dbname="app", user="app")
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (name, email) VALUES (%s, %s)", (name, email)
    )
    conn.commit()
    conn.close()
