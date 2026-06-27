from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from scripts import backup_postgres


def test_pg_dump_command_is_safe_and_has_expected_shape(tmp_path: Path) -> None:
    output = tmp_path / "metadata.dump"

    plan = backup_postgres.build_pg_dump_command(
        database_url="postgresql+psycopg://mrrik:raw-password@localhost:5432/mrrik",
        output_path=output,
    )

    assert plan.output_path == output
    assert plan.command[0] == "pg_dump"
    assert "--format=custom" in plan.command
    assert str(output) in plan.command
    assert "raw-password" not in " ".join(plan.command)


def test_main_is_a_secret_safe_dry_run_by_default(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    settings = SimpleNamespace(
        file=SimpleNamespace(backup=SimpleNamespace(output_dir=str(tmp_path))),
        env=SimpleNamespace(
            database_url="postgresql://mrrik:never-print-this@localhost:5432/mrrik"
        ),
    )
    monkeypatch.setattr(backup_postgres, "load_config", lambda: settings)
    monkeypatch.setattr("sys.argv", ["backup_postgres"])

    backup_postgres.main()

    output = capsys.readouterr().out
    assert "mode=dry-run" in output
    assert "never-print-this" not in output
    assert list(tmp_path.glob("*.dump")) == []
