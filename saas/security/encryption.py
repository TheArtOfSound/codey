from __future__ import annotations

import base64
import hashlib
import os
import secrets
import string

import bcrypt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from codey.saas.config import settings

# ---------------------------------------------------------------------------
# Key derivation — we derive a 256-bit key from ``settings.secret_key`` via
# a single PBKDF2-HMAC-SHA256 pass so that the raw config value is never used
# directly as cryptographic material.
# ---------------------------------------------------------------------------

_KDF_SALT = b"codey-saas-encryption-v1"  # fixed salt; rotation requires re-encrypt
_KDF_ITERATIONS = 480_000  # OWASP 2023 recommendation for PBKDF2-SHA256


def _derive_key() -> bytes:
    """Derive a 256-bit AES key from the application secret."""
    return hashlib.pbkdf2_hmac(
        "sha256",
        settings.secret_key.encode("utf-8"),
        _KDF_SALT,
        _KDF_ITERATIONS,
    )


# ---------------------------------------------------------------------------
# AES-256-GCM encrypt / decrypt
# ---------------------------------------------------------------------------

_NONCE_BYTES = 12  # 96-bit nonce for AES-GCM (NIST recommended)


def encrypt_token(plaintext: str) -> str:
    """Encrypt *plaintext* with AES-256-GCM and return a URL-safe base64 string.

    The output format is ``base64(nonce ‖ ciphertext ‖ tag)``.
    """
    key = _derive_key()
    nonce = os.urandom(_NONCE_BYTES)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    # ciphertext already includes the 16-byte GCM tag appended by cryptography
    return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def decrypt_token(ciphertext: str) -> str:
    """Decrypt a value produced by :func:`encrypt_token`."""
    key = _derive_key()
    raw = base64.urlsafe_b64decode(ciphertext)
    if len(raw) < _NONCE_BYTES + 16:
        raise ValueError("Ciphertext too short — corrupted or invalid data")
    nonce = raw[:_NONCE_BYTES]
    ct = raw[_NONCE_BYTES:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ct, None).decode("utf-8")


# ---------------------------------------------------------------------------
# API key hashing (bcrypt)
# ---------------------------------------------------------------------------

_BCRYPT_ROUNDS = 12


def hash_api_key(key: str) -> str:
    """Return a bcrypt hash of *key* suitable for database storage."""
    return bcrypt.hashpw(key.encode("utf-8"), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode("ascii")


def verify_api_key(key: str, hashed: str) -> bool:
    """Check *key* against a bcrypt *hashed* value."""
    return bcrypt.checkpw(key.encode("utf-8"), hashed.encode("ascii"))


# ---------------------------------------------------------------------------
# API key generation
# ---------------------------------------------------------------------------

_KEY_PREFIX = "cdy_"
_KEY_LENGTH = 40  # characters of randomness after prefix


def generate_api_key() -> tuple[str, str]:
    """Generate a new API key.

    Returns ``(plaintext_key, hashed_key)``.  The plaintext is shown to the
    user exactly once; only the hash is persisted.
    """
    alphabet = string.ascii_letters + string.digits
    random_part = "".join(secrets.choice(alphabet) for _ in range(_KEY_LENGTH))
    plaintext = f"{_KEY_PREFIX}{random_part}"
    hashed = hash_api_key(plaintext)
    return plaintext, hashed
