"""Test PII encryption and decryption using tripwire crypto_mock."""

from cryptography.fernet import Fernet

import tripwire

from .app import decrypt_pii_field, encrypt_pii_field

# Generate a valid Fernet key for the example
TEST_KEY = Fernet.generate_key()


def test_encrypt_pii():
    tripwire.crypto_mock.mock_encrypt(returns=b"gAAAAABencrypted_ssn")

    with tripwire:
        ciphertext = encrypt_pii_field(TEST_KEY, "123-45-6789")

    assert ciphertext == b"gAAAAABencrypted_ssn"

    tripwire.crypto_mock.assert_encrypt(plaintext_length=11)


def test_decrypt_pii():
    tripwire.crypto_mock.mock_decrypt(returns=b"123-45-6789")

    with tripwire:
        plaintext = decrypt_pii_field(TEST_KEY, b"gAAAAABencrypted_ssn")

    assert plaintext == "123-45-6789"

    tripwire.crypto_mock.assert_decrypt(token=b"gAAAAABencrypted_ssn", ttl=None)
