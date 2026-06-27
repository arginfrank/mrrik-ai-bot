from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from shared.config import load_config
from shared.models import DemoAccount, DemoTrade, DemoTradeLeg, Signal, User, UserSetting


def make_engine_from_config() -> Any:
    """Create a SQLAlchemy engine from configured DATABASE_URL."""
    return create_engine(load_config().env.database_url)


def make_session_factory(engine: Any | None = None) -> sessionmaker[Session]:
    """Create a SQLAlchemy session factory."""
    return sessionmaker(
        bind=engine if engine is not None else make_engine_from_config(),
        class_=Session,
        expire_on_commit=False,
    )


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """Commit on success, rollback on exception, close always."""
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class DemoRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_signal(self, signal_id: int) -> Signal | None:
        return self._session.get(Signal, signal_id)

    def list_demo_accounts(self) -> list[DemoAccount]:
        """Return all demo accounts. In M4, existing demo_accounts are considered enabled."""
        statement = select(DemoAccount).order_by(DemoAccount.user_id)
        return list(self._session.scalars(statement))

    def get_or_create_demo_account(
        self,
        *,
        user_id: int,
        start_balance_usdt: Decimal,
    ) -> DemoAccount:
        account = self._session.get(DemoAccount, user_id)
        if account is not None:
            return account
        account = DemoAccount(
            user_id=user_id,
            start_balance_usdt=start_balance_usdt,
            balance_usdt=start_balance_usdt,
        )
        self._session.add(account)
        self._session.flush()
        return account

    def get_user_settings(self, user_id: int) -> UserSetting | None:
        return self._session.get(UserSetting, user_id)

    def get_user(self, user_id: int) -> User | None:
        return self._session.get(User, user_id)

    def has_open_demo_trade(self, *, user_id: int, signal_id: int) -> bool:
        """Idempotency guard: one open demo trade per user per signal."""
        statement = select(DemoTrade.id).where(
            DemoTrade.user_id == user_id,
            DemoTrade.signal_id == signal_id,
            DemoTrade.status == "open",
        )
        return self._session.scalar(statement) is not None

    def create_open_demo_trade(
        self,
        *,
        account: DemoAccount,
        signal: Signal,
        margin_usdt: Decimal,
        notional_usdt: Decimal,
        qty: Decimal,
        liq_price: Decimal,
        legs: tuple[object, ...],
        fields_realism_applied: dict[str, bool],
    ) -> DemoTrade:
        """Persist an open demo trade and legs."""
        demo_trade = DemoTrade(
            signal_id=signal.id,
            user_id=account.user_id,
            symbol=signal.symbol,
            side=signal.side,
            leverage=signal.leverage,
            margin_usdt=margin_usdt,
            notional_usdt=notional_usdt,
            qty=qty,
            liq_price=liq_price,
            status="open",
            touched_tps=[],
            fields_realism_applied=dict(fields_realism_applied),
            opened_at=datetime.now(UTC),
        )
        self._session.add(demo_trade)
        self._session.flush()
        for leg in legs:
            self._session.add(
                DemoTradeLeg(
                    demo_trade_id=demo_trade.id,
                    leg_index=int(getattr(leg, "leg_index")),
                    target_price=Decimal(getattr(leg, "target_price")),
                    qty=Decimal(getattr(leg, "qty")),
                    status="open",
                )
            )
        self._session.flush()
        return demo_trade

    def list_open_demo_trades_by_symbol(self, symbol: str) -> list[DemoTrade]:
        statement = (
            select(DemoTrade)
            .where(DemoTrade.symbol == symbol.upper(), DemoTrade.status == "open")
            .options(
                selectinload(DemoTrade.legs),
                selectinload(DemoTrade.signal),
                selectinload(DemoTrade.user).selectinload(User.demo_account),
            )
            .order_by(DemoTrade.id)
        )
        return list(self._session.scalars(statement))

    def list_open_demo_symbols(self) -> list[str]:
        statement = (
            select(DemoTrade.symbol)
            .where(DemoTrade.status == "open")
            .distinct()
            .order_by(DemoTrade.symbol)
        )
        return list(self._session.scalars(statement))

    def mark_demo_legs_filled(
        self,
        *,
        demo_trade: DemoTrade,
        leg_indices: tuple[int, ...],
    ) -> None:
        if not leg_indices:
            return
        selected = set(leg_indices)
        filled_at = datetime.now(UTC)
        for leg in demo_trade.legs:
            if leg.leg_index in selected and leg.status == "open":
                leg.status = "filled"
                leg.filled_at = filled_at
        demo_trade.touched_tps = sorted(
            set(demo_trade.touched_tps or []).union(selected)
        )
        self._session.flush()

    def close_demo_trade(
        self,
        *,
        demo_trade: DemoTrade,
        closed_reason: str,
        realized_roi_pct: Decimal,
        realized_pnl_usdt: Decimal,
        touched_tps: tuple[int, ...],
    ) -> DemoTrade:
        """Close trade and update demo account balance with closed-trade PnL only."""
        if demo_trade.status != "open":
            raise ValueError("only open demo trades can be closed")
        if demo_trade.user_id is None:
            raise ValueError("demo trade is missing user_id")
        account = self._session.get(DemoAccount, demo_trade.user_id)
        if account is None:
            raise ValueError("demo account does not exist")

        demo_trade.status = "closed"
        demo_trade.closed_reason = closed_reason
        demo_trade.realized_roi_pct = realized_roi_pct
        demo_trade.realized_pnl_usdt = realized_pnl_usdt
        demo_trade.touched_tps = list(sorted(set(touched_tps)))
        demo_trade.closed_at = datetime.now(UTC)
        for leg in demo_trade.legs:
            if leg.status == "open":
                leg.status = "cancelled"
        account.balance_usdt += realized_pnl_usdt
        self._session.flush()
        return demo_trade

    def get_demo_stats(self, user_id: int) -> dict[str, Any]:
        """Return raw stats data for services.demo_engine.stats to format."""
        account = self._session.get(DemoAccount, user_id)
        settings = self._session.get(UserSetting, user_id)
        rows = self._session.execute(
            select(
                DemoTrade.signal_id,
                DemoTrade.status,
                DemoTrade.realized_pnl_usdt,
            )
            .where(DemoTrade.user_id == user_id)
            .order_by(DemoTrade.id)
        ).all()
        start_balance = account.start_balance_usdt if account else Decimal("0")
        current_balance = account.balance_usdt if account else start_balance
        return {
            "start_balance_usdt": start_balance,
            "current_balance_usdt": current_balance,
            "fixed_margin_usdt": (
                settings.fixed_margin_usdt if settings else Decimal("10")
            ),
            "trades": [
                {
                    "signal_id": row.signal_id,
                    "status": row.status,
                    "realized_pnl_usdt": row.realized_pnl_usdt,
                }
                for row in rows
            ],
        }
