from __future__ import annotations

from typing import Any

from shared.health import ServiceHealth, overall_ok, probe_dependency, utc_now


async def check_health(
    *, db: Any | None = None, redis_client: Any | None = None
) -> ServiceHealth:
    """Return safe service health."""
    dependencies = []
    if db is not None:
        dependencies.append(await probe_dependency(name="database", dependency=db))
    if redis_client is not None:
        dependencies.append(await probe_dependency(name="redis", dependency=redis_client))
    checked = tuple(dependencies)
    return ServiceHealth(
        service="telegram_bot",
        ok=overall_ok(checked),
        ts_utc=utc_now(),
        dependencies=checked,
    )
