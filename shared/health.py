from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import inspect
from typing import Any

from shared.logging import redact_mapping


@dataclass(frozen=True)
class DependencyHealth:
    name: str
    ok: bool
    detail: str | None = None


@dataclass(frozen=True)
class ServiceHealth:
    service: str
    ok: bool
    ts_utc: datetime
    dependencies: tuple[DependencyHealth, ...] = field(default_factory=tuple)
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.ts_utc.tzinfo is None or self.ts_utc.utcoffset() is None:
            raise ValueError("ts_utc must be timezone-aware")
        if not overall_ok(self.dependencies):
            object.__setattr__(self, "ok", False)


def utc_now() -> datetime:
    return datetime.now(UTC)


def overall_ok(dependencies: tuple[DependencyHealth, ...]) -> bool:
    return all(dependency.ok for dependency in dependencies)


def health_to_dict(health: ServiceHealth) -> dict[str, Any]:
    return {
        "service": health.service,
        "ok": health.ok and overall_ok(health.dependencies),
        "ts_utc": health.ts_utc.astimezone(UTC).isoformat(),
        "dependencies": [
            {
                "name": dependency.name,
                "ok": dependency.ok,
                "detail": dependency.detail,
            }
            for dependency in health.dependencies
        ],
        "meta": redact_mapping(health.meta),
    }


async def probe_dependency(*, name: str, dependency: Any) -> DependencyHealth:
    """Probe an injected dependency without exposing exception messages."""
    try:
        result = await _invoke_probe(dependency)
        if result is False:
            return DependencyHealth(name=name, ok=False, detail="probe returned false")
        return DependencyHealth(name=name, ok=True)
    except Exception as error:
        return DependencyHealth(
            name=name,
            ok=False,
            detail=f"probe failed ({type(error).__name__})",
        )


async def _invoke_probe(dependency: Any) -> Any:
    ping = getattr(dependency, "ping", None)
    if callable(ping):
        return await _await_if_needed(ping())

    connect = getattr(dependency, "connect", None)
    if callable(connect):
        connection = connect()
        if hasattr(connection, "__aenter__"):
            async with connection as entered:
                return await _execute_database_probe(entered)
        if hasattr(connection, "__enter__"):
            with connection as entered:
                return await _execute_database_probe(entered)
        return await _execute_database_probe(await _await_if_needed(connection))

    return await _execute_database_probe(dependency)


async def _execute_database_probe(connection: Any) -> Any:
    driver_execute = getattr(connection, "exec_driver_sql", None)
    if callable(driver_execute):
        return await _await_if_needed(driver_execute("SELECT 1"))

    execute = getattr(connection, "execute", None)
    if not callable(execute):
        raise TypeError("dependency has no supported health probe")

    try:
        from sqlalchemy import text

        result = execute(text("SELECT 1"))
    except TypeError:
        result = execute()
    return await _await_if_needed(result)


async def _await_if_needed(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value
