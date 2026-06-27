# Live canary

> **Warning:** a live canary uses real money. Loss, liquidation, exchange outage, and software
> failure are possible. The checklist script never places an order.

Prerequisites: reviewed test suite, verified DB backup, successful Binance testnet E2E, healthy
services, empty reconciliation error queue, working admin alerts, tested global/per-user kill
switches, adequate free isolated margin, and an API key independently verified to have withdrawals
disabled. A human operator must be present.

For an approved window only, set `live_canary.enabled: true`, keep
`live_canary.max_margin_usdt` at `5` USDT or lower, set
`MRRIK_RUN_LIVE_CANARY_CHECKLIST=1`, and set the exact confirmation:

```text
LIVE_CANARY_CONFIRM=I_ACCEPT_REAL_MONEY_RISK
```

Optionally set `LIVE_CANARY_MARGIN_USDT` to a positive Decimal no greater than the configured cap.
Run `python -m scripts.live_canary_checklist`. Review and sign off every printed item. This command
does not connect to an exchange and does not authorize automatic order placement.

Abort if any check is incomplete; health or reconciliation is degraded; withdrawal-disabled
status is uncertain; market conditions are disorderly; rate limits or timeouts repeat; the entry,
SL, TP, quantity, leverage, or isolated margin differs from the approved scenario; or an alert
cannot be delivered.

After a manually authorized canary, verify fills, protective orders, exchange position, DB state,
Redis events, user/admin notifications, realized PnL, reconciliation, and audit evidence. Then
enable the global kill switch, disable `live_canary.enabled`, remove the temporary confirmation,
and complete incident review for any discrepancy. If rollback is needed, stop new trades first;
do not cancel protective orders until the exchange position is independently confirmed.
