from __future__ import annotations

import asyncio
import json

from services.admin_panel.health import check_health as check_admin_panel
from services.core_engine.health import check_health as check_core_engine
from services.demo_engine.health import check_health as check_demo_engine
from services.signal_ingestor.health import check_health as check_signal_ingestor
from services.telegram_bot.health import check_health as check_telegram_bot
from shared.health import health_to_dict


async def _check_all_services() -> list[dict[str, object]]:
    checks = await asyncio.gather(
        check_signal_ingestor(),
        check_demo_engine(),
        check_telegram_bot(),
        check_admin_panel(),
        check_core_engine(),
    )
    return [health_to_dict(health) for health in checks]


def main() -> None:
    services = asyncio.run(_check_all_services())
    summary = {
        "ok": all(bool(service["ok"]) for service in services),
        "services": services,
    }
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
