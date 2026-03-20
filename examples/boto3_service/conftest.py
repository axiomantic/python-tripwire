"""Allow DNS and Socket calls for boto3 examples via guard mode.

boto3.client() internally resolves the EC2 metadata endpoint (169.254.169.254)
for credential discovery and opens socket connections. The guard mode allowlist
permits these calls without assertion tracking.
"""

import pytest

pytestmark = pytest.mark.allow("dns", "socket")
