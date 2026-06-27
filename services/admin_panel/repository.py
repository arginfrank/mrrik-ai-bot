from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import create_engine, or_, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from services.admin_panel.constants import (
    PAYMENT_STATUS_APPROVED,
    PAYMENT_STATUS_REJECTED,
    PAYMENT_STATUS_SUBMITTED,
)
from shared.config import load_config
from shared.models import AuditLog, Payment, Signal, Subscription, User


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
        return self._session.get(User, user_id)

    def list_users(self) -> list[User]:
        statement = select(User).order_by(User.created_at, User.id)
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


def _utc_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)
