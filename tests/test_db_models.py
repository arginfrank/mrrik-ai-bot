from __future__ import annotations

import shared.models as models
from sqlalchemy import CheckConstraint, Numeric, UniqueConstraint

from shared.models import Base

EXPECTED_TABLES = {
    "audit_log",
    "demo_accounts",
    "demo_trade_legs",
    "demo_trades",
    "exchange_credentials",
    "payments",
    "plans",
    "processed_events",
    "signals",
    "subscriptions",
    "trade_legs",
    "trades",
    "user_settings",
    "users",
}

EXPECTED_MODEL_CLASSES = {
    "AuditLog",
    "DemoAccount",
    "DemoTrade",
    "DemoTradeLeg",
    "ExchangeCredential",
    "Payment",
    "Plan",
    "ProcessedEvent",
    "Signal",
    "Subscription",
    "Trade",
    "TradeLeg",
    "User",
    "UserSetting",
}


def test_metadata_contains_exactly_the_core_tables() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_high_precision_price_and_quantity_columns() -> None:
    for table_name, column_name in (
        ("signals", "entry"),
        ("signals", "stop_loss"),
        ("trade_legs", "qty"),
        ("demo_trade_legs", "qty"),
    ):
        column_type = Base.metadata.tables[table_name].c[column_name].type

        assert isinstance(column_type, Numeric)
        assert (column_type.precision, column_type.scale) == (38, 18)


def test_replay_and_credential_unique_constraints() -> None:
    expected_constraints = {
        "payments": ("network", "txid"),
        "exchange_credentials": ("user_id", "exchange"),
    }

    for table_name, expected_columns in expected_constraints.items():
        constraints = Base.metadata.tables[table_name].constraints
        unique_columns = {
            tuple(column.name for column in constraint.columns)
            for constraint in constraints
            if isinstance(constraint, UniqueConstraint)
        }

        assert expected_columns in unique_columns


def test_processed_event_id_is_the_primary_key() -> None:
    primary_key = Base.metadata.tables["processed_events"].primary_key

    assert tuple(column.name for column in primary_key.columns) == ("event_id",)


def test_exchange_credentials_track_hedge_mode_validation() -> None:
    column = Base.metadata.tables["exchange_credentials"].c.hedge_enabled

    assert column.nullable is False
    assert str(column.server_default.arg) == "false"


def test_risk_model_check_constraint_allows_only_supported_models() -> None:
    constraints = Base.metadata.tables["user_settings"].constraints
    check_sql = {
        str(constraint.sqltext).replace(" ", "")
        for constraint in constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert "risk_modelIN(1,2,3)" in check_sql


def test_all_core_model_classes_are_exported() -> None:
    assert EXPECTED_MODEL_CLASSES <= set(models.__all__)

    for class_name in EXPECTED_MODEL_CLASSES:
        model_class = getattr(models, class_name)
        assert issubclass(model_class, Base)
