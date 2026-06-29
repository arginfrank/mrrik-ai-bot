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
    assert settings.file.admin_panel.bind_host == "127.0.0.1"
    assert settings.file.admin_panel.auth_mode == "telegram_login"
    assert settings.file.admin_panel.session_ttl_sec == 43_200
    assert settings.file.admin_panel.telegram_auth_max_age_sec == 86_400
    assert settings.file.admin_panel.require_ip_allowlist is False
    assert settings.file.retry.signal_lookup_delays_sec == [0.05, 0.15, 0.4]


def test_missing_config_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_config("missing.yaml")
