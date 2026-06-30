# Binance testnet E2E

The harness is opt-in and never uses mainnet. Create Binance Futures testnet-only credentials and
set `BINANCE_TESTNET_API_KEY` and `BINANCE_TESTNET_API_SECRET` in `.env.testnet` or the approved
secret store. Never supply mainnet keys.

Set `testnet.enabled: true` for the test window and export
`MRRIK_RUN_TESTNET_E2E=1`. Then run:

```text
python -m scripts.run_testnet_e2e
```

The current M8 harness prints JSON readiness and a tiny deterministic scenario. It intentionally
does not place an order or call Binance; `execution` remains `not_started`. A future wired client
must use only the Binance Futures testnet base URL and retain this explicit opt-in.

A readiness pass requires the config feature flag, explicit environment flag, and both testnet
credentials. Fail on any missing guard, any mainnet credential, a scenario above the reviewed
tiny margin, unexpected symbol/leverage, missing protective-order evidence, reconciliation error,
or leaked credential. Preserve the output as deployment evidence without secret values.

## Protective-order E2E (places real testnet orders)

Unlike the readiness harness above, this separate script places a real, tiny BTCUSDT market order
on Binance USD-M Futures testnet. It runs the production initial-order path, independently confirms
that the stop-loss client order ID is visible in Binance open orders, then cancels all BTCUSDT open
orders and closes the testnet position in a `finally` cleanup.

Use a dedicated, otherwise-flat testnet account. Keep `testnet.enabled: true`, provide only
`BINANCE_TESTNET_API_KEY` and `BINANCE_TESTNET_API_SECRET`, and set both explicit opt-ins:

```text
MRRIK_RUN_TESTNET_E2E=1
MRRIK_RUN_TESTNET_PROTECTIVE_E2E=1
python -m scripts.run_testnet_protective_e2e
```

The script refuses to run if either required guard is missing, if a mainnet Binance credential is
present, or if the client URL is not exactly `https://testnet.binancefuture.com`. It also refuses
before entry if the smallest exchange-valid scenario exceeds the reviewed 100 USDT notional cap.

The single JSON result has `status: "ok"` only when the production path reports the trade opened
and an independent `get_open_orders` call finds `sl_order_id`. `tp_confirmed_count` reports how many
returned TP IDs were independently visible. `position_qty_after_open` records the position before
cleanup. Always inspect `cleanup.cleanup_ok`, `cleanup.residual_position`, and
`cleanup.residual_orders`; a `failed` result with residue requires the operator to flatten the
testnet account manually.
