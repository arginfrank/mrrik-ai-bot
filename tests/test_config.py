from decimal import Decimal

import pytest

from shared.config import load_config


def test_load_default_config() -> None:
    settings = load_config("config.yaml")

    assert settings.file.plans[0].code == "P30"
    assert settings.file.risk.fixed_margin_usdt == Decimal("10")
    assert settings.file.execution.margin_type == "isolated"
    assert settings.file.demo.require_api_key is False
    assert settings.file.telegram_bot.notify_group_name == "telegram-bot-notify"
    assert settings.file.telegram_bot.notify_consumer_name == "telegram-bot-1"
    assert settings.file.telegram_bot.notify_read_count == 100
    assert settings.file.telegram_bot.notify_block_ms == 5000


def test_missing_config_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_config("missing.yaml")
