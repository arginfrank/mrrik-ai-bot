from __future__ import annotations

import json

from scripts.healthcheck import main


def test_healthcheck_main_prints_json_safe_offline_output(capsys) -> None:
    main()

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["ok"] is True
    assert len(payload["services"]) == 5
    assert "secret" not in output.casefold()
    assert "password" not in output.casefold()
