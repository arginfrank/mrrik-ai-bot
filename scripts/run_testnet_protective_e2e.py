from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from decimal import ROUND_CEILING, Decimal
import json
import os
import time
from typing import Any, cast

from services.core_engine.ids import client_order_id
from services.core_engine.orders import EntryGuard, place_initial_orders
from services.core_engine.risk import ExecutionPlan, build_execution_plan
from services.core_engine.testnet import check_testnet_readiness
from shared.config import AppSettings, load_config
from shared.exchange.binance import BinanceFuturesClient
from shared.exchange.client import ExchangeClient
from shared.exchange.types import MarkPrice, PositionSnapshot, SymbolFilters
from shared.models import Signal, Trade
from shared.signal.types import SignalSide


_SYMBOL = "BTCUSDT"
_SIDE = SignalSide.LONG
_LEVERAGE = 5
_MAX_NOTIONAL_USDT = Decimal("100")
_TESTNET_REST_URL = "https://testnet.binancefuture.com"
_MARK_PRICE_TIMEOUT_SEC = 15
_CLEANUP_ATTEMPTS = 20
_CLEANUP_RETRY_DELAY_SEC = 0.25


class _ScenarioRefusal(RuntimeError):
    """Refuse before placing an entry order."""


def main() -> None:
    try:
        settings = load_config()
    except Exception as error:
        _print_json(
            {
                "status": "refused",
                "reasons": [f"configuration could not be loaded ({type(error).__name__})"],
            }
        )
        return

    reasons = _guard_reasons(settings)
    if reasons:
        _print_json({"status": "refused", "reasons": reasons})
        return

    output = asyncio.run(_run_scenario(settings))
    _print_json(output)


def _guard_reasons(settings: AppSettings) -> list[str]:
    explicit_env = os.getenv("MRRIK_RUN_TESTNET_E2E") == "1"
    enabled = settings.file.testnet.enabled and (
        explicit_env or not settings.file.testnet.require_explicit_env
    )
    readiness = check_testnet_readiness(
        enabled=enabled,
        api_key_present=_secret_present(settings.env.binance_testnet_api_key),
        api_secret_present=_secret_present(settings.env.binance_testnet_api_secret),
    )
    reasons = list(readiness.reasons)
    if os.getenv("MRRIK_RUN_TESTNET_PROTECTIVE_E2E") != "1":
        reasons.append("MRRIK_RUN_TESTNET_PROTECTIVE_E2E must equal 1")
    if _mainnet_credential_present(settings):
        reasons.append("mainnet Binance credential variables are present")
    return reasons


async def _run_scenario(settings: AppSettings) -> dict[str, Any]:
    output = _empty_evidence()
    exchange: BinanceFuturesClient | None = None
    trusted_testnet_client = False
    trade_id = max(1, int(time.time() * 1000))

    try:
        exchange = BinanceFuturesClient(
            api_key=_secret_value(settings.env.binance_testnet_api_key),
            api_secret=_secret_value(settings.env.binance_testnet_api_secret),
            testnet=True,
        )
        rest_base_url = getattr(getattr(exchange, "_rest", None), "base_url", None)
        if rest_base_url != _TESTNET_REST_URL:
            output["status"] = "refused"
            output["reasons"] = ["Binance REST base URL is not the testnet URL"]
            return output
        trusted_testnet_client = True

        await _require_flat_start(exchange)
        filters = await exchange.get_symbol_filters(_SYMBOL)
        mark_price = await _current_mark_price(exchange)
        plan = _build_plan(filters=filters, mark_price=mark_price)
        trade, signal = _build_models(trade_id=trade_id, plan=plan)

        result = await place_initial_orders(
            exchange=cast(ExchangeClient, exchange),
            trade=trade,
            signal=signal,
            plan=plan,
            entry_mode="market",
            entry_guard=EntryGuard(
                entry_fill_timeout_sec=20,
                entry_max_deviation_pct=Decimal("5"),
            ),
        )

        open_algo_orders = await exchange.get_open_algo_orders(symbol=_SYMBOL)
        open_client_ids = {order.client_order_id for order in open_algo_orders}
        position = await exchange.get_position(symbol=_SYMBOL)
        sl_confirmed = (
            result.sl_order_id is not None
            and result.sl_order_id in open_client_ids
        )
        tp_order_ids = [
            order_id
            for order_id in result.tp_order_ids
            if order_id in open_client_ids
        ]
        output.update(
            {
                "status": "ok"
                if result.status == "opened" and sl_confirmed
                else "failed",
                "entry_order_id": result.entry_order_id,
                "entry_status": result.status,
                "sl_order_id": result.sl_order_id,
                "sl_confirmed": sl_confirmed,
                "tp_order_ids": tp_order_ids,
                "tp_confirmed_count": len(tp_order_ids),
                "open_order_count": len(open_algo_orders),
                "position_qty_after_open": _position_qty(position),
            }
        )
        if result.reason is not None:
            output["failure_reason"] = result.reason
    except _ScenarioRefusal as error:
        output["status"] = "refused"
        output["reasons"] = [str(error)]
    except Exception as error:
        output["status"] = "failed"
        output["failure_reason"] = f"scenario raised {type(error).__name__}"
    finally:
        if exchange is not None and trusted_testnet_client:
            output["cleanup"] = await _cleanup(exchange, trade_id=trade_id)
            if not output["cleanup"]["cleanup_ok"]:
                output["status"] = "failed"

    return output


async def _require_flat_start(exchange: BinanceFuturesClient) -> None:
    position = await exchange.get_position(symbol=_SYMBOL)
    open_orders = await exchange.get_open_orders(symbol=_SYMBOL)
    open_algo_orders = await exchange.get_open_algo_orders(symbol=_SYMBOL)
    if position is not None or open_orders or open_algo_orders:
        raise _ScenarioRefusal(
            "BTCUSDT testnet state was not flat before the scenario; cleanup was attempted"
        )


async def _current_mark_price(exchange: BinanceFuturesClient) -> Decimal:
    stream = cast(
        AsyncGenerator[MarkPrice, None],
        exchange.mark_price_stream([_SYMBOL]),
    )
    try:
        mark = await asyncio.wait_for(anext(stream), timeout=_MARK_PRICE_TIMEOUT_SEC)
    finally:
        await stream.aclose()
    if mark.symbol.upper() != _SYMBOL or mark.price <= 0:
        raise _ScenarioRefusal("Binance testnet returned an invalid BTCUSDT mark price")
    return mark.price


def _build_plan(*, filters: SymbolFilters, mark_price: Decimal) -> ExecutionPlan:
    if filters.step_size <= 0 or filters.tick_size <= 0:
        raise _ScenarioRefusal("BTCUSDT testnet symbol filters are invalid")

    entry_price = _round_down(mark_price, filters.tick_size)
    if entry_price <= 0:
        raise _ScenarioRefusal("BTCUSDT testnet mark price is below one price tick")
    minimum_qty = max(filters.min_qty, filters.min_notional / entry_price)
    qty = _round_up(minimum_qty, filters.step_size)
    notional = qty * entry_price
    estimated_market_notional = qty * mark_price
    if qty <= 0 or estimated_market_notional > _MAX_NOTIONAL_USDT:
        raise _ScenarioRefusal(
            "smallest valid BTCUSDT scenario exceeds the 100 USDT notional ceiling"
        )

    margin = notional / Decimal(_LEVERAGE)
    plan = build_execution_plan(
        side=_SIDE,
        entry=entry_price,
        stop_loss=entry_price * Decimal("0.97"),
        leverage=_LEVERAGE,
        targets=(entry_price * Decimal("1.03"),),
        margin_usdt=margin,
        risk_model=1,
        model2_weights=(),
        model3_exit_roi_pct=Decimal("20"),
        filters=filters,
        maintenance_margin_rate=Decimal("0.005"),
    )
    if plan.qty != qty or plan.notional_usdt > _MAX_NOTIONAL_USDT:
        raise _ScenarioRefusal("execution plan exceeds the reviewed scenario bounds")
    return plan


def _build_models(*, trade_id: int, plan: ExecutionPlan) -> tuple[Trade, Signal]:
    targets = [format(leg.target_price, "f") for leg in plan.legs]
    signal = Signal(
        id=trade_id,
        symbol=_SYMBOL,
        side=_SIDE.value,
        entry=plan.entry_price,
        stop_loss=plan.stop_loss,
        leverage=plan.leverage,
        targets_raw=targets,
        targets_clean=targets,
        status="accepted",
    )
    trade = Trade(
        id=trade_id,
        signal_id=signal.id,
        user_id=None,
        symbol=_SYMBOL,
        side=_SIDE.value,
        leverage=plan.leverage,
        margin_usdt=plan.margin_usdt,
        notional_usdt=plan.notional_usdt,
        qty=plan.qty,
        status="pending_entry",
    )
    return trade, signal


async def _cleanup(
    exchange: BinanceFuturesClient,
    *,
    trade_id: int,
) -> dict[str, Any]:
    errors: list[str] = []
    try:
        await exchange.cancel_open_orders(symbol=_SYMBOL)
    except Exception:
        errors.append("cancel_open_orders")
    try:
        await exchange.cancel_all_algo_orders(symbol=_SYMBOL)
    except Exception:
        errors.append("cancel_all_algo_orders")

    position = await _get_position_for_cleanup(exchange, errors=errors)
    if position is not None:
        close_side = "SELL" if position.qty > 0 else "BUY"
        try:
            await exchange.close_position_market(
                symbol=_SYMBOL,
                side=close_side,
                qty=None,
                client_order_id=client_order_id(
                    trade_id=trade_id,
                    purpose="close",
                ),
            )
        except Exception:
            errors.append("close_position_market")

    residual_position, residual_orders = await _verify_cleanup(
        exchange,
        errors=errors,
    )
    cleanup_ok = not residual_position and residual_orders == 0
    return {
        "cleanup_ok": cleanup_ok,
        "errors": errors,
        "residual_orders": residual_orders,
        "residual_position": residual_position,
    }


async def _get_position_for_cleanup(
    exchange: BinanceFuturesClient,
    *,
    errors: list[str],
) -> PositionSnapshot | None:
    for attempt in range(3):
        try:
            return await exchange.get_position(symbol=_SYMBOL)
        except Exception:
            if attempt < 2:
                await asyncio.sleep(_CLEANUP_RETRY_DELAY_SEC)
    errors.append("get_position_before_close")
    return None


async def _verify_cleanup(
    exchange: BinanceFuturesClient,
    *,
    errors: list[str],
) -> tuple[bool, int]:
    residual_position = True
    residual_orders = -1
    for attempt in range(_CLEANUP_ATTEMPTS):
        try:
            position = await exchange.get_position(symbol=_SYMBOL)
            open_orders = await exchange.get_open_orders(symbol=_SYMBOL)
            open_algo_orders = await exchange.get_open_algo_orders(symbol=_SYMBOL)
            residual_position = position is not None
            residual_orders = len(open_orders) + len(open_algo_orders)
            if not residual_position and residual_orders == 0:
                return False, 0
        except Exception:
            if "verify_cleanup" not in errors:
                errors.append("verify_cleanup")
        if attempt < _CLEANUP_ATTEMPTS - 1:
            await asyncio.sleep(_CLEANUP_RETRY_DELAY_SEC)
    return residual_position, residual_orders


def _mainnet_credential_present(settings: AppSettings) -> bool:
    values = (
        os.getenv("BINANCE_API_KEY"),
        os.getenv("BINANCE_API_SECRET"),
        getattr(settings.env, "binance_api_key", None),
        getattr(settings.env, "binance_api_secret", None),
    )
    return any(_secret_present(value) for value in values)


def _secret_present(value: Any) -> bool:
    if value is None:
        return False
    return bool(_secret_value(value).strip())


def _secret_value(value: Any) -> str:
    getter = getattr(value, "get_secret_value", None)
    raw = getter() if callable(getter) else value
    return str(raw)


def _round_up(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def _round_down(value: Decimal, step: Decimal) -> Decimal:
    return (value // step) * step


def _position_qty(position: PositionSnapshot | None) -> str:
    return "0" if position is None else format(position.qty, "f")


def _empty_evidence() -> dict[str, Any]:
    return {
        "cleanup": {
            "cleanup_ok": True,
            "errors": [],
            "residual_orders": 0,
            "residual_position": False,
        },
        "entry_order_id": None,
        "entry_status": None,
        "leverage": _LEVERAGE,
        "open_order_count": 0,
        "position_qty_after_open": "0",
        "sl_confirmed": False,
        "sl_order_id": None,
        "status": "failed",
        "symbol": _SYMBOL,
        "tp_confirmed_count": 0,
        "tp_order_ids": [],
    }


def _print_json(output: dict[str, Any]) -> None:
    print(json.dumps(output, default=str, sort_keys=True))


if __name__ == "__main__":
    main()
