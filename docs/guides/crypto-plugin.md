# CryptoPlugin Guide

`CryptoPlugin` intercepts `cryptography.fernet.Fernet.encrypt`, `Fernet.decrypt`, and `cryptography.hazmat.primitives.asymmetric.rsa.generate_private_key` at the class/module level. It uses a per-operation FIFO queue. For security, actual plaintext, keys, and signatures are not stored in interaction details; only metadata (lengths, algorithm names, key sizes) is recorded.

## Installation

```bash
pip install python-tripwire[crypto]
```

This installs `cryptography`.

## Setup

In pytest, access `CryptoPlugin` through the `tripwire.crypto` proxy. It auto-creates the plugin for the current test on first use:

```python
import tripwire

def test_encrypt_payload():
    tripwire.crypto.mock_encrypt(returns=b"gAAAAABencrypted...")

    with tripwire:
        from cryptography.fernet import Fernet
        f = Fernet(b"test-key-base64-encoded-padding=")
        ciphertext = f.encrypt(b"sensitive data")

    assert ciphertext == b"gAAAAABencrypted..."

    tripwire.crypto.assert_encrypt(plaintext_length=14)
```

For manual use outside pytest, construct `CryptoPlugin` explicitly:

```python
from tripwire import StrictVerifier
from tripwire.plugins.crypto_plugin import CryptoPlugin

verifier = StrictVerifier()
crypto = CryptoPlugin(verifier)
```

Each verifier may have at most one `CryptoPlugin`. A second `CryptoPlugin(verifier)` raises `ValueError`.

## Registering mock operations

`CryptoPlugin` provides three mock methods, one for each intercepted function.

### `mock_encrypt(*, returns, ...)`

Register a mock for `Fernet.encrypt()`:

```python
tripwire.crypto.mock_encrypt(returns=b"gAAAAABencrypted_token")
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `returns` | `Any` | required | Ciphertext bytes to return |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

### `mock_decrypt(*, returns, ...)`

Register a mock for `Fernet.decrypt()`:

```python
tripwire.crypto.mock_decrypt(returns=b"decrypted plaintext")
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `returns` | `Any` | required | Plaintext bytes to return |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

### `mock_generate_key(*, returns, ...)`

Register a mock for `rsa.generate_private_key()`:

```python
tripwire.crypto.mock_generate_key(returns=mock_private_key)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `returns` | `Any` | required | Private key object to return |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

## Per-operation FIFO queues

Each operation (`fernet_encrypt`, `fernet_decrypt`, `generate_key`) has its own independent FIFO queue. Multiple mocks are consumed in registration order:

```python
def test_encrypt_multiple_fields():
    tripwire.crypto.mock_encrypt(returns=b"encrypted_email")
    tripwire.crypto.mock_encrypt(returns=b"encrypted_ssn")

    with tripwire:
        from cryptography.fernet import Fernet
        f = Fernet(b"test-key-base64-encoded-padding=")
        ct1 = f.encrypt(b"alice@example.com")
        ct2 = f.encrypt(b"123-45-6789")

    assert ct1 == b"encrypted_email"
    assert ct2 == b"encrypted_ssn"

    tripwire.crypto.assert_encrypt(plaintext_length=17)
    tripwire.crypto.assert_encrypt(plaintext_length=11)
```

## Asserting interactions

Use the typed assertion helpers on `tripwire.crypto`.

### `assert_encrypt(*, plaintext_length)`

Asserts the next `Fernet.encrypt()` interaction. Only the plaintext length is recorded, not the actual data.

```python
tripwire.crypto.assert_encrypt(plaintext_length=14)
```

| Parameter | Type | Description |
|---|---|---|
| `plaintext_length` | `int` | Length of the plaintext data passed to `encrypt()` |

### `assert_decrypt(*, token, ttl=None)`

Asserts the next `Fernet.decrypt()` interaction. The token (ciphertext) is safe to record since it is not secret.

```python
tripwire.crypto.assert_decrypt(token=b"gAAAAABencrypted_token", ttl=None)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `token` | `bytes \| str` | required | The ciphertext token passed to `decrypt()` |
| `ttl` | `int \| None` | `None` | The TTL parameter passed to `decrypt()` |

### `assert_generate_key(*, algorithm, key_size)`

Asserts the next `rsa.generate_private_key()` interaction.

```python
tripwire.crypto.assert_generate_key(algorithm="RSA", key_size=2048)
```

| Parameter | Type | Description |
|---|---|---|
| `algorithm` | `str` | Algorithm name (always `"RSA"` for this interceptor) |
| `key_size` | `int` | The key size in bits (e.g., 2048, 4096) |

## Security note

`CryptoPlugin` intentionally avoids storing sensitive data in interaction details:

- **`Fernet.encrypt()`**: Only the `plaintext_length` is recorded, not the actual plaintext.
- **`Fernet.decrypt()`**: The `token` (ciphertext) is stored because it is not secret. The decrypted result is not stored.
- **`rsa.generate_private_key()`**: Only `algorithm` and `key_size` metadata is stored, not the actual key material.

## Simulating errors

Use the `raises` parameter to simulate cryptography errors:

```python
from cryptography.fernet import InvalidToken
import tripwire

def test_invalid_token():
    tripwire.crypto.mock_decrypt(
        returns=None,
        raises=InvalidToken(),
    )

    with tripwire:
        from cryptography.fernet import Fernet
        f = Fernet(b"test-key-base64-encoded-padding=")
        with pytest.raises(InvalidToken):
            f.decrypt(b"corrupted_ciphertext")

    tripwire.crypto.assert_decrypt(token=b"corrupted_ciphertext", ttl=None)
```

## Full example

**Production code** (`examples/crypto_sign/app.py`):

```python
--8<-- "examples/crypto_sign/app.py"
```

**Test** (`examples/crypto_sign/test_app.py`):

```python
--8<-- "examples/crypto_sign/test_app.py"
```

## Optional mocks

Mark a mock as optional with `required=False`:

```python
tripwire.crypto.mock_encrypt(returns=b"optional_ct", required=False)
```

An optional mock that is never triggered does not cause `UnusedMocksError` at teardown.

## UnmockedInteractionError

When code calls an intercepted cryptography function with no remaining mocks in its queue, tripwire raises `UnmockedInteractionError`:

```
crypto.fernet_encrypt(...) was called but no mock was registered.
Register a mock with:
    tripwire.crypto.mock_encrypt(returns=...)
```
