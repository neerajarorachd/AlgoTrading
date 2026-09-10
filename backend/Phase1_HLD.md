# Phase 1 — High-Level Design
## Live Data, Candles, Walkthroughs, Activities, Events & Notifications

---

## 1. Scope

**In scope for Phase 1:**
- Live market data via Dhan WebSocket (dynamic symbol subscribe/unsubscribe from the UI)
- Tick → 1-min candle aggregation, generic multi-timeframe resampling (1/3/5-min persisted; higher timeframes on demand)
- Historical data backfill with gap detection/recovery (no repeated re-fetching)
- Condition Builder (Chartink-style) & shared expression evaluator
- Walkthrough engine (price, RSI, MACD, BB, VWAP series)
- Activity detectors (crossovers, BB widening, candlestick formations, RSI reversal, institutional-entry heuristic)
- Event registry + dispatcher
- Telegram notifications
- Supporting REST + WebSocket APIs
- Frontend: Market Watch, live chart, Condition Builder UI, Activity Feed, Event Registration, Settings (Notification Channels)

**Explicitly out of scope for Phase 1** (later phases): order placement, strategies, backtesting, paper/live trading, ledger, risk management, multi-broker (Zerodha/Upstox).

---

## 2. Architecture

```
                    ┌───────────────────────────────┐
                    │          React Frontend          │
                    │  Market Watch │ Chart │ Condition │
                    │  Builder │ Activity Feed │ Events  │
                    └────────────────┬──────────────────┘
                                     │ REST + WebSocket
                                     ▼
                    ┌───────────────────────────────┐
                    │     Nginx (reverse proxy)        │
                    └────────────────┬──────────────────┘
                                     ▼
                    ┌───────────────────────────────┐
                    │   Flask API + WebSocket Server   │
                    └───┬─────────────┬───────────┬─────┘
                        │             │           │
            ┌───────────┘             │           └───────────┐
            ▼                         ▼                       ▼
  ┌──────────────────┐   ┌──────────────────────┐   ┌──────────────────┐
  │  Candle Pipeline   │   │  Condition / Walkthrough│   │  Event & Notify   │
  │  market_feed        │   │  walkthrough_engine     │   │  event_dispatcher  │
  │  candle_aggregator  │   │  condition_evaluator    │   │  notification_queue│
  │  gap_scanner/worker │   │  activity_detectors      │   │  telegram_notifier │
  └─────────┬──────────┘   └───────────┬─────────────┘   └─────────┬─────────┘
            │                          │                          │
            ▼                          ▼                          ▼
  ┌──────────────────┐       ┌──────────────────┐       ┌──────────────────┐
  │  Dhan WebSocket    │       │  SQL Server Express│       │  Telegram Bot API  │
  │  Dhan REST (hist.) │       │  (all persistence) │       │                    │
  └──────────────────┘       └──────────────────┘       └──────────────────┘
```

---

## 3. Components

### 3.1 Broker Adapter (Market Data Only)
- `base_broker.py` — full interface defined now (per master HLD §4.1), but only `connect()`, `subscribe_feed()`, `get_historical_data()` are implemented/exercised in Phase 1.
- `dhan_broker.py` — Dhan WebSocket + REST historical data implementation.

### 3.2 Live Feed & Subscription Management
- `subscribed_symbols` table drives what's live-subscribed; add/remove from the frontend updates both DB and the live WebSocket connection immediately.
- Handles broker per-connection symbol limits transparently (multiple underlying connections if needed).
- Session-aware: connects at 9:15 by default; optional 9:00–9:08 pre-open handled as a distinct indicative-price mode.

### 3.3 Candle Pipeline
- Tick → 1-min forming candle → finalized on minute boundary → generic resampler builds 3/5-min from 1-min.
- `candles_today` (small, current day) vs `candles_historical` (archive); EOD job copies and clears.
- `last_fetch_status` watermark avoids re-fetching known data.
- `gap_scanner` detects missing ranges → `candle_gap_queue` → `gap_worker` fills them (poll-based to start).

### 3.4 Condition Builder & Shared Evaluator
- Chartink-style visual builder; conditions stored once, evaluated by one shared `condition_evaluator.py`.
- Used in Phase 1 to define activity/event trigger conditions (reused unmodified by Scanner and Strategies in later phases).

### 3.5 Walkthrough Engine
- Synchronized price/RSI/MACD/BB/VWAP series, computed incrementally on every `candle_closed` event.
- Feeds both activity detectors and the condition evaluator.

### 3.6 Activity Detectors
- Built-in: line crossings, BB widening/narrowing, candlestick formations (hammer, three soldiers, etc.), RSI reversal/divergence, institutional-entry heuristic (volume Z-score + directional close + follow-through — flagged as heuristic, not certainty).

### 3.7 Event System & Notifications
- Every activity automatically raises an event; `event_registry` holds user subscriptions (broad or scoped); `event_dispatcher` matches and queues notifications.
- `telegram_notifier.py` delivers via `notification_queue` (retry-on-failure).

### 3.8 Backend API
- REST: symbol/watchlist management, historical candle queries, condition CRUD, event registration CRUD, notification channel setup, activity feed queries.
- WebSocket (server → frontend): live ticks, candle-close events, live activity/event stream.

### 3.9 Frontend
- Market Watch (live prices, add/remove symbols), live candle chart, Condition Builder, Activity Feed, Event Registration, **Settings → Notification Channels** — add, change, or remove notification accounts (Telegram bot/chat ID now; same screen extends to email, SMS, WhatsApp as those channels are added later, one at a time, without redesign).

---

## 4. Data Model (Phase 1 tables)

- `subscribed_symbols` — symbol, exchange, segment, added_by, active, added_at
- `candles_today` — symbol, timeframe, timestamp, OHLCV
- `candles_historical` — same, multi-day archive
- `last_fetch_status` — symbol, timeframe, last_fetched_timestamp
- `candle_gap_queue` — symbol, timeframe, missing_from, missing_to, status
- `conditions` — id, name, expression (JSON), created_by, is_reusable
- `activities` — id, symbol, timeframe, activity_type, timestamp, details (JSON), strength_score
- `event_registry` — id, user_id, event_type, scope (JSON), channel_preference, active
- `notification_queue` — id, activity_id, user_id, channel, status, sent_at, error_message
- `user_notification_channels` — user_id, channel, channel_identifier, verified

(Full column-level schema in the companion LLD document.)

---

## 5. Deployment (Phase 1 slice)

```
Azure Linux VM (2 vCPU / 16 GB RAM)
├── Nginx              → serves React build, proxies /api & /ws → Flask
├── Flask backend        → gunicorn workers (API + WS), systemd-managed
├── SQL Server Express   → local instance
├── market_feed process   → maintains Dhan WebSocket connection(s)
├── gap_worker process    → fills historical gaps from queue
└── notification_worker   → delivers queued Telegram notifications
```

---

## 6. Non-Functional Notes (Phase 1)

- **Reliability:** feed reconnect with backoff on Dhan WebSocket drop; gap detection catches any missed candles automatically rather than requiring manual intervention.
- **Performance:** `candles_today` kept small/fast by design (EOD archival); walkthrough computed incrementally, not recomputed from scratch each candle.
- **Extensibility:** activity detectors and walkthrough calculators are registry-based — new ones added without touching the pipeline.
