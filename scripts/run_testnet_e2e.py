from __future__ import annotations

from dataclasses import asdict
import json
import os
from typing import Any

from services.core_engine.testnet import (
    check_testnet_readiness,
    default_testnet_scenario,
)
from shared.config import load_config


def main() -> None:
    settings = load_config()
    explicit_env = os.getenv("MRRIK_RUN_TESTNET_E2E") == "1"
    enabled = settings.file.testnet.enabled and (
        explicit_env or not settings.file.testnet.require_explicit_env
    )
    readiness = check_testnet_readiness(
        enabled=enabled,
        api_key_present=_secret_present(settings.env.binance_testnet_api_key),
        api_secret_present=_secret_present(settings.env.binance_testnet_api_secret),
    )
    scenario = default_testnet_scenario()
    output = {
        "status": "ready" if readiness.ready else "refused",
        "ready": readiness.ready,
        "reasons": list(readiness.reasons),
        "scenario": asdict(scenario),
        "execution": "not_started",
    }
    print(json.dumps(output, default=_json_default, sort_keys=True))


def _secret_present(value: Any) -> bool:
    if value is None:
        return False
    getter = getattr(value, "get_secret_value", None)
    raw = getter() if callable(getter) else value
    return bool(str(raw).strip())


def _json_default(value: Any) -> str:
    return str(value)


if __name__ == "__main__":
    main()
