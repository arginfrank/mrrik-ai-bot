from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class PlanConfig(BaseModel):
    code: str
    duration_days: int
    price_usdt: Decimal


class RiskConfig(BaseModel):
    fixed_margin_usdt: Decimal = Decimal("10")
    default_model: int = 1
    model2_weights: list[Decimal] = Field(
        default_factory=lambda: [
            Decimal("0.60"),
            Decimal("0.20"),
            Decimal("0.10"),
            Decimal("0.07"),
            Decimal("0.03"),
        ]
    )
    model3_exit_roi_pct: Decimal = Decimal("20")
    move_sl_to_be_after_tp1: bool = True
    max_concurrent: int = 10


class ExecutionConfig(BaseModel):
    entry_mode: Literal["limit", "market"] = "limit"
    entry_fill_timeout_sec: int = 900
    entry_max_deviation_pct: Decimal = Decimal("0.5")
    margin_type: Literal["isolated"] = "isolated"
    maintenance_margin_rate_default: Decimal = Decimal("0.005")


class SanitizerConfig(BaseModel):
    decimal_shift_lo: Decimal = Decimal("5")
    decimal_shift_hi: Decimal = Decimal("20")


class DemoConfig(BaseModel):
    start_balance_usdt: Decimal = Decimal("1000")
    require_api_key: bool = False
    api_key_scope: Literal["read_only"] = "read_only"
    include_commission: bool = False
    include_funding: bool = False
    include_slippage: bool = False
    taker_fee_pct: Decimal = Decimal("0.04")


class TelegramBotServiceConfig(BaseModel):
    notify_group_name: str = "telegram-bot-notify"
    notify_consumer_name: str = "telegram-bot-1"
    notify_read_count: int = Field(default=100, ge=1)
    notify_block_ms: int = Field(default=5000, ge=1)


class PaymentNetworkConfig(BaseModel):
    usdt_contract: str
    decimals: int
    min_confirmations: int
    explorer_base_url: str


class PaymentPrecheckConfig(BaseModel):
    trc20: PaymentNetworkConfig
    bep20: PaymentNetworkConfig
    polygon: PaymentNetworkConfig


class AdminPanelConfig(BaseModel):
    bind_host: str = "127.0.0.1"
    auth_mode: Literal["telegram_login", "bootstrap_token"] = "telegram_login"
    session_ttl_sec: int = Field(default=43_200, gt=0)
    telegram_auth_max_age_sec: int = Field(default=86_400, gt=0)
    ip_allowlist: list[str] = Field(default_factory=lambda: ["127.0.0.1", "::1"])
    require_ip_allowlist: bool = False


class RetryConfig(BaseModel):
    max_attempts: int = 3
    base_delay_sec: Decimal = Decimal("0.5")
    max_delay_sec: Decimal = Decimal("8")
    jitter_pct: Decimal = Decimal("0.10")


class RateLimitConfig(BaseModel):
    telegram_messages_per_second: Decimal = Decimal("20")
    exchange_requests_per_second: Decimal = Decimal("8")
    explorer_requests_per_second: Decimal = Decimal("3")


class MonitoringConfig(BaseModel):
    health_stale_after_sec: int = 120
    alert_stream: str = "notify"
    admin_alerts_enabled: bool = True


class BackupConfig(BaseModel):
    enabled: bool = True
    output_dir: str = "backups"
    keep_last: int = 7


class TestnetConfig(BaseModel):
    enabled: bool = False
    require_explicit_env: bool = True


class LiveCanaryConfig(BaseModel):
    enabled: bool = False
    max_margin_usdt: Decimal = Decimal("5")
    require_confirmation_text: str = "I_ACCEPT_REAL_MONEY_RISK"


class FileConfig(BaseModel):
    plans: list[PlanConfig]
    risk: RiskConfig
    execution: ExecutionConfig
    sanitizer: SanitizerConfig
    demo: DemoConfig
    telegram_bot: TelegramBotServiceConfig = Field(
        default_factory=TelegramBotServiceConfig
    )
    payment_precheck: PaymentPrecheckConfig
    admin_panel: AdminPanelConfig
    retry: RetryConfig = Field(default_factory=RetryConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    backup: BackupConfig = Field(default_factory=BackupConfig)
    testnet: TestnetConfig = Field(default_factory=TestnetConfig)
    live_canary: LiveCanaryConfig = Field(default_factory=LiveCanaryConfig)


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    telegram_bot_token: SecretStr | None = None
    tg_api_id: int | None = None
    tg_api_hash: SecretStr | None = None
    tg_userbot_session: SecretStr | None = None
    source_channel_id: int | None = None
    admin_telegram_ids: str | None = None
    admin_bootstrap_token: SecretStr | None = None
    database_url: str = "postgresql+psycopg://mrrik:mrrik@localhost:5432/mrrik"
    redis_url: str = "redis://localhost:6379/0"
    fernet_key: SecretStr | None = None
    wallet_trc20: SecretStr | None = None
    wallet_bep20: SecretStr | None = None
    wallet_polygon: SecretStr | None = None
    tronscan_api_key: SecretStr | None = None
    bscscan_api_key: SecretStr | None = None
    polygonscan_api_key: SecretStr | None = None
    binance_testnet_api_key: SecretStr | None = None
    binance_testnet_api_secret: SecretStr | None = None
    live_canary_confirm: str | None = None


class AppSettings(BaseModel):
    file: FileConfig
    env: EnvSettings


def load_config(config_path: str | Path = "config.yaml") -> AppSettings:
    """Load non-secret YAML config plus secret/runtime env settings."""
    path = Path(config_path)
    if not path.is_file():
        raise FileNotFoundError(path)

    with path.open(encoding="utf-8") as config_file:
        values = yaml.safe_load(config_file)

    return AppSettings(file=FileConfig.model_validate(values), env=EnvSettings())
