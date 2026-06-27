from __future__ import annotations

import asyncio
from datetime import UTC
import json

from services.core_engine.health import check_health
from shared.health import DependencyHealth, ServiceHealth, health_to_dict, utc_now


def test_dependency_health_converts_to_dict() -> None:
    health = ServiceHealth(
        service="core_engine",
        ok=True,
        ts_utc=utc_now(),
        dependencies=(DependencyHealth("redis", True),),
    )

    result = health_to_dict(health)

    assert result["dependencies"] == [{"name": "redis", "ok": True, "detail": None}]
    assert result["ts_utc"].endswith("+00:00")


def test_service_health_is_false_when_a_dependency_fails() -> None:
    health = ServiceHealth(
        service="core_engine",
        ok=True,
        ts_utc=utc_now(),
        dependencies=(DependencyHealth("database", False, "probe failed"),),
    )

    assert health.ok is False


def test_service_check_has_utc_timestamp_and_no_default_network_probes() -> None:
    health = asyncio.run(check_health())

    assert health.ok is True
    assert health.ts_utc.tzinfo is UTC
    assert health.dependencies == ()


def test_health_meta_redacts_secrets() -> None:
    health = ServiceHealth(
        service="test",
        ok=True,
        ts_utc=utc_now(),
        meta={"api_key": "raw-secret", "worker_count": 2},
    )

    rendered = json.dumps(health_to_dict(health))

    assert "raw-secret" not in rendered
    assert health_to_dict(health)["meta"]["worker_count"] == 2
