"""Test DNS service resolution using bigfoot dns_mock."""

import socket

import bigfoot

from .app import resolve_service_endpoint


def test_resolve_service_endpoint():
    bigfoot.dns_mock.mock_getaddrinfo(
        "payments.internal",
        returns=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.2.15", 443)),
        ],
    )

    with bigfoot:
        addr = resolve_service_endpoint("payments.internal")

    assert addr == ("10.0.2.15", 443)

    bigfoot.dns_mock.assert_getaddrinfo(
        host="payments.internal",
        port=443,
        family=socket.AF_INET,
        type=socket.SOCK_STREAM,
        proto=0,
    )
