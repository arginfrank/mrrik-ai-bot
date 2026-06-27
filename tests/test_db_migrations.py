from __future__ import annotations

import os
from decimal import Decimal
from typing import Iterator

import pytest
from sqlalchemy import Engine, create_engine, inspect, text

from shared.config import load_config

RUN_DB_TESTS = os.getenv("RUN_DB_TESTS") == "1"
pytestmark = pytest.mark.skipif(
    not RUN_DB_TESTS,
    reason="PostgreSQL migration tests require RUN_DB_TESTS=1",
)

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


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    engine = create_engine(load_config().env.database_url)
    try:
        yield engine
    finally:
        engine.dispose()


def test_migration_created_all_core_tables(database_engine: Engine) -> None:
    table_names = set(inspect(database_engine).get_table_names())

    assert EXPECTED_TABLES <= table_names


def test_migration_seeded_subscription_plans(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT code, duration_days, price_usdt, is_active "
                "FROM plans ORDER BY code"
            )
        ).mappings()
        plans = {row["code"]: row for row in rows}

    assert plans["P30"]["duration_days"] == 30
    assert plans["P30"]["price_usdt"] == Decimal("49")
    assert plans["P30"]["is_active"] is True
    assert plans["P90"]["duration_days"] == 90
    assert plans["P90"]["price_usdt"] == Decimal("129")
    assert plans["P90"]["is_active"] is True


@pytest.mark.parametrize(
    ("table_name", "expected_columns"),
    [
        ("payments", {"network", "txid"}),
        ("exchange_credentials", {"user_id", "exchange"}),
    ],
)
def test_migration_created_required_unique_constraints(
    database_engine: Engine,
    table_name: str,
    expected_columns: set[str],
) -> None:
    unique_constraints = inspect(database_engine).get_unique_constraints(table_name)

    assert any(
        set(constraint["column_names"]) == expected_columns
        for constraint in unique_constraints
    )


def test_migration_created_processed_event_primary_key(database_engine: Engine) -> None:
    primary_key = inspect(database_engine).get_pk_constraint("processed_events")

    assert primary_key["constrained_columns"] == ["event_id"]
