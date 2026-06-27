"""Create the core MRRIK AI bot database tables.

Revision ID: 20260627_0001
Revises:
Create Date: 2026-06-27
"""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260627_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.Text(), nullable=True),
        sa.Column("language", sa.Text(), server_default=sa.text("'en'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("is_admin", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_blocked", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.CheckConstraint("language <> ''", name="ck_users_language_not_empty"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_id"),
    )
    op.create_table(
        "plans",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("price_usdt", sa.Numeric(18, 6), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.CheckConstraint(
            "duration_days > 0",
            name="ck_plans_duration_days_positive",
        ),
        sa.CheckConstraint("price_usdt > 0", name="ck_plans_price_usdt_positive"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )

    plans_table = sa.table(
        "plans",
        sa.column("code", sa.Text()),
        sa.column("duration_days", sa.Integer()),
        sa.column("price_usdt", sa.Numeric(18, 6)),
        sa.column("is_active", sa.Boolean()),
    )
    op.bulk_insert(
        plans_table,
        [
            {
                "code": "P30",
                "duration_days": 30,
                "price_usdt": Decimal("49"),
                "is_active": True,
            },
            {
                "code": "P90",
                "duration_days": 90,
                "price_usdt": Decimal("129"),
                "is_active": True,
            },
        ],
    )

    op.create_table(
        "signals",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_msg_id", sa.BigInteger(), nullable=True),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("entry", sa.Numeric(38, 18), nullable=False),
        sa.Column("stop_loss", sa.Numeric(38, 18), nullable=False),
        sa.Column("leverage", sa.Integer(), nullable=False),
        sa.Column(
            "targets_raw",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "targets_clean",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "sanitizer_notes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("side IN ('LONG','SHORT')", name="ck_signals_side"),
        sa.CheckConstraint("leverage > 0", name="ck_signals_leverage_positive"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("actor", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=True),
        sa.Column("entity", sa.Text(), nullable=True),
        sa.Column("entity_id", sa.Text(), nullable=True),
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "processed_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("plan_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reminded_24h", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "payments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("plan_id", sa.Integer(), nullable=True),
        sa.Column("network", sa.Text(), nullable=False),
        sa.Column("to_address", sa.Text(), nullable=False),
        sa.Column("amount_expected", sa.Numeric(18, 6), nullable=False),
        sa.Column("txid", sa.Text(), nullable=True),
        sa.Column("amount_seen", sa.Numeric(18, 6), nullable=True),
        sa.Column("confirmations", sa.Integer(), nullable=True),
        sa.Column("explorer_url", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'submitted'"),
            nullable=False,
        ),
        sa.Column("precheck_result", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "network IN ('TRC20','BEP20','POLYGON')",
            name="ck_payments_network",
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("network", "txid", name="uq_payments_network_txid"),
    )
    op.create_table(
        "exchange_credentials",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "exchange",
            sa.Text(),
            server_default=sa.text("'binance'"),
            nullable=False,
        ),
        sa.Column("api_key_enc", sa.LargeBinary(), nullable=False),
        sa.Column("api_secret_enc", sa.LargeBinary(), nullable=False),
        sa.Column(
            "scope_verified",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("is_valid", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "exchange",
            name="uq_exchange_credentials_user_id_exchange",
        ),
    )
    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "fixed_margin_usdt",
            sa.Numeric(18, 6),
            server_default=sa.text("10"),
            nullable=False,
        ),
        sa.Column(
            "risk_model",
            sa.SmallInteger(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "model3_exit_roi_pct",
            sa.Numeric(6, 3),
            server_default=sa.text("20"),
            nullable=False,
        ),
        sa.Column(
            "max_concurrent",
            sa.Integer(),
            server_default=sa.text("10"),
            nullable=False,
        ),
        sa.Column(
            "leverage_mode",
            sa.Text(),
            server_default=sa.text("'signal'"),
            nullable=False,
        ),
        sa.Column("leverage_cap", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "fixed_margin_usdt > 0",
            name="ck_user_settings_fixed_margin_positive",
        ),
        sa.CheckConstraint(
            "max_concurrent >= 0",
            name="ck_user_settings_max_concurrent_nonnegative",
        ),
        sa.CheckConstraint(
            "risk_model IN (1,2,3)",
            name="ck_user_settings_risk_model",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "trades",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("signal_id", sa.BigInteger(), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("leverage", sa.Integer(), nullable=False),
        sa.Column("margin_usdt", sa.Numeric(18, 6), nullable=False),
        sa.Column("notional_usdt", sa.Numeric(18, 6), nullable=False),
        sa.Column("qty", sa.Numeric(38, 18), nullable=False),
        sa.Column("entry_order_id", sa.Text(), nullable=True),
        sa.Column("sl_order_id", sa.Text(), nullable=True),
        sa.Column("liq_price", sa.Numeric(38, 18), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("realized_pnl_usdt", sa.Numeric(18, 6), nullable=True),
        sa.Column("realized_roi_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column(
            "touched_tps",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("closed_reason", sa.Text(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("side IN ('LONG','SHORT')", name="ck_trades_side"),
        sa.CheckConstraint("leverage > 0", name="ck_trades_leverage_positive"),
        sa.ForeignKeyConstraint(["signal_id"], ["signals.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "trade_legs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("trade_id", sa.BigInteger(), nullable=True),
        sa.Column("leg_index", sa.Integer(), nullable=False),
        sa.Column("target_price", sa.Numeric(38, 18), nullable=False),
        sa.Column("qty", sa.Numeric(38, 18), nullable=False),
        sa.Column("tp_order_id", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'open'"),
            nullable=False,
        ),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["trade_id"], ["trades.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "demo_accounts",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "start_balance_usdt",
            sa.Numeric(18, 6),
            server_default=sa.text("1000"),
            nullable=False,
        ),
        sa.Column(
            "balance_usdt",
            sa.Numeric(18, 6),
            server_default=sa.text("1000"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "demo_trades",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("signal_id", sa.BigInteger(), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("leverage", sa.Integer(), nullable=False),
        sa.Column("margin_usdt", sa.Numeric(18, 6), nullable=False),
        sa.Column("notional_usdt", sa.Numeric(18, 6), nullable=False),
        sa.Column("qty", sa.Numeric(38, 18), nullable=False),
        sa.Column("liq_price", sa.Numeric(38, 18), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("realized_pnl_usdt", sa.Numeric(18, 6), nullable=True),
        sa.Column("realized_roi_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column(
            "touched_tps",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("closed_reason", sa.Text(), nullable=True),
        sa.Column(
            "fields_realism_applied",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("side IN ('LONG','SHORT')", name="ck_demo_trades_side"),
        sa.CheckConstraint(
            "leverage > 0",
            name="ck_demo_trades_leverage_positive",
        ),
        sa.ForeignKeyConstraint(["signal_id"], ["signals.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "demo_trade_legs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("demo_trade_id", sa.BigInteger(), nullable=True),
        sa.Column("leg_index", sa.Integer(), nullable=False),
        sa.Column("target_price", sa.Numeric(38, 18), nullable=False),
        sa.Column("qty", sa.Numeric(38, 18), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'open'"),
            nullable=False,
        ),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["demo_trade_id"], ["demo_trades.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("demo_trade_legs")
    op.drop_table("demo_trades")
    op.drop_table("demo_accounts")
    op.drop_table("trade_legs")
    op.drop_table("trades")
    op.drop_table("user_settings")
    op.drop_table("exchange_credentials")
    op.drop_table("payments")
    op.drop_table("subscriptions")
    op.drop_table("processed_events")
    op.drop_table("audit_log")
    op.drop_table("signals")
    op.drop_table("plans")
    op.drop_table("users")
