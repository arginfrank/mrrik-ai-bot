from __future__ import annotations

from cryptography.fernet import Fernet
import pytest

from services.telegram_bot.credentials import (
    ApiCredentialFormatError,
    decrypt_api_credentials,
    encrypt_api_credentials,
    parse_api_credentials,
    redact_secret,
)


def test_parse_labeled_credentials() -> None:
    parsed = parse_api_credentials("api_key: KEY\napi_secret: SECRET")
    assert parsed.api_key == "KEY"
    assert parsed.api_secret == "SECRET"


def test_parse_two_line_credentials() -> None:
    parsed = parse_api_credentials("  KEY  \n\n  SECRET  ")
    assert parsed.api_key == "KEY"
    assert parsed.api_secret == "SECRET"


@pytest.mark.parametrize(
    "text",
    ("", "ONLY_ONE", "ONE\nTWO\nTHREE", "api_key: KEY\nSECRET", "api_key:\napi_secret: X"),
)
def test_invalid_credential_formats_raise(text: str) -> None:
    with pytest.raises(ApiCredentialFormatError):
        parse_api_credentials(text)


def test_redaction_does_not_reveal_full_secret() -> None:
    secret = "abcdefghijklmnop"
    redacted = redact_secret(secret)
    assert redacted != secret
    assert "efghijkl" not in redacted


def test_encrypt_decrypt_round_trip_hides_plaintext() -> None:
    key = Fernet.generate_key().decode("utf-8")
    api_key = "plain-api-key"
    api_secret = "plain-api-secret"

    api_key_enc, api_secret_enc = encrypt_api_credentials(
        api_key=api_key,
        api_secret=api_secret,
        fernet_key=key,
    )
    restored = decrypt_api_credentials(
        api_key_enc=api_key_enc,
        api_secret_enc=api_secret_enc,
        fernet_key=key,
    )

    assert restored.api_key == api_key
    assert restored.api_secret == api_secret
    assert api_key.encode() not in api_key_enc
    assert api_secret.encode() not in api_secret_enc
