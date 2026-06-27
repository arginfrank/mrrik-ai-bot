from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
import os
from pathlib import Path
import subprocess
from urllib.parse import unquote, urlsplit

from shared.config import load_config


@dataclass(frozen=True)
class BackupPlan:
    output_path: Path
    command: tuple[str, ...]


def build_pg_dump_command(
    *,
    database_url: str,
    output_path: Path,
) -> BackupPlan:
    """Build pg_dump command without logging secrets."""
    parsed = urlsplit(_normalize_postgres_scheme(database_url))
    database_name = unquote(parsed.path.lstrip("/"))
    if not parsed.hostname or not database_name:
        raise ValueError("DATABASE_URL must include a host and database name")

    command = [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-acl",
        "--host",
        parsed.hostname,
        "--port",
        str(parsed.port or 5432),
        "--dbname",
        database_name,
        "--file",
        str(output_path),
    ]
    if parsed.username:
        command.extend(("--username", unquote(parsed.username)))
    return BackupPlan(output_path=output_path, command=tuple(command))


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a safe PostgreSQL backup plan.")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute pg_dump. The default is a dry run.",
    )
    parser.add_argument("--output", type=Path, help="Optional .dump output path")
    args = parser.parse_args()

    settings = load_config()
    output_path = args.output or _default_output_path(settings.file.backup.output_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plan = build_pg_dump_command(
        database_url=settings.env.database_url,
        output_path=output_path,
    )
    mode = "execute" if args.execute else "dry-run"
    print(f"backup mode={mode} output={plan.output_path}")
    print(f"command={subprocess.list2cmdline(plan.command)}")

    if not args.execute:
        return

    environment = os.environ.copy()
    password = urlsplit(
        _normalize_postgres_scheme(settings.env.database_url)
    ).password
    if password:
        environment["PGPASSWORD"] = unquote(password)
    subprocess.run(plan.command, check=True, env=environment)
    print(f"backup completed output={plan.output_path}")


def _normalize_postgres_scheme(database_url: str) -> str:
    if database_url.startswith("postgresql+"):
        _, suffix = database_url.split("://", maxsplit=1)
        return f"postgresql://{suffix}"
    return database_url


def _default_output_path(output_dir: str) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path(output_dir) / f"mrrik-{timestamp}.dump"


if __name__ == "__main__":
    main()
