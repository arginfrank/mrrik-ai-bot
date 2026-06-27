from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from services.admin_panel.explorers import TransactionEvidence
from services.admin_panel.routes import create_app
from shared.models import Payment, Subscription, User


class FakeExplorer:
    def fetch_transaction(self, txid: str) -> TransactionEvidence:
        return TransactionEvidence(
            txid=txid,
            network="TRC20",
            exists=True,
            to_address="TRON-WALLET",
            token_contract="TRON-USDT",
            amount=Decimal("49"),
            confirmations=20,
            explorer_url=f"https://tronscan.test/{txid}",
        )


class FakeRepository:
    def __init__(self) -> None:
        self.user = User(id=2, telegram_id=222, username="safe-user", language="en")
        self.payment = Payment(
            id=1,
            user_id=2,
            plan_id=3,
            user=self.user,
            network="TRC20",
            to_address="TRON-WALLET",
            amount_expected=Decimal("49"),
            txid="tx-1",
            status="submitted",
        )
        self.subscription = Subscription(
            id=4,
            user_id=2,
            plan_id=3,
            status="pending",
            reminded_24h=False,
        )
        self.audit_logs: list[dict[str, Any]] = []

    def get_payment(self, payment_id: int) -> Payment | None:
        return self.payment if payment_id == self.payment.id else None

    def list_payment_queue(self) -> list[Payment]:
        return [self.payment] if self.payment.status == "submitted" else []

    def update_payment_precheck(self, **values: Any) -> Payment:
        payment = values.pop("payment")
        payment.precheck_result = values["precheck_result"]
        payment.amount_seen = values["amount_seen"]
        payment.confirmations = values["confirmations"]
        payment.explorer_url = values["explorer_url"]
        return payment

    def approve_payment(self, **values: Any) -> tuple[Payment, Subscription]:
        self.payment.status = "approved"
        self.payment.decided_by = values["admin_telegram_id"]
        self.subscription.status = "active"
        self.subscription.starts_at = datetime(2026, 1, 1, tzinfo=UTC)
        self.subscription.ends_at = datetime(2026, 1, 31, tzinfo=UTC)
        return self.payment, self.subscription

    def reject_payment(self, **values: Any) -> Payment:
        self.payment.status = "rejected"
        self.payment.decided_by = values["admin_telegram_id"]
        self.subscription.status = "rejected"
        return self.payment

    def get_user(self, user_id: int) -> User | None:
        return self.user if user_id == self.user.id else None

    def list_users(self) -> list[User]:
        return [self.user]

    def list_signal_anomalies(self) -> list[Any]:
        return []

    def write_audit_log(self, **values: Any) -> None:
        self.audit_logs.append(values)


class FakePublisher:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def publish(self, **event: Any) -> None:
        self.events.append(event)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)


@pytest.fixture
def panel() -> tuple[TestClient, FakeRepository, FakePublisher, FakeRedis]:
    repository = FakeRepository()
    publisher = FakePublisher()
    redis_client = FakeRedis()
    config = {
        "admin_telegram_ids": (99,),
        "ip_allowlist": ("testclient",),
        "expected_wallets_by_network": {"TRC20": "TRON-WALLET"},
        "token_contracts_by_network": {"TRC20": "TRON-USDT"},
        "min_confirmations_by_network": {"TRC20": 20},
        "explorer_clients_by_network": {"TRC20": FakeExplorer()},
        "database_url": "must-not-appear",
        "explorer_api_key": "must-not-appear",
    }
    app = create_app(
        repository_factory=lambda: repository,
        publisher=publisher,
        redis_client=redis_client,
        config=config,
    )
    return TestClient(app), repository, publisher, redis_client


def test_health_works_without_auth(panel: tuple[Any, ...]) -> None:
    client = panel[0]

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_payment_queue_requires_auth_and_renders_no_secrets(
    panel: tuple[Any, ...],
) -> None:
    client = panel[0]

    assert client.get("/payments").status_code == 403
    response = client.get("/payments", headers=_headers())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "tx-1" in response.text
    assert "must-not-appear" not in response.text


def test_precheck_updates_payment_and_publishes_admin_notification(
    panel: tuple[Any, ...],
) -> None:
    client, repository, publisher, _redis = panel

    response = client.post("/payments/1/precheck", headers=_headers())

    assert response.status_code == 200
    assert response.json()["result"] == "pass"
    assert repository.payment.precheck_result == "pass"
    assert publisher.events[-1]["stream"] == "notify"
    assert publisher.events[-1]["event_type"] == "notify.admin"
    assert repository.audit_logs[-1]["action"] == "payment.precheck"


def test_approve_publishes_payment_and_user_events(panel: tuple[Any, ...]) -> None:
    client, _repository, publisher, _redis = panel

    response = client.post("/payments/1/approve", headers=_headers())

    assert response.status_code == 200
    assert [(event["stream"], event["event_type"]) for event in publisher.events] == [
        ("payments", "payment.approved"),
        ("notify", "notify.user"),
    ]


def test_reject_publishes_payment_and_user_events(panel: tuple[Any, ...]) -> None:
    client, _repository, publisher, _redis = panel

    response = client.post(
        "/payments/1/reject",
        headers=_headers(),
        json={"reason": "wrong wallet"},
    )

    assert response.status_code == 200
    assert [(event["stream"], event["event_type"]) for event in publisher.events] == [
        ("payments", "payment.rejected"),
        ("notify", "notify.user"),
    ]
    assert publisher.events[0]["payload"]["reason"] == "wrong wallet"


@pytest.mark.parametrize(
    ("path", "key"),
    [
        ("/kill-switch/global", "kill_switch:global"),
        ("/kill-switch/user/2", "kill_switch:user:2"),
    ],
)
def test_kill_switch_on_and_off(
    panel: tuple[Any, ...],
    path: str,
    key: str,
) -> None:
    client, repository, _publisher, redis_client = panel

    on_response = client.post(f"{path}/on", headers=_headers())
    assert on_response.status_code == 200
    assert redis_client.values[key] == "1"

    off_response = client.post(f"{path}/off", headers=_headers())
    assert off_response.status_code == 200
    assert key not in redis_client.values
    assert repository.audit_logs[-1]["meta"]["enabled"] is False


def _headers() -> dict[str, str]:
    return {"X-Admin-Telegram-Id": "99"}
