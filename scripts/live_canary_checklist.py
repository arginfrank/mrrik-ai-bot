from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
import os

from services.core_engine.safety import (
    enforce_canary_margin_limit,
    require_live_canary_confirmation,
)
from shared.config import load_config


_CHECKLIST = (
    "DB backup done",
    "Testnet E2E passed",
    "API key withdrawal disabled verified",
    "Max margin cap confirmed",
    "Kill switch tested",
    "Admin contact available",
    "Logs and alerts checked",
)


def main() -> None:
    settings = load_config()
    config = settings.file.live_canary
    reasons = []

    if os.getenv("MRRIK_RUN_LIVE_CANARY_CHECKLIST") != "1":
        reasons.append("MRRIK_RUN_LIVE_CANARY_CHECKLIST must equal 1")

    confirmation = require_live_canary_confirmation(
        provided=settings.env.live_canary_confirm,
        required=config.require_confirmation_text,
    )
    if not confirmation.allowed:
        reasons.append(confirmation.reason)

    requested_margin = _requested_margin(config.max_margin_usdt, reasons)
    margin = enforce_canary_margin_limit(
        requested_margin_usdt=requested_margin,
        max_margin_usdt=config.max_margin_usdt,
    )
    if not margin.allowed:
        reasons.append(margin.reason)

    if reasons:
        print(json.dumps({"status": "refused", "reasons": reasons}, sort_keys=True))
        return

    print(
        json.dumps(
            {
                "status": "checklist_ready",
                "warning": "REAL MONEY: this checklist never places an order",
                "max_margin_usdt": format(config.max_margin_usdt, "f"),
                "requested_margin_usdt": format(requested_margin, "f"),
                "live_canary_enabled": config.enabled,
                "checklist": list(_CHECKLIST),
                "execution": "not_started",
            },
            sort_keys=True,
        )
    )


def _requested_margin(default: Decimal, reasons: list[str]) -> Decimal:
    raw = os.getenv("LIVE_CANARY_MARGIN_USDT")
    if raw is None or not raw.strip():
        return default
    try:
        return Decimal(raw)
    except InvalidOperation:
        reasons.append("LIVE_CANARY_MARGIN_USDT must be a decimal value")
        return Decimal(0)


if __name__ == "__main__":
    main()
