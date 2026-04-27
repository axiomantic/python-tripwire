"""Test chat_client using tripwire sync_websocket_mock."""

import tripwire

from .app import chat_client


def test_chat_client():
    (tripwire.sync_websocket_mock
        .new_session()
        .expect("connect", returns=None)
        .expect("send",    returns=None)
        .expect("recv",    returns="echo: hello")
        .expect("send",    returns=None)
        .expect("recv",    returns="echo: world")
        .expect("close",   returns=None))

    with tripwire:
        responses = chat_client("ws://chat.example.com/ws", ["hello", "world"])

    assert responses == ["echo: hello", "echo: world"]

    tripwire.sync_websocket_mock.assert_connect(uri="ws://chat.example.com/ws")
    tripwire.sync_websocket_mock.assert_send(message="hello")
    tripwire.sync_websocket_mock.assert_recv(message="echo: hello")
    tripwire.sync_websocket_mock.assert_send(message="world")
    tripwire.sync_websocket_mock.assert_recv(message="echo: world")
    tripwire.sync_websocket_mock.assert_close()
