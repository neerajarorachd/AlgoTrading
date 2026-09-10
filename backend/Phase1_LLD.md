# Phase 1 — Low-Level Design

---

## 1. Project Structure

```
trading-system/
├── backend/
│   ├── app.py                       # Flask app entrypoint (REST + WebSocket server)
│   ├── config.py                    # env-driven config
│   ├── db/
│   │   ├── models.py                 # SQLAlchemy models (all Phase 1 tables)
│   │   └── session.py                # DB session/engine setup (pyodbc + SQLAlchemy)
│   ├── brokers/
│   │   ├── base_broker.py
│   │   └── dhan_broker.py
│   ├── feed/
│   │   ├── market_feed.py            # WebSocket connection manager, subscribe/unsubscribe
│   │   ├── candle_aggregator.py      # tick → 1-min → 3/5-min
│   │   ├── gap_scanner.py
│   │   └── gap_worker.py
│   ├── conditions/
│   │   ├── condition_evaluator.py
│   │   └── operand_resolvers.py      # resolves indicator/value operands from walkthrough data
│   ├── walkthrough/
│   │   ├── walkthrough_engine.py
│   │   └── indicators/               # rsi.py, macd.py, bollinger.py, vwap.py — one calculator each
│   ├── activities/
│   │   ├── activity_engine.py
│   │   └── detectors/                # crossover.py, bb_width.py, candlestick.py, rsi_reversal.py, institutional_entry.py
│   ├── events/
│   │   ├── event_dispatcher.py
│   │   └── notifiers/
│   │       ├── base_notifier.py
│   │       └── telegram_notifier.py
│   ├── api/
│   │   ├── routes_symbols.py
│   │   ├── routes_candles.py
│   │   ├── routes_conditions.py
│   │   ├── routes_events.py
│   │   ├── routes_notifications.py
│   │   └── ws_handlers.py
│   └── scheduler/
│       ├── session_scheduler.py      # market-hours connect/disconnect
│       └── eod_archiver.py
├── frontend/
│   └── src/
│       ├── pages/
│       │   ├── MarketWatch.jsx
│       │   ├── ChartView.jsx
│       │   ├── ConditionBuilder.jsx
│       │   ├── ActivityFeed.jsx
│       │   └── EventRegistration.jsx
│       ├── components/
│       └── api/                      # REST/WS client wrappers
└── deploy/
    ├── nginx.conf
    └── systemd/                      # backend.service, gap_worker.service, notification_worker.service
```

---

## 2. Database Schema (SQL Server)

```sql
CREATE TABLE subscribed_symbols (
    id              INT IDENTITY PRIMARY KEY,
    symbol          VARCHAR(32)  NOT NULL,
    exchange        VARCHAR(16)  NOT NULL,          -- NSE, BSE
    segment         VARCHAR(16)  NOT NULL,          -- EQUITY, INDEX
    added_by        INT          NOT NULL,          -- FK -> users.id
    active          BIT          NOT NULL DEFAULT 1,
    added_at        DATETIME2    NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT UQ_subscribed_symbol UNIQUE (symbol, exchange, segment)
);

CREATE TABLE candles_today (
    id          BIGINT IDENTITY PRIMARY KEY,
    symbol      VARCHAR(32)  NOT NULL,
    timeframe   VARCHAR(8)   NOT NULL,               -- '1min','3min','5min'
    ts          DATETIME2    NOT NULL,
    [open]      DECIMAL(18,4) NOT NULL,
    high        DECIMAL(18,4) NOT NULL,
    low         DECIMAL(18,4) NOT NULL,
    [close]     DECIMAL(18,4) NOT NULL,
    volume      BIGINT       NOT NULL,
    CONSTRAINT UQ_candles_today UNIQUE (symbol, timeframe, ts)
);
CREATE INDEX IX_candles_today_symbol_tf_ts ON candles_today (symbol, timeframe, ts);

CREATE TABLE candles_historical (
    id          BIGINT IDENTITY PRIMARY KEY,
    symbol      VARCHAR(32)  NOT NULL,
    timeframe   VARCHAR(8)   NOT NULL,               -- '1min','3min','5min','1day'
    ts          DATETIME2    NOT NULL,
    [open]      DECIMAL(18,4) NOT NULL,
    high        DECIMAL(18,4) NOT NULL,
    low         DECIMAL(18,4) NOT NULL,
    [close]     DECIMAL(18,4) NOT NULL,
    volume      BIGINT       NOT NULL,
    CONSTRAINT UQ_candles_historical UNIQUE (symbol, timeframe, ts)
);
CREATE INDEX IX_candles_hist_symbol_tf_ts ON candles_historical (symbol, timeframe, ts);

CREATE TABLE last_fetch_status (
    symbol                  VARCHAR(32) NOT NULL,
    timeframe               VARCHAR(8)  NOT NULL,
    last_fetched_timestamp  DATETIME2   NOT NULL,
    updated_at              DATETIME2   NOT NULL DEFAULT SYSUTCDATETIME(),
    PRIMARY KEY (symbol, timeframe)
);

CREATE TABLE candle_gap_queue (
    id              BIGINT IDENTITY PRIMARY KEY,
    symbol          VARCHAR(32) NOT NULL,
    timeframe       VARCHAR(8)  NOT NULL,
    missing_from    DATETIME2   NOT NULL,
    missing_to      DATETIME2   NOT NULL,
    status          VARCHAR(16) NOT NULL DEFAULT 'pending',   -- pending|processing|done|failed
    created_at      DATETIME2   NOT NULL DEFAULT SYSUTCDATETIME(),
    processed_at    DATETIME2   NULL
);
CREATE INDEX IX_gap_queue_status ON candle_gap_queue (status);

CREATE TABLE conditions (
    id              INT IDENTITY PRIMARY KEY,
    name            VARCHAR(128) NOT NULL,
    expression      NVARCHAR(MAX) NOT NULL,          -- JSON expression tree
    created_by      INT          NOT NULL,
    is_reusable     BIT          NOT NULL DEFAULT 1,
    created_at      DATETIME2    NOT NULL DEFAULT SYSUTCDATETIME()
);

CREATE TABLE activities (
    id              BIGINT IDENTITY PRIMARY KEY,
    symbol          VARCHAR(32)  NOT NULL,
    timeframe       VARCHAR(8)   NOT NULL,
    activity_type   VARCHAR(64)  NOT NULL,           -- 'hammer_formed','rsi_bullish_reversal', etc.
    ts              DATETIME2    NOT NULL,
    details         NVARCHAR(MAX) NULL,               -- JSON
    strength_score  DECIMAL(5,2) NULL,
    created_at      DATETIME2    NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX IX_activities_symbol_type_ts ON activities (symbol, activity_type, ts);

CREATE TABLE event_registry (
    id                  INT IDENTITY PRIMARY KEY,
    user_id             INT          NOT NULL,
    event_type          VARCHAR(64)  NOT NULL,        -- specific activity_type, or condition_id ref, or 'any_activity'
    condition_id        INT          NULL,             -- FK -> conditions.id, if condition-based
    scope               NVARCHAR(MAX) NULL,             -- JSON: {symbol, watchlist_id, timeframe}
    channel_preference  VARCHAR(16)  NOT NULL,          -- 'telegram' (Phase 1 only option)
    active              BIT          NOT NULL DEFAULT 1,
    created_at          DATETIME2    NOT NULL DEFAULT SYSUTCDATETIME()
);

CREATE TABLE notification_queue (
    id              BIGINT IDENTITY PRIMARY KEY,
    activity_id     BIGINT       NOT NULL,             -- FK -> activities.id
    user_id         INT          NOT NULL,
    channel         VARCHAR(16)  NOT NULL,
    status          VARCHAR(16)  NOT NULL DEFAULT 'pending',  -- pending|sent|failed
    sent_at         DATETIME2    NULL,
    error_message   NVARCHAR(512) NULL,
    created_at      DATETIME2    NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX IX_notification_queue_status ON notification_queue (status);

CREATE TABLE user_notification_channels (
    id                  INT IDENTITY PRIMARY KEY,
    user_id             INT          NOT NULL,
    channel             VARCHAR(16)  NOT NULL,          -- 'telegram'
    channel_identifier  VARCHAR(128) NOT NULL,          -- telegram chat_id
    verified            BIT          NOT NULL DEFAULT 0,
    created_at          DATETIME2    NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT UQ_user_channel UNIQUE (user_id, channel)
);
```

---

## 3. Core Module Design

### 3.1 `market_feed.py`
```python
class MarketFeed:
    def __init__(self, broker: BaseBroker, on_tick: Callable):
        ...
    def start(self):                       # opens WS connection(s), starts session_scheduler-driven lifecycle
    def subscribe(self, symbol: str, exchange: str, segment: str): ...
    def unsubscribe(self, symbol: str, exchange: str, segment: str): ...
    def stop(self): ...
    # internally: manages multiple underlying WS connections if broker's per-connection symbol cap is exceeded
```

### 3.2 `candle_aggregator.py`
```python
class CandleAggregator:
    def __init__(self, on_candle_closed: Callable):
        self.forming = {}          # {(symbol, '1min'): Candle}
        self.buffers = {}          # {(symbol, '3min'): [Candle,...]}, {(symbol,'5min'): [...]}

    def on_tick(self, symbol: str, ltp: float, volume: int, ts: datetime): ...
        # updates self.forming[(symbol,'1min')]; on minute boundary -> finalize()

    def finalize_1min(self, symbol: str, candle: Candle):
        # persist to candles_today, emit event, feed into 3min/5min buffers
        # when buffer reaches N candles aligned to boundary -> aggregate_candles() -> finalize higher TF

def aggregate_candles(candles: list[Candle]) -> Candle:
    return Candle(
        open=candles[0].open, high=max(c.high for c in candles),
        low=min(c.low for c in candles), close=candles[-1].close,
        volume=sum(c.volume for c in candles), ts=candles[0].ts
    )
```

### 3.3 `gap_scanner.py` / `gap_worker.py`
```python
def gap_scanner(symbol: str, timeframe: str):
    last_ts = last_fetch_status.get(symbol, timeframe)
    expected = generate_expected_timestamps(last_ts, now(), timeframe)
    existing = candles_today.get_timestamps(symbol, timeframe)
    for gap in find_gaps(expected, existing):
        candle_gap_queue.insert(symbol, timeframe, gap.start, gap.end, status="pending")

def gap_worker_loop(poll_interval=5):
    while True:
        job = candle_gap_queue.get_next_pending()
        if not job:
            time.sleep(poll_interval); continue
        job.status = "processing"
        candles = dhan_broker.get_historical_data(job.symbol, job.timeframe, job.missing_from, job.missing_to)
        candles_today.insert_many(candles)
        last_fetch_status.upsert(job.symbol, job.timeframe, job.missing_to)
        job.status = "done"; job.processed_at = now()
```

### 3.4 `condition_evaluator.py`
```python
def evaluate(expr: dict, symbol: str, timeframe: str, walkthrough: WalkthroughSnapshot) -> bool:
    if expr["type"] == "group":
        results = [evaluate(c, symbol, timeframe, walkthrough) for c in expr["children"]]
        return all(results) if expr["logic"] == "AND" else any(results)
    left = resolve_operand(expr["left"], symbol, timeframe, walkthrough)
    right = resolve_operand(expr["right"], symbol, timeframe, walkthrough)
    return OPERATORS[expr["operator"]](left, right)

OPERATORS = {
    ">": operator.gt, "<": operator.lt, ">=": operator.ge, "<=": operator.le, "==": operator.eq,
    "CROSS_ABOVE": lambda l, r: l.prev <= r.prev and l.curr > r.curr,
    "CROSS_BELOW": lambda l, r: l.prev >= r.prev and l.curr < r.curr,
}
```

### 3.5 `walkthrough_engine.py`
```python
class WalkthroughEngine:
    def __init__(self):
        self.series = {}   # {(symbol, timeframe): {"rsi": [...], "macd": [...], "bb": [...], "vwap": [...]}}
        self.calculators = {"rsi": RSICalculator(), "macd": MACDCalculator(),
                             "bb": BollingerCalculator(), "vwap": VWAPCalculator()}

    def on_candle_closed(self, symbol, timeframe, candle):
        for name, calc in self.calculators.items():
            self.series[(symbol, timeframe)][name].append(
                calc.update_incremental(self.series[(symbol, timeframe)][name], candle)
            )

    def latest(self, symbol, timeframe) -> WalkthroughSnapshot: ...
    def compute_historical(self, symbol, timeframe, candles) -> WalkthroughSeries: ...   # bulk mode
```

### 3.6 `activity_engine.py`
```python
DETECTOR_REGISTRY = [CrossoverDetector(), BBWidthDetector(), CandlestickDetector(),
                      RSIReversalDetector(), InstitutionalEntryDetector()]

def on_candle_closed(symbol, timeframe, candle):
    walkthrough_engine.on_candle_closed(symbol, timeframe, candle)
    snapshot = walkthrough_engine.latest(symbol, timeframe)

    for detector in DETECTOR_REGISTRY:
        activity = detector.check(symbol, timeframe, snapshot)
        if activity:
            activity_id = activities_table.insert(activity)
            event_dispatcher.dispatch(activity_id, activity)
```

### 3.7 `event_dispatcher.py`
```python
def dispatch(activity_id: int, activity: Activity):
    matches = event_registry.find_matches(activity.activity_type, activity.symbol, activity.timeframe)
    for reg in matches:
        notification_queue.enqueue(activity_id=activity_id, user_id=reg.user_id, channel=reg.channel_preference)
```

### 3.8 `telegram_notifier.py`
```python
class TelegramNotifier(BaseNotifier):
    def send(self, user_id: int, message: str):
        chat_id = user_notification_channels.get_identifier(user_id, "telegram")
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                       json={"chat_id": chat_id, "text": message})

def notification_worker_loop(poll_interval=2):
    while True:
        job = notification_queue.get_next_pending()
        if not job:
            time.sleep(poll_interval); continue
        try:
            get_notifier(job.channel).send(job.user_id, format_message(job.activity_id))
            job.status = "sent"; job.sent_at = now()
        except Exception as e:
            job.status = "failed"; job.error_message = str(e)
```

---

## 4. REST API Specification

| Method | Path | Purpose | Request Body | Response |
|---|---|---|---|---|
| GET | `/api/symbols` | List subscribed symbols | — | `[{symbol, exchange, segment, active}]` |
| POST | `/api/symbols` | Add symbol to live feed | `{symbol, exchange, segment}` | `{id, symbol, ...}` |
| DELETE | `/api/symbols/{id}` | Remove symbol from feed | — | `204` |
| GET | `/api/candles` | Fetch candles | query: `symbol, timeframe, from, to` | `[{ts, open, high, low, close, volume}]` |
| GET | `/api/conditions` | List saved conditions | — | `[{id, name, expression}]` |
| POST | `/api/conditions` | Save a new condition | `{name, expression}` | `{id, name, expression}` |
| PUT | `/api/conditions/{id}` | Update condition | `{name, expression}` | `{id, ...}` |
| DELETE | `/api/conditions/{id}` | Delete condition | — | `204` |
| POST | `/api/conditions/{id}/preview` | Live match preview | `{watchlist_id}` | `{matches: [symbol,...]}` |
| GET | `/api/activities` | Query activity feed | query: `symbol, type, from, to` | `[{id, symbol, activity_type, ts, details, strength_score}]` |
| GET | `/api/events/registrations` | List user's event registrations | — | `[{id, event_type, scope, channel_preference, active}]` |
| POST | `/api/events/registrations` | Register for an event | `{event_type, condition_id?, scope, channel_preference}` | `{id, ...}` |
| DELETE | `/api/events/registrations/{id}` | Unregister | — | `204` |
| GET | `/api/notifications/channels` | List user's linked notification channels | — | `[{id, channel, channel_identifier, verified}]` |
| POST | `/api/notifications/channels` | Add a notification channel | `{channel, channel_identifier}` | `{id, verified: false}` |
| POST | `/api/notifications/channels/{id}/verify` | Confirm verification (e.g. Telegram bot handshake code) | `{verification_code}` | `{id, verified: true}` |
| PUT | `/api/notifications/channels/{id}` | Change a channel's account (e.g. switch to a different Telegram bot/chat ID) — re-verification required | `{channel_identifier}` | `{id, verified: false}` |
| DELETE | `/api/notifications/channels/{id}` | Remove a linked channel | — | `204` |

---

## 5. WebSocket Protocol (Server ⇄ Frontend)

**Client → Server**
```json
{"action": "subscribe_ticks", "symbols": ["RELIANCE", "TCS"]}
{"action": "unsubscribe_ticks", "symbols": ["TCS"]}
{"action": "subscribe_activity_feed"}
```

**Server → Client**
```json
{"type": "tick", "symbol": "RELIANCE", "ltp": 2456.30, "ts": "2026-09-05T09:16:02Z"}
{"type": "candle_closed", "symbol": "RELIANCE", "timeframe": "1min", "candle": {...}}
{"type": "activity", "symbol": "RELIANCE", "activity_type": "hammer_formed", "ts": "...", "details": {...}}
```

---

## 6. Key Sequence Flows

### 6.1 Symbol subscribe (from frontend)
1. Frontend: `POST /api/symbols {symbol, exchange, segment}`
2. Backend: insert into `subscribed_symbols`
3. Backend: `market_feed.subscribe(symbol, exchange, segment)` → sends subscribe frame to Dhan WebSocket
4. Backend: returns `201` with the new row
5. Frontend: adds symbol to Market Watch list, opens local WS subscription for ticks

### 6.2 Tick → Candle → Walkthrough → Activity → Event → Notification
1. Dhan WS pushes a tick → `market_feed.on_tick(symbol, ltp, volume, ts)`
2. `candle_aggregator.on_tick(...)` updates the forming 1-min candle
3. On minute boundary: `candle_aggregator.finalize_1min(...)` → persists to `candles_today`, appends to 3-min/5-min buffers, emits `candle_closed`
4. `walkthrough_engine.on_candle_closed(...)` extends RSI/MACD/BB/VWAP series by one point
5. `activity_engine.on_candle_closed(...)` runs all registered detectors against the latest walkthrough snapshot
6. On a match: insert into `activities`, call `event_dispatcher.dispatch(activity_id, activity)`
7. `event_dispatcher` finds matching `event_registry` rows, enqueues rows into `notification_queue`
8. `notification_worker_loop` picks up pending rows, calls `telegram_notifier.send(...)`, updates status
9. In parallel, backend WS server pushes `candle_closed` and `activity` events directly to any connected frontend clients (independent of the notification path, for the live UI)

### 6.3 Gap detection & recovery
1. `gap_scanner` runs on a schedule (e.g. every 2 minutes during market hours) per subscribed symbol/timeframe
2. Compares expected vs. actual timestamps in `candles_today`
3. Missing ranges → inserted into `candle_gap_queue` (status `pending`)
4. `gap_worker_loop` (separate process) polls, fetches the exact missing range via `dhan_broker.get_historical_data()`, inserts into `candles_today`, updates `last_fetch_status`, marks job `done`

### 6.4 End-of-day archival
1. `session_scheduler` triggers `eod_archiver.run()` after market close
2. Copies all of today's rows from `candles_today` → `candles_historical`
3. Computes and inserts the day's daily candle into `candles_historical` (timeframe `'1day'`)
4. Truncates `candles_today`, ready for next session

---

## 7. Process / Thread Model

| Process | Responsibility | Managed by |
|---|---|---|
| `backend` (gunicorn) | Flask REST + WebSocket server, hosts `market_feed`, `candle_aggregator`, `walkthrough_engine`, `activity_engine`, `event_dispatcher` in-process (threads) | systemd |
| `gap_worker` | Polls `candle_gap_queue`, fills missing candle ranges | systemd, separate process |
| `notification_worker` | Polls `notification_queue`, delivers via Telegram | systemd, separate process |
| `session_scheduler` | Triggers feed connect (9:15 default) / disconnect and EOD archival | systemd timer or in-process scheduler thread |

Running `gap_worker` and `notification_worker` as separate OS processes (not threads inside the main backend) keeps them isolated from GIL contention with the tick-processing hot path.

---

## 8. Configuration (env vars)

```
DHAN_API_KEY=
DHAN_API_SECRET=
DHAN_CLIENT_ID=
DB_CONNECTION_STRING=          # SQL Server via pyodbc
TELEGRAM_BOT_TOKEN=
MARKET_OPEN_TIME=09:15
PRE_OPEN_ENABLED=false
GAP_SCANNER_INTERVAL_SEC=120
GAP_WORKER_POLL_INTERVAL_SEC=5
NOTIFICATION_WORKER_POLL_INTERVAL_SEC=2
```

---

## 9. Error Handling Notes (Phase 1)

- **Dhan WebSocket disconnect** — `market_feed` reconnects with exponential backoff; on reconnect, immediately triggers a `gap_scanner` pass for all subscribed symbols to catch anything missed while disconnected.
- **Telegram API failure** — `notification_queue` row marked `failed` with `error_message`; a periodic retry pass (or manual "retry failed" action in the UI) can re-queue failed rows.
- **Historical data fetch failure (gap_worker)** — job stays `pending` (or marked `failed` after N retries), retried on the next poll cycle rather than blocking other queued gaps.
