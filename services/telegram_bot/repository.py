from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from services.telegram_bot.constants import SUPPORTED_PAYMENT_NETWORKS
from shared.config import load_config
from shared.models import (
    DemoAccount,
    DemoTrade,
    DemoTradeLeg,
    ExchangeCredential,
    Payment,
    Plan,
    Subscription,
    User,
    UserSetting,
)


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


class TelegramBotRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_or_create_user(
        self,
        *,
        telegram_id: int,
        username: str | None = None,
        language: str = "en",
    ) -> User:
        statement = select(User).where(User.telegram_id == telegram_id)
        user = self._session.scalar(statement)
        if user is not None:
            if username is not None and user.username != username:
                user.username = username
                self._session.flush()
            return user
        user = User(
            telegram_id=telegram_id,
            username=username,
            language=language,
        )
        self._session.add(user)
        self._session.flush()
        return user

    def set_language(self, *, telegram_id: int, language: str) -> User:
        statement = select(User).where(User.telegram_id == telegram_id)
        user = self._session.scalar(statement)
        if user is None:
            raise ValueError("user does not exist")
        user.language = language
        self._session.flush()
        return user

    def list_active_plans(self) -> list[Plan]:
        statement = (
            select(Plan)
            .where(Plan.is_active.is_(True))
            .order_by(Plan.duration_days, Plan.id)
        )
        return list(self._session.scalars(statement))

    def get_plan_by_code(self, code: str) -> Plan | None:
        statement = select(Plan).where(Plan.code == code.upper())
        return self._session.scalar(statement)

    def get_active_subscription(self, user_id: int) -> Subscription | None:
        now_utc = datetime.now(UTC)
        statement = (
            select(Subscription)
            .where(
                Subscription.user_id == user_id,
                Subscription.status == "active",
                Subscription.ends_at.is_not(None),
                Subscription.ends_at > now_utc,
            )
            .order_by(Subscription.ends_at.desc(), Subscription.id.desc())
        )
        return self._session.scalar(statement)

    def get_latest_subscription(self, user_id: int) -> Subscription | None:
        statement = (
            select(Subscription)
            .where(Subscription.user_id == user_id)
            .order_by(Subscription.created_at.desc(), Subscription.id.desc())
        )
        return self._session.scalar(statement)

    def create_pending_subscription_and_payment(
        self,
        *,
        user: User,
        plan: Plan,
        network: str,
        to_address: str,
        txid: str,
    ) -> tuple[Subscription, Payment]:
        if network not in SUPPORTED_PAYMENT_NETWORKS:
            raise ValueError(f"unsupported payment network: {network}")
        clean_txid = txid.strip()
        if not clean_txid:
            raise ValueError("txid must not be empty")
        if not to_address.strip():
            raise ValueError("wallet address must not be empty")

        subscription = Subscription(
            user=user,
            plan=plan,
            status="pending",
            starts_at=None,
            ends_at=None,
            reminded_24h=False,
        )
        payment = Payment(
            user=user,
            plan=plan,
            network=network,
            to_address=to_address.strip(),
            amount_expected=plan.price_usdt,
            txid=clean_txid,
            status="submitted",
        )
        self._session.add_all((subscription, payment))
        self._session.flush()
        return subscription, payment

    def get_or_create_demo_account(
        self,
        *,
        user: User,
        start_balance_usdt: Decimal,
    ) -> DemoAccount:
        account = self._session.get(DemoAccount, user.id)
        if account is not None:
            return account
        account = DemoAccount(
            user=user,
            start_balance_usdt=start_balance_usdt,
            balance_usdt=start_balance_usdt,
        )
        self._session.add(account)
        self._session.flush()
        return account

    def get_demo_stats(self, user_id: int) -> dict[str, Any]:
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
        return {
            "start_balance_usdt": start_balance,
            "current_balance_usdt": (
                account.balance_usdt if account is not None else start_balance
            ),
            "fixed_margin_usdt": (
                settings.fixed_margin_usdt if settings is not None else Decimal("10")
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

    def reset_demo_for_user(self, user_id: int) -> int:
        trade_ids = select(DemoTrade.id).where(DemoTrade.user_id == user_id)
        self._session.execute(
            delete(DemoTradeLeg).where(DemoTradeLeg.demo_trade_id.in_(trade_ids))
        )
        result = self._session.execute(
            delete(DemoTrade).where(DemoTrade.user_id == user_id)
        )
        account = self._session.get(DemoAccount, user_id)
        if account is not None:
            account.balance_usdt = account.start_balance_usdt
        self._session.flush()
        return int(result.rowcount or 0)

    def get_or_create_user_settings(self, *, user: User) -> UserSetting:
        settings = self._session.get(UserSetting, user.id)
        if settings is not None:
            return settings
        settings = UserSetting(user=user)
        self._session.add(settings)
        self._session.flush()
        return settings

    def update_fixed_margin(
        self,
        *,
        user: User,
        fixed_margin_usdt: Decimal,
    ) -> UserSetting:
        if fixed_margin_usdt <= 0:
            raise ValueError("fixed margin must be positive")
        settings = self.get_or_create_user_settings(user=user)
        settings.fixed_margin_usdt = fixed_margin_usdt
        settings.updated_at = datetime.now(UTC)
        self._session.flush()
        return settings

    def update_risk_model(
        self,
        *,
        user: User,
        risk_model: int,
    ) -> UserSetting:
        if risk_model not in (1, 2, 3):
            raise ValueError("risk model must be 1, 2, or 3")
        settings = self.get_or_create_user_settings(user=user)
        settings.risk_model = risk_model
        settings.updated_at = datetime.now(UTC)
        self._session.flush()
        return settings

    def store_exchange_credentials(
        self,
        *,
        user: User,
        api_key_enc: bytes,
        api_secret_enc: bytes,
    ) -> ExchangeCredential:
        statement = select(ExchangeCredential).where(
            ExchangeCredential.user_id == user.id,
            ExchangeCredential.exchange == "binance",
        )
        credential = self._session.scalar(statement)
        if credential is None:
            credential = ExchangeCredential(
                user=user,
                exchange="binance",
                api_key_enc=api_key_enc,
                api_secret_enc=api_secret_enc,
                scope_verified=False,
                is_valid=False,
            )
            self._session.add(credential)
        else:
            credential.api_key_enc = api_key_enc
            credential.api_secret_enc = api_secret_enc
            credential.scope_verified = False
            credential.is_valid = False
        self._session.flush()
        return credential

    def list_subscriptions_due_for_24h_reminder(
        self,
        *,
        now_utc: datetime,
    ) -> list[Subscription]:
        now = _require_aware_utc(now_utc)
        statement = (
            select(Subscription)
            .where(
                Subscription.status == "active",
                Subscription.ends_at.is_not(None),
                Subscription.ends_at > now,
                Subscription.ends_at <= now + timedelta(hours=24),
                Subscription.reminded_24h.is_(False),
            )
            .options(selectinload(Subscription.user))
            .order_by(Subscription.ends_at, Subscription.id)
        )
        return list(self._session.scalars(statement))

    def mark_subscription_reminded_24h(self, subscription: Subscription) -> None:
        subscription.reminded_24h = True
        self._session.flush()

    def list_subscriptions_due_for_expiry(
        self,
        *,
        now_utc: datetime,
    ) -> list[Subscription]:
        now = _require_aware_utc(now_utc)
        statement = (
            select(Subscription)
            .where(
                Subscription.status == "active",
                Subscription.ends_at.is_not(None),
                Subscription.ends_at <= now,
            )
            .options(selectinload(Subscription.user))
            .order_by(Subscription.ends_at, Subscription.id)
        )
        return list(self._session.scalars(statement))

    def mark_subscription_expired(self, subscription: Subscription) -> None:
        subscription.status = "expired"
        self._session.flush()


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)
