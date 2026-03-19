"""Test save_user using bigfoot db_mock."""

import bigfoot

from .app import save_user


def test_save_user():
    (bigfoot.db_mock
        .new_session()
        .expect("connect",  returns=None)
        .expect("execute",  returns=[])
        .expect("commit",   returns=None)
        .expect("close",    returns=None))

    with bigfoot:
        save_user("Alice", "alice@example.com")

    bigfoot.db_mock.assert_connect(database="app.db")
    bigfoot.db_mock.assert_execute(
        sql="INSERT INTO users (name, email) VALUES (?, ?)",
        parameters=("Alice", "alice@example.com"),
    )
    bigfoot.db_mock.assert_commit()
    bigfoot.db_mock.assert_close()
