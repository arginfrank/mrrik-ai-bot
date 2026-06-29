from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from services.demo_engine.events import (
    build_demo_closed_payload,
    build_demo_opened_payload,
    build_notify_user_payload,
    format_demo_close_message,
    format_demo_open_message,
)
from services.demo_engine.trade_logic import (
    DemoLegPlan,
    build_demo_open_plan,
    evaluate_price_tick,
)
from shared.signal.types import SignalSide


SIGNALS_STREAM = "signals"
DEMO_STREAM = "demo"
NOTIFY_STREAM = "notify"


class EventPublisher(Protocol):
    async def publish(
        self,
        *,
        stream: str,
        event_type: str,
        payload: dict,
    ) -> object: ...


@dataclass(frozen=True)
class DemoEngineConfig:
    start_balance_usdt: Decimal
    fixed_margin_usdt: Decimal
    risk_model: int
    model2_weights: tuple[Decimal, ...]
    model3_exit_roi_pct: Decimal
    move_sl_to_be_after_tp1: bool
    maintenance_margin_rate: Decimal
    include_commission: bool
    include_funding: bool
    include_slippage: bool


@dataclass(frozen=True)
class DemoHandleResult:
    status: str
    opened_count: int = 0
    closed_count: int = 0
    ignored_reason: str | None = None


def config_from_app_config(app_config: object) -> DemoEngineConfig:
    """Read defaults from existing shared.config.load_config() result."""
    file_config = getattr(app_config, "file")
    risk = getattr(file_config, "risk")
    execution = getattr(file_config, "execution")
    demo = getattr(file_config, "demo")
    return DemoEngineConfig(
        start_balance_usdt=Decimal(demo.start_balance_usdt),
        fixed_margin_usdt=Decimal(risk.fixed_margin_usdt),
        risk_model=int(risk.default_model),
        model2_weights=tuple(Decimal(weight) for weight in risk.model2_weights),
        model3_exit_roi_pct=Decimal(risk.model3_exit_roi_pct),
        move_sl_to_be_after_tp1=bool(risk.move_sl_to_be_after_tp1),
        maintenance_margin_rate=Decimal(execution.maintenance_margin_rate_default),
        include_commission=bool(demo.include_commission),
        include_funding=bool(demo.include_funding),
        include_slippage=bool(demo.include_slippage),
    )


async def handle_signal_created(
    *,
    payload: dict,
    repository: object,
    publisher: EventPublisher,
    config: DemoEngineConfig,
) -> DemoHandleResult:
    """Open demo trades for all demo accounts for one signal.created payload."""
    signal_id = _positive_int(payload.get("signal_id"))
    if signal_id is None:
        return DemoHandleResult(status="ignored", ignored_reason="missing_signal_id")

    signal = repository.get_signal(signal_id)  # type: ignore[attr-defined]
    if signal is None:
        return DemoHandleResult(status="ignored", ignored_reason="signal_not_found")
    if signal.status != "accepted":
        return DemoHandleResult(status="ignored", ignored_reason="signal_not_accepted")

    accounts = repository.list_demo_accounts()  # type: ignore[attr-defined]
    if not accounts:
        return DemoHandleResult(status="ignored", ignored_reason="no_demo_accounts")

    try:
        side = SignalSide(signal.side)
        targets = tuple(Decimal(str(target)) for target in signal.targets_clean)
    except (ValueError, InvalidOperation):
        return DemoHandleResult(status="ignored", ignored_reason="invalid_signal")

    opened_count = 0
    duplicate_count = 0
    plan_rejected_count = 0
    for account in accounts:
        if repository.has_open_demo_trade(  # type: ignore[attr-defined]
            user_id=account.user_id,
            signal_id=signal_id,
        ):
            duplicate_count += 1
            continue
        settings = repository.get_user_settings(account.user_id)  # type: ignore[attr-defined]
        margin = (
            Decimal(settings.fixed_margin_usdt)
            if settings is not None
            else config.fixed_margin_usdt
        )
        risk_model = int(settings.risk_model) if settings is not None else config.risk_model
        model3_roi = (
            Decimal(settings.model3_exit_roi_pct)
            if settings is not None
            else config.model3_exit_roi_pct
        )
        leverage = _effective_leverage(signal.leverage, settings)
        try:
            plan = build_demo_open_plan(
                side=side,
                entry=Decimal(signal.entry),
                stop_loss=Decimal(signal.stop_loss),
                leverage=leverage,
                targets=targets,
                margin_usdt=margin,
                risk_model=risk_model,
                model3_exit_roi_pct=model3_roi,
                model2_weights=config.model2_weights,
                maintenance_margin_rate=config.maintenance_margin_rate,
                include_commission=config.include_commission,
                include_funding=config.include_funding,
                include_slippage=config.include_slippage,
            )
        except ValueError:
            plan_rejected_count += 1
            continue

        original_leverage = signal.leverage
        signal.leverage = leverage
        try:
            demo_trade = repository.create_open_demo_trade(  # type: ignore[attr-defined]
                account=account,
                signal=signal,
                margin_usdt=plan.margin_usdt,
                notional_usdt=plan.notional_usdt,
                qty=plan.qty,
                liq_price=plan.liq_price,
                legs=plan.legs,
                fields_realism_applied=plan.fields_realism_applied,
            )
        finally:
            signal.leverage = original_leverage

        await publisher.publish(
            stream=DEMO_STREAM,
            event_type="demo.opened",
            payload=build_demo_opened_payload(demo_trade),
        )
        user = repository.get_user(account.user_id)  # type: ignore[attr-defined]
        if user is not None:
            await publisher.publish(
                stream=NOTIFY_STREAM,
                event_type="notify.user",
                payload=build_notify_user_payload(
                    user=user,
                    text=format_demo_open_message(demo_trade),
                ),
            )
        opened_count += 1

    if opened_count:
        return DemoHandleResult(status="opened", opened_count=opened_count)
    if duplicate_count == len(accounts):
        reason = "duplicate_open_trade"
    elif plan_rejected_count:
        reason = "demo_open_plan_rejected"
    else:
        reason = "no_eligible_accounts"
    return DemoHandleResult(status="ignored", ignored_reason=reason)


async def handle_mark_price(
    *,
    symbol: str,
    price: Decimal,
    repository: object,
    publisher: EventPublisher,
    config: DemoEngineConfig,
) -> DemoHandleResult:
    """Apply one mark-price tick to every open demo trade for the symbol."""
    trades = repository.list_open_demo_trades_by_symbol(  # type: ignore[attr-defined]
        symbol.upper()
    )
    if not trades:
        return DemoHandleResult(status="ignored", ignored_reason="no_open_trades")
    accounts = {
        account.user_id: account
        for account in repository.list_demo_accounts()  # type: ignore[attr-defined]
    }
    closed_count = 0
    for demo_trade in trades:
        signal = demo_trade.signal
        if signal is None and demo_trade.signal_id is not None:
            signal = repository.get_signal(demo_trade.signal_id)  # type: ignore[attr-defined]
        if signal is None or demo_trade.liq_price is None or demo_trade.user_id is None:
            continue

        settings = repository.get_user_settings(demo_trade.user_id)  # type: ignore[attr-defined]
        risk_model = 3 if not demo_trade.legs else 1
        model3_roi = (
            Decimal(settings.model3_exit_roi_pct)
            if settings is not None
            else config.model3_exit_roi_pct
        )
        touched_tps = tuple(
            sorted(
                set(demo_trade.touched_tps or []).union(
                    leg.leg_index for leg in demo_trade.legs if leg.status == "filled"
                )
            )
        )
        legs = _plans_from_trade(demo_trade)
        decision = evaluate_price_tick(
            side=SignalSide(demo_trade.side),
            entry=Decimal(signal.entry),
            original_stop_loss=Decimal(signal.stop_loss),
            leverage=demo_trade.leverage,
            margin_usdt=Decimal(demo_trade.margin_usdt),
            liq_price=Decimal(demo_trade.liq_price),
            current_price=price,
            risk_model=risk_model,
            model3_exit_roi_pct=model3_roi,
            move_sl_to_be_after_tp1=config.move_sl_to_be_after_tp1,
            open_legs=legs,
            touched_tps=touched_tps,
            maintenance_margin_rate=config.maintenance_margin_rate,
        )
        if decision.filled_leg_indices:
            repository.mark_demo_legs_filled(  # type: ignore[attr-defined]
                demo_trade=demo_trade,
                leg_indices=decision.filled_leg_indices,
            )
        if not decision.should_close:
            continue
        if (
            decision.closed_reason is None
            or decision.realized_roi_pct is None
            or decision.realized_pnl_usdt is None
        ):
            raise ValueError("close decision is missing required result fields")
        closed_trade = repository.close_demo_trade(  # type: ignore[attr-defined]
            demo_trade=demo_trade,
            closed_reason=decision.closed_reason,
            realized_roi_pct=decision.realized_roi_pct,
            realized_pnl_usdt=decision.realized_pnl_usdt,
            touched_tps=decision.touched_tps,
        )
        account = accounts.get(demo_trade.user_id)
        if account is None:
            account = repository.get_or_create_demo_account(  # type: ignore[attr-defined]
                user_id=demo_trade.user_id,
                start_balance_usdt=config.start_balance_usdt,
            )
        await publisher.publish(
            stream=DEMO_STREAM,
            event_type="demo.closed",
            payload=build_demo_closed_payload(
                demo_trade=closed_trade,
                demo_account=account,
            ),
        )
        user = repository.get_user(demo_trade.user_id)  # type: ignore[attr-defined]
        if user is not None:
            await publisher.publish(
                stream=NOTIFY_STREAM,
                event_type="notify.user",
                payload=build_notify_user_payload(
                    user=user,
                    text=format_demo_close_message(closed_trade),
                ),
            )
        closed_count += 1
    return DemoHandleResult(status="processed", closed_count=closed_count)


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        converted = int(value)
    except (TypeError, ValueError):
        return None
    return converted if converted > 0 else None


def _effective_leverage(signal_leverage: int, settings: Any) -> int:
    if (
        settings is not None
        and settings.leverage_mode == "cap"
        and settings.leverage_cap is not None
    ):
        return min(signal_leverage, int(settings.leverage_cap))
    return signal_leverage


def _plans_from_trade(demo_trade: Any) -> tuple[DemoLegPlan, ...]:
    total_qty = sum((Decimal(leg.qty) for leg in demo_trade.legs), Decimal("0"))
    if total_qty <= 0:
        return ()
    return tuple(
        DemoLegPlan(
            leg_index=leg.leg_index,
            target_price=Decimal(leg.target_price),
            fraction=Decimal(leg.qty) / total_qty,
            qty=Decimal(leg.qty),
        )
        for leg in sorted(demo_trade.legs, key=lambda item: item.leg_index)
    )
