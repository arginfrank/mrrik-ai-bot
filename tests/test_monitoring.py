from __future__ import annotations

from shared.health import DependencyHealth, ServiceHealth, utc_now
from shared.monitoring import (
    build_admin_alert_payload,
    build_audit_log_payload,
    build_health_alerts,
)


def test_admin_alert_payload_contains_required_fields() -> None:
    payload = build_admin_alert_payload(
        admin_telegram_id=123,
        text="Reconciliation failed",
        severity="critical",
    )

    assert payload["telegram_id"] == 123
    assert payload["text"] == "Reconciliation failed"
    assert payload["severity"] == "critical"


def test_health_alert_is_only_built_for_unhealthy_service() -> None:
    unhealthy = ServiceHealth(
        service="core_engine",
        ok=False,
        ts_utc=utc_now(),
        dependencies=(DependencyHealth("database", False, "private detail"),),
    )
    healthy = ServiceHealth(service="demo_engine", ok=True, ts_utc=utc_now())

    alerts = build_health_alerts(health=unhealthy, admin_telegram_ids=(1, 2))

    assert len(alerts) == 2
    assert "private detail" not in str(alerts)
    assert build_health_alerts(health=healthy, admin_telegram_ids=(1,)) == []


def test_audit_payload_redacts_incident_secrets() -> None:
    payload = build_audit_log_payload(
        actor="service:core_engine",
        action="reconciliation.failed",
        entity="trade",
        entity_id="42",
        meta={"api_secret": "never-print", "attempt": 3},
    )

    assert "never-print" not in str(payload)
    assert payload["meta"]["attempt"] == 3
