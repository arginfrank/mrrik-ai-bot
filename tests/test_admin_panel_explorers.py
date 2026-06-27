from __future__ import annotations

from decimal import Decimal

import pytest

from services.admin_panel import explorers
from services.admin_panel.explorers import (
    EvmScanClient,
    TronscanClient,
    build_explorer_url,
    normalize_evm_address,
    normalize_token_amount,
)


def test_normalize_evm_addresses_and_token_amounts() -> None:
    assert normalize_evm_address(" 0xAbCd ") == "0xabcd"
    assert normalize_evm_address(None) is None
    assert normalize_token_amount("49000000", 6) == Decimal("49")
    assert normalize_token_amount(49_000_000_000_000_000_000, 18) == Decimal("49")


@pytest.mark.parametrize(
    ("network", "base_url", "expected"),
    [
        ("TRC20", "https://tronscan.org/#/transaction", "https://tronscan.org/#/transaction/abc"),
        ("BEP20", "https://bscscan.com/tx", "https://bscscan.com/tx/abc"),
        ("POLYGON", "https://polygonscan.com/tx/", "https://polygonscan.com/tx/abc"),
    ],
)
def test_build_explorer_urls(network: str, base_url: str, expected: str) -> None:
    assert build_explorer_url(
        network=network,
        txid="abc",
        explorer_base_url=base_url,
    ) == expected


def test_parse_fake_tronscan_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        explorers,
        "_fetch_json",
        lambda *_args, **_kwargs: {
            "hash": "tron-tx",
            "contractRet": "SUCCESS",
            "confirmations": 25,
            "trc20TransferInfo": [
                {
                    "contract_address": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
                    "to_address": "TRON-WALLET",
                    "amount_str": "49000000",
                }
            ],
        },
    )
    client = TronscanClient(
        api_key="test-key",
        usdt_contract="TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
        decimals=6,
        explorer_base_url="https://tronscan.org/#/transaction",
    )

    evidence = client.fetch_transaction("tron-tx")

    assert evidence is not None
    assert evidence.exists is True
    assert evidence.to_address == "TRON-WALLET"
    assert evidence.amount == Decimal("49")
    assert evidence.confirmations == 25
    assert evidence.raw_status == "SUCCESS"


@pytest.mark.parametrize(
    ("network", "api_url", "explorer_url"),
    [
        ("BEP20", "https://api.bscscan.test/api", "https://bscscan.com/tx"),
        ("POLYGON", "https://api.polygonscan.test/api", "https://polygonscan.com/tx"),
    ],
)
def test_parse_fake_evm_scan_response(
    monkeypatch: pytest.MonkeyPatch,
    network: str,
    api_url: str,
    explorer_url: str,
) -> None:
    monkeypatch.setattr(
        explorers,
        "_fetch_json",
        lambda *_args, **_kwargs: {
            "status": "1",
            "result": [
                {
                    "hash": "0xtx",
                    "contractAddress": "0xToken",
                    "to": "0xWallet",
                    "value": "12500000",
                    "confirmations": "51",
                    "txreceipt_status": "1",
                }
            ],
        },
    )
    client = EvmScanClient(
        network=network,
        api_key="test-key",
        usdt_contract="0xToken",
        decimals=6,
        explorer_base_url=explorer_url,
        api_base_url=api_url,
    )

    evidence = client.fetch_transaction("0xtx")

    assert evidence is not None
    assert evidence.network == network
    assert evidence.exists is True
    assert evidence.token_contract == "0xToken"
    assert evidence.to_address == "0xWallet"
    assert evidence.amount == Decimal("12.5")
    assert evidence.confirmations == 51
