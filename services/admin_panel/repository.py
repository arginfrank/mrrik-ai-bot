from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import create_engine, distinct, func, or_, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from services.admin_panel.constants import (
    PAYMENT_STATUS_APPROVED,
    PAYMENT_STATUS_REJECTED,
    PAYMENT_STATUS_SUBMITTED,
)
from shared.config import load_config
from shared.models import AuditLog, Payment, Signal, Subscription, Trade, User


@dataclass(frozen=True)
class OverviewMetrics:
    active_users: int
    active_subscriptions: int
    expired_subscriptions: int
    pending_payments: int
    open_trades: int
    realized_pnl_today: Decimal
    realized_pnl_7d: Decimal


@dataclass(frozen=True)
class UserSummary:
    user: User
    subscription_status: str
    subscription_ends_at: datetime | None
    credential_valid: bool
    risk_model: int | None
    demo_balance_usdt: Decimal | None
    demo_open_trades: int
    demo_wins: int
    demo_losses: int


def make_engine_from_config() -> Any:
    """Create SQLAlchemy engine from configured DATABASE_URL."""
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


class AdminPanelRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_payment(self, payment_id: int) -> Payment | None:
        statement = (
            select(Payment)
            .where(Payment.id == payment_id)
            .options(selectinload(Payment.user), selectinload(Payment.plan))
        )
        return self._session.scalar(statement)

    def list_payment_queue(self) -> list[Payment]:
        """Return submitted payments ordered oldest first."""
        statement = (
            select(Payment)
            .where(Payment.status == PAYMENT_STATUS_SUBMITTED)
            .options(selectinload(Payment.user), selectinload(Payment.plan))
            .order_by(Payment.created_at, Payment.id)
        )
        return list(self._session.scalars(statement))

    def list_payments(self, *, status: str | None = None) -> list[Payment]:
        """Return payments, optionally filtered by decision status."""
        statement = select(Payment).options(
            selectinload(Payment.user), selectinload(Payment.plan)
        )
        if status:
            statement = statement.where(Payment.status == status)
        statement = statement.order_by(Payment.created_at.desc(), Payment.id.desc())
        return list(self._session.scalars(statement))

    def update_payment_precheck(
        self,
        *,
        payment: Payment,
        precheck_result: str,
        amount_seen: Decimal | None,
        confirmations: int | None,
        explorer_url: str | None,
    ) -> Payment:
        payment.precheck_result = precheck_result
        payment.amount_seen = amount_seen
        payment.confirmations = confirmations
        payment.explorer_url = explorer_url
        self._session.flush()
        return payment

    def approve_payment(
        self,
        *,
        payment: Payment,
        admin_telegram_id: int,
        now_utc: datetime | None = None,
    ) -> tuple[Payment, Subscription]:
        """Approve payment and activate matching pending subscription."""
        if payment.status != PAYMENT_STATUS_SUBMITTED:
            raise ValueError("payment is not submitted")
        now = _utc_datetime(now_utc)
        statement = (
            select(Subscription)
            .where(
                Subscription.user_id == payment.user_id,
                Subscription.plan_id == payment.plan_id,
                Subscription.status == "pending",
            )
            .options(selectinload(Subscription.plan))
            .order_by(Subscription.created_at.desc(), Subscription.id.desc())
        )
        subscription = self._session.scalar(statement)
        if subscription is None or subscription.plan is None:
            raise ValueError("matching pending subscription does not exist")

        payment.status = PAYMENT_STATUS_APPROVED
        payment.decided_at = now
        payment.decided_by = admin_telegram_id
        subscription.status = "active"
        subscription.starts_at = now
        subscription.ends_at = now + timedelta(days=subscription.plan.duration_days)
        subscription.reminded_24h = False
        self.write_audit_log(
            actor=f"admin:{admin_telegram_id}",
            action="payment.approve",
            entity="payment",
            entity_id=str(payment.id),
            meta={
                "user_id": payment.user_id,
                "subscription_id": subscription.id,
                "network": payment.network,
            },
        )
        self._session.flush()
        return payment, subscription

    def reject_payment(
        self,
        *,
        payment: Payment,
        admin_telegram_id: int,
        reason: str,
        now_utc: datetime | None = None,
    ) -> Payment:
        """Reject payment and leave subscription inactive."""
        if payment.status != PAYMENT_STATUS_SUBMITTED:
            raise ValueError("payment is not submitted")
        now = _utc_datetime(now_utc)
        payment.status = PAYMENT_STATUS_REJECTED
        payment.decided_at = now
        payment.decided_by = admin_telegram_id
        statement = (
            select(Subscription)
            .where(
                Subscription.user_id == payment.user_id,
                Subscription.plan_id == payment.plan_id,
                Subscription.status == "pending",
            )
            .order_by(Subscription.created_at.desc(), Subscription.id.desc())
        )
        subscription = self._session.scalar(statement)
        if subscription is not None:
            subscription.status = "rejected"
        self.write_audit_log(
            actor=f"admin:{admin_telegram_id}",
            action="payment.reject",
            entity="payment",
            entity_id=str(payment.id),
            meta={
                "user_id": payment.user_id,
                "subscription_id": subscription.id if subscription else None,
                "network": payment.network,
                "reason": reason,
            },
        )
        self._session.flush()
        return payment

    def get_user(self, user_id: int) -> User | None:
        statement = (
            select(User)
            .where(User.id == user_id)
            .options(
                selectinload(User.subscriptions),
                selectinload(User.payments).selectinload(Payment.plan),
                selectinload(User.exchange_credentials),
                selectinload(User.settings),
                selectinload(User.trades).selectinload(Trade.legs),
                selectinload(User.demo_account),
                selectinload(User.demo_trades),
            )
        )
        return self._session.scalar(statement)

    def list_users(self) -> list[User]:
        statement = select(User).order_by(User.created_at, User.id)
        return list(self._session.scalars(statement))

    def list_user_summaries(self) -> list[UserSummary]:
        """Return admin-safe user facts without credential material."""
        statement = (
            select(User)
            .options(
                selectinload(User.subscriptions),
                selectinload(User.exchange_credentials),
                selectinload(User.settings),
                selectinload(User.demo_account),
                selectinload(User.demo_trades),
            )
            .order_by(User.created_at, User.id)
        )
        return [_user_summary(user) for user in self._session.scalars(statement)]

    def set_user_blocked(self, *, user: User, blocked: bool) -> User:
        user.is_blocked = blocked
        self._session.flush()
        return user

    def list_trades(
        self,
        *,
        history: bool,
        user_id: int | None = None,
        symbol: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> list[Trade]:
        """Return real trades for the admin live/history views."""
        statement = select(Trade).options(
            selectinload(Trade.user),
            selectinload(Trade.signal),
            selectinload(Trade.legs),
        )
        if history:
            statement = statement.where(Trade.status == "closed")
            date_column = Trade.closed_at
            statement = statement.order_by(Trade.closed_at.desc(), Trade.id.desc())
        else:
            statement = statement.where(Trade.status.in_(("pending_entry", "open")))
            date_column = Trade.opened_at
            statement = statement.order_by(Trade.opened_at.desc(), Trade.id.desc())
        if user_id is not None:
            statement = statement.where(Trade.user_id == user_id)
        if symbol:
            statement = statement.where(Trade.symbol == symbol.upper())
        if date_from is not None:
            statement = statement.where(date_column >= date_from)
        if date_to is not None:
            statement = statement.where(date_column < date_to)
        return list(self._session.scalars(statement))

    def get_overview_metrics(
        self,
        *,
        now_utc: datetime | None = None,
    ) -> OverviewMetrics:
        now = _utc_datetime(now_utc)
        start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_7d = start_today - timedelta(days=6)
        return OverviewMetrics(
            active_users=self._count(
                select(func.count(distinct(Subscription.user_id))).where(
                    Subscription.status == "active"
                )
            ),
            active_subscriptions=self._count(
                select(func.count(Subscription.id)).where(
                    Subscription.status == "active"
                )
            ),
            expired_subscriptions=self._count(
                select(func.count(Subscription.id)).where(
                    Subscription.status == "expired"
                )
            ),
            pending_payments=self._count(
                select(func.count(Payment.id)).where(
                    Payment.status == PAYMENT_STATUS_SUBMITTED
                )
            ),
            open_trades=self._count(
                select(func.count(Trade.id)).where(
                    Trade.status.in_(("pending_entry", "open"))
                )
            ),
            realized_pnl_today=self._sum_pnl(start_today),
            realized_pnl_7d=self._sum_pnl(start_7d),
        )

    def list_recent_audit_logs(self, *, limit: int = 10) -> list[AuditLog]:
        statement = (
            select(AuditLog)
            .order_by(AuditLog.ts.desc(), AuditLog.id.desc())
            .limit(limit)
        )
        return list(self._session.scalars(statement))

    def list_signal_anomalies(self) -> list[Signal]:
        """Return signals with sanitizer alert or rejected status."""
        statement = (
            select(Signal)
            .where(
                or_(
                    Signal.status == "rejected",
                    Signal.sanitizer_notes["alert"].as_boolean().is_(True),
                )
            )
            .order_by(Signal.created_at.desc(), Signal.id.desc())
        )
        return list(self._session.scalars(statement))

    def write_audit_log(
        self,
        *,
        actor: str,
        action: str,
        entity: str,
        entity_id: str,
        meta: dict[str, Any] | None = None,
    ) -> AuditLog:
        audit_log = AuditLog(
            actor=actor,
            action=action,
            entity=entity,
            entity_id=entity_id,
            meta=meta,
        )
        self._session.add(audit_log)
        self._session.flush()
        return audit_log

    def _count(self, statement: Any) -> int:
        return int(self._session.scalar(statement) or 0)

    def _sum_pnl(self, earliest: datetime) -> Decimal:
        statement = select(func.coalesce(func.sum(Trade.realized_pnl_usdt), 0)).where(
            Trade.status == "closed",
            Trade.closed_at >= earliest,
        )
        return Decimal(self._session.scalar(statement) or 0)


def _utc_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def _user_summary(user: User) -> UserSummary:
    subscription = max(
        user.subscriptions,
        key=lambda item: (
            item.created_at or datetime.min.replace(tzinfo=UTC),
            item.id or 0,
        ),
        default=None,
    )
    demo_trades = list(user.demo_trades)
    closed_demo = [trade for trade in demo_trades if trade.status == "closed"]
    return UserSummary(
        user=user,
        subscription_status=subscription.status if subscription else "none",
        subscription_ends_at=subscription.ends_at if subscription else None,
        credential_valid=any(
            credential.is_valid for credential in user.exchange_credentials
        ),
        risk_model=user.settings.risk_model if user.settings else None,
        demo_balance_usdt=(
            user.demo_account.balance_usdt if user.demo_account is not None else None
        ),
        demo_open_trades=sum(trade.status == "open" for trade in demo_trades),
        demo_wins=sum((trade.realized_pnl_usdt or Decimal(0)) > 0 for trade in closed_demo),
        demo_losses=sum((trade.realized_pnl_usdt or Decimal(0)) <= 0 for trade in closed_demo),
    )
