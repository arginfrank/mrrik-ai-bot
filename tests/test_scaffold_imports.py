from datetime import datetime
from importlib import import_module

from shared.bus import make_event


def test_scaffold_modules_are_importable() -> None:
    modules = (
        "shared.config",
        "shared.bus",
        "shared.crypto",
        "services.signal_ingestor.main",
        "services.core_engine.main",
        "services.demo_engine.main",
        "services.telegram_bot.main",
        "services.admin_panel.main",
    )

    for module in modules:
        import_module(module)


def test_make_event() -> None:
    event = make_event("signal.created", {"signal_id": 1})

    assert event.event_id
    assert event.type == "signal.created"
    assert event.payload == {"signal_id": 1}
    assert datetime.fromisoformat(event.ts_utc).utcoffset() is not None
