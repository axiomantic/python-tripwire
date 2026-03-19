"""Disable DNS and Socket plugins for boto3 examples.

boto3.client() internally resolves the EC2 metadata endpoint (169.254.169.254)
for credential discovery and opens socket connections. The DnsPlugin and
SocketPlugin would intercept those calls and raise UnmockedInteractionError.
"""

import pytest


@pytest.fixture(autouse=True)
def _disable_network_plugins(bigfoot_verifier):
    from bigfoot.plugins.dns_plugin import DnsPlugin
    from bigfoot.plugins.socket_plugin import SocketPlugin

    bigfoot_verifier._plugins = [
        p for p in bigfoot_verifier._plugins
        if not isinstance(p, (DnsPlugin, SocketPlugin))
    ]
