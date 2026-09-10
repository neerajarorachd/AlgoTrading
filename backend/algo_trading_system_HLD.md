# Algo Trading System — High-Level Design (HLD)

## 1. Overview

A broker-agnostic algorithmic trading platform supporting **backtesting**, **paper trading**, and **live trading** from a single strategy codebase, initially integrated with **Dhan**, with additional brokers pluggable later.

Strategies are stored as **Python files on disk** (version-controllable, easy to diff/review), with metadata tracked in the database, dynamically loaded, and run in a sandboxed execution environment against any of the three modes (backtest / paper / live) without modification.

**Deployment target:** Azure Linux VM, 2 vCPU / 16 GB RAM, SQL Server Express, self-hosted (no containers required initially).

---

## 2. Goals & Non-Goals

**Goals**
- Broker abstraction — Dhan first, extensible to Zerodha/Upstox/etc. later
- Strategies authored/stored in DB, hot-loadable without full system restart
- Same strategy code runs unmodified in backtest, paper, and live modes
- Real-time market data, order execution, and portfolio tracking
- Web dashboard for strategy management, monitoring, and control

**Non-Goals (initially)**
- Multi-user / multi-tenant support (single-user system to start)
- High-frequency trading (sub-millisecond latency) — not a design target
- Options/futures Greeks analytics (can be added later)

---

## 3. System Architecture

```
                        ┌─────────────────────────────┐
                        │        React Frontend        │
                        │  (Dashboard, Strategy Editor, │
                        │   Backtest UI, Live Monitor)  │
                        └───────────────┬───────────────┘
                                        │ REST + WebSocket
                                        ▼
                        ┌─────────────────────────────┐
                        │       Nginx (Reverse Proxy)   │
                        └───────────────┬───────────────┘
                                        ▼
                        ┌─────────────────────────────┐
                        │      Flask API + WebSocket    │
                        │         Server (Backend)      │
                        └───┬───────┬───────┬───────────┘
                            │       │       │
              ┌─────────────┘       │       └─────────────┐
              ▼                     ▼                     ▼
    ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
    │  Strategy Runner   │ │  Backtest Engine  │ │  Paper/Live Engine │
    │ (sandboxed exec,   │ │ (historical replay,│ │ (order mgmt, fills,│
    │  loads code from file)│ │  metrics, reports) │ │  portfolio state)  │
    └─────────┬──────────┘ └─────────┬──────────┘ └─────────┬──────────┘
              │                      │                      │
              └──────────────────────┴──────────────────────┘
                                     │
                    ┌────────────────┴────────────────┐
                    ▼                                  ▼
        ┌───────────────────────┐         ┌───────────────────────┐
        │   Broker Abstraction   │         │      Data Layer         │
        │   Layer (base_broker)  │         │  Market feed (WS/REST), │
        │   └── Dhan adapter     │         │  Historical data cache  │
        │   └── (future brokers) │         │                         │
        └───────────┬─────────────┘         └───────────┬─────────────┘
                    ▼                                  ▼
            ┌───────────────┐                  ┌───────────────┐
            │   Dhan API     │                  │  SQL Server    │
            │ (orders, quotes,│                  │  Express       │
            │  historical data)│                 │ (all persistence)│
            └───────────────┘                  └───────────────┘
```

---

## 4. Core Components

### 4.1 Broker Abstraction Layer
- `base_broker.py`: abstract interface — `connect()`, `get_quote()`, `place_order()`, `modify_order()`, `cancel_order()`, `get_positions()`, `get_holdings()`, `get_historical_data()`, `subscribe_feed()`.
- `dhan_broker.py`: concrete implementation using Dhan's REST + WebSocket API — built first (Phase 1).
- `zerodha_broker.py`, `upstox_broker.py`: same interface, added in Phase 3 (§8) — historical data + WebSocket feed to start, order-placement methods exercised from Phase 4/5 onward.
- Adding a new broker later = implementing this interface only; no changes needed elsewhere in the system.

### 4.2 Strategy Layer
- `strategy_base.py`: fixed interface strategies must implement — `on_init()`, `on_tick()`, `on_candle()`, `on_order_update()`, `on_stop()`.
- Strategy source code lives as individual `.py` files under a `strategies/` directory on disk (e.g. `strategies/moving_avg_crossover.py`) — easy to edit, version-control (git), and review as diffs.
- The `strategies` DB table stores **metadata only**: id, name, file_path, parameters (JSON), mode, status, created_at, updated_at — not the code itself.
- The frontend's Strategy Editor writes to these files (via a backend endpoint) and updates the corresponding DB metadata row; direct file edits on the VM (e.g. via SSH/git pull) are also picked up automatically.
- `strategy_runner.py`: loads code from the file path at runtime, validates/sandboxes it (restricted builtins, execution timeouts, no direct filesystem/network access — only via injected broker/data handles), and runs it in its own thread with resource limits.
- A file-watcher (or manual "reload" trigger from the frontend) detects changes and hot-reloads the strategy without restarting the whole system.
- Same strategy object is fed into backtest, paper, or live engine interchangeably.
- A strategy has two distinct parts: **entry criteria** (the strategy's own code — `on_tick`/`on_candle` signal logic — or a no-code condition built in §4.5) and **exit criteria** (a structured, declarative list — see §4.8 Monitoring Engine — evaluated independently and continuously, not just when the strategy code runs). This separation is what lets exit conditions like "wind up at 2pm" or "close if BB has narrowed" apply even if the strategy's own logic never re-checks them.

### 4.3 Execution Engines
| Engine | Data Source | Order Handling |
|---|---|---|
| **Backtest** | Historical candles from cache/DB | Simulated fills, slippage, brokerage model |
| **Paper** | Live market feed | Simulated fills against real live prices; virtual portfolio |
| **Live** | Live market feed | Real orders routed to broker |

All three write to the same schema (`orders`, `trades`, `positions`, `equity_curve`) so the frontend renders them identically regardless of mode.

**"Backtest" is actually two distinct engines, answering two different questions:**

#### 4.3.1 Strategy Backtesting
The one described above — replays a specific strategy's entry/exit code against historical candles, simulating real trade entries, exits, sizing, and P&L (§7 pseudocode). Answers: *"if I had run this exact strategy, what would my P&L have been?"*

**P&L reporting (day / monthly / overall)**
- Every backtest run's trades and equity curve are broken down into three published views:
  - **Day-wise P&L** — one row per trading day: date, P&L, trade count, win/loss count.
  - **Monthly P&L** — one row per calendar month: aggregated P&L, trade count, win rate for that month.
  - **Overall P&L** — single summary for the full backtest period: total P&L, Sharpe, max drawdown, CAGR, win rate, total trades.
- `backtest_pnl_breakdown` (DB table) — run_id, period_type (`day`/`month`/`overall`), period_label (date or `YYYY-MM`), pnl, trade_count, win_count, loss_count — computed once when the backtest completes, from the same `trades`/`equity_curve` data, so all three views stay consistent with each other.

#### 4.3.2 Stock Backtesting (Clue / Pattern Discovery)
A different kind of backtest — not simulating trades or P&L at all, but discovering whether an **activity or condition is a useful predictive clue** for a given stock, by running the Walkthrough Engine and Activity Detectors (§4.9) over historical data and measuring what tends to happen *afterward*. Answers: *"historically, when X happens on this stock, what usually happens next?"*

- **Two modes:**
  - **Day trading mode** — 1/3/5-min candles, for intraday pattern discovery.
  - **Long-term mode** — daily (or higher) candles, for swing/positional pattern discovery.
- **Process:**
  1. Ensure historical candle data is present for the symbol/timeframe/date range (reuses the incremental fetch-and-resume logic from §4.4 — never re-downloads what's already fetched).
  2. Run the Walkthrough Engine (§4.9) in historical/bulk mode over that range — generates the price, RSI, MACD, BB, VWAP series.
  3. Scan for the **target** — either a built-in activity type (crossover, candle formation, BB widening, RSI reversal, etc.) or a custom condition built in the Condition Builder (§4.5), including conditions that relate *multiple* walkthroughs to each other (e.g. "price crosses above VWAP AND BB width is below its 20-period average" — relationships among walkthroughs, not just single-indicator triggers).
  4. For every historical occurrence (trigger) found, look forward N candles/days and measure the outcome: return %, whether it moved favorably, how far it moved, how long it took.
  5. Aggregate across all occurrences into summary statistics: win rate, average return, median return, best/worst case, sample size.
- **Pseudocode:**
  ```python
  def run_stock_backtest(symbol, timeframe, mode, start_date, end_date, target, forward_window):
      ensure_historical_data(symbol, timeframe, start_date, end_date)      # incremental, resumes from last_fetch_status
      candles = candles_historical.get_range(symbol, timeframe, start_date, end_date)

      walkthrough = walkthrough_engine.compute_historical(symbol, timeframe, candles)

      if target.type == "activity":
          triggers = activity_detectors.scan_historical(target.activity_type, walkthrough)
      else:  # target.type == "condition" — reuses the same Condition Builder / evaluator as everywhere else
          triggers = condition_evaluator.scan_historical(target.condition_id, walkthrough)

      results = []
      for trigger in triggers:
          forward_candles = get_forward_candles(candles, trigger.timestamp, forward_window)
          results.append(outcome_analyzer.compute(trigger, forward_candles))    # return %, direction, time-to-move

      summary = outcome_analyzer.summarize(results)      # win rate, avg/median return, best/worst, sample size
      save_stock_backtest_result(run_id, results, summary)
      return summary
  ```
- **Data model:**
  - `stock_backtest_runs` — id, symbol, timeframe, mode (day_trading/long_term), start_date, end_date, target (activity_type or condition_id), forward_window, status, created_at
  - `stock_backtest_results` — id, run_id, trigger_timestamp, forward_return, forward_high, forward_low, outcome (win/loss/neutral) — one row per historical occurrence
  - `stock_backtest_summary` — id, run_id, total_triggers, win_rate, avg_return, median_return, std_dev, best_case, worst_case
- This engine is what makes the Condition Builder genuinely useful before committing a condition to a live strategy or event — you can first check, on real history, whether it actually tends to precede favorable moves.

#### 4.3.3 Parameter Optimization (Multi-Combination Backtesting)
Instead of backtesting one fixed set of strategy parameters, run the **same strategy across every combination** in a defined parameter grid, and surface which combination performed best.

**Defining a parameter sweep**
```json
"param_ranges": {
  "rsi_threshold": {"min": 45, "max": 65, "step": 5},
  "target_pct":    {"min": 0.5, "max": 1.8, "step": 0.3}
}
```
This example generates `5 × 5 = 25` combinations (45/50/55/60/65 × 0.5/0.8/1.1/1.4/1.7... up to 1.8) — every parameter listed gets its own min/max/step, and the system takes the full Cartesian product across all of them.

**Execution**
- Reuses the exact same single-backtest function (§4.3.1) and the same 2-vCPU-aware process pool from §7 — each combination is just another job in the queue (`MAX_PARALLEL_BACKTESTS = 2`), so a 25-combination sweep runs 2 at a time rather than needing 25 cores.
- Each combination's result (metrics + day/month/overall P&L breakdown) is stored individually, tagged with the exact parameter set that produced it.

**Pseudocode**
```python
def generate_param_combinations(param_ranges: dict) -> list[dict]:
    grids = {k: list(frange(v["min"], v["max"], v["step"])) for k, v in param_ranges.items()}
    keys = list(grids.keys())
    return [dict(zip(keys, combo)) for combo in itertools.product(*grids.values())]

def run_optimization(strategy_file, symbol, timeframe, start_date, end_date, param_ranges, objective="total_pnl"):
    combinations = generate_param_combinations(param_ranges)
    jobs = [{"strategy_file": strategy_file, "symbol": symbol, "timeframe": timeframe,
             "start_date": start_date, "end_date": end_date, "params": combo} for combo in combinations]

    results = process_backtest_queue(jobs)                          # same Pool(MAX_PARALLEL_BACKTESTS) as §7

    for combo, result in zip(combinations, results):
        backtest_optimization_results.save(run_batch_id, combo, result.metrics)

    best = max(results, key=lambda r: r.metrics[objective])         # objective is user-selectable: total_pnl, sharpe, win_rate, etc.
    return best, results
```

**Comparing & selecting the best combination**
- Results view lists every combination with its parameters and metrics, sortable/filterable by any column (total P&L, Sharpe, max drawdown, win rate).
- User picks the **objective metric** to optimize for (default: total P&L, but Sharpe or win rate can be selected instead) — the "best" combination is whichever ranks highest on that objective.
- Drilling into any single combination shows its full day/monthly/overall P&L breakdown and equity curve, same as a regular single backtest.

**Data model**
- `backtest_optimization_runs` — id, strategy_id, symbol, timeframe, start_date, end_date, param_ranges (JSON), objective_metric, status, created_at
- `backtest_optimization_results` — id, run_id, params (JSON), total_pnl, sharpe, max_drawdown, win_rate, backtest_pnl_breakdown_ref, created_at


### 4.4 Data Layer

**Live subscription management (dynamic, frontend-driven)**
- `subscribed_symbols` (DB table) — symbol, exchange, segment (equity/index), added_by, active, added_at. This is the live source of truth for what the WebSocket feed subscribes to.
- Adding/removing a stock or index from the frontend (Watchlist / Market Watch screens) calls a backend API that updates `subscribed_symbols` **and** dynamically subscribes/unsubscribes on the live Dhan WebSocket connection — no restart of the feed connection needed.
- If a broker's single WebSocket connection has a max-symbols limit, `market_feed.py` transparently manages multiple underlying connections/batches and merges the tick stream — this stays invisible to the rest of the system.

**Tick → 1-minute candle aggregation**
- `candle_aggregator.py` maintains an in-memory "forming candle" per subscribed symbol: `{open, high, low, close, volume, tick_count}`.
- On each incoming tick: update the forming candle (`high = max(high, ltp)`, `low = min(low, ltp)`, `close = ltp`, `volume += tick_volume`).
- On every minute boundary: finalize the 1-min candle, emit a `candle_closed` event, push it into the in-memory candle store, and start a fresh forming candle.

**Generic multi-timeframe generation (not hardcoded per timeframe)**
- All higher timeframes (3, 5, 10, 15, 30, 60-min, daily) are derived **only from 1-min candles** — never independently fetched or computed from ticks directly.
- A single generic resampler handles any N-minute multiple:
  ```python
  def aggregate_candles(one_min_candles: list[Candle]) -> Candle:
      return Candle(
          open=one_min_candles[0].open,
          high=max(c.high for c in one_min_candles),
          low=min(c.low for c in one_min_candles),
          close=one_min_candles[-1].close,
          volume=sum(c.volume for c in one_min_candles),
          timestamp=one_min_candles[0].timestamp
      )
  ```
- Each subscribed higher timeframe (e.g. "5-min") keeps its own rolling buffer; on every `candle_closed` (1-min) event, the buffer appends the new candle, and once it holds N candles matching that timeframe's boundary (e.g. 5 candles aligned to a 5-min mark), it calls `aggregate_candles()` to emit the higher-timeframe candle and resets.
- **Daily candle** = aggregate of all of that day's 1-min candles (open = day's first, close = day's last, high/low = day's extremes, volume = day's sum) — same generic function, just the full day as input.
- Timeframes are only computed **on demand** — a strategy/watchlist declares which timeframes it needs, and only those buffers are maintained, to avoid wasted CPU/memory on unused timeframes.
- **Persisted vs. on-the-fly:** only **1, 3, and 5-min** candles are persisted to `candles_today`/`candles_historical` (these are the frequently-reused base granularities). Larger timeframes (10/15/30/60-min, daily) are generated on demand from the persisted 1-min data using the same generic resampler — not separately stored — since they can always be rebuilt cheaply from 1-min candles.

**Market session timing**
- Feed connects and tick processing starts automatically at a configured time — default **9:15 AM** (regular session open).
- Optional **pre-open window** (9:00–9:08 AM) can be enabled separately: this period uses order-collection/price-discovery data rather than continuous trade ticks, so it's handled as a distinct mode (captures indicative opening price only) rather than feeding the same 1-min candle aggregator.
- Feed disconnects and engines idle automatically after market close; a scheduler (systemd timer or in-app scheduler) manages connect/disconnect timing so nothing needs manual start/stop daily.

**Today vs. historical candle storage (separate tables)**
- `candles_today` — holds only the **current trading day's** candles (1/3/5-min). Small, fast, hot table — every read during live trading hits this.
- `candles_historical` — the long-term multi-day archive (1/3/5-min + daily rollups).
- **End-of-day archival job** (`eod_archiver.py`): after market close, copies the day's rows from `candles_today` → `candles_historical`, computes and stores the day's daily candle, then clears `candles_today` ready for the next session. This keeps the hot table small and query-fast permanently, regardless of how much history accumulates.

**Fetch watermark + gap detection + queue (instead of blind periodic re-fetching)**
- `last_fetch_status` (DB table) — one row per `(symbol, timeframe)`, storing `last_fetched_timestamp`. This is the single source of truth for "how up to date is our data for this symbol/timeframe."
- `gap_scanner.py` — periodically (or triggered on demand) compares each symbol/timeframe's expected candle sequence (every 1/3/5 min between session start and now) against what's actually present in `candles_today`. Any missing timestamp range is a **gap**.
- Detected gaps are written to a `candle_gap_queue` table (`symbol`, `timeframe`, `missing_from`, `missing_to`, `status: pending/processing/done/failed`, `created_at`) rather than fetched immediately inline — decouples detection from fetching.
- `gap_worker.py` — a separate process that watches this queue, picks up `pending` rows, fetches exactly that missing range from Dhan's historical API, inserts the candles into `candles_today`, updates `last_fetch_status`, and marks the queue row `done`.
- **Two ways to run the worker** (either works with this design; can start with polling and move to push):
  - *Pull/poll* — worker polls `candle_gap_queue` for pending rows every few seconds. Simple, no extra infra.
  - *Push* — `gap_scanner.py` publishes directly to a lightweight task queue (e.g. Redis/RQ, or an in-process pub-sub) the moment it detects a gap, waking the worker immediately instead of waiting for the next poll cycle. Worth adding once gap-fill latency actually matters; not needed for an MVP.

**Pseudocode**
```python
def gap_scanner(symbol, timeframe):
    last_ts = last_fetch_status.get(symbol, timeframe)          # watermark
    expected = generate_expected_timestamps(last_ts, now(), timeframe)
    existing = candles_today.get_timestamps(symbol, timeframe)
    missing_ranges = find_gaps(expected, existing)

    for gap in missing_ranges:
        candle_gap_queue.insert(symbol, timeframe, gap.start, gap.end, status="pending")
        # push variant: task_queue.enqueue(fill_gap, symbol, timeframe, gap)

def gap_worker():
    while True:
        job = candle_gap_queue.get_next_pending()               # poll variant
        if not job:
            sleep(5); continue

        job.status = "processing"
        candles = broker.get_historical_data(job.symbol, job.timeframe, job.missing_from, job.missing_to)
        candles_today.insert_many(candles)
        last_fetch_status.update(job.symbol, job.timeframe, last_fetched_timestamp=job.missing_to)
        job.status = "done"
```

**Backtesting: always fetch up to date, resume from last fetch**
- Before a backtest runs, it doesn't assume the DB is current — it checks `last_fetch_status` for the required symbol/timeframe, and fetches only the **delta** between `last_fetched_timestamp` and the backtest's required end date (not the whole range every time).
- New candles are appended to `candles_historical`, `last_fetch_status` is updated, and only then does the backtest engine read the full required range from DB to run.
```python
def prepare_backtest_data(symbol, timeframe, start_date, end_date):
    last_ts = last_fetch_status.get(symbol, timeframe)
    fetch_from = max(last_ts, start_date) if last_ts else start_date

    if fetch_from < end_date:
        new_candles = broker.get_historical_data(symbol, timeframe, fetch_from, end_date)
        candles_historical.insert_many(new_candles)
        last_fetch_status.update(symbol, timeframe, last_fetched_timestamp=end_date)

    return candles_historical.get_range(symbol, timeframe, start_date, end_date)
```
- This means repeated backtests on the same symbol/timeframe only ever fetch the newly-elapsed period since the last run — never the full history again.

### 4.5 Condition Builder & Shared Expression Engine

A **Chartink-style visual condition builder** — this is the single, reusable way conditions get defined across the whole system, so the same condition a user builds for a scan can be reused, unmodified, as an event trigger or as a strategy's entry/exit criteria.

**Why unify this**
Previously, scanner conditions (§4.5→now embedded here), event triggers (§4.9), and strategy entry/exit criteria (§4.2, §4.8) risked becoming separate, duplicated implementations. Instead, they all reference the **same underlying condition definition and the same evaluator** — build a condition once, use it anywhere.

**Condition definition (stored, reusable)**
- `conditions` (DB table) — id, name, expression (JSON expression tree), created_by, is_reusable, created_at.
- Expression tree — nested AND/OR groups of comparisons, e.g. "RSI(14) > 45 AND Close crosses above VWAP":
  ```json
  {
    "type": "group", "logic": "AND",
    "children": [
      {"type": "comparison", "left": {"indicator": "RSI", "params": {"period": 14}}, "operator": ">", "right": {"value": 45}},
      {"type": "comparison", "left": {"indicator": "CLOSE"}, "operator": "CROSS_ABOVE", "right": {"indicator": "VWAP"}}
    ]
  }
  ```
- Operands can be an indicator (RSI, MACD/MACD_SIGNAL/MACD_HIST, VWAP, BB_UPPER/BB_LOWER/BB_WIDTH, OPEN/HIGH/LOW/CLOSE/VOLUME, or a prior activity flag), a literal value, or another indicator.
- Operators: `>`, `<`, `>=`, `<=`, `==`, `CROSS_ABOVE`, `CROSS_BELOW`, `WITHIN_PERCENT_OF`, etc.
- Groups can nest arbitrarily (AND/OR combinations), matching how Chartink-style scans are composed.

**Shared evaluator (`condition_evaluator.py`)**
```python
def evaluate(expression, symbol, timeframe, walkthrough_data):
    if expression.type == "group":
        results = [evaluate(child, symbol, timeframe, walkthrough_data) for child in expression.children]
        return all(results) if expression.logic == "AND" else any(results)
    else:  # comparison
        left_val = resolve_operand(expression.left, symbol, timeframe, walkthrough_data)
        right_val = resolve_operand(expression.right, symbol, timeframe, walkthrough_data)
        return apply_operator(expression.operator, left_val, right_val)
```
- Pulls values from the walkthrough engine (§4.4/§4.9) — RSI, MACD, VWAP, BB series are already being computed there, so evaluation is just a lookup, not a recompute.
- One evaluator, reused everywhere a condition needs checking.

**Where the same condition gets reused**
- **Scanner** (§4.6 below) — a condition scanner is just a saved `conditions` row; the scanner engine loops the watchlist and evaluates it per symbol.
- **Event registration** (§4.9) — a user builds a condition in the UI ("notify me when this is true") instead of only picking from fixed activity types; when true on a new candle, it fires an event through the same registry/notification pipeline.
- **Strategy entry criteria** (§4.2) — a strategy can define entry as a condition (no-code) instead of/alongside custom Python `on_candle` logic — same evaluator checks it on every new candle.
- **Strategy exit criteria** (§4.8 Monitoring Engine) — the `custom_indicator` exit type is literally a saved condition evaluated by the monitoring loop — no separate implementation needed.

**Frontend: Condition Builder UI (Chartink-style)**
- Visual rows: `[Indicator ▾ + params] [Operator ▾] [Value or Indicator ▾]`, grouped with AND/OR, nested groups supported, live preview ("matches 4 stocks in Nifty50 right now").
- Save as a named, reusable condition — then attach it from a dropdown wherever a condition is needed: Scanner setup, Event registration, Strategy entry criteria, Strategy exit criteria. Build once, use in all four places.
### 4.6 Scanner Engine

Runs independently of (and can feed into) strategies — scans a DB-driven watchlist against configurable technical criteria, built via the shared Condition Builder (§4.5), and returns ranked results.

**Components**

- `watchlists` (DB table) — id, name, symbols (list), created_by, active. A user can maintain multiple named watchlists (e.g. "Nifty50", "My Swing List").
- Scanner conditions are just saved `conditions` rows (§4.5) — a **condition scanner** filters (e.g. `RSI > 45`), evaluated `true`/`false` per symbol via `condition_evaluator.py`.
- A **ranking score** is a simple indicator expression evaluated per symbol (e.g. `RSI` value, MACD histogram, `LTP − VWAP`) — used purely for sort order, separate from the pass/fail condition filters.
- Filter conditions and ranking expressions are composable: apply one or more filter conditions first, then sort surviving symbols by a ranking expression.
- `scanner_engine.py` — the orchestration function:
  1. Loads the target watchlist from DB (`get_watchlist(watchlist_id)`)
  2. Fetches latest walkthrough data (LTP, OHLC, indicators — §4.4/§4.5) for each symbol
  3. Iterates the watchlist, evaluates the selected filter condition(s) per symbol via the shared evaluator
  4. Filters out symbols failing any condition
  5. Sorts remaining symbols by the ranking expression, **descending**
  6. Returns either the **full sorted list** or **top N**, per caller's request

**Example call pattern**
```python
results = scanner_engine.run(
    watchlist_id=1,
    filter_condition_id=42,        # a saved "RSI > 45" condition
    rank_by="RSI",                  # ranking expression
    top_n=10                        # or None for all
)
# → [{"symbol": "RELIANCE", "score": 68.2, ...}, {"symbol": "TCS", "score": 61.4, ...}, ...]
```

**Usage modes**
- **Manual/on-demand** — triggered from the frontend (e.g. "Scan Now" button on a watchlist page), results shown in a sortable table.
- **Scheduled** — run periodically (e.g. every N minutes during market hours) via the scheduler, results cached for the frontend to poll/display.
- **Strategy-integrated** — a strategy can call the scanner engine internally as part of its `on_tick`/`on_candle` logic to dynamically pick its trading universe (e.g. "trade the top 5 RSI momentum stocks from my watchlist right now").

This keeps scanning logic decoupled and reusable — the same scanner conditions work whether triggered manually, on a schedule, or from inside a strategy, and are built once in the same Condition Builder UI used for events and strategy criteria.

### 4.7 Order Sizing, Risk & Multi-Order Management

Strategies don't just decide *when* to enter/exit — they need configurable rules for *how much*, *how many at once*, and *when to stop for the day*. This sits as a config layer between the strategy's signal logic and the execution engine (paper/live/backtest all use it identically).

**Order sizing (per strategy config)**
- Base unit = `1` → one lot/share as defined for that symbol.
- Multiplier notation = `1x`, `2x` → margin multiplier applied to the base unit/quantity for that order.
- **Sequential order sizing** — when a strategy pyramids or averages into a position, each entry in the sequence can have its own multiplier, e.g.:
  ```json
  "order_sequence": [
    {"order_no": 1, "size": "1x"},
    {"order_no": 2, "size": "1x"},
    {"order_no": 3, "size": "2x"}
  ]
  ```
  The engine tracks which sequence number it's placing next for a given symbol/strategy and applies the configured size.

**Parallel order limits (per strategy config)**
- `max_parallel_orders_per_stock` — cap on simultaneously open positions for one symbol under this strategy.
- `max_parallel_orders_per_strategy` — cap across all symbols for this strategy.
- New entries are blocked once either cap is hit, until an existing position closes.

**Exit / circuit-breaker rules (per strategy config) — scoped**

Rules can apply at two different scopes, and both are usually configured together:

- **`strategy` scope** — looks across *all stocks* in the strategy's watchlist. e.g. "halt the whole strategy for the day if first-orders across 3 different stocks have gone into loss."
- **`stock` scope** — looks only *within one stock's* order group. e.g. "stop trading this specific stock if its orders hit a 3:1 loss:win ratio."

```json
"exit_rules": [
  {"scope": "strategy", "type": "first_order_loss_count", "threshold": 3, "action": "halt_strategy"},
  {"scope": "stock",    "type": "loss_win_ratio",        "threshold": "3:1", "action": "halt_stock"},
  {"scope": "strategy", "type": "max_daily_loss_amount",  "threshold": 10000, "action": "square_off_all"}
]
```

- `exit_after_loss_count` / `exit_after_profit_count` — stop taking new trades (or square off) after N losing/winning trades in the day.
- `max_daily_loss_amount` / `max_daily_profit_amount` — amount-based equivalents.
- Crucially: these thresholds are evaluated against **aggregated day P&L** at the relevant scope (stock or strategy) — not a single order in isolation — see multi-order aggregation below.

**Dynamic win/loss allowance ladder**

Instead of a flat loss limit, the allowed number of losses can scale with how many wins have been banked — e.g. "after 4 wins, 2 further losses are tolerated before halting; after 2 wins, only 1 loss is tolerated."

```json
"dynamic_loss_allowance": [
  {"after_wins": 4, "allowed_losses": 2},
  {"after_wins": 2, "allowed_losses": 1},
  {"after_wins": 0, "allowed_losses": 1}
]
```
Evaluated by looking up the highest `after_wins` threshold the current win-count qualifies for, and halting (per configured scope) once realized losses since the last reset exceed the corresponding `allowed_losses`. This ladder can be defined at strategy scope, stock scope, or both independently.

**Adaptive SL/Target**
- Rules like "after N consecutive winning trades, widen SL by X% and/or increase Target by Y%" (and the inverse — tighten after losses, if configured).
- Implemented as a simple state machine per strategy: tracks a rolling win/loss streak, and on each closed trade, checks configured adaptive rules to adjust SL/Target for the *next* order(s).

**Multi-order P&L aggregation (the key nuance)**
- Because a strategy/stock can have several parallel open orders at once (from the sequencing above), "am I at a loss?" must be evaluated on the **combined P&L of all open + closed orders for that stock/strategy for the day** — not per individual order.
- Every order carries an `order_group_id` (groups all sequential entries for the same symbol+strategy+day together) so the aggregator can roll them up.
- `pnl_aggregator.py` computes, on every fill/close event:
  - Net day P&L per `(strategy, symbol)` — sums all trades in that group
  - Net day P&L per `strategy` — sums across all its symbol groups
  - Feeds both back to the exit-rule check *before* any new order is placed, and after every close.

**Flow (pseudocode)**

```python
def on_order_close(strategy, symbol, trade):
    ledger.post_trade_pnl(strategy, symbol, trade)          # ledger entry, updates balances

    stock_day_pnl = pnl_aggregator.get_day_pnl(strategy.id, symbol)      # sums order_group_id
    strategy_day_pnl = pnl_aggregator.get_day_pnl(strategy.id)           # sums across all symbols

    streak = streak_tracker.update(strategy.id, symbol, trade.result)    # win/loss/streak count

    if adaptive_rules.applies(strategy, streak):
        adaptive_rules.adjust_sl_target(strategy, symbol)                # widen/tighten next order's SL/Target

    if exit_rules.breached(strategy, stock_day_pnl, strategy_day_pnl, streak):
        engine.square_off_and_halt(strategy, symbol_or_all="all")        # stop new entries for the day


def on_signal(strategy, symbol, signal):
    open_count_stock = position_tracker.open_count(strategy.id, symbol)
    open_count_strategy = position_tracker.open_count(strategy.id)

    if open_count_stock >= strategy.max_parallel_orders_per_stock:
        return   # blocked
    if open_count_strategy >= strategy.max_parallel_orders_per_strategy:
        return   # blocked

    order_no = position_tracker.next_sequence_no(strategy.id, symbol)
    size = strategy.order_sequence[order_no].size                        # e.g. "2x"
    qty = compute_qty(symbol, size)

    order = engine.place_order(strategy, symbol, signal, qty, order_group_id=position_tracker.group_id(strategy.id, symbol))
```

**Order types**

Every order in the system is one of three types, tracked via `order_type` on the `orders` record:

1. **Normal order** — a plain market/limit entry order with no SL/Target attached at the broker level. Closing it later requires the system to explicitly place a counter order.
2. **Super order** — the broker's bracket/cover order type, with SL and Target attached at the broker level at entry time. The broker manages the exit automatically when either level is hit (this is also what the Monitoring Engine's broker-failsafe check in §4.8 is safeguarding — for the rare case where a spike/gap causes the broker's own SL/TG to misfire).
3. **Counter order** — a system-generated order that closes an existing open position: same symbol, opposite side, matching quantity. Created whenever a position needs to close and there's no broker-managed super order already handling it — i.e. closing a normal order, or a monitoring-engine-triggered exit (time wind-up, custom indicator condition, manual-close-outside-broker-detected, or failsafe). A counter order always carries `parent_order_id` pointing back to the entry order it closes, so the pairing is traceable.

**Closure flow — SL/Target monitoring → counter order → P&L → ledger**

On every tick/candle, in addition to the broker-failsafe check (§4.8), the system checks live LTP against each open position's SL/Target directly (covers both normal orders, which have no broker-side SL/TG at all, and acts as the primary trigger path rather than relying solely on the broker). When a level is hit — or when any other exit criteria fires (strategy signal, monitoring rule, manual override) — the same closure sequence runs:

```python
def close_position(order, exit_price, reason):
    if order.order_type == "normal":
        counter_order = engine.place_order(
            symbol=order.symbol,
            side=opposite(order.side),
            qty=order.qty,
            order_type="counter",
            parent_order_id=order.id
        )
        fill = wait_for_fill(counter_order)
        exit_price = fill.price
    # super orders: broker already closed it — exit_price comes from the broker's fill confirmation

    pnl = compute_pnl(order.entry_price, exit_price, order.qty, order.side, charges=brokerage_model.compute(order))

    orders.update(order.id, status="closed", exit_price=exit_price, pnl=pnl, closed_at=now(), close_reason=reason)
    positions.update(order.position_id, status="closed")

    ledger.post_trade_pnl(order.strategy_id, order.symbol, pnl)     # updates strategy_accounts.current_balance
    ledger.post_trade_pnl_rollup(order.broker_account_id, pnl)      # updates broker_accounts.current_balance

    pnl_aggregator.refresh(order.strategy_id, order.symbol)         # feeds exit-rule checks (this section, above)
    streak_tracker.update(order.strategy_id, order.symbol, "win" if pnl > 0 else "loss")


def monitor_sl_target(open_positions):
    for position in open_positions:
        ltp = market_feed.get_ltp(position.symbol)
        if ltp <= position.sl:
            close_position(position.order, ltp, reason="sl_hit")
        elif ltp >= position.target:
            close_position(position.order, ltp, reason="target_hit")
```

- Every closure — regardless of what triggered it — always ends in the same three updates: **order record closed with P&L**, **position closed**, **ledger entries posted at both strategy and account level**. This keeps balances, P&L aggregation, and audit history consistent no matter which path (SL/TG hit, strategy exit signal, monitoring rule, or manual) caused the close.
- `close_reason` is stored on every closed order (`sl_hit`, `target_hit`, `strategy_signal`, `time_wind_up`, `custom_indicator`, `failsafe_missed_sl_tg`, `manual`) — gives full traceability into why every position was closed, feeding both the Order/Trade log UI and the Monitoring/Audit view (§4.11).

**Data model additions**
- `orders` gets: `order_group_id`, `sequence_no`, `size_multiplier`
- `orders` gets: `order_type` (`normal` | `super` | `counter`), `parent_order_id` (set on counter orders, links to the entry order being closed), `close_reason`
- `strategy_risk_config` — strategy_id, order_sequence (JSON), max_parallel_orders_per_stock, max_parallel_orders_per_strategy, exit_after_loss_count, exit_after_profit_count, max_daily_loss_amount, max_daily_profit_amount, adaptive_sl_target_rules (JSON)
- `streak_tracker` (or computed on the fly from `trades`) — running win/loss streak per strategy/symbol/day


### 4.8 Monitoring Engine (Exit Criteria Beyond SL/Target)

A continuously-running background service, independent of individual strategy tick logic, that watches every open position against a broader set of exit conditions — and acts as a **safety net** for cases where SL/Target orders placed at the broker fail to trigger cleanly (e.g. a sudden price spike/gap that jumps past the SL/TG level without the broker's order filling at the expected price).

**Why separate from the strategy's own code**

A strategy's `on_tick`/`on_candle` only runs when the strategy chooses to check. Exit conditions like "square off everything at 2pm" or "exit if Bollinger Bands have narrowed so the target is unlikely to hit" need to be checked **on every cycle, for every open position, regardless of what the strategy logic is doing**. So exit criteria are declarative and strategy-independent — the monitoring engine evaluates them, not the strategy object.

**Exit criteria types (declarative, per strategy or per order)**

```json
"exit_criteria": [
  {"type": "stop_loss", "value": "..."},
  {"type": "target", "value": "..."},
  {"type": "time_based", "exit_time": "14:00", "action": "square_off"},
  {"type": "custom_indicator", "condition": "bb_width < threshold", "action": "square_off"},
  {"type": "broker_failsafe", "tolerance_pct": 0.3},
  {"type": "manual_close_sync"}
]
```

- **Time-based wind-up** — force square-off at a configured time (e.g. 2:00 PM), regardless of P&L.
- **Custom indicator exit** — the `custom_indicator` exit type is just a saved condition from the shared Condition Builder (§4.5) — e.g. a "BB width < threshold" condition, implying the target is now unlikely to be hit — evaluated by the same `condition_evaluator.py` used everywhere else, just triggered here as an exit instead of an entry/scan filter.
- **Manual-close sync** — if the user manually closes a position directly on the broker terminal/app (outside this system), the monitoring engine detects the mismatch on its next reconciliation pass and updates internal state (order status, position, ledger) to match — instead of the system thinking a position is still open.
- **Broker failsafe (the "missed SL/TG" case)** — the core safety net: on every cycle, the monitoring engine independently checks live LTP against each open position's SL/Target. If price has already crossed the level (by more than a small tolerance, to account for normal fill slippage) and the broker's bracket/cover ("super") order hasn't closed the position, the engine force-places an exit order itself. This catches spike/gap scenarios where the broker's native SL/TG order missed its trigger.

**Reconciliation loop (pseudocode)**

```python
def monitoring_loop(poll_interval_sec=5):
    while market_open:
        open_positions = position_tracker.get_all_open()          # across all strategies/accounts
        broker_positions = {acct: broker.get_positions(acct) for acct in active_accounts}

        reconcile(open_positions, broker_positions)                # detect manual closes, sync ledger + status

        for position in position_tracker.get_all_open():           # re-fetch after reconciliation
            for rule in position.strategy.exit_criteria:
                if rule.type == "time_based" and now() >= rule.exit_time:
                    engine.square_off(position, reason="time_wind_up")

                elif rule.type == "custom_indicator":
                    if scanner_engine.evaluate_condition(rule.condition, position.symbol):
                        engine.square_off(position, reason=rule.type)

                elif rule.type == "broker_failsafe":
                    ltp = market_feed.get_ltp(position.symbol)
                    if breached_beyond_tolerance(ltp, position.sl, position.target, rule.tolerance_pct):
                        if not broker.order_already_closed(position):
                            engine.force_exit(position, reason="failsafe_missed_sl_tg")

        sleep(poll_interval_sec)
```

- Runs as its own thread/process, polling on a short interval (e.g. every 5s) during market hours — separate from the strategy runner threads, so it keeps working as a safety net even if a specific strategy's own logic is slow, stuck, or has a bug.
- Every action it takes (square-off, force-exit, manual-close sync) writes a ledger entry and an audit log entry with the triggering reason, so it's always clear *why* a position was closed.

**Data model additions**
- `strategy_exit_criteria` — strategy_id, criteria (JSON list as above)
- `monitoring_actions_log` — id, position_id, action, reason, triggered_at (audit trail for every non-strategy-initiated close)

### 4.9 Walkthrough, Activity Detection & Event Notification

A layer that turns raw candle/indicator series into **detected activities** (patterns, crossovers, formations), which in turn **fire events** that any user or strategy can subscribe to, delivered via **notifications** (Telegram first).

**Walkthrough engine (`walkthrough_engine.py`)**
- A "walkthrough" is a synchronized, point-by-point series derived from candles — e.g. price walkthrough (OHLC stream), RSI walkthrough (RSI value per candle), MACD walkthrough (line/signal/histogram per candle), BB walkthrough (upper/mid/lower band per candle), VWAP walkthrough.
- Runs on any persisted or on-demand timeframe (1/3/5-min now; others pluggable later the same way higher timeframes are already generated in §4.4).
- **Live mode** — on every `candle_closed` event (from §4.4), incrementally computes the next point for each active walkthrough series (append-only; avoids recomputing the full indicator history each time where the indicator supports incremental update, e.g. EMA-based ones).
- **Historical/backtest mode** — bulk-computes a walkthrough over a date range for replay.
- Adding a new indicator walkthrough = adding one calculator to a registry, same extensible pattern as scanners (§4.5) — no changes needed elsewhere.

**Activity detectors (`activity_detectors/`)**
- Each detector consumes one or more walkthrough series and, on every new point, checks a defined rule. A match emits an **activity** record: `{activity_type, symbol, timeframe, timestamp, details, strength_score}`.
- Built-in categories:
  - **Line crossings** — MA crossover, price crossing VWAP, RSI crossing 30/70, MACD line crossing signal line.
  - **Volatility pattern** — BB widening/narrowing (current band width vs. its rolling average).
  - **Candlestick formations** — hammer, three white soldiers, doji, engulfing, etc. — rule-based on OHLC shape ratios (body size vs. wick size vs. range).
  - **Indicator reversal** — e.g. RSI bullish/bearish divergence (RSI makes a higher low while price makes a lower low, or vice versa).
  - **Institutional entry (heuristic, not certain)** — true institutional order flow isn't directly observable from public tick/volume data, so this is a best-effort composite proxy, tunable per your preference:
    - Volume Z-score: current candle's volume is more than *k* standard deviations above the rolling average volume for that symbol/timeframe.
    - Strong directional close: candle closes in the top (or bottom) ~25% of its own high-low range, indicating conviction rather than indecision.
    - Follow-through confirmation: the next 1–2 candles continue in the same direction rather than reversing (filters out single-candle noise/spikes).
    - Optional secondary confirmation where available: rising open interest (F&O) or high delivery percentage (cash market) strengthens the signal further.
    - This is flagged explicitly as a **heuristic signal**, not a factual claim of institutional activity — the strength_score reflects how many of these conditions were met.
- New activity types are added as new detector modules — same registry pattern used for scanners and walkthrough calculators.

**Event system**
- Every detected activity automatically raises a corresponding **event** — activity detection *is* the event source, not a separate step.
- `event_registry` (DB table) — id, user_id, event_type (specific, e.g. `hammer_formed`, `rsi_bullish_reversal`, `institutional_entry_heuristic` — or a wildcard `any_activity`), scope (JSON filter: symbol / watchlist / strategy / timeframe), channel_preference, active, created_at.
- Registration can be broad ("notify me on any activity for this watchlist") or narrow ("only hammer formations on RELIANCE, 5-min").
- `event_dispatcher.py` — on each new activity, matches it against all active registrations (event_type + scope match) and queues a notification per match.

**Notification delivery**
- `notifier_base.py` — abstract interface: `send(user, message, channel)` — same broker-style abstraction pattern used elsewhere, so adding a channel later doesn't touch existing code.
- `telegram_notifier.py` — first implementation (Telegram Bot API); user links their Telegram chat ID once via the frontend/bot handshake.
- Future channels, same interface: `email_notifier.py`, `sms_notifier.py`, `whatsapp_notifier.py` (e.g. via a gateway like Twilio) — added incrementally, no redesign needed.
- `notification_queue` (DB table) — id, activity_id, user_id, channel, status (pending/sent/failed), sent_at, error_message — decouples detection from delivery, and supports retry-on-failure, similar pattern to the candle gap queue in §4.4.

**Flow (pseudocode)**
```python
def on_candle_closed(symbol, timeframe, candle):
    walkthrough_engine.update(symbol, timeframe, candle)          # extend price/RSI/MACD/BB/VWAP series by one point

    for detector in activity_detectors.registry:
        activity = detector.check(symbol, timeframe, walkthrough_engine.latest(symbol, timeframe))
        if activity:
            activities.save(activity)
            for reg in event_registry.match(activity.activity_type, activity.symbol, activity.timeframe):
                notification_queue.enqueue(activity.id, reg.user_id, reg.channel_preference)

def notification_worker():
    while True:
        job = notification_queue.get_next_pending()
        if not job:
            sleep(2); continue
        notifier = get_notifier(job.channel)                       # telegram_notifier, later email/sms/whatsapp
        try:
            notifier.send(job.user_id, format_message(job.activity_id), job.channel)
            job.status = "sent"
        except Exception as e:
            job.status = "failed"; job.error_message = str(e)
```

**Data model additions**
- `activities` — id, symbol, timeframe, activity_type, timestamp, details (JSON), strength_score
- `event_registry` — id, user_id, event_type, scope (JSON), channel_preference, active, created_at
- `notification_queue` — id, activity_id, user_id, channel, status, sent_at, error_message
- `user_notification_channels` — user_id, channel, channel_identifier (e.g. Telegram chat_id, email address, phone number), verified

### 4.10 Backend API (Flask)
- REST endpoints: strategy CRUD, backtest trigger, order/trade history, portfolio/P&L, broker connection status.
- WebSocket: live price ticks, live P&L, order status updates to frontend.

### 4.11 Frontend (React)
- **Strategy Editor** — write/upload strategy code, set parameters, save to DB, activate/deactivate.
- **Backtest Runner** — select strategy, date range, capital, run backtest, view equity curve & metrics, plus **day-wise / monthly / overall P&L** breakdown views.
- **Parameter Optimization Runner** — define parameter ranges (min/max/step) for a strategy, choose objective metric, run all combinations; results table sortable/filterable by any metric, best combination highlighted, drill into any single combination for its full P&L breakdown.
- **Stock Backtest (Clue Discovery) Runner** — pick symbol, timeframe/mode (day trading 1/3/5-min or long-term), date range, target (an activity type or a custom condition from the Condition Builder), forward window; view results as a trigger-occurrence table + summary stats (win rate, avg/median return, best/worst case) with trigger points overlaid on the price chart.
- **Live/Paper Dashboard** — real-time positions, P&L, order book, trade log.
- **Market Watch** — live quotes/charts (via broker feed); add/remove stocks or indexes to the live WebSocket subscription directly from here (updates `subscribed_symbols` and the live feed connection immediately, no restart).
- **Scanner** — pick watchlist + a saved condition (built via the Condition Builder), run scan, view ranked results (full list / top N), optionally save as a new watchlist.
- **Condition Builder** (Chartink-style) — build reusable conditions visually (indicator/operator/value rows, AND/OR grouping, live match preview); the same saved condition can then be attached to a Scanner, an Event Registration, or a Strategy's entry/exit criteria.
- **Data Management (CRUD UI)** — since strategies, orders, watchlists, ledger entries, and risk-config are frequently viewed/edited, these get dedicated table-based UI screens (sortable/filterable grids + edit forms) rather than needing direct DB access:
  - Strategies (list, edit params/risk-config/exit-criteria, activate/deactivate)
  - Orders & trades (filter by strategy/stock/date/status)
  - Watchlists (add/remove symbols, create/clone lists)
  - Ledger (deposits/withdrawals, per-account/per-strategy P&L history)
  - Built as a generic reusable data-grid component driven by table schema, so adding a new manageable entity later doesn't need a bespoke screen each time.
- **Monitoring/Audit view** — live feed of monitoring-engine actions (time wind-ups, failsafe force-exits, manual-close syncs) with reasons, for transparency into non-strategy-initiated closes.
- **Activity Feed** — live stream of detected activities (crossovers, candle formations, BB widening, institutional-entry heuristic hits, etc.) across watched symbols, filterable by symbol/type.
- **Event Registration** — subscribe to specific activity types OR a custom condition built in the Condition Builder (broad or scoped to a symbol/watchlist/strategy), and choose notification channel(s).
- **Settings → Notification Channels** — add, change, or remove notification accounts per channel. Telegram first (add/verify a bot + chat ID, change to a different bot/chat, remove it); the same screen extends to email, SMS, and WhatsApp as those channels are added in later phases — same add/change/remove pattern, one new channel type at a time, no redesign needed.
- **Broker Connections** — add/manage API keys per broker per account (Dhan, Zerodha, Upstox), test connection status, see which broker powers which account's data/execution.

---

## 5. Data Model (High-Level)

- `strategies` — id, name, file_path, parameters (JSON), mode, status, created_at, updated_at *(code lives in the file, not the DB)*
- `orders` — id, strategy_id, broker_order_id, symbol, side, qty, price, status, mode (backtest/paper/live), timestamp
- `trades` — id, order_id, fill_price, fill_qty, timestamp
- `positions` — id, strategy_id, symbol, qty, avg_price, mode
- `equity_curve` — id, strategy_id, timestamp, equity, pnl, mode
- `backtest_results` — id, strategy_id, params, metrics (Sharpe, max drawdown, CAGR, win rate), date_range
- `historical_data` — symbol, timeframe, OHLC candles (cache)
- `broker_accounts` — broker name, credentials (encrypted), status
- `watchlists` — id, name, symbols (list), created_by, active
- `scan_results` — id, watchlist_id, scanner_name, run_at, results (JSON: ranked symbol/score list) — cache of scheduled/manual scan runs
- `subscribed_symbols` — id, symbol, exchange, segment, added_by, active, added_at (live WebSocket subscription list, editable from frontend)
- `candles_today` — symbol, timeframe (1/3/5-min), timestamp, open, high, low, close, volume — current trading day only, small & fast, cleared after EOD archival
- `candles_historical` — same columns as above, multi-day archive (incl. daily rollups), populated by the EOD archiver and backtest data prep
- `last_fetch_status` — symbol, timeframe, last_fetched_timestamp — fetch watermark, avoids re-fetching already-fetched ranges
- `candle_gap_queue` — id, symbol, timeframe, missing_from, missing_to, status (pending/processing/done/failed), created_at, processed_at
- `conditions` — id, name, expression (JSON expression tree), created_by, is_reusable, created_at — the single shared source for scanner filters, event triggers, and strategy entry/exit criteria

### 5.1 Ledger Management (User / Account / Strategy)

Three-level hierarchy: **User → Broker Account(s) → Strategy(ies)**. A user can hold multiple accounts across multiple brokers; each account can run multiple strategies; each level maintains its own ledger.

**Entities**

- `users` — id, name, email, status
- `broker_accounts` *(extended)* — id, user_id, broker_name, account_id (broker's client id), opening_balance, current_balance, status, created_at
- `strategy_accounts` — id, strategy_id, broker_account_id, opening_balance, current_balance, allocated_capital, status, created_at
  *(a strategy's balance is a sub-ledger within a broker account — lets you run multiple strategies on one account with separate capital allocation and P&L tracking)*
- `ledger_entries` — id, entity_type (`account` | `strategy`), entity_id, entry_type (`deposit` | `withdrawal` | `trade_pnl` | `charges` | `adjustment`), amount, balance_after, reference_id (order/trade id if applicable), remarks, timestamp

**Balance mechanics**

- **Opening balance** — set once when account/strategy is created (or capital is allocated).
- **Current balance** — running balance, updated on every ledger entry (deposit, withdrawal, trade P&L, charges).
- **Deposits/Withdrawals** — manual ledger entries at account level (adding/removing funds from broker account) or strategy level (reallocating capital between strategies within an account).
- **Trade P&L** — on every trade fill/close, a `trade_pnl` ledger entry is posted at both the strategy level (which strategy generated it) and rolled up to the account level (account's overall balance), keeping both in sync automatically.
- **Charges** — brokerage, taxes, fees posted as ledger entries, reducing balance at the relevant level.

**Update flow on trade execution**

```
Trade fills → trade_pnl computed
    ├── ledger_entries (entity_type=strategy) → strategy_accounts.current_balance updated
    └── ledger_entries (entity_type=account)   → broker_accounts.current_balance updated
```

This gives full traceability: every balance change is backed by an immutable ledger entry, so account/strategy balances can always be reconstructed and audited from the ledger history — not just read off a mutable balance field.

**Reporting needs this enables**

- Per-account P&L, per-strategy P&L, and roll-up across all accounts/strategies for a user
- Capital allocation view: how much of an account's capital is deployed to which strategies
- Deposit/withdrawal history separate from trading P&L
- Point-in-time balance reconstruction (audit trail)

---

## 6. Deployment View

```
Azure Linux VM (2 vCPU / 16 GB RAM)
├── Nginx           → serves React build, reverse-proxies /api & /ws → Flask
├── Flask backend    → gunicorn/uvicorn workers, managed by systemd
├── SQL Server Express → local instance, systemd-managed
├── Strategy runner threads → spawned/managed by backend process
├── strategies/       → strategy .py files on disk (git-trackable)
└── Scheduler        → backtest jobs queued/run during off-peak (office) hours
```

- **Process management:** systemd units for backend, Nginx, SQL Server.
- **Secrets:** Dhan API keys / DB credentials stored in environment variables or a `.env` file with restricted permissions (not committed to source).
- **Logging:** structured logs per strategy/mode, rotated via `logrotate`.

---

## 7. Non-Functional Considerations

- **Isolation:** each strategy sandboxed with CPU/time limits so one bad strategy can't crash the system or others.
- **Resource contention:** backtests scheduled to run outside live trading hours to avoid competing with live/paper engines for CPU.
- **Extensibility:** new brokers = new adapter class only; new strategies = drop a new file in `strategies/` + metadata row, no deployment needed. File-based storage also enables git version control and code review for strategy changes.
- **Resilience:** broker disconnects/reconnects handled with retry + backoff in the broker layer; live engine pauses (not crashes) on feed loss.
- **Security:** encrypted credential storage, sandboxed strategy execution, HTTPS for frontend/API access.

---

## 8. Phased Roadmap

Rather than building everything at once, the system is delivered in phases — each phase is a working, usable slice on its own.

### Phase 1 (current target) — Live Data, Candles, Walkthroughs, Events & Notifications

No order placement, no strategies, no ledger yet — this phase is entirely about **getting reliable live market data flowing, turning it into structured signals, and notifying on them.** Concretely:

**Backend / Data**
1. DB models & schema for this phase: `subscribed_symbols`, `candles_today`, `candles_historical`, `last_fetch_status`, `candle_gap_queue`, `conditions`, `activities`, `event_registry`, `notification_queue`, `user_notification_channels`
2. Broker abstraction layer (§4.1) — just enough of the Dhan adapter for **market data**: WebSocket connect/subscribe/unsubscribe, historical OHLC fetch (order placement methods stubbed for later phases)
3. Live WebSocket feed (§4.4) — dynamic symbol subscribe/unsubscribe, session-aware start (9:15 default)
4. Tick → 1-min candle aggregator + generic resampler for 3/5-min (and on-demand 10/15/30/60/daily)
5. `candles_today` / `candles_historical` split + EOD archiver job
6. `last_fetch_status` watermark + `gap_scanner`/`gap_worker` for missing-candle recovery
7. Condition Builder & shared expression evaluator (§4.5) — needed here to define activity/event conditions (reused later by scanner and strategies, but built now)
8. Walkthrough engine (§4.9) — price/RSI/MACD/BB/VWAP series, live incremental mode
9. Activity detectors (§4.9) — crossings, BB widening, candlestick formations, RSI reversal, institutional-entry heuristic
10. Event registry + dispatcher (§4.9)
11. Notification delivery — `telegram_notifier.py` first, `notification_queue` with retry

**APIs**
12. REST: symbol/watchlist CRUD (add/remove subscriptions), historical candle fetch, condition CRUD, event registration CRUD, notification channel setup/verification, activity feed query
13. WebSocket (server → frontend): live tick/LTP stream, live candle-close events, live activity/event stream

**Frontend**
14. **Market Watch** — live ticking prices, add/remove stocks/indexes to the subscription
15. **Candle/chart view** — live-updating candles per timeframe (1/3/5-min, plus on-demand higher timeframes)
16. **Condition Builder UI** (Chartink-style)
17. **Activity Feed** — live stream of detected activities, filterable
18. **Event Registration** — subscribe to activity types or custom conditions, pick notification channel
19. **Settings → Notification Channels** — add/change/remove Telegram bot/account, structured to extend to email/SMS/WhatsApp later

### Phase 2 — Strategies & Backtesting
- Strategy base interface + sandboxed runner + file-based strategy storage
- Order sizing & risk-rule engine (§4.7) — defined here so backtests can honor the same sizing/parallel-order/exit rules a live strategy would use
- Strategy backtest engine (§4.3.1) — simulates trade P&L for a specific strategy, with **day-wise / monthly / overall P&L reporting**
- Parameter optimization engine (§4.3.3) — multi-combination grid-search backtesting (e.g. sweep RSI 45–65 step 5 × Target 0.5–1.8 step 0.3), results stored per combination, compared, best combination surfaced by a chosen objective metric
- Stock backtest engine (§4.3.2) — clue/pattern discovery: runs walkthrough + activity detectors over history, measures forward outcomes for a given activity/condition, day-trading and long-term modes
- Scanner engine (§4.6) — reuses the Condition Builder already built in Phase 1

### Phase 3 — Multi-Broker Setup (Dhan, Zerodha, Upstox)
- New broker adapters — `zerodha_broker.py`, `upstox_broker.py` — implemented against the same `base_broker` interface (§4.1) already proven by the Dhan adapter, so nothing else in the system changes.
- Scope for this phase, same market-data-first approach as Phase 1's Dhan integration: **historical data + WebSocket live feed** for all three brokers. Order-placement methods exist on each adapter per the interface but aren't exercised live until Phase 4/5.
- Multi-broker account config — a user's `broker_accounts` (§5.1) can now point to Dhan, Zerodha, or Upstox; the live feed and historical data layers (§4.4) become broker-selectable per subscribed symbol/account rather than Dhan-only.
- Frontend: **Broker Connections** settings screen — add/manage API keys per broker per account, test connection status, see which broker is powering which account's data feed.

### Phase 4 — Paper Trading
- Paper trading engine — simulated fills against real live prices (from whichever broker's feed is active) via the virtual portfolio (§4.3 table).
- Monitoring engine (§4.8) goes live here in simulation: time-based exits, custom-indicator exits, manual-close reconciliation, and the order types/closure flow (normal/super/counter — §4.7) all run against paper positions, so the exact same logic that will later handle live trading gets validated risk-free first.
- Data Management CRUD UI, monitoring/audit view.

### Phase 5 — Live Trading & Ledger Management
- Live trading engine enabled — real orders routed to the selected broker (Dhan/Zerodha/Upstox), after paper trading has been validated.
- Broker failsafe monitoring (§4.8) becomes meaningful here — this is the safety net for real orders where a spike/gap causes a broker's own SL/Target order to misfire.
- Ledger service (§5.1) — user/account/strategy opening & current balances, deposits/withdrawals, trade P&L posting to both strategy and account level on every real closed trade.
- Full order lifecycle (§4.7) — normal/super/counter orders, closure flow, `close_reason` tracking — now operating on real broker execution rather than simulation.

### Phase 6 — Expansion
- Additional notification channels (email, SMS, WhatsApp)
- Multi-user support, if needed beyond the initial single-user design
- Any further brokers beyond Dhan/Zerodha/Upstox, same adapter pattern


