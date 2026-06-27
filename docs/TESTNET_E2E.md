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
