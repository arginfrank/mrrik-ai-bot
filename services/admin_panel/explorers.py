from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
from typing import Any, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from services.admin_panel.constants import NETWORK_TRC20


@dataclass(frozen=True)
class TransactionEvidence:
    txid: str
    network: str
    exists: bool
    to_address: str | None
    token_contract: str | None
    amount: Decimal | None
    confirmations: int | None
    explorer_url: str
    raw_status: str | None = None


class ExplorerClient(Protocol):
    def fetch_transaction(self, txid: str) -> TransactionEvidence | None:
        """Fetch and normalize transaction evidence."""


def normalize_evm_address(value: str | None) -> str | None:
    """Lowercase EVM addresses for comparison."""
    if value is None:
        return None
    normalized = value.strip()
    return normalized.lower() if normalized else None


def normalize_token_amount(raw_amount: str | int, decimals: int) -> Decimal:
    """Convert integer token units to Decimal token amount."""
    if decimals < 0:
        raise ValueError("token decimals must not be negative")
    return Decimal(str(raw_amount)) / (Decimal(10) ** decimals)


def build_explorer_url(*, network: str, txid: str, explorer_base_url: str) -> str:
    """Build public explorer URL."""
    if network not in {"TRC20", "BEP20", "POLYGON"}:
        raise ValueError(f"unsupported payment network: {network}")
    return f"{explorer_base_url.rstrip('/')}/{txid}"


class TronscanClient:
    def __init__(
        self,
        *,
        api_key: str | None,
        usdt_contract: str,
        decimals: int,
        explorer_base_url: str,
    ) -> None:
        self._api_key = api_key
        self._usdt_contract = usdt_contract
        self._decimals = decimals
        self._explorer_base_url = explorer_base_url

    def fetch_transaction(self, txid: str) -> TransactionEvidence | None:
        url = "https://apilist.tronscanapi.com/api/transaction-info?" + urlencode(
            {"hash": txid}
        )
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["TRON-PRO-API-KEY"] = self._api_key
        try:
            payload = _fetch_json(url, headers=headers)
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, Mapping):
            return None

        explorer_url = build_explorer_url(
            network=NETWORK_TRC20,
            txid=txid,
            explorer_base_url=self._explorer_base_url,
        )
        transfers = payload.get("trc20TransferInfo")
        transfer = _select_tron_transfer(transfers, self._usdt_contract)
        exists = bool(
            payload.get("hash")
            or payload.get("transactionHash")
            or transfer is not None
        )
        if not exists:
            return TransactionEvidence(
                txid=txid,
                network=NETWORK_TRC20,
                exists=False,
                to_address=None,
                token_contract=None,
                amount=None,
                confirmations=None,
                explorer_url=explorer_url,
                raw_status=_as_optional_string(payload.get("contractRet")),
            )

        source = transfer or _as_mapping(payload.get("contractData")) or payload
        to_address = _first_string(source, "to_address", "toAddress", "to")
        token_contract = _first_string(
            source,
            "contract_address",
            "contractAddress",
            "token_contract",
        )
        amount = _parse_tron_amount(source, self._decimals)
        confirmations = _parse_int(
            payload.get("confirmations", payload.get("confirmation"))
        )
        raw_status = _first_string(payload, "contractRet", "contract_ret", "status")
        return TransactionEvidence(
            txid=txid,
            network=NETWORK_TRC20,
            exists=True,
            to_address=to_address,
            token_contract=token_contract,
            amount=amount,
            confirmations=confirmations,
            explorer_url=explorer_url,
            raw_status=raw_status,
        )


class EvmScanClient:
    def __init__(
        self,
        *,
        network: str,
        api_key: str | None,
        usdt_contract: str,
        decimals: int,
        explorer_base_url: str,
        api_base_url: str,
    ) -> None:
        self._network = network
        self._api_key = api_key
        self._usdt_contract = usdt_contract
        self._decimals = decimals
        self._explorer_base_url = explorer_base_url
        self._api_base_url = api_base_url

    def fetch_transaction(self, txid: str) -> TransactionEvidence | None:
        parameters = {
            "module": "proxy",
            "action": "eth_getTransactionByHash",
            "txhash": txid,
        }
        if self._api_key:
            parameters["apikey"] = self._api_key
        try:
            payload = _fetch_json(
                f"{self._api_base_url}?{urlencode(parameters)}",
                headers={"Accept": "application/json"},
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, Mapping) or "result" not in payload:
            return None

        explorer_url = build_explorer_url(
            network=self._network,
            txid=txid,
            explorer_base_url=self._explorer_base_url,
        )
        transaction = _select_evm_transaction(payload.get("result"), txid)
        if transaction is None:
            return TransactionEvidence(
                txid=txid,
                network=self._network,
                exists=False,
                to_address=None,
                token_contract=None,
                amount=None,
                confirmations=None,
                explorer_url=explorer_url,
                raw_status=_as_optional_string(payload.get("message")),
            )

        token_contract, to_address, raw_amount = _parse_evm_transfer(transaction)
        amount = _normalize_optional_amount(raw_amount, self._decimals)
        confirmations = _parse_int(transaction.get("confirmations"))
        if confirmations is None:
            confirmations = self._calculate_confirmations(transaction)
        raw_status = _first_string(transaction, "txreceipt_status", "status")
        return TransactionEvidence(
            txid=txid,
            network=self._network,
            exists=True,
            to_address=to_address,
            token_contract=token_contract,
            amount=amount,
            confirmations=confirmations,
            explorer_url=explorer_url,
            raw_status=raw_status,
        )

    def _calculate_confirmations(self, transaction: Mapping[str, Any]) -> int | None:
        block_number = _parse_int(transaction.get("blockNumber"))
        if block_number is None:
            return None
        parameters = {"module": "proxy", "action": "eth_blockNumber"}
        if self._api_key:
            parameters["apikey"] = self._api_key
        try:
            payload = _fetch_json(
                f"{self._api_base_url}?{urlencode(parameters)}",
                headers={"Accept": "application/json"},
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, Mapping):
            return None
        latest_block = _parse_int(payload.get("result"))
        if latest_block is None or latest_block < block_number:
            return None
        return latest_block - block_number + 1


def _fetch_json(url: str, *, headers: dict[str, str]) -> Any:
    request = Request(url, headers=headers)
    with urlopen(request, timeout=10) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _select_tron_transfer(value: Any, contract: str) -> Mapping[str, Any] | None:
    if not isinstance(value, list):
        return None
    candidates = [item for item in value if isinstance(item, Mapping)]
    for candidate in candidates:
        candidate_contract = _first_string(
            candidate,
            "contract_address",
            "contractAddress",
            "token_contract",
        )
        if candidate_contract == contract:
            return candidate
    return candidates[0] if candidates else None


def _parse_tron_amount(source: Mapping[str, Any], decimals: int) -> Decimal | None:
    for key in ("amount_str", "amount", "value", "quant"):
        value = source.get(key)
        if value is not None:
            return _normalize_optional_amount(value, decimals)
    return None


def _select_evm_transaction(value: Any, txid: str) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    if not isinstance(value, list):
        return None
    candidates = [item for item in value if isinstance(item, Mapping)]
    for candidate in candidates:
        transaction_hash = _first_string(candidate, "hash", "transactionHash")
        if transaction_hash and transaction_hash.lower() == txid.lower():
            return candidate
    return candidates[0] if candidates else None


def _parse_evm_transfer(
    transaction: Mapping[str, Any],
) -> tuple[str | None, str | None, str | int | None]:
    direct_contract = _first_string(
        transaction,
        "contractAddress",
        "contract_address",
        "token_contract",
    )
    direct_amount = transaction.get("value")
    if direct_contract is not None:
        return direct_contract, _first_string(transaction, "to", "to_address"), direct_amount

    token_contract = _first_string(transaction, "to")
    input_data = _first_string(transaction, "input", "data")
    if input_data is None:
        return token_contract, None, None
    clean_input = input_data.removeprefix("0x")
    if not clean_input.startswith("a9059cbb") or len(clean_input) < 136:
        return token_contract, None, None
    arguments = clean_input[8:]
    to_address = "0x" + arguments[:64][-40:]
    try:
        raw_amount = int(arguments[64:128], 16)
    except ValueError:
        return token_contract, to_address, None
    return token_contract, to_address, raw_amount


def _normalize_optional_amount(value: Any, decimals: int) -> Decimal | None:
    if not isinstance(value, (str, int)):
        return None
    try:
        return normalize_token_amount(value, decimals)
    except (InvalidOperation, ValueError):
        return None


def _parse_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    try:
        return int(value, 16) if value.lower().startswith("0x") else int(value)
    except ValueError:
        return None


def _first_string(source: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = source.get(key)
        if value is not None:
            text = str(value).strip()
            if text:
                return text
    return None


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _as_optional_string(value: Any) -> str | None:
    return None if value is None else str(value)
