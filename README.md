# MRRIK AI bot

Milestone M0 establishes the Python 3.12 monorepo scaffold, layered configuration,
PostgreSQL 16 and Redis 7 development services, Alembic, and CI. Trading, Telegram,
signal processing, payments, and admin functionality are intentionally deferred to later milestones.

## Configuration

Non-secret defaults live in `config.yaml`. Copy `.env.example` to `.env` and fill in runtime
secrets locally. Never commit `.env` or print secret settings.

## Development

Install the project and development tools in a Python 3.12 environment:

```shell
python -m pip install -e ".[dev]"
ruff check .
python -m pytest
```

Validate and start the local infrastructure:

```shell
docker compose config
docker compose up -d db redis
docker compose ps
```

Apply future database migrations with `alembic upgrade head`. M0 does not define application
models or migration revisions.
