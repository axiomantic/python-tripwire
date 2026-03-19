"""Test save_user using bigfoot psycopg2_mock."""

import bigfoot

from .app import save_user


def test_save_user():
    (bigfoot.psycopg2_mock
        .new_session()
        .expect("connect",  returns=None)
        .expect("execute",  returns=[])
        .expect("commit",   returns=None)
        .expect("close",    returns=None))

    with bigfoot:
        save_user("Alice", "alice@example.com")

    bigfoot.psycopg2_mock.assert_connect(host="localhost", dbname="app", user="app")
    bigfoot.psycopg2_mock.assert_execute(
        sql="INSERT INTO users (name, email) VALUES (%s, %s)",
        parameters=("Alice", "alice@example.com"),
    )
    bigfoot.psycopg2_mock.assert_commit()
    bigfoot.psycopg2_mock.assert_close()
