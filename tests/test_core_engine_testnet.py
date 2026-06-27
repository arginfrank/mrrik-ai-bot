from __future__ import annotations

from decimal import Decimal

from services.core_engine.testnet import (
    check_testnet_readiness,
    default_testnet_scenario,
)


def test_default_scenario_uses_tiny_decimal_margin() -> None:
    scenario = default_testnet_scenario()

    assert isinstance(scenario.margin_usdt, Decimal)
    assert Decimal("0") < scenario.margin_usdt <= Decimal("5")


def test_readiness_is_false_when_disabled_or_credentials_missing() -> None:
    assert check_testnet_readiness(
        enabled=False, api_key_present=True, api_secret_present=True
    ).ready is False
    assert check_testnet_readiness(
        enabled=True, api_key_present=False, api_secret_present=True
    ).ready is False
    assert check_testnet_readiness(
        enabled=True, api_key_present=True, api_secret_present=False
    ).ready is False


def test_readiness_requires_explicit_enablement_and_both_credentials() -> None:
    readiness = check_testnet_readiness(
        enabled=True, api_key_present=True, api_secret_present=True
    )

    assert readiness.ready is True
    assert readiness.reasons == ()
