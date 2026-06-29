from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from services.admin_panel.explorers import TransactionEvidence
from services.admin_panel.repository import OverviewMetrics
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

    def list_payments(self, *, status: str | None = None) -> list[Payment]:
        if status is None or self.payment.status == status:
            return [self.payment]
        return []

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
        self.audit_logs.append({"action": "payment.approve"})
        return self.payment, self.subscription

    def reject_payment(self, **values: Any) -> Payment:
        self.payment.status = "rejected"
        self.payment.decided_by = values["admin_telegram_id"]
        self.subscription.status = "rejected"
        self.audit_logs.append({"action": "payment.reject"})
        return self.payment

    def get_user(self, user_id: int) -> User | None:
        return self.user if user_id == self.user.id else None

    def list_users(self) -> list[User]:
        return [self.user]

    def list_user_summaries(self) -> list[User]:
        return [self.user]

    def set_user_blocked(self, *, user: User, blocked: bool) -> User:
        user.is_blocked = blocked
        return user

    def list_signal_anomalies(self) -> list[Any]:
        return []

    def list_trades(self, **_values: Any) -> list[Any]:
        return []

    def get_overview_metrics(self) -> OverviewMetrics:
        return OverviewMetrics(1, 1, 0, 1, 0, Decimal("4.2"), Decimal("9.1"))

    def list_recent_audit_logs(self, *, limit: int) -> list[Any]:
        assert limit == 10
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

    async def set(
        self,
        key: str,
        value: str,
        *,
        ex: int | None = None,
    ) -> None:
        if key.startswith("admin_session:"):
            assert ex == 43_200
        self.values[key] = value

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)


@pytest.fixture
def panel() -> tuple[TestClient, FakeRepository, FakePublisher, FakeRedis]:
    repository = FakeRepository()
    publisher = FakePublisher()
    redis_client = FakeRedis()
    config = {
        "auth_mode": "bootstrap_token",
        "admin_bootstrap_token": "long-random-bootstrap-token",
        "session_signing_secret": "session-signing-secret",
        "session_ttl_sec": 43_200,
        "admin_telegram_ids": (99,),
        "ip_allowlist": ("testclient",),
        "require_ip_allowlist": False,
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
    client = TestClient(app, base_url="https://testserver")
    return client, repository, publisher, redis_client


def test_health_and_login_page_work_without_auth(panel: tuple[Any, ...]) -> None:
    client = panel[0]

    assert client.get("/health").status_code == 200
    response = client.get("/login")

    assert response.status_code == 200
    assert "Bootstrap token" in response.text


def test_bootstrap_login_sets_hardened_cookie_and_wrong_token_fails(
    panel: tuple[Any, ...],
) -> None:
    client = panel[0]

    assert client.post("/login", data={"token": "wrong"}).status_code == 401
    response = _login(client)

    assert response.status_code == 303
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=strict" in cookie


def test_missing_session_and_old_header_do_not_authorize(panel: tuple[Any, ...]) -> None:
    client = panel[0]

    assert client.get("/overview").status_code == 401
    response = client.get(
        "/overview",
        headers={"X-Admin-Telegram-Id": "99"},
    )

    assert response.status_code == 401


def test_valid_session_renders_six_sections_without_secrets(
    panel: tuple[Any, ...],
) -> None:
    client = panel[0]
    _login(client)

    for path in ("/overview", "/payments", "/users", "/trades", "/signals", "/system"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith("text/html")
        assert "must-not-appear" not in response.text


def test_expired_session_does_not_authorize(panel: tuple[Any, ...]) -> None:
    client, _repository, _publisher, redis_client = panel
    _login(client)
    for key in list(redis_client.values):
        if key.startswith("admin_session:"):
            redis_client.values.pop(key)

    assert client.get("/overview").status_code == 401


def test_precheck_updates_payment_and_publishes_admin_notification(
    panel: tuple[Any, ...],
) -> None:
    client, repository, publisher, _redis = panel
    _login(client)

    response = client.post("/payments/1/precheck")

    assert response.status_code == 200
    assert response.json()["result"] == "pass"
    assert repository.payment.precheck_result == "pass"
    assert publisher.events[-1]["event_type"] == "notify.admin"
    assert repository.audit_logs[-1]["action"] == "payment.precheck"


def test_approve_and_reject_publish_and_audit(panel: tuple[Any, ...]) -> None:
    client, repository, publisher, _redis = panel
    _login(client)

    response = client.post("/payments/1/approve")

    assert response.status_code == 200
    assert [(event["stream"], event["event_type"]) for event in publisher.events] == [
        ("payments", "payment.approved"),
        ("notify", "notify.user"),
    ]
    assert repository.audit_logs[-1]["action"] == "payment.approve"


def test_reject_publishes_payment_and_user_events(panel: tuple[Any, ...]) -> None:
    client, repository, publisher, _redis = panel
    _login(client)

    response = client.post("/payments/1/reject", json={"reason": "wrong wallet"})

    assert response.status_code == 200
    assert [(event["stream"], event["event_type"]) for event in publisher.events] == [
        ("payments", "payment.rejected"),
        ("notify", "notify.user"),
    ]
    assert publisher.events[0]["payload"]["reason"] == "wrong wallet"
    assert repository.audit_logs[-1]["action"] == "payment.reject"


@pytest.mark.parametrize(
    ("path", "key"),
    [
        ("/kill-switch/global", "kill_switch:global"),
        ("/kill-switch/user/2", "kill_switch:user:2"),
    ],
)
def test_kill_switch_requires_confirmation_and_writes_audit(
    panel: tuple[Any, ...],
    path: str,
    key: str,
) -> None:
    client, repository, _publisher, redis_client = panel
    _login(client)

    assert client.post(f"{path}/on").status_code == 400
    on_response = client.post(f"{path}/on", json={"confirm": "PAUSE"})
    assert on_response.status_code == 200
    assert redis_client.values[key] == "1"

    off_response = client.post(f"{path}/off")
    assert off_response.status_code == 200
    assert key not in redis_client.values
    assert repository.audit_logs[-1]["meta"]["enabled"] is False


def _login(client: TestClient) -> Any:
    return client.post(
        "/login",
        data={"token": "long-random-bootstrap-token"},
        follow_redirects=False,
    )
