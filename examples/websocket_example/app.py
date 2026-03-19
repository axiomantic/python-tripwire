"""Chat client that sends messages over a WebSocket."""

import websocket


def chat_client(uri: str, messages: list[str]) -> list[str]:
    """Send messages to a WebSocket server and collect responses."""
    ws = websocket.create_connection(uri)
    responses = []
    for msg in messages:
        ws.send(msg)
        responses.append(ws.recv())
    ws.close()
    return responses
