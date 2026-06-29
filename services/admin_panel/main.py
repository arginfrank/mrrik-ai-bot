from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

from fastapi import FastAPI
import redis.asyncio as redis

from services.admin_panel.auth import parse_admin_telegram_ids
from services.admin_panel.constants import NETWORK_BEP20, NETWORK_POLYGON, NETWORK_TRC20
from services.admin_panel.explorers import EvmScanClient, TronscanClient
from services.admin_panel.repository import (
    AdminPanelRepository,
    make_engine_from_config,
    make_session_factory,
    session_scope,
)
from services.admin_panel.routes import create_app
from shared.bus import RedisStreamPublisher
from shared.config import load_config


def config_from_app_config(app_config: object) -> dict[str, Any]:
    """Extract admin panel runtime config from shared config."""
    file_config = getattr(app_config, "file")
    env_config = getattr(app_config, "env")
    precheck_config = file_config.payment_precheck

    wallets = {
        NETWORK_TRC20: _secret_value(env_config.wallet_trc20),
        NETWORK_BEP20: _secret_value(env_config.wallet_bep20),
        NETWORK_POLYGON: _secret_value(env_config.wallet_polygon),
    }
    token_contracts = {
        NETWORK_TRC20: precheck_config.trc20.usdt_contract,
        NETWORK_BEP20: precheck_config.bep20.usdt_contract,
        NETWORK_POLYGON: precheck_config.polygon.usdt_contract,
    }
    confirmations = {
        NETWORK_TRC20: precheck_config.trc20.min_confirmations,
        NETWORK_BEP20: precheck_config.bep20.min_confirmations,
        NETWORK_POLYGON: precheck_config.polygon.min_confirmations,
    }
    explorer_clients = {
        NETWORK_TRC20: TronscanClient(
            api_key=_secret_value(env_config.tronscan_api_key),
            usdt_contract=precheck_config.trc20.usdt_contract,
            decimals=precheck_config.trc20.decimals,
            explorer_base_url=precheck_config.trc20.explorer_base_url,
        ),
        NETWORK_BEP20: EvmScanClient(
            network=NETWORK_BEP20,
            api_key=_secret_value(env_config.bscscan_api_key),
            usdt_contract=precheck_config.bep20.usdt_contract,
            decimals=precheck_config.bep20.decimals,
            explorer_base_url=precheck_config.bep20.explorer_base_url,
            api_base_url="https://api.bscscan.com/api",
        ),
        NETWORK_POLYGON: EvmScanClient(
            network=NETWORK_POLYGON,
            api_key=_secret_value(env_config.polygonscan_api_key),
            usdt_contract=precheck_config.polygon.usdt_contract,
            decimals=precheck_config.polygon.decimals,
            explorer_base_url=precheck_config.polygon.explorer_base_url,
            api_base_url="https://api.polygonscan.com/api",
        ),
    }
    auth_mode = file_config.admin_panel.auth_mode
    bot_token = _secret_value(env_config.telegram_bot_token)
    bootstrap_token = _secret_value(env_config.admin_bootstrap_token)
    signing_secret = bot_token if auth_mode == "telegram_login" else bootstrap_token
    return {
        "admin_telegram_ids": parse_admin_telegram_ids(env_config.admin_telegram_ids),
        "auth_mode": auth_mode,
        "bind_host": file_config.admin_panel.bind_host,
        "session_ttl_sec": file_config.admin_panel.session_ttl_sec,
        "telegram_auth_max_age_sec": (
            file_config.admin_panel.telegram_auth_max_age_sec
        ),
        "telegram_bot_token": bot_token,
        "admin_bootstrap_token": bootstrap_token,
        "session_signing_secret": (
            f"mrrik-admin-session:{signing_secret}" if signing_secret else ""
        ),
        "ip_allowlist": list(file_config.admin_panel.ip_allowlist),
        "require_ip_allowlist": file_config.admin_panel.require_ip_allowlist,
        "testnet_enabled": file_config.testnet.enabled,
        "live_canary_enabled": file_config.live_canary.enabled,
        "expected_wallets_by_network": wallets,
        "token_contracts_by_network": token_contracts,
        "min_confirmations_by_network": confirmations,
        "explorer_clients_by_network": explorer_clients,
    }


def create_application() -> FastAPI:
    """Create production FastAPI app with DB, Redis, publisher, and config."""
    app_config = load_config()
    redis_client = redis.from_url(app_config.env.redis_url, decode_responses=True)
    publisher = RedisStreamPublisher(redis_client)
    engine = make_engine_from_config()
    session_factory = make_session_factory(engine)

    @contextmanager
    def repository_factory() -> Iterator[AdminPanelRepository]:
        with session_scope(session_factory) as session:
            yield AdminPanelRepository(session)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await redis_client.aclose()
            engine.dispose()

    application = create_app(
        repository_factory=repository_factory,
        publisher=publisher,
        redis_client=redis_client,
        config=config_from_app_config(app_config),
    )
    application.router.lifespan_context = lifespan
    application.state.database_engine = engine
    application.state.redis_client = redis_client

    return application


def _secret_value(value: Any) -> str:
    if value is None:
        return ""
    getter = getattr(value, "get_secret_value", None)
    return str(getter() if callable(getter) else value).strip()


app = create_application()
