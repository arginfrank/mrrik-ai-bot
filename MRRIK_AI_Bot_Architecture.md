# MRRIK AI Bot — System Architecture & Build Specification

> **Audience:** This document is written so that an AI coding agent (Codex / GPT‑5.x) can implement the system **milestone by milestone** with no further design decisions required. Every load‑bearing rule is deterministic. Where a human choice is needed, it is exposed as a **config flag with a documented default** (see §2 *Decisions*).
>
> **Language of the product:** English (international audience; multi‑language is a later phase).
> **Bot name:** `MRRIK AI bot`.
> **Time:** All time math is in **UTC**.

---

## 0. Legal / risk note (read once, not legal advice)

This system places real trades on users' exchange accounts for a fee and hides the strategy. In many jurisdictions that is a **regulated activity** (asset/investment management). This document does **not** provide legal advice. Before going live you must add: a Terms of Service, an explicit risk disclaimer shown in the bot before any API key is accepted, and a clear statement that past/demo performance does not guarantee future results. The **demo must never look better than reality** — the engineering below enforces that technically (liquidation cap, real price feed). Do not weaken it.

---

## 1. What the system does (one paragraph)

A private Telegram signal channel ("source channel") posts crypto futures signals. A **userbot** (a real Telegram account, not a BotFather bot) reads those signals, a **parser+sanitizer** normalizes and repairs them, and a **core engine** executes them on each subscribed user's **Binance USDⓈ‑M Futures** account using native exchange orders (entry, `STOP_MARKET` for SL, multiple `TAKE_PROFIT_MARKET` for targets). A separate **demo engine** runs the exact same logic on a virtual balance, driven only by **real Binance prices over websocket** (it never reads the channel's result messages), so prospective customers can watch genuine performance before subscribing. A **customer Telegram bot** (BotFather) handles onboarding, language, subscription purchase via USDT (TRC20/BEP20/Polygon), TXID submission, API‑key intake, expiry reminders, and notifications. An **admin panel** approves/rejects payments, manages users, and surfaces signal anomalies.

---

## 2. DECISIONS — confirm or override (defaults are safe)

These are exposed in `config.yaml`. Defaults chosen by the architect are marked **DEFAULT**.

| # | Decision | Default | Why | Flag |
|---|----------|---------|-----|------|
| D1 | Entry order type | **LIMIT at entry price, with expiry + max‑deviation guard** | Signals give a price; chasing market causes bad fills. If price already passed entry by more than `entry_max_deviation_pct` or not filled within `entry_fill_timeout_sec`, **skip + alert**. | `execution.entry_mode = limit` / `market` |
| D2 | Move SL to break‑even after TP1 fills | **ON for risk model 1 & 2** | Standard capital protection; materially changes win/loss stats, so it is explicit. | `risk.move_sl_to_be_after_tp1` |
| D3 | Demo requires user's API key | **NO (price feed is public)** | Binance mark price is public via websocket; demo needs no key. If you gate demo behind API for funnel reasons, require a **read‑only** key only — never withdrawal. | `demo.require_api_key`, `demo.api_key_scope = read_only` |
| D4 | Demo realism costs | **commission OFF, funding OFF, slippage OFF** (per product owner) | ⚠️ All three OFF makes demo **optimistic vs real**. Recommended: turn funding+slippage ON for an honest demo. | `demo.include_commission/funding/slippage` |
| D5 | Margin type | **ISOLATED** | Caps per‑trade loss at margin; required for the liquidation math below. | `execution.margin_type = isolated` |
| D6 | Position size unit | **margin (collateral) per trade, fixed USDT** | `notional = margin × leverage`. e.g. 10 USDT × x42 = 420 USDT notional. | `risk.fixed_margin_usdt = 10` |

---

## 3. High‑level architecture

Five long‑running services + one DB + Redis. They communicate **only** through a Redis Streams event bus and the shared Postgres DB. No service imports another's internals.

```
                         ┌──────────────────────────┐
   Telegram source       │   signal-ingestor        │   (Telethon userbot, MTProto)
   channel (VIP) ───────▶│  read → parse → sanitize │
                         └─────────────┬────────────┘
                                       │ publishes: signal.created / signal.rejected
                                       ▼
                         ┌──────────────────────────┐        ┌──────────────────────┐
                         │      Redis Streams        │◀──────▶│     PostgreSQL        │
                         │      (event bus)          │        │  (single source of   │
                         └───┬───────────────┬───────┘        │      truth)          │
                             │               │                └──────────┬───────────┘
            signal.created   │               │  signal.created           │
                             ▼               ▼                           │
              ┌──────────────────┐   ┌──────────────────┐                │
              │   core-engine    │   │   demo-engine    │                │
              │ real users:      │   │ virtual accts:   │                │
              │ size→orders→     │   │ ws price→TP/SL/  │                │
              │ Binance Futures  │   │ liq→blended PnL  │                │
              └───────┬──────────┘   └────────┬─────────┘                │
                      │ order/trade events     │ demo events             │
                      ▼                        ▼                         │
              ┌─────────────────────────────────────────┐               │
              │        telegram-bot (aiogram, Bot API)   │◀──────────────┘
              │ onboarding • payments • API intake •     │
              │ reminders • notifications • demo UI      │
              └────────────────────┬────────────────────┘
                                   │
              ┌────────────────────▼────────────────────┐
              │      admin-panel (FastAPI + minimal UI)  │
              │ payment approve/reject • users • alerts  │
              └─────────────────────────────────────────┘

   scheduler (APScheduler inside core): expiry reminders, expiry enforcement,
   reconciliation, daily signal-count export.
```

### Why these boundaries
- **Userbot is mandatory and separate.** A BotFather bot **cannot** read a channel it is not admin of. You cannot make your bot admin of someone else's VIP channel. Reading signals therefore **requires** an MTProto userbot logged in as *your* account (you have lifetime membership). Keep it isolated so a crash there never touches order execution.
- **Demo is fully separate from core** (your explicit requirement). They share parser/sanitizer code as a library, nothing else.

---

## 4. Tech stack (pin these)

- **Language:** Python 3.12
- **Userbot (signal read):** `Telethon`
- **Customer bot:** `aiogram` v3 (Bot API)
- **Exchange (real):** `binance-futures-connector` (official) for REST + `websockets` for the user‑data stream
- **Price feed (demo + execution monitoring):** Binance Futures public websocket (`<symbol>@markPrice@1s` and/or `<symbol>@aggTrade`)
- **DB:** PostgreSQL 16, `SQLAlchemy 2.x` + `Alembic` migrations
- **Bus / cache / locks / idempotency:** Redis 7 (Streams + keys)
- **Scheduling:** `APScheduler` (in‑process) — reminders, expiry, reconciliation
- **Config:** `pydantic-settings`, layered: `config.yaml` (non‑secret) + `.env` (secrets)
- **Crypto for stored keys:** `cryptography.Fernet` (or cloud KMS in prod)
- **Packaging/run:** Docker + docker‑compose, one container per service
- **Tests:** `pytest`

---

## 5. Event bus contract (Redis Streams)

All events are JSON. Every event has `event_id` (uuid), `type`, `ts_utc`, `payload`. Consumers are idempotent on `event_id`.

| Stream | Producer | Event types | Key payload fields |
|--------|----------|-------------|--------------------|
| `signals` | signal-ingestor | `signal.created`, `signal.rejected` | `signal_id`, normalized signal object (see §7), or `reason` |
| `orders` | core-engine | `trade.opened`, `trade.leg_filled`, `trade.closed`, `trade.error` | `trade_id`, `user_id`, `symbol`, leg info, realized pnl |
| `demo` | demo-engine | `demo.opened`, `demo.closed` | `demo_trade_id`, `user_id`, symbol, blended pnl % and USDT, touched TPs |
| `notify` | any | `notify.user`, `notify.admin` | `telegram_id`, `text`, `lang`, optional buttons |
| `payments` | telegram-bot | `payment.submitted`, `payment.approved`, `payment.rejected` | `payment_id`, `user_id`, network, txid, amount |

The **canonical normalized signal** object is defined in §7.3 and is what `signal.created` carries.

---

## 6. Data model (PostgreSQL)

Illustrative DDL; Codex should turn these into SQLAlchemy models + Alembic migrations. Money stored as `NUMERIC(38,18)`; never floats for prices/amounts in DB.

```sql
-- Users & i18n
CREATE TABLE users (
  id              BIGSERIAL PRIMARY KEY,
  telegram_id     BIGINT UNIQUE NOT NULL,
  username        TEXT,
  language        TEXT NOT NULL DEFAULT 'en',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  is_admin        BOOLEAN NOT NULL DEFAULT false,
  is_blocked      BOOLEAN NOT NULL DEFAULT false
);

-- Subscription plans (seed: 30d, 90d)
CREATE TABLE plans (
  id              SERIAL PRIMARY KEY,
  code            TEXT UNIQUE NOT NULL,         -- 'P30','P90'
  duration_days   INT NOT NULL,
  price_usdt      NUMERIC(18,6) NOT NULL,
  is_active       BOOLEAN NOT NULL DEFAULT true
);

-- Subscriptions
CREATE TABLE subscriptions (
  id              BIGSERIAL PRIMARY KEY,
  user_id         BIGINT REFERENCES users(id),
  plan_id         INT REFERENCES plans(id),
  status          TEXT NOT NULL,               -- 'pending','active','expired','cancelled'
  starts_at       TIMESTAMPTZ,                  -- set at approval (UTC)
  ends_at         TIMESTAMPTZ,                  -- starts_at + duration
  reminded_24h    BOOLEAN NOT NULL DEFAULT false,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Payments (USDT on TRC20/BEP20/Polygon)
CREATE TABLE payments (
  id              BIGSERIAL PRIMARY KEY,
  user_id         BIGINT REFERENCES users(id),
  plan_id         INT REFERENCES plans(id),
  network         TEXT NOT NULL,               -- 'TRC20','BEP20','POLYGON'
  to_address      TEXT NOT NULL,               -- your receiving wallet for that network
  amount_expected NUMERIC(18,6) NOT NULL,
  txid            TEXT,
  amount_seen     NUMERIC(18,6),               -- from explorer (auto-precheck)
  confirmations   INT,
  explorer_url    TEXT,
  status          TEXT NOT NULL DEFAULT 'submitted', -- submitted|prechecked|approved|rejected
  precheck_result TEXT,                         -- pass|fail|unknown + reason
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at      TIMESTAMPTZ,
  decided_by      BIGINT,
  UNIQUE (network, txid)                        -- prevents TXID replay
);

-- Encrypted exchange credentials (real trading)
CREATE TABLE exchange_credentials (
  id              BIGSERIAL PRIMARY KEY,
  user_id         BIGINT REFERENCES users(id),
  exchange        TEXT NOT NULL DEFAULT 'binance',
  api_key_enc     BYTEA NOT NULL,               -- Fernet
  api_secret_enc  BYTEA NOT NULL,
  scope_verified  BOOLEAN NOT NULL DEFAULT false, -- futures-trade yes, withdrawal MUST be no
  is_valid        BOOLEAN NOT NULL DEFAULT false,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, exchange)
);

-- Per-user settings (real + demo share this where applicable)
CREATE TABLE user_settings (
  user_id             BIGINT PRIMARY KEY REFERENCES users(id),
  fixed_margin_usdt   NUMERIC(18,6) NOT NULL DEFAULT 10,
  risk_model          SMALLINT NOT NULL DEFAULT 1,  -- 1 | 2 | 3
  model3_exit_roi_pct NUMERIC(6,3) NOT NULL DEFAULT 20,
  max_concurrent      INT NOT NULL DEFAULT 10,
  leverage_mode       TEXT NOT NULL DEFAULT 'signal', -- 'signal' or 'cap'
  leverage_cap        INT,                          -- if mode='cap'
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Signals (normalized & sanitized)
CREATE TABLE signals (
  id              BIGSERIAL PRIMARY KEY,
  source_msg_id   BIGINT,
  symbol          TEXT NOT NULL,               -- 'HBARUSDT'
  side            TEXT NOT NULL,               -- 'LONG' | 'SHORT'
  entry           NUMERIC(38,18) NOT NULL,
  stop_loss       NUMERIC(38,18) NOT NULL,
  leverage        INT NOT NULL,
  targets_raw     JSONB NOT NULL,              -- as parsed
  targets_clean   JSONB NOT NULL,              -- after sanitizer
  sanitizer_notes JSONB,                       -- corrections/drops/alerts
  status          TEXT NOT NULL,               -- 'accepted','rejected'
  reject_reason   TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Real trades (one per user per signal)
CREATE TABLE trades (
  id              BIGSERIAL PRIMARY KEY,
  signal_id       BIGINT REFERENCES signals(id),
  user_id         BIGINT REFERENCES users(id),
  symbol          TEXT NOT NULL,
  side            TEXT NOT NULL,
  leverage        INT NOT NULL,
  margin_usdt     NUMERIC(18,6) NOT NULL,
  notional_usdt   NUMERIC(18,6) NOT NULL,
  qty             NUMERIC(38,18) NOT NULL,
  entry_order_id  TEXT,
  sl_order_id     TEXT,
  liq_price       NUMERIC(38,18),
  status          TEXT NOT NULL,               -- pending_entry|open|closed|skipped|error
  realized_pnl_usdt NUMERIC(18,6),
  realized_roi_pct  NUMERIC(10,4),
  touched_tps     JSONB,                        -- [1,2]
  closed_reason   TEXT,                         -- 'all_tp'|'sl'|'be'|'liquidation'|'model3_exit'
  opened_at       TIMESTAMPTZ,
  closed_at       TIMESTAMPTZ
);

-- Trade legs (per-TP portions) for real
CREATE TABLE trade_legs (
  id              BIGSERIAL PRIMARY KEY,
  trade_id        BIGINT REFERENCES trades(id),
  leg_index       INT NOT NULL,                -- TP1..TPn
  target_price    NUMERIC(38,18) NOT NULL,
  qty             NUMERIC(38,18) NOT NULL,
  tp_order_id     TEXT,
  status          TEXT NOT NULL DEFAULT 'open',-- open|filled|cancelled
  filled_at       TIMESTAMPTZ
);

-- Demo accounts (virtual)
CREATE TABLE demo_accounts (
  user_id            BIGINT PRIMARY KEY REFERENCES users(id),
  start_balance_usdt NUMERIC(18,6) NOT NULL DEFAULT 1000,
  balance_usdt       NUMERIC(18,6) NOT NULL DEFAULT 1000, -- closed-trades only
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Demo trades + legs mirror the real tables (demo_trades, demo_trade_legs).
-- (same columns minus exchange order ids; add fields_realism_applied JSONB)

-- Audit & idempotency
CREATE TABLE audit_log (
  id BIGSERIAL PRIMARY KEY, actor TEXT, action TEXT, entity TEXT,
  entity_id TEXT, meta JSONB, ts TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE processed_events (event_id UUID PRIMARY KEY, ts TIMESTAMPTZ DEFAULT now());
```

---

## 7. Signal parsing & normalization

### 7.1 The real signal formats (from the provided screenshots)

**Multiline entry signal:**
```
VIP CRYPTO JEMAL
#HBAR/USDT - Long🟢
Entry: 0.07145
Stop Loss: 0.07077

Target 1: 0.072
Target 2: 0.07186
Target 3: 0.07238
Target 4: 0.07296
Target 5: 0.07309

Leverage: x42
```

**Single‑line variant** (also occurs):
```
#ETH/USDT - Long🟢 Entry: 1581.72 Stop Loss: 1572.68914 Target 1: 1584.4697 ...
```

**Result message (reply, used ONLY for logging — never for exits):**
```
#ETH/USDT
Target Tuch 1 ✅
Profit: 9.3544% 📈
Period: 14 Minutes ⏰
```

**Stop message (logging only):**
```
#AGLD/USDT
Stop Target Hit ⛔
Loss: 243.4286% 📉
```

### 7.2 Parsing rules (deterministic)
- **Symbol:** take token after `#`, strip `/`, uppercase → `#HBAR/USDT` → `HBARUSDT`. Validate the symbol exists on Binance Futures `exchangeInfo`; if not, reject.
- **Side:** word `Long`/`Short` (case‑insensitive) and/or emoji 🟢=long, 🔴=short. If word and emoji disagree → reject + alert.
- **Numbers:** `Entry`, `Stop Loss`, `Target N`, `Leverage: x<INT>`. Use tolerant regex (handles spaces inside numbers like `0 .07145` seen in OCR; collapse internal spaces in numeric tokens before parse).
- Leverage is **always present** in these signals; if missing → reject + alert.
- Result/stop messages are matched to an open `signals` row by symbol + recency and stored for logging/analytics only.

### 7.3 Normalized signal object (carried by `signal.created`)
```json
{
  "signal_id": 123,
  "symbol": "HBARUSDT",
  "side": "LONG",
  "entry": "0.07145",
  "stop_loss": "0.07077",
  "leverage": 42,
  "targets": ["0.07186","0.07238","0.07296","0.07309"],   // CLEANED (see §8)
  "sanitizer": {"dropped": ["0.072"], "corrected": [], "alert": true}
}
```

---

## 8. Signal sanitizer — the "broken signal" solution (deterministic)

> Two real defects exist in the screenshots, and they are **ordering defects**, not (only) decimal shifts:
> - HBAR: `T1=0.072` but `T2=0.07186` → `T1 > T2` (long must be ascending). `T1` is the outlier.
> - ETH: `T4=1608.13` but `T5=1604.96` → `T5 < T4`. `T5` is the outlier.
>
> We also keep a decimal‑shift repair because real channels do occasionally drop/add a zero. **Never invent or interpolate a target. Only correct a clear decimal shift, or drop.**

**Algorithm `sanitize(signal)`:**

1. **SL side check.** LONG ⇒ `SL < entry`; SHORT ⇒ `SL > entry`. If violated → **reject whole signal**, alert admin. (A wrong‑side SL is unrecoverable.)
2. **Decimal‑shift repair (per target).** Let `m = median(targets)`. For each target `t`: if `t/m` (or `m/t`) ∈ `[decimal_shift_lo, decimal_shift_hi]` (default `[5, 20]`, i.e. off by ~one power of ten), try `t' ∈ {t×10, t÷10}`; pick the candidate closest to `m` that lands on the **correct side of entry**. If found, replace and record a `corrected` note. (This fixes the user‑described `0.7186 → 0.07186` family.)
3. **Side filter.** Drop any target on the wrong side of entry (LONG: `target ≤ entry`; SHORT: `target ≥ entry`). Record drops.
4. **Monotonic enforcement via Longest Monotonic Subsequence.** Among surviving targets *in original order*, compute the longest strictly **ascending** (LONG) / **descending** (SHORT) subsequence (LIS/LDS). Keep only those; drop the rest. **Tie‑break (equal length):** prefer the subsequence that keeps the **earliest** targets (T1 matters most for low‑risk model); equivalently drop the highest‑indexed offender. This is deterministic.
   - HBAR `[0.072, 0.07186, 0.07238, 0.07296, 0.07309]` → keep `[0.07186, 0.07238, 0.07296, 0.07309]`, drop `0.072`.
   - ETH `[1584.47, 1589.98, 1593.85, 1608.13, 1604.96]` → keep `[1584.47, 1589.98, 1593.85, 1604.96]`, drop `1608.13` (highest‑indexed offender by tie‑break).
5. **Minimum viable.** If `len(clean) < 1` → reject + alert. Else accept with `alert=true` if any drop/correction happened (admin sees it; trading still proceeds on the cleaned set — never on a guessed value).
6. **Output** `targets_clean` + `sanitizer_notes`.

This guarantees: TP orders are only ever placed at prices that are (a) on the profitable side of entry and (b) strictly improving — so a malformed signal **cannot** create a losing/again‑against‑you target.

---

## 9. PnL & liquidation math (this is where most bots lie — do not)

Definitions (per trade): `margin` (USDT, fixed), `lev` (leverage), `entry`, side. `notional = margin × lev`. `qty = notional / entry` (rounded to symbol `stepSize`).

### 9.1 Signed price move and ROI on margin
For a price `p`:
- LONG: `move% = (p − entry) / entry`
- SHORT: `move% = (entry − p) / entry`
- **ROI on margin = `move% × lev`** ← this is the % the source channel reports (verified: ETH `Profit 9.3544%` = `((1584.4697−1581.72)/1581.72) × 54`).
- **PnL_USDT for a portion `f` of the position = `margin × f × move% × lev`**.

### 9.2 Liquidation (isolated margin) — the part the channel ignores
Approximate isolated liquidation:
- LONG: `liq ≈ entry × (1 − (1/lev − mmr))`
- SHORT: `liq ≈ entry × (1 + (1/lev − mmr))`

where `mmr` = maintenance margin rate (symbol/notional dependent; default `0.004`–`0.01`, fetch tier from Binance for real, use config default for demo).

**Rule:** if, for a trade, the SL is **beyond** the liquidation price (i.e. `|SL move%| × lev > ~100%`), the position **liquidates before the SL fills**. Loss = **100% of margin**, closed_reason=`liquidation`. The channel's "Loss: 243%" is therefore physically impossible on margin; real and demo both **cap loss at one margin (−100%)**.

> **Worked example (AGLD, channel said Loss 243.43%):** with isolated margin, the position is liquidated at −100% margin long before −243% is reachable. Real loss = full `margin` (e.g. 10 USDT). Your earlier belief "SL = lose full 10 USDT" is only ever true in this liquidation case; for a normal‑distance SL (e.g. HBAR's 0.95% × 42 = 40%) the loss is **0.40 × margin = 4 USDT**, not 10.

### 9.3 Blended result of a multi‑target trade (for the demo "result" message and real reporting)
`realized_roi% = Σ_legs ( f_leg × exit_move%_leg × lev )`, where each leg exits at its TP (positive) or at SL/BE (could be 0 at break‑even, negative at SL), and `f_leg` is that leg's fraction. `realized_usdt = margin × realized_roi% ` … (i.e. `margin × Σ f_leg × move%_leg × lev`). If liquidation triggers, the whole remaining position closes at liq and total is floored at −margin.

---

## 10. Risk models (config‑switchable, defaults documented)

Let cleaned targets be `T1..Tn`. Position fraction notation `f_i` sums to 1.

**Model 1 — Equal across targets (DEFAULT).** `f_i = 1/n` for each target. Each TP leg = `qty/n`. SL `closePosition` covers remaining. If D2 on: after TP1 fills, cancel SL and re‑place at entry (break‑even).

**Model 2 — Low‑risk, TP1‑weighted.** Default weights configurable, default `[0.60, 0.20, 0.10, 0.07, 0.03]` truncated/renormalized to `n` targets. After TP1 fills (D2 on), move SL→BE. This banks most size at the highest‑probability first target.

**Model 3 — Lowest risk, ROI exit (you said this MUST exist).** Ignore individual targets for exit. Monitor ROI on margin; when `ROI% ≥ model3_exit_roi_pct` (default **20%**) → **close 100%** (`closed_reason=model3_exit`). `price threshold = entry × (1 ± exit_roi/(100×lev))`. SL still active; if SL/liq hits first → loss as in §9. This maximizes win‑rate.

All weights and the 20% are in `user_settings` / `config.yaml`.

### 10.1 Position sizing & exchange filters
- Fetch `exchangeInfo` per symbol: `stepSize` (qty), `tickSize` (price), `minQty`, `minNotional` (~5 USDT typical).
- `qty = floor_to_step(notional/entry)`. For multi‑leg models, **each leg qty** must be ≥ `minQty` and each leg notional ≥ `minNotional`. If a leg is too small, **merge** it into the previous leg (and reduce target count for execution), recording a note — never silently drop profit logic.
- Round TP/SL prices to `tickSize` in the **conservative** direction (TP slightly nearer is fine; SL never tighter than signal).

### 10.2 Concurrency & capital guard (your requirement)
- `max_concurrent` per user (default 10). Before opening, check free margin on the account.
- **skip‑on‑insufficient‑margin:** if free margin < required `margin`, **skip the signal** for that user and notify ("skipped: insufficient free margin"). **Never** auto‑shrink size below the signal's intent without telling the user.

---

## 11. Real execution lifecycle (core-engine)

Per user, per accepted signal:

1. **Pre‑checks:** subscription `active`? credentials valid & withdrawal‑disabled? concurrency/margin OK? symbol tradable? Else skip + (maybe) notify.
2. **Set leverage** (`/fapi/v1/leverage`) to signal leverage (or capped per settings) and **margin type = ISOLATED**.
3. **Entry (D1).** `limit` at `entry` with `entry_fill_timeout_sec` and `entry_max_deviation_pct` guard. If guard trips → cancel + skip + notify.
4. On entry fill (via **user‑data websocket** `ORDER_TRADE_UPDATE`): compute legs per risk model, compute `liq_price`.
5. **Place protective orders:**
   - SL: one `STOP_MARKET`, `closePosition=true`, `stopPrice = SL` (model‑3 also has this).
   - TPs: one `TAKE_PROFIT_MARKET` per leg, `reduceOnly=true`, `quantity = leg.qty`, `stopPrice = Ti` (models 1 & 2). Model 3 places **no** TP orders; it watches ROI on the price websocket and closes 100% at threshold.
6. **Lifecycle management (event‑driven, from user‑data stream):**
   - On a TP leg fill → mark leg filled; if D2 and it's TP1 → cancel SL, re‑place SL at entry (BE).
   - On SL fill → cancel all open TP legs; close trade; compute blended PnL.
   - On all TP legs filled → trade closed (`all_tp`).
   - Model 3: on ROI threshold (price ws) → market‑close remaining; cancel SL.
7. **Liquidation safety:** also subscribe to position updates; if exchange liquidates, mark `liquidation`, loss=−margin.
8. **Idempotency & reconciliation:** every exchange call carries a `newClientOrderId = f(trade_id, leg)`. On service restart, **reconcile**: pull open orders/positions from Binance and rebuild in‑memory state from DB + exchange truth before acting.

---

## 12. Demo engine (separate; price‑driven; never uses channel results)

Per user demo account (virtual `start_balance`, default 1000 USDT, configurable; optionally seeded from the user's real balance if D3 collects a read‑only key).

On `signal.created`:
1. **Open notification only:** "Demo: LONG ETHUSDT opened." No numbers (this is not a signal channel). Persist `demo_trades` + legs identical to real sizing/model.
2. **Price tracking:** subscribe to `<symbol>@markPrice@1s` websocket. Maintain a per‑symbol fan‑out so many demo trades share one stream.
3. **Fill detection (deterministic, real prices):**
   - For each open leg, if mark price crosses its TP (LONG: `price ≥ Ti`) → leg filled at `Ti`.
   - If price crosses SL (and BE after TP1 per D2) → remaining legs exit at SL/BE.
   - **Compute `liq_price` and check it first** — if hit before SL, close all at liq, loss = −margin (§9.2). This is what makes demo honest.
   - Model 3: when ROI ≥ threshold → close 100% at threshold price.
4. **Realism costs (D4):** if `include_commission/funding/slippage` are ON, subtract them (taker fee per fill, funding every 8h while open, slippage band on market exits). Defaults OFF per product owner — **documented as optimistic**.
5. **Close & report (only when fully closed):** post a single message, e.g.
   - Win: `LONG ETHUSDT — TP1, TP2 hit. Result: +42.0% (+4.20 USDT).`
   - Loss: `LONG ETHUSDT — Stopped. Result: −1.0% (−0.10 USDT).`
   - Full: `LONG ETHUSDT — TP1‑TP5 hit. Result: +182.0% (+18.20 USDT).`
   - Mid‑trade: **silence** (your requirement).
6. Update `demo_accounts.balance` with closed‑trade PnL only.

### 12.1 Demo stats command (`/demo` → Stats)
Show, computed live:
- Start balance, **current balance (closed trades only, excludes open)**, fixed margin per entry.
- Signals traded; **open**, **closed‑loss**, **closed‑win** counts (win = *blended* trade result > 0, not a single TP).
- **Win‑rate %** over closed trades only (excludes open).
- **Net profit** in USDT and in % vs start balance.

---

## 13. Customer Telegram bot (aiogram) — flows

**FSM main menu after `/start`:** Language → Main: `Subscribe` · `Run Demo` · `My Subscription` · `Connect Exchange API` · `Settings` · `Help`.

**Language:** stored on `users.language`; all strings from an i18n table/files (`en` first; structure ready for more).

**Subscribe flow:**
1. Choose plan (30d / 90d) → show price.
2. Choose network (TRC20 / BEP20 / POLYGON) → show **your receiving wallet** for that network with a **Copy** button + exact amount.
3. User pays externally, returns and sends **TXID**.
4. Create `payments` row `status=submitted`; run **auto‑precheck** (§14); emit `payment.submitted` → admin notified with explorer link + approve/reject buttons.
5. On admin **approve**: create/activate `subscriptions` (`starts_at=now UTC`, `ends_at=+duration`), notify user "Subscription active until <UTC>". On **reject**: notify user "TXID invalid, service not activated, please resubmit a correct TXID."

**Connect Exchange API flow:** explain required permissions (**Futures: enabled; Withdrawals: MUST be disabled**), accept key+secret in a single message that the bot **deletes immediately** after reading; validate with a signed read call; verify withdrawal is disabled if detectable; store **encrypted**. Trading only starts when subscription active **and** valid key present.

**Expiry handling (scheduler):**
- **24h before `ends_at`:** reminder (once; `reminded_24h`).
- **At `ends_at`:** set `expired`; **stop opening new trades**; **keep existing open trades** until their own SL/TP; notify: "Subscription ended. No new trades will be opened; your currently open trades remain until they close (SL/TP)."

---

## 14. Payment auto‑precheck (recommended; prevents fake/replayed TXIDs)

Before the admin even sees it, verify on‑chain:
- **Networks & USDT contracts:** TRON TRC20 (`TR7NHq…` USDT, 6 decimals), BSC BEP20 (`0x55d3…` USDT, 18 decimals), Polygon (USDT `0xc2132…`, 6 decimals). Put exact addresses in config.
- Check: txid exists, **to‑address == your wallet**, token == USDT, `amount_seen ≥ amount_expected`, `confirmations ≥ min`, and `txid` not already used (DB unique).
- Set `precheck_result = pass|fail|unknown`. Admin still approves/rejects manually, but with this evidence and the explorer link (`bscscan.com/tx/<txid>`, `tronscan.org/#/transaction/<txid>`, `polygonscan.com/tx/<txid>`).

---

## 15. Admin panel (FastAPI + minimal HTML, admin‑only)

- **Payment queue:** user, plan, amount, network, txid, explorer link, precheck result, **Approve / Reject** buttons → triggers user notify + activation/rejection (same logic as §13).
- **Users:** subscription status, demo stats, real trades, credential validity (never show secret), block/unblock.
- **Signals & anomalies:** every sanitizer `alert=true` (dropped/corrected targets, rejects) listed for review.
- **Kill switch:** global "pause new trades" + per‑user pause.
- **Auth:** admin Telegram IDs in config; panel behind login + IP allowlist; all actions in `audit_log`.

---

## 16. Security

- Secrets only in `.env`; **never** logged. API keys encrypted at rest (Fernet/KMS). Decrypt only in core memory at trade time.
- Require **withdrawal‑disabled** keys; reject/flag otherwise. Least privilege.
- Userbot session string stored encrypted; treat as a credential.
- Idempotency keys on all exchange writes; per‑user DB‑level locks to avoid double‑open.
- Rate‑limit Telegram and exchange calls; backoff on `-1003`/`429`.
- PII minimal; payments table keeps only what's needed.

---

## 17. Configuration

`config.yaml` (non‑secret) — every tunable, with defaults shown in §2/§10. `.env` (secrets) — fill these:

```dotenv
# Telegram customer bot (BotFather)
TELEGRAM_BOT_TOKEN=

# Telegram userbot (signal reader) — from https://my.telegram.org/apps
TG_API_ID=
TG_API_HASH=
TG_USERBOT_SESSION=            # Telethon StringSession (generate once, see §18)
SOURCE_CHANNEL_ID=             # numeric -100... id of the VIP channel

# Admin
ADMIN_TELEGRAM_IDS=123,456

# Database / Redis
DATABASE_URL=postgresql+psycopg://user:pass@db:5432/mrrik
REDIS_URL=redis://redis:6379/0

# Encryption
FERNET_KEY=                    # 32-byte urlsafe base64

# Receiving wallets (USDT) per network
WALLET_TRC20=
WALLET_BEP20=
WALLET_POLYGON=

# Explorer API keys (for auto-precheck)
TRONSCAN_API_KEY=
BSCSCAN_API_KEY=
POLYGONSCAN_API_KEY=

# Binance is per-user (entered via bot); no global trading key needed.
# Public market websocket needs no key.
```

```yaml
# config.yaml (excerpt)
plans:
  - {code: P30, duration_days: 30, price_usdt: 49}
  - {code: P90, duration_days: 90, price_usdt: 129}
risk:
  fixed_margin_usdt: 10
  default_model: 1
  model2_weights: [0.60, 0.20, 0.10, 0.07, 0.03]
  model3_exit_roi_pct: 20
  move_sl_to_be_after_tp1: true
  max_concurrent: 10
execution:
  entry_mode: limit
  entry_fill_timeout_sec: 900
  entry_max_deviation_pct: 0.5
  margin_type: isolated
  maintenance_margin_rate_default: 0.005
sanitizer:
  decimal_shift_lo: 5
  decimal_shift_hi: 20
demo:
  start_balance_usdt: 1000
  require_api_key: false
  api_key_scope: read_only
  include_commission: false   # OFF = optimistic; recommend true
  include_funding: false      # OFF = optimistic; recommend true
  include_slippage: false     # OFF = optimistic; recommend true
  taker_fee_pct: 0.04
```

---

## 18. BotFather & userbot setup (step by step)

**Customer bot (BotFather):**
1. Telegram → @BotFather → `/newbot` → set name `MRRIK AI bot`, pick a username → copy token into `TELEGRAM_BOT_TOKEN`.
2. `/setdescription`, `/setabouttext`, optional `/setcommands` (`start`, `subscribe`, `demo`, `status`, `help`).

**Userbot (signal reader):**
1. Go to `https://my.telegram.org/apps`, create an app → copy `api_id`, `api_hash` into `.env`.
2. Run the provided `scripts/make_session.py` once: it logs in **your** account (with code) and prints a `StringSession` → put in `TG_USERBOT_SESSION`.
3. Get the source channel id: with the userbot, run `scripts/get_channel_id.py` (lists your dialogs) → copy the VIP channel's `-100...` id into `SOURCE_CHANNEL_ID`.

---

## 19. Scheduler jobs (APScheduler in core)

| Job | Schedule | Action |
|-----|----------|--------|
| expiry_reminder | every 5 min | find subs with `ends_at − now ≤ 24h` and `reminded_24h=false` → notify, set flag |
| expiry_enforce | every 1 min | subs past `ends_at`, status `active` → set `expired`, stop new trades, notify |
| reconcile | every 1 min | core: reconcile open orders/positions vs DB |
| signal_count_export | daily 00:05 UTC | run §20 extractor, store/report daily count |

---

## 20. Signal‑count extractor (you asked for this)

`scripts/count_signals.py` (Telethon): given a date range, pull `SOURCE_CHANNEL_ID` history, run the §7 parser, count **entry** signals per UTC day, and print/save CSV. Purpose: size capital needs (if ~60–70 signals/day at 10 USDT margin and `max_concurrent` cap, estimate peak margin in use). This justifies the fixed‑margin choice empirically rather than by guess.

---

## 21. Repository layout (monorepo)

```
mrrik-ai-bot/
├─ docker-compose.yml
├─ .env.example
├─ config.yaml
├─ shared/                 # importable library (no service logic)
│  ├─ models/              # SQLAlchemy models
│  ├─ schemas/             # pydantic event schemas
│  ├─ signal/              # parser.py, sanitizer.py, pnl.py, sizing.py
│  ├─ exchange/            # ExchangeClient interface + binance.py
│  ├─ bus.py               # Redis Streams helpers
│  ├─ crypto.py            # Fernet encrypt/decrypt
│  └─ config.py            # pydantic-settings
├─ services/
│  ├─ signal_ingestor/     # Telethon userbot
│  ├─ core_engine/         # real execution + scheduler
│  ├─ demo_engine/         # virtual trading
│  ├─ telegram_bot/        # aiogram customer bot
│  └─ admin_panel/         # FastAPI
├─ scripts/                # make_session, get_channel_id, count_signals
├─ migrations/             # alembic
└─ tests/                  # parser/sanitizer/pnl/liquidation fixtures
```

### 21.1 Exchange abstraction (future exchanges)
Define `ExchangeClient` ABC: `set_leverage`, `set_margin_type`, `place_entry`, `place_sl`, `place_tp`, `cancel`, `get_position`, `get_filters`, `user_stream`, `mark_price_stream`. Implement `BinanceFutures` now. CoinEx/KuCoin/BingX/OKX/MEXC are later phases implementing the same ABC — no other code changes.

---

## 22. Build order (each row = one Codex prompt/milestone)

1. **M0 Scaffold:** repo layout, config loader, docker‑compose (db, redis), Alembic init, CI lint/test.
2. **M1 Shared signal lib:** parser + sanitizer + pnl + sizing, with §23 tests. *(No I/O — pure functions; easiest to verify first.)*
3. **M2 DB models + migrations** for all §6 tables.
4. **M3 signal_ingestor:** Telethon userbot, read channel, parse→sanitize→persist→`signal.created`. Scripts: make_session, get_channel_id, count_signals.
5. **M4 demo_engine:** subscribe `signal.created`, mark‑price ws, fill/SL/liq detection, blended result, demo notifications + `/demo` stats. *(Build demo before real money.)*
6. **M5 telegram_bot:** onboarding, language, subscribe flow, wallet+copy, TXID, API intake (encrypted), notifications, expiry reminders.
7. **M6 payment precheck + admin_panel:** explorer verification, approve/reject → activation/notify, anomaly list, kill switch.
8. **M7 core_engine (real):** exchange client, leverage/margin, entry, SL/TP legs, user‑data stream lifecycle, move‑to‑BE, model 3, concurrency/margin guard, reconciliation, idempotency.
9. **M8 hardening:** rate limits, retries/backoff, audit log, healthchecks, backups, monitoring/alerts, end‑to‑end test on Binance **testnet**, then a tiny‑size live canary.

---

## 23. Tests (use the real screenshots as fixtures)

- **parser:** HBAR multiline, ETH single‑line, AVAX multiline, AGLD stop‑msg, ETH result‑msg → expected normalized objects.
- **sanitizer:** HBAR → drops `0.072`; ETH targets → drops `1604.96` family per tie‑break; wrong‑side SL → reject; decimal‑shift `0.7186→0.07186` → corrected.
- **pnl/liquidation:** ETH `Profit 9.3544%` reproduced from `move×lev`; AGLD `Loss 243%` → **capped at −100% margin / liquidation**; HBAR normal SL → −40% margin (−4 USDT on 10).
- **sizing:** stepSize/minNotional rounding; tiny‑leg merge.
- **lifecycle (mocked exchange):** TP1 fill → SL→BE; SL fill cancels TPs; model 3 ROI exit.

---

## 24. Glossary

`margin` collateral per trade (fixed USDT). `notional = margin×lev`. `ROI on margin = price_move%×lev`. `leg` a per‑target slice of the position. `liquidation` forced close at −~100% margin. `closePosition` Binance flag to close whatever remains.

---

## 25. ChatGPT Project "Instructions" text (paste this into the Project's Instructions field)

> You are the lead engineer for **MRRIK AI bot**, building it strictly from `MRRIK_AI_Bot_Architecture.md` in the repo. Implement the system in the milestone order defined in section 22 (M0→M8). For each milestone, produce a precise, self‑contained Codex prompt that: (1) states the files to create/modify with paths from section 21, (2) gives exact function signatures, data shapes (section 7.3, 6), and acceptance tests from section 23, (3) respects every deterministic rule in sections 8 (sanitizer), 9 (PnL/liquidation — loss is always capped at one margin; never trust the channel's percentages), 10 (risk models), 11 (execution lifecycle), and the config flags/defaults in sections 2/17. Never invent or interpolate signal targets — only decimal‑shift‑correct or drop, per section 8. The demo (section 12) must be driven only by real Binance websocket prices, must compute liquidation, and must never read the channel's result messages. Honor `max_concurrent` and skip‑on‑insufficient‑margin (10.2); never silently shrink position size below the signal's intent. All times are UTC. The product UI/text is English. Secrets come from `.env`; never hardcode or log them; require withdrawal‑disabled API keys. After generating each Codex prompt, also generate the matching pytest fixtures using the real example signals (HBAR/ETH/AVAX/AGLD) from section 23. If any requirement in the architecture is ambiguous, ask one precise question before emitting code; otherwise proceed. Do not reorder milestones: the pure signal library (M1) and demo (M4) are verified before any real‑money execution (M7).

---

*End of architecture. No size limit was assumed; sections may be expanded per milestone as Codex requests detail.*
