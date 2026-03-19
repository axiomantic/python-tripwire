"""Fetch status from a monitoring service via raw socket."""

import socket


def fetch_status(host: str, port: int) -> str:
    """Connect to a monitoring service and return the status response."""
    sock = socket.socket()
    sock.connect((host, port))
    sock.sendall(b"STATUS\r\n")
    response = sock.recv(4096)
    sock.close()
    return response.decode()
