# Deployment checklist

## Before deployment

- Confirm the branch and reviewed diff modify only the milestone scope.
- Run `ruff check .`, `python -m pytest -p no:cacheprovider`, smoke imports, and
  `git diff --check`.
- Run `python -m alembic upgrade head` against the deployment database and confirm no unexpected
  migration is generated.
- Take and verify a PostgreSQL backup. Record the image tag and rollback target.
- Verify `.env` names without printing values. Confirm mainnet and testnet credentials are not
  mixed, withdrawal is disabled, and `live_canary.enabled` remains false for normal deployment.

## Infrastructure and services

- Start the Docker PostgreSQL 16 and Redis 7 services and verify persistent volumes.
- Verify the `signals`, `orders`, `demo`, `notify`, and `payments` Redis Streams and consumer
  behavior with non-secret synthetic events.
- Start the admin panel and verify its IP allowlist, authenticated access, payment queue,
  anomaly view, and both kill switches.
- Verify the BotFather token, bot identity, commands, polling, and admin notification delivery.
- Verify the Telethon userbot session, numeric source channel ID, source access, and its isolation
  from the customer bot. Do not print or regenerate the session during routine deployment.
- Start services in the order documented in `docs/OPERATIONS.md`, with core engine last.
- Run health/readiness probes and inspect logs for redaction before enabling traffic.

## Rollback

1. Enable the global kill switch and stop core engine first.
2. Preserve logs, audit records, open-order IDs, and reconciliation output.
3. Roll back application containers to the recorded image tag; do not downgrade the database
   unless a reviewed database restore plan explicitly requires it.
4. Restore PostgreSQL only from a verified backup and only after confirming the target database.
5. Restart in normal order, reconcile against exchange truth, and keep new trades paused until
   an operator signs off.
