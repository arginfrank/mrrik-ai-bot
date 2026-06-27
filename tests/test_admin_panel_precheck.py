from __future__ import annotations

from decimal import Decimal

import pytest

from services.admin_panel.explorers import TransactionEvidence
from services.admin_panel.precheck import compare_address, precheck_payment
from shared.models import Payment


class FakeExplorer:
    def __init__(self, evidence: TransactionEvidence | None) -> None:
        self.evidence = evidence

    def fetch_transaction(self, txid: str) -> TransactionEvidence | None:
        assert txid == "tx-1"
        return self.evidence


def test_precheck_passes_correct_evidence() -> None:
    decision = _precheck(_evidence())

    assert decision.result == "pass"
    assert decision.amount_seen == Decimal("49")
    assert isinstance(decision.amount_seen, Decimal)


@pytest.mark.parametrize(
    ("change", "reason_fragment"),
    [
        ({"to_address": "0xWrong"}, "wallet"),
        ({"token_contract": "0xWrong"}, "token"),
        ({"amount": Decimal("48.999999")}, "amount"),
        ({"confirmations": 14}, "confirmations"),
    ],
)
def test_precheck_fails_deterministic_mismatch(
    change: dict[str, object],
    reason_fragment: str,
) -> None:
    evidence = _evidence(**change)

    decision = _precheck(evidence)

    assert decision.result == "fail"
    assert reason_fragment in decision.reason


def test_precheck_is_unknown_when_explorer_returns_none() -> None:
    decision = _precheck(None)

    assert decision.result == "unknown"


def test_address_comparison_is_case_insensitive_only_for_evm() -> None:
    assert compare_address(network="BEP20", actual="0xABC", expected="0xabc")
    assert not compare_address(network="TRC20", actual="Tron", expected="TRON")


def _precheck(evidence: TransactionEvidence | None):
    payment = Payment(
        id=1,
        user_id=2,
        plan_id=3,
        network="BEP20",
        to_address="0xWallet",
        amount_expected=Decimal("49"),
        txid="tx-1",
        status="submitted",
    )
    return precheck_payment(
        payment=payment,
        expected_wallets_by_network={"BEP20": "0xWallet"},
        token_contracts_by_network={"BEP20": "0xToken"},
        min_confirmations_by_network={"BEP20": 15},
        explorer_clients_by_network={"BEP20": FakeExplorer(evidence)},
    )


def _evidence(**changes: object) -> TransactionEvidence:
    values = {
        "txid": "tx-1",
        "network": "BEP20",
        "exists": True,
        "to_address": "0xwallet",
        "token_contract": "0xtoken",
        "amount": Decimal("49"),
        "confirmations": 15,
        "explorer_url": "https://bscscan.com/tx/tx-1",
    }
    values.update(changes)
    return TransactionEvidence(**values)  # type: ignore[arg-type]
