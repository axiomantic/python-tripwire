"""Test fetch_status using bigfoot socket_mock."""

import bigfoot

from .app import fetch_status


def test_fetch_status():
    (bigfoot.socket_mock
        .new_session()
        .expect("connect",  returns=None)
        .expect("sendall",  returns=None)
        .expect("recv",     returns=b"OK 200\r\n")
        .expect("close",    returns=None))

    with bigfoot:
        result = fetch_status("monitoring.internal", 5000)

    assert result == "OK 200\r\n"

    bigfoot.socket_mock.assert_connect(host="monitoring.internal", port=5000)
    bigfoot.socket_mock.assert_sendall(data=b"STATUS\r\n")
    bigfoot.socket_mock.assert_recv(size=4096, data=b"OK 200\r\n")
    bigfoot.socket_mock.assert_close()
