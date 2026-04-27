"""Test save_user using tripwire db_mock."""

import tripwire

from .app import save_user


def test_save_user():
    (tripwire.db_mock
        .new_session()
        .expect("connect",  returns=None)
        .expect("execute",  returns=[])
        .expect("commit",   returns=None)
        .expect("close",    returns=None))

    with tripwire:
        save_user("Alice", "alice@example.com")

    tripwire.db_mock.assert_connect(database="app.db")
    tripwire.db_mock.assert_execute(
        sql="INSERT INTO users (name, email) VALUES (?, ?)",
        parameters=("Alice", "alice@example.com"),
    )
    tripwire.db_mock.assert_commit()
    tripwire.db_mock.assert_close()
