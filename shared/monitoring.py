from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from shared.health import ServiceHealth
from shared.logging import redact_mapping


_SEVERITIES = frozenset({"info", "warning", "critical"})


def build_admin_alert_payload(
    *,
    admin_telegram_id: int,
    text: str,
    severity: str = "warning",
) -> dict[str, Any]:
    if severity not in _SEVERITIES:
        raise ValueError("severity must be info, warning, or critical")
    return {
        "telegram_id": admin_telegram_id,
        "text": text,
        "lang": "en",
        "severity": severity,
    }


def build_health_alerts(
    *,
    health: ServiceHealth,
    admin_telegram_ids: tuple[int, ...],
) -> list[dict[str, Any]]:
    if health.ok:
        return []
    failed = [dependency.name for dependency in health.dependencies if not dependency.ok]
    suffix = f" Failed dependencies: {', '.join(failed)}." if failed else ""
    text = f"Healthcheck failed for {health.service}.{suffix}"
    return [
        build_admin_alert_payload(
            admin_telegram_id=admin_id,
            text=text,
            severity="critical",
        )
        for admin_id in admin_telegram_ids
    ]


def build_audit_log_payload(
    *,
    actor: str,
    action: str,
    entity: str,
    entity_id: str,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a redacted audit record for dangerous actions and incidents."""
    return {
        "actor": actor,
        "action": action,
        "entity": entity,
        "entity_id": entity_id,
        "meta": redact_mapping(meta or {}),
    }
