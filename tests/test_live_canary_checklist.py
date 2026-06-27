from __future__ import annotations

from decimal import Decimal
import json
from types import SimpleNamespace

from scripts import live_canary_checklist


def _settings(*, confirmation: str | None, enabled: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        file=SimpleNamespace(
            live_canary=SimpleNamespace(
                enabled=enabled,
                max_margin_usdt=Decimal("5"),
                require_confirmation_text="I_ACCEPT_REAL_MONEY_RISK",
            )
        ),
        env=SimpleNamespace(live_canary_confirm=confirmation),
    )


def test_default_refuses(monkeypatch, capsys) -> None:
    monkeypatch.delenv("MRRIK_RUN_LIVE_CANARY_CHECKLIST", raising=False)
    monkeypatch.setattr(
        live_canary_checklist,
        "load_config",
        lambda: _settings(confirmation="I_ACCEPT_REAL_MONEY_RISK"),
    )

    live_canary_checklist.main()

    assert json.loads(capsys.readouterr().out)["status"] == "refused"


def test_missing_confirmation_refuses(monkeypatch, capsys) -> None:
    monkeypatch.setenv("MRRIK_RUN_LIVE_CANARY_CHECKLIST", "1")
    monkeypatch.setattr(
        live_canary_checklist, "load_config", lambda: _settings(confirmation=None)
    )

    live_canary_checklist.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "refused"
    assert any("confirmation" in reason for reason in payload["reasons"])


def test_correct_confirmation_prints_checklist_without_orders(monkeypatch, capsys) -> None:
    monkeypatch.setenv("MRRIK_RUN_LIVE_CANARY_CHECKLIST", "1")
    monkeypatch.setenv("LIVE_CANARY_MARGIN_USDT", "1")
    monkeypatch.setattr(
        live_canary_checklist,
        "load_config",
        lambda: _settings(confirmation="I_ACCEPT_REAL_MONEY_RISK"),
    )

    live_canary_checklist.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "checklist_ready"
    assert "DB backup done" in payload["checklist"]
    assert payload["live_canary_enabled"] is False
    assert payload["execution"] == "not_started"
