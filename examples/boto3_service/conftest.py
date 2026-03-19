"""Disable DnsPlugin for boto3 examples.

boto3.client() internally resolves the EC2 metadata endpoint (169.254.169.254)
for credential discovery. The DnsPlugin would intercept that getaddrinfo call
and raise UnmockedInteractionError.
"""

import pytest


@pytest.fixture(autouse=True)
def _disable_dns_plugin(bigfoot_verifier):
    from bigfoot.plugins.dns_plugin import DnsPlugin

    bigfoot_verifier._plugins = [
        p for p in bigfoot_verifier._plugins if not isinstance(p, DnsPlugin)
    ]
