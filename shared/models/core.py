from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID as PythonUUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.models.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("language <> ''", name="ck_users_language_not_empty"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'en'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    is_admin: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=false(),
    )
    is_blocked: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=false(),
    )

    subscriptions: Mapped[list[Subscription]] = relationship(back_populates="user")
    payments: Mapped[list[Payment]] = relationship(back_populates="user")
    exchange_credentials: Mapped[list[ExchangeCredential]] = relationship(
        back_populates="user"
    )
    settings: Mapped[UserSetting | None] = relationship(
        back_populates="user",
        uselist=False,
    )
    trades: Mapped[list[Trade]] = relationship(back_populates="user")
    demo_account: Mapped[DemoAccount | None] = relationship(
        back_populates="user",
        uselist=False,
    )
    demo_trades: Mapped[list[DemoTrade]] = relationship(back_populates="user")


class Plan(Base):
    __tablename__ = "plans"
    __table_args__ = (
        CheckConstraint("duration_days > 0", name="ck_plans_duration_days_positive"),
        CheckConstraint("price_usdt > 0", name="ck_plans_price_usdt_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    price_usdt: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=true(),
    )

    subscriptions: Mapped[list[Subscription]] = relationship(back_populates="plan")
    payments: Mapped[list[Payment]] = relationship(back_populates="plan")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("plans.id"), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    reminded_24h: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=false(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped[User | None] = relationship(back_populates="subscriptions")
    plan: Mapped[Plan | None] = relationship(back_populates="subscriptions")


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("network", "txid", name="uq_payments_network_txid"),
        CheckConstraint(
            "network IN ('TRC20','BEP20','POLYGON')",
            name="ck_payments_network",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("plans.id"), nullable=True)
    network: Mapped[str] = mapped_column(Text, nullable=False)
    to_address: Mapped[str] = mapped_column(Text, nullable=False)
    amount_expected: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    txid: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount_seen: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    confirmations: Mapped[int | None] = mapped_column(Integer, nullable=True)
    explorer_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'submitted'"),
    )
    precheck_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    decided_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    user: Mapped[User | None] = relationship(back_populates="payments")
    plan: Mapped[Plan | None] = relationship(back_populates="payments")


class ExchangeCredential(Base):
    __tablename__ = "exchange_credentials"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "exchange",
            name="uq_exchange_credentials_user_id_exchange",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    exchange: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'binance'"),
    )
    api_key_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    api_secret_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    scope_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=false(),
    )
    is_valid: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=false(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped[User | None] = relationship(back_populates="exchange_credentials")


class UserSetting(Base):
    __tablename__ = "user_settings"
    __table_args__ = (
        CheckConstraint("risk_model IN (1,2,3)", name="ck_user_settings_risk_model"),
        CheckConstraint(
            "fixed_margin_usdt > 0",
            name="ck_user_settings_fixed_margin_positive",
        ),
        CheckConstraint(
            "max_concurrent >= 0",
            name="ck_user_settings_max_concurrent_nonnegative",
        ),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        primary_key=True,
    )
    fixed_margin_usdt: Mapped[Decimal] = mapped_column(
        Numeric(18, 6),
        nullable=False,
        server_default=text("10"),
    )
    risk_model: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        server_default=text("1"),
    )
    model3_exit_roi_pct: Mapped[Decimal] = mapped_column(
        Numeric(6, 3),
        nullable=False,
        server_default=text("20"),
    )
    max_concurrent: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("10"),
    )
    leverage_mode: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'signal'"),
    )
    leverage_cap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped[User] = relationship(back_populates="settings")


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        CheckConstraint("side IN ('LONG','SHORT')", name="ck_signals_side"),
        CheckConstraint("leverage > 0", name="ck_signals_leverage_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_msg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    entry: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    stop_loss: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    leverage: Mapped[int] = mapped_column(Integer, nullable=False)
    targets_raw: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    targets_clean: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    sanitizer_notes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    trades: Mapped[list[Trade]] = relationship(back_populates="signal")
    demo_trades: Mapped[list[DemoTrade]] = relationship(back_populates="signal")


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (
        CheckConstraint("side IN ('LONG','SHORT')", name="ck_trades_side"),
        CheckConstraint("leverage > 0", name="ck_trades_leverage_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    leverage: Mapped[int] = mapped_column(Integer, nullable=False)
    margin_usdt: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    notional_usdt: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    entry_order_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    sl_order_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    liq_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    realized_pnl_usdt: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 6),
        nullable=True,
    )
    realized_roi_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 4),
        nullable=True,
    )
    touched_tps: Mapped[list[int] | None] = mapped_column(JSONB, nullable=True)
    closed_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    signal: Mapped[Signal | None] = relationship(back_populates="trades")
    user: Mapped[User | None] = relationship(back_populates="trades")
    legs: Mapped[list[TradeLeg]] = relationship(back_populates="trade")


class TradeLeg(Base):
    __tablename__ = "trade_legs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_id: Mapped[int | None] = mapped_column(ForeignKey("trades.id"), nullable=True)
    leg_index: Mapped[int] = mapped_column(Integer, nullable=False)
    target_price: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    tp_order_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'open'"),
    )
    filled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    trade: Mapped[Trade | None] = relationship(back_populates="legs")


class DemoAccount(Base):
    __tablename__ = "demo_accounts"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        primary_key=True,
    )
    start_balance_usdt: Mapped[Decimal] = mapped_column(
        Numeric(18, 6),
        nullable=False,
        server_default=text("1000"),
    )
    balance_usdt: Mapped[Decimal] = mapped_column(
        Numeric(18, 6),
        nullable=False,
        server_default=text("1000"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped[User] = relationship(back_populates="demo_account")


class DemoTrade(Base):
    __tablename__ = "demo_trades"
    __table_args__ = (
        CheckConstraint("side IN ('LONG','SHORT')", name="ck_demo_trades_side"),
        CheckConstraint("leverage > 0", name="ck_demo_trades_leverage_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    leverage: Mapped[int] = mapped_column(Integer, nullable=False)
    margin_usdt: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    notional_usdt: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    liq_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    realized_pnl_usdt: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 6),
        nullable=True,
    )
    realized_roi_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 4),
        nullable=True,
    )
    touched_tps: Mapped[list[int] | None] = mapped_column(JSONB, nullable=True)
    closed_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    fields_realism_applied: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    opened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    signal: Mapped[Signal | None] = relationship(back_populates="demo_trades")
    user: Mapped[User | None] = relationship(back_populates="demo_trades")
    legs: Mapped[list[DemoTradeLeg]] = relationship(back_populates="demo_trade")


class DemoTradeLeg(Base):
    __tablename__ = "demo_trade_legs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    demo_trade_id: Mapped[int | None] = mapped_column(
        ForeignKey("demo_trades.id"),
        nullable=True,
    )
    leg_index: Mapped[int] = mapped_column(Integer, nullable=False)
    target_price: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'open'"),
    )
    filled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    demo_trade: Mapped[DemoTrade | None] = relationship(back_populates="legs")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str | None] = mapped_column(Text, nullable=True)
    entity: Mapped[str | None] = mapped_column(Text, nullable=True)
    entity_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class ProcessedEvent(Base):
    __tablename__ = "processed_events"

    event_id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    ts: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        server_default=func.now(),
    )
