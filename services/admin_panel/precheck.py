from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from shared.models import Payment

from services.admin_panel.constants import (
    NETWORK_TRC20,
    PRECHECK_FAIL,
    PRECHECK_PASS,
    PRECHECK_UNKNOWN,
)
from services.admin_panel.explorers import (
    ExplorerClient,
    TransactionEvidence,
    normalize_evm_address,
)


@dataclass(frozen=True)
class PrecheckDecision:
    result: str
    reason: str
    amount_seen: Decimal | None
    confirmations: int | None
    explorer_url: str | None


def compare_address(*, network: str, actual: str | None, expected: str) -> bool:
    """Compare TRON or EVM addresses correctly."""
    if actual is None:
        return False
    if network == NETWORK_TRC20:
        return actual.strip() == expected.strip()
    return normalize_evm_address(actual) == normalize_evm_address(expected)


def verify_payment_evidence(
    *,
    payment: Payment,
    evidence: TransactionEvidence | None,
    expected_wallet: str,
    expected_token_contract: str,
    min_confirmations: int,
) -> PrecheckDecision:
    """Verify tx exists, destination wallet, token, amount, confirmations."""
    if evidence is None:
        return _decision(PRECHECK_UNKNOWN, "explorer evidence is unavailable")
    if not evidence.exists:
        return _decision(PRECHECK_FAIL, "transaction was not found", evidence=evidence)
    if not compare_address(
        network=payment.network,
        actual=evidence.to_address,
        expected=expected_wallet,
    ):
        return _decision(PRECHECK_FAIL, "destination wallet does not match", evidence=evidence)
    if not compare_address(
        network=payment.network,
        actual=evidence.token_contract,
        expected=expected_token_contract,
    ):
        return _decision(PRECHECK_FAIL, "token contract is not configured USDT", evidence=evidence)
    if evidence.amount is None:
        return _decision(PRECHECK_FAIL, "token amount is missing", evidence=evidence)
    if evidence.amount < payment.amount_expected:
        return _decision(PRECHECK_FAIL, "token amount is below expected amount", evidence=evidence)
    if evidence.confirmations is None:
        return _decision(PRECHECK_FAIL, "confirmation count is missing", evidence=evidence)
    if evidence.confirmations < min_confirmations:
        return _decision(
            PRECHECK_FAIL,
            "transaction has insufficient confirmations",
            evidence=evidence,
        )
    return _decision(PRECHECK_PASS, "on-chain evidence passed all checks", evidence=evidence)


def precheck_payment(
    *,
    payment: Payment,
    expected_wallets_by_network: Mapping[str, str],
    token_contracts_by_network: Mapping[str, str],
    min_confirmations_by_network: Mapping[str, int],
    explorer_clients_by_network: Mapping[str, ExplorerClient],
) -> PrecheckDecision:
    """Fetch explorer evidence and return deterministic precheck decision."""
    network = payment.network
    expected_wallet = expected_wallets_by_network.get(network)
    token_contract = token_contracts_by_network.get(network)
    min_confirmations = min_confirmations_by_network.get(network)
    client = explorer_clients_by_network.get(network)
    if (
        not expected_wallet
        or not token_contract
        or min_confirmations is None
        or client is None
    ):
        return _decision(PRECHECK_UNKNOWN, "payment network is not fully configured")
    if not payment.txid:
        return _decision(PRECHECK_UNKNOWN, "payment TXID is missing")
    try:
        evidence = client.fetch_transaction(payment.txid)
    except Exception:
        return _decision(PRECHECK_UNKNOWN, "explorer request failed")
    return verify_payment_evidence(
        payment=payment,
        evidence=evidence,
        expected_wallet=expected_wallet,
        expected_token_contract=token_contract,
        min_confirmations=min_confirmations,
    )


def _decision(
    result: str,
    reason: str,
    *,
    evidence: TransactionEvidence | None = None,
) -> PrecheckDecision:
    return PrecheckDecision(
        result=result,
        reason=reason,
        amount_seen=evidence.amount if evidence else None,
        confirmations=evidence.confirmations if evidence else None,
        explorer_url=evidence.explorer_url if evidence else None,
    )
