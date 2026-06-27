from __future__ import annotations

from dataclasses import dataclass
import re

from shared.crypto import decrypt_secret, encrypt_secret


@dataclass(frozen=True)
class ParsedApiCredentials:
    api_key: str
    api_secret: str


class ApiCredentialFormatError(ValueError):
    """Raised when the user API credential message cannot be parsed."""


_LABELED_VALUE = re.compile(r"^(api_key|api_secret)\s*:\s*(.*)$", re.IGNORECASE)


def parse_api_credentials(text: str) -> ParsedApiCredentials:
    """Parse API credentials from either two lines or key-labeled text."""
    if not isinstance(text, str):
        raise ApiCredentialFormatError("credential message must be text")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 2:
        raise ApiCredentialFormatError("exactly two credential values are required")

    matches = [_LABELED_VALUE.fullmatch(line) for line in lines]
    if any(match is not None for match in matches):
        if not all(match is not None for match in matches):
            raise ApiCredentialFormatError("both credentials must use labels")
        values: dict[str, str] = {}
        for match in matches:
            assert match is not None
            label = match.group(1).lower()
            value = match.group(2).strip()
            if label in values or not value:
                raise ApiCredentialFormatError("credential labels must be unique and non-empty")
            values[label] = value
        if set(values) != {"api_key", "api_secret"}:
            raise ApiCredentialFormatError("api_key and api_secret are required")
        return ParsedApiCredentials(
            api_key=values["api_key"],
            api_secret=values["api_secret"],
        )

    api_key, api_secret = lines
    if not api_key or not api_secret:
        raise ApiCredentialFormatError("credential values must not be empty")
    return ParsedApiCredentials(api_key=api_key, api_secret=api_secret)


def redact_secret(value: str, *, visible: int = 4) -> str:
    """Redact a secret for safe logs/messages."""
    if visible < 0:
        raise ValueError("visible must be non-negative")
    if not value:
        return ""
    if visible == 0 or len(value) <= visible * 2:
        return "*" * len(value)
    return f"{value[:visible]}{'*' * (len(value) - visible * 2)}{value[-visible:]}"


def encrypt_api_credentials(
    *,
    api_key: str,
    api_secret: str,
    fernet_key: str,
) -> tuple[bytes, bytes]:
    """Encrypt API key and secret for DB storage."""
    return (
        encrypt_secret(api_key, fernet_key),
        encrypt_secret(api_secret, fernet_key),
    )


def decrypt_api_credentials(
    *,
    api_key_enc: bytes,
    api_secret_enc: bytes,
    fernet_key: str,
) -> ParsedApiCredentials:
    """Decrypt API key and secret for tests/future validators."""
    return ParsedApiCredentials(
        api_key=decrypt_secret(api_key_enc, fernet_key),
        api_secret=decrypt_secret(api_secret_enc, fernet_key),
    )
