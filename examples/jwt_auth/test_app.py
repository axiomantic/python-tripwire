"""Test JWT token issuance and verification using bigfoot jwt_mock."""

import pytest

jwt = pytest.importorskip("jwt")

import bigfoot  # noqa: E402

from .app import issue_access_token, verify_access_token  # noqa: E402


def test_issue_and_verify_token():
    bigfoot.jwt_mock.mock_encode(returns="signed.access.token")
    bigfoot.jwt_mock.mock_decode(returns={"sub": "user_42", "role": "editor", "iat": 1700000000})

    with bigfoot:
        token = issue_access_token("user_42", "editor", "my-secret")
        claims = verify_access_token(token, "my-secret")

    assert token == "signed.access.token"
    assert claims["sub"] == "user_42"
    assert claims["role"] == "editor"

    bigfoot.jwt_mock.assert_encode(
        payload={"sub": "user_42", "role": "editor", "iat": 1700000000},
        algorithm="HS256",
        extra_kwargs={},
    )
    bigfoot.jwt_mock.assert_decode(
        token="signed.access.token",
        algorithms=["HS256"],
        options=None,
    )
