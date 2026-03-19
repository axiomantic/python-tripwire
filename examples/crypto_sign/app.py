"""PII field encryption and decryption with Fernet."""

from cryptography.fernet import Fernet


def encrypt_pii_field(key, value):
    """Encrypt a PII field before storing in the database."""
    f = Fernet(key)
    return f.encrypt(value.encode("utf-8"))


def decrypt_pii_field(key, ciphertext):
    """Decrypt a PII field retrieved from the database."""
    f = Fernet(key)
    return f.decrypt(ciphertext).decode("utf-8")
