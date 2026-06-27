from __future__ import annotations

from cryptography.fernet import Fernet


def encrypt_secret(value: str, fernet_key: str) -> bytes:
    """Encrypt a secret string using Fernet."""
    return Fernet(fernet_key.encode("utf-8")).encrypt(value.encode("utf-8"))


def decrypt_secret(value: bytes, fernet_key: str) -> str:
    """Decrypt a Fernet-encrypted secret string."""
    return Fernet(fernet_key.encode("utf-8")).decrypt(value).decode("utf-8")
