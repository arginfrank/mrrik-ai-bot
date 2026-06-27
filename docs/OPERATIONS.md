# MRRIK AI bot operations

All timestamps and schedules are UTC. Never put credentials in command arguments, tickets,
screenshots, alerts, or logs. Secrets belong in `.env` only.

## Daily startup checklist

1. Confirm the previous PostgreSQL backup completed and can be restored.
2. Check free disk space, PostgreSQL, Redis, and the latest audit records.
3. Run `python -m alembic upgrade head`.
4. Run `python -m scripts.healthcheck`; all five services must report `ok: true`.
5. Confirm the global and per-user kill switches are in their intended state.
6. Confirm the admin Telegram contact receives a test alert.
7. Review failed reconciliation, exchange rate-limit, payment, and signal-anomaly alerts.

## Startup and shutdown order

Start PostgreSQL, Redis, `admin_panel`, `telegram_bot`, `signal_ingestor`, `demo_engine`, and
finally `core_engine`. Starting core last ensures persistence, controls, and notifications are
available before real execution. Shut down in reverse order, stopping core first.

Required environment variables are `DATABASE_URL`, `REDIS_URL`, `FERNET_KEY`,
`TELEGRAM_BOT_TOKEN`, `TG_API_ID`, `TG_API_HASH`, `TG_USERBOT_SESSION`,
`SOURCE_CHANNEL_ID`, `ADMIN_TELEGRAM_IDS`, the three receiving wallets, and the applicable
explorer keys. Testnet keys are separate and must never be mainnet keys.

## Health and backups

`python -m scripts.healthcheck` performs an offline liveness summary. Service-specific
`check_health(db=..., redis_client=...)` functions perform injected DB/Redis readiness probes;
they do not call Telegram, Binance, or explorers.

Preview a PostgreSQL backup command with `python -m scripts.backup_postgres`. Execute it only
after verifying its destination: `python -m scripts.backup_postgres --execute`. The command
never prints the database password. Export daily UTC signal counts separately with
`python -m scripts.count_signals --start YYYY-MM-DD --end YYYY-MM-DD --output signals.csv`.
That command reads Telegram and therefore requires an approved operational window.

## Payment and demo flows

For a submitted payment, inspect the network, destination, token contract, expected amount,
confirmations, replay status, and explorer evidence. Run precheck, then explicitly approve or
reject in the admin panel. Approval, rejection, precheck, and kill-switch changes are written to
the audit log. Never approve on a screenshot or a TXID string alone.

For demo incidents, verify the public price feed, persisted open trade, liquidation check, and
closed-trade-only balance. Channel result messages are never execution evidence. Demo realism
costs are disabled by default and this optimistic setting must remain visible to operators.

## Real trading safety

Before enabling new trades, verify active subscription, valid encrypted credentials, Futures
permission, withdrawal-disabled status, isolated margin, concurrency/free-margin limits,
idempotent client order IDs, reconciliation, alerts, and kill switches. Never run a live canary
without completing `docs/LIVE_CANARY.md` and recording human approval.

Global kill switch (authenticated admin request):

```text
POST /kill-switch/global/on
POST /kill-switch/global/off
```

Per-user kill switch:

```text
POST /kill-switch/user/{user_id}/on
POST /kill-switch/user/{user_id}/off
```

If reconciliation fails, turn the global kill switch on, stop opening trades, preserve all
evidence, compare DB trades with exchange positions and open orders, and resolve protective
SL/TP coverage manually. Do not restart repeatedly or create replacement orders until exchange
truth is known. Record the incident and alert the admin.

On exchange HTTP 429 or Binance `-1003`, stop new requests for the indicated retry window,
honor server retry metadata, use capped exponential backoff, and reduce concurrency. Do not
bypass the rate limiter. Escalate repeated exhaustion and keep the kill switch on if protective
order state cannot be confirmed.

Never log secrets. Never run a live canary without the checklist.
