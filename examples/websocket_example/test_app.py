"""Test chat_client using bigfoot sync_websocket_mock."""

import pytest

pytest.importorskip("websocket")

import bigfoot  # noqa: E402

from .app import chat_client  # noqa: E402


def test_chat_client():
    (bigfoot.sync_websocket_mock
        .new_session()
        .expect("connect", returns=None)
        .expect("send",    returns=None)
        .expect("recv",    returns="echo: hello")
        .expect("send",    returns=None)
        .expect("recv",    returns="echo: world")
        .expect("close",   returns=None))

    with bigfoot:
        responses = chat_client("ws://chat.example.com/ws", ["hello", "world"])

    assert responses == ["echo: hello", "echo: world"]

    bigfoot.sync_websocket_mock.assert_connect(uri="ws://chat.example.com/ws")
    bigfoot.sync_websocket_mock.assert_send(message="hello")
    bigfoot.sync_websocket_mock.assert_recv(message="echo: hello")
    bigfoot.sync_websocket_mock.assert_send(message="world")
    bigfoot.sync_websocket_mock.assert_recv(message="echo: world")
    bigfoot.sync_websocket_mock.assert_close()
