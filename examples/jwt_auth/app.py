"""JWT token issuance and verification."""

import jwt


def issue_access_token(user_id, role, secret_key):
    """Issue a signed JWT access token."""
    payload = {
        "sub": user_id,
        "role": role,
        "iat": 1700000000,
    }
    return jwt.encode(payload, secret_key, algorithm="HS256")


def verify_access_token(token, secret_key):
    """Verify and decode a JWT access token."""
    return jwt.decode(token, secret_key, algorithms=["HS256"])
