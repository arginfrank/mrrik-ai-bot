from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import create_engine, func, or_, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from shared.config import load_config
from shared.models import (
    ExchangeCredential,
    ProcessedEvent,
    Signal,
    Subscription,
    Trade,
    TradeLeg,
    User,
    UserSetting,
)


_ACTIVE_TRADE_STATUSES = ("pending_entry", "open")


def make_engine_from_config() -> Any:
    return create_engine(load_config().env.database_url)


def make_session_factory(engine: Any | None = None) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine if engine is not None else make_engine_from_config(),
        class_=Session,
        expire_on_commit=False,
    )


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class CoreRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def mark_event_processed(self, event_id: UUID) -> bool:
        """Return False if already processed."""
        if self._session.get(ProcessedEvent, event_id) is not None:
            return False
        self._session.add(ProcessedEvent(event_id=event_id, ts=datetime.now(UTC)))
        self._session.flush()
        return True

    def lock_user_for_execution(self, user_id: int) -> None:
        """Serialize capital/concurrency checks for one user in this transaction."""
        self._session.execute(select(func.pg_advisory_xact_lock(user_id)))

    def get_signal(self, signal_id: int) -> Signal | None:
        return self._session.get(Signal, signal_id)

    def get_trade(self, trade_id: int) -> Trade | None:
        statement = (
            select(Trade)
            .where(Trade.id == trade_id)
            .options(selectinload(Trade.legs), selectinload(Trade.signal))
        )
        return self._session.scalar(statement)

    def list_eligible_users_for_signal(self) -> list[User]:
        """Return non-blocked users with active subscriptions."""
        now = datetime.now(UTC)
        statement = (
            select(User)
            .join(Subscription, Subscription.user_id == User.id)
            .where(
                User.is_blocked.is_(False),
                Subscription.status == "active",
                Subscription.ends_at.is_not(None),
                Subscription.ends_at > now,
            )
            .distinct()
            .order_by(User.id)
        )
        return list(self._session.scalars(statement))

    def get_active_subscription(self, user_id: int) -> Subscription | None:
        now = datetime.now(UTC)
        statement = (
            select(Subscription)
            .where(
                Subscription.user_id == user_id,
                Subscription.status == "active",
                Subscription.ends_at.is_not(None),
                Subscription.ends_at > now,
            )
            .order_by(Subscription.ends_at.desc(), Subscription.id.desc())
        )
        return self._session.scalar(statement)

    def get_user_settings(self, user_id: int) -> UserSetting | None:
        return self._session.get(UserSetting, user_id)

    def get_exchange_credentials(self, user_id: int) -> ExchangeCredential | None:
        statement = select(ExchangeCredential).where(
            ExchangeCredential.user_id == user_id,
            ExchangeCredential.exchange == "binance",
        )
        return self._session.scalar(statement)

    def get_user(self, user_id: int) -> User | None:
        return self._session.get(User, user_id)

    def count_open_trades(self, user_id: int) -> int:
        statement = select(func.count(Trade.id)).where(
            Trade.user_id == user_id,
            Trade.status.in_(_ACTIVE_TRADE_STATUSES),
        )
        return int(self._session.scalar(statement) or 0)

    def has_open_trade_for_signal(self, *, user_id: int, signal_id: int) -> bool:
        statement = select(Trade.id).where(
            Trade.user_id == user_id,
            Trade.signal_id == signal_id,
            Trade.status.in_(_ACTIVE_TRADE_STATUSES),
        )
        return self._session.scalar(statement) is not None

    def create_trade_from_plan(
        self,
        *,
        user_id: int,
        signal: Signal,
        margin_usdt: Decimal,
        notional_usdt: Decimal,
        qty: Decimal,
        leverage: int,
        liq_price: Decimal,
        legs: tuple[object, ...],
    ) -> Trade:
        trade = Trade(
            signal_id=signal.id,
            user_id=user_id,
            symbol=signal.symbol,
            side=signal.side,
            leverage=leverage,
            margin_usdt=margin_usdt,
            notional_usdt=notional_usdt,
            qty=qty,
            liq_price=liq_price,
            status="pending_entry",
            touched_tps=[],
        )
        self._session.add(trade)
        self._session.flush()
        for plan_leg in legs:
            trade.legs.append(
                TradeLeg(
                    leg_index=int(getattr(plan_leg, "leg_index")),
                    target_price=Decimal(getattr(plan_leg, "target_price")),
                    qty=Decimal(getattr(plan_leg, "qty")),
                    status="open",
                )
            )
        self._session.flush()
        return trade

    def set_trade_entry_order(self, *, trade: Trade, entry_order_id: str) -> None:
        trade.entry_order_id = entry_order_id
        self._session.flush()

    def mark_trade_opened(self, *, trade: Trade, sl_order_id: str | None) -> None:
        trade.status = "open"
        trade.sl_order_id = sl_order_id
        trade.opened_at = datetime.now(UTC)
        self._session.flush()

    def set_leg_tp_order(self, *, leg: TradeLeg, tp_order_id: str) -> None:
        leg.tp_order_id = tp_order_id
        self._session.flush()

    def mark_leg_filled(
        self,
        *,
        trade: Trade,
        leg_index: int,
        filled_at: datetime | None = None,
    ) -> TradeLeg:
        leg = next((item for item in trade.legs if item.leg_index == leg_index), None)
        if leg is None:
            raise ValueError(f"trade leg {leg_index} does not exist")
        if leg.status != "filled":
            leg.status = "filled"
            leg.filled_at = filled_at or datetime.now(UTC)
            trade.touched_tps = sorted(
                set(trade.touched_tps or []).union({leg_index})
            )
            self._session.flush()
        return leg

    def set_trade_sl_order(self, *, trade: Trade, sl_order_id: str) -> None:
        trade.sl_order_id = sl_order_id
        self._session.flush()

    def mark_trade_status(self, *, trade: Trade, status: str) -> None:
        if status not in {"pending_entry", "open", "closed", "skipped", "error"}:
            raise ValueError("unsupported trade status")
        trade.status = status
        self._session.flush()

    def mark_open_legs_canceled(self, *, trade: Trade) -> None:
        for leg in trade.legs:
            if leg.status == "open":
                leg.status = "canceled"
        self._session.flush()

    def close_trade(
        self,
        *,
        trade: Trade,
        closed_reason: str,
        realized_pnl_usdt: Decimal,
        realized_roi_pct: Decimal,
        touched_tps: tuple[int, ...],
        closed_at: datetime | None = None,
    ) -> Trade:
        trade.status = "closed"
        trade.closed_reason = closed_reason
        trade.realized_pnl_usdt = realized_pnl_usdt
        trade.realized_roi_pct = realized_roi_pct
        trade.touched_tps = list(sorted(set(touched_tps)))
        trade.closed_at = closed_at or datetime.now(UTC)
        for leg in trade.legs:
            if leg.status == "open":
                leg.status = "canceled"
        self._session.flush()
        return trade

    def list_open_trades(self) -> list[Trade]:
        statement = (
            select(Trade)
            .where(Trade.status.in_(_ACTIVE_TRADE_STATUSES))
            .options(
                selectinload(Trade.legs),
                selectinload(Trade.signal),
                selectinload(Trade.user),
            )
            .order_by(Trade.id)
        )
        return list(self._session.scalars(statement))

    def get_trade_by_client_order_id(self, client_order_id: str) -> Trade | None:
        statement = (
            select(Trade)
            .where(
                or_(
                    Trade.entry_order_id == client_order_id,
                    Trade.sl_order_id == client_order_id,
                )
            )
            .options(selectinload(Trade.legs), selectinload(Trade.signal))
        )
        return self._session.scalar(statement)

    def get_trade_leg_by_client_order_id(
        self, client_order_id: str
    ) -> tuple[Trade, TradeLeg] | None:
        statement = (
            select(TradeLeg)
            .where(TradeLeg.tp_order_id == client_order_id)
            .options(
                selectinload(TradeLeg.trade).selectinload(Trade.legs),
                selectinload(TradeLeg.trade).selectinload(Trade.signal),
            )
        )
        leg = self._session.scalar(statement)
        if leg is None or leg.trade is None:
            return None
        return leg.trade, leg
