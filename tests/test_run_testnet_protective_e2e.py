from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

import scripts.run_testnet_protective_e2e as protective_e2e


class _OrderCallSpy:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        if name in {
            "set_leverage",
            "set_margin_type_isolated",
            "place_entry_limit",
            "place_entry_market",
            "place_stop_market",
            "place_take_profit_market",
            "close_position_market",
        }:
            self.calls.append(name)
            raise AssertionError(f"refusal guard allowed {name}")
        raise AttributeError(name)


@pytest.mark.parametrize(
    ("testnet_enabled", "e2e_flag", "protective_flag", "mainnet_key"),
    [
        (True, "1", None, None),
        (False, "1", "1", None),
        (True, "1", "1", "mainnet-key-must-refuse"),
    ],
)
def test_unsatisfied_guards_refuse_without_order_calls(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    testnet_enabled: bool,
    e2e_flag: str | None,
    protective_flag: str | None,
    mainnet_key: str | None,
) -> None:
    settings = SimpleNamespace(
        file=SimpleNamespace(
            testnet=SimpleNamespace(
                enabled=testnet_enabled,
                require_explicit_env=True,
            )
        ),
        env=SimpleNamespace(
            binance_testnet_api_key="testnet-key",
            binance_testnet_api_secret="testnet-secret",
        ),
    )
    monkeypatch.setattr(protective_e2e, "load_config", lambda: settings)

    for name, value in {
        "MRRIK_RUN_TESTNET_E2E": e2e_flag,
        "MRRIK_RUN_TESTNET_PROTECTIVE_E2E": protective_flag,
        "BINANCE_API_KEY": mainnet_key,
        "BINANCE_API_SECRET": None,
    }.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    spy = _OrderCallSpy()
    client_constructions = 0

    def fake_client(**_: Any) -> _OrderCallSpy:
        nonlocal client_constructions
        client_constructions += 1
        return spy

    monkeypatch.setattr(protective_e2e, "BinanceFuturesClient", fake_client)

    protective_e2e.main()

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "refused"
    assert output["reasons"]
    assert client_constructions == 0
    assert spy.calls == []
