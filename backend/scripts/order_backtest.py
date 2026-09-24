"""Position/order-management backtest — a step up from backtest_historical.py's
independent-per-pattern P&L (which had no concept of concurrency, daily risk
limits, or a trading session window). Adds real order-management rules,
parameterized per the user's own naming:

  maxVolPerCall        - cap shares per order (position sizing still starts
                          from capital_per_trade / entry price, then caps)
  MaxOrdersAtATime     - max CONCURRENT open positions, shared across every
                          timeframe/pattern (one shared order book per
                          instrument, not one per timeframe)
  ExitAtLoss/Count     - after this many stop-losses in a trading day, no
                          NEW entries for the rest of that day (positions
                          already open keep managing normally)
  StrategyTradingStart/EndTime - entries only allowed inside this IST window;
                          "sideways" is gone entirely — anything still open
                          at EndTime is squared off at that candle's close

Supports two stop/target sourcing modes per run: "fixed_pct" (flat % off
entry, same for every pattern) or "atr_neckline" (reuses
prediction_tracker.py's own ATR/measured-move formulas — "your own ratios
calculated through neckline atr etc.").

OrderBook (below) is deliberately decision-logic-only, with zero dependency
on ActivityEngine — it only ever sees already-computed (pattern, direction,
entry, stop, target) tuples. simulate() is the thin orchestration loop that
wires ActivityEngine's detections into it. This split is what makes the
concurrency/daily-halt/squareoff rules unit-testable without a real replay.

Run manually:

    .venv/Scripts/python.exe backend/scripts/order_backtest.py \\
        SYMBOL TIMEFRAME[,TIMEFRAME...] START END [--json-prefix PREFIX]

e.g.
    .venv/Scripts/python.exe backend/scripts/order_backtest.py \\
        RELIANCE 1min,3min,5min 2026-06-16 2026-09-11 --json-prefix out
"""
from __future__ import annotations

import bisect
import json
import sys
import time as _time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from activity_engine import ActivityEngine, FORMATION_LEVEL_FUNCS, seed_pattern_definitions
from brokers.models import Candle
from db.models import Base, CandleIndicators
from db.ops import LibCandlesHistorical, LibSymbols
from db.session import build_engine, build_session_factory
from indicators import (
    INDICATOR_TREND_LOOKBACK, classify_macd, classify_rsi, classify_series_trend, classify_stochastic,
)
from prediction_tracker import (
    BEARISH_PATTERNS, BULLISH_PATTERNS, PREDICTION_SETTING_DEFAULTS,
    crossover_stop_loss, crossover_target, load_prediction_settings,
)

IST_OFFSET = timedelta(hours=5, minutes=30)
_GRAPH_FORMATIONS = set(FORMATION_LEVEL_FUNCS.keys())


def _as_utc(ts: datetime) -> datetime:
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


def ist_time(ts_utc: datetime) -> time:
    return (_as_utc(ts_utc) + IST_OFFSET).time()


def ist_date(ts_utc: datetime) -> date:
    return (_as_utc(ts_utc) + IST_OFFSET).date()


def quantity_for(
    capital_per_trade: float, entry_price: float, max_vol_per_call: int,
    margin_multiplier: float = 1.0, liquidity_cap: Optional[int] = None,
) -> int:
    """margin_multiplier: "Nx margin" sizing (e.g. 5x on the 3rd order of
    the day) — scales the capital-based quantity before the max_vol_per_call
    cap is applied, it's never allowed to bypass that cap.

    liquidity_cap: an additional, tighter ceiling derived from the entry
    candle's own real volume (see EngineConfig.liquidity_safety_divisor) —
    None (default) leaves this uncapped, unchanged from before this
    parameter existed. Applied last, on top of max_vol_per_call, never as
    a replacement for it."""
    if entry_price <= 0:
        return 0
    by_capital = int((capital_per_trade * margin_multiplier) // entry_price)
    qty = max(0, min(by_capital, max_vol_per_call))
    if liquidity_cap is not None:
        qty = min(qty, liquidity_cap)
    return qty


def fixed_pct_levels(entry: float, direction: str, sl_pct: float, target_pct: float) -> Tuple[float, float]:
    if direction == "bull":
        return entry * (1 - sl_pct), entry * (1 + target_pct)
    return entry * (1 + sl_pct), entry * (1 - target_pct)


# Itemized Indian equity intraday cost model — an alternative to the flat
# round_trip_cost_rate approximation, opt in via EngineConfig.itemized_costs
# (explicit instruction, 2026-09-17: model real brokerage/STT/exchange/SEBI/
# stamp-duty/GST rather than one blended %, after a manual double_top
# backtest showed costs eating 32%+ of gross P&L — a single flat rate hides
# exactly which charge actually drives that). See double_top_pnl_backtest.py
# for the original ad-hoc version this generalizes from.
_BROKERAGE_FLAT = 20.0
_BROKERAGE_PCT = 0.03 / 100
_STT_PCT = 0.025 / 100          # sell-side turnover only, intraday equity
_EXCHANGE_PCT = 0.00297 / 100   # NSE, both legs
_SEBI_PCT = 0.0001 / 100        # both legs
_STAMP_DUTY_PCT = 0.003 / 100   # buy-side turnover only
_GST_RATE = 0.18


def itemized_round_trip_cost(quantity: int, sell_price: float, buy_price: float) -> float:
    """sell_price/buy_price are whichever leg is actually the sale vs. the
    purchase — for a bull trade that's (exit, entry), for a bear trade
    (entry, exit), since STT and stamp duty are asymmetric by side."""
    sell_turnover = quantity * sell_price
    buy_turnover = quantity * buy_price
    total_turnover = sell_turnover + buy_turnover

    brokerage = 2 * min(_BROKERAGE_FLAT, _BROKERAGE_PCT * (total_turnover / 2))
    stt = _STT_PCT * sell_turnover
    exchange_charge = _EXCHANGE_PCT * total_turnover
    sebi_fee = _SEBI_PCT * total_turnover
    stamp_duty = _STAMP_DUTY_PCT * buy_turnover
    gst = _GST_RATE * (brokerage + exchange_charge + sebi_fee)
    return brokerage + stt + exchange_charge + sebi_fee + stamp_duty + gst


@dataclass
class SLTargetConfig:
    name: str
    mode: str  # "fixed_pct" | "atr_neckline"
    sl_pct: Optional[float] = None
    target_pct: Optional[float] = None


@dataclass
class EngineConfig:
    # This is a SHARED POOL now, not a per-trade allowance — OrderBook
    # tracks a running available_fund starting at this value, drawn down by
    # each open position's actual margin outlay and released (margin + net
    # P&L) when it closes. With MaxOrdersAtATime==1 this is indistinguishable
    # from the old "always the full amount" model (the pool is always fully
    # available again before the next entry, since the prior one already
    # closed) — the pooling only bites once >1 position can be open at
    # once, which is exactly the scenario the user pointed at ("it will
    # impact in multitrades at a time") when order_volume_multipliers
    # showed no visible effect under the old always-full-capital sizing.
    # See [[order_sequence_margin_multiplier]] for the earlier, per-trade
    # version of this; this supersedes it.
    capital_per_trade: float = 2_000_000.0
    # True (default, unchanged): sizing draws from the running available_fund
    # pool above — realized P&L compounds into the next order's size, same
    # as a real account balance. False: every order sizes off the ORIGINAL
    # capital_per_trade instead, ignoring realized P&L entirely — the
    # "fixed capital" comparison point (explicit instruction, 2026-09-17:
    # "are you accumulating profit to the capital?" — this makes that a
    # runnable comparison instead of two separate scripts). available_fund
    # itself keeps tracking the real running balance either way, for
    # reporting/drawdown purposes — this flag only changes what SIZING reads.
    compounding: bool = True
    max_vol_per_call: int = 5000
    max_orders_at_a_time: int = 1
    # Reverted to 1 (explicit instruction, 2026-09-14: "exit at loss count
    # 1") — was raised to 2 specifically so a 3rd order could occur under
    # order_volume_multipliers; that combination made losses compound (see
    # [[shared_fund_pool_order_backtest]]'s 3-concurrent/cap-20000 finding),
    # so the next round of testing goes back to halting after a single loss.
    exit_at_loss_count: int = 1
    # Opt-in daily circuit breaker: once today's cumulative REALIZED GROSS
    # LOSS (losing trades' magnitude only, NOT netted against the day's
    # winning trades -- explicit instruction, 2026-09-17: "calculate day's
    # loss and profit separately... not overall") reaches max_daily_loss_pct
    # of capital_per_trade, can_open() refuses every new entry for the rest
    # of that trading day -- already-open positions still resolve normally
    # (stop/target/squareoff), they just aren't force-closed. A day with,
    # say, 1.5% gross loss offset by 1% gross profit still trips this (net
    # -0.5% would NOT have) -- a real loss budget, not a net-P&L floor.
    # None (default) leaves this off, unchanged from before this field
    # existed.
    max_daily_loss_pct: Optional[float] = None
    # Opt-in: exit early on a MACD reversal against the position's own
    # direction, checked candle-by-candle alongside the stop/target check
    # (stop/target still takes priority) -- see resolve_against_candle's
    # own docstring for the full reasoning and exact trigger condition.
    # Needs CandleIndicators already populated for this symbol/timeframe
    # (see _load_indicators) -- silently a no-op otherwise, same "None
    # until ready" convention as every other indicator lookup in this
    # codebase.
    exit_on_macd_reversal: bool = False
    # First order of the day sized at a LITERAL fixed quantity (e.g. 1
    # share) instead of the capital/pool-based quantity_for() formula —
    # explicit instruction ("first order qty 1"), presumably to keep the
    # day's opening trade's risk/P&L trivial while verifying mechanics.
    # None (default) keeps the old behavior: the 1st order sizes exactly
    # like any other, via order_volume_multipliers[0] against the pool.
    # Only the FIRST order of the day is affected — the 2nd/3rd/etc. still
    # go through the normal multiplier-and-pool sizing below.
    first_order_quantity: Optional[int] = None
    # "Nx margin" sizing per order SEQUENCE NUMBER within the trading day
    # (1st, 2nd, 3rd, ...), not per pattern/timeframe — e.g. (1.0, 1.0, 5.0)
    # means the day's 1st and 2nd orders use normal 1x capital-based sizing
    # and the 3rd (and every order after it — "further orders will take
    # from third", explicit instruction) uses 5x, still capped by
    # max_vol_per_call. Index i in this tuple is order i+1 of the day;
    # once the day's order count exceeds len(tuple), the LAST value keeps
    # being reused rather than raising or falling back to 1x. These are
    # deliberately kept as engine-run config for now rather than Strategy
    # columns ("it's ok for this testing session, we can move these to
    # strategy when we will use strategy in backtesting" — explicit
    # instruction, same as trading_start_time etc. before it).
    order_volume_multipliers: Tuple[float, ...] = (1.0, 1.0, 5.0)
    # Opt-in "start low, size up on a win streak" sizing (explicit
    # instruction, 2026-09-17) -- a SEPARATE axis from order_volume_
    # multipliers above (that one keys off the day's order SEQUENCE
    # NUMBER; this one keys off CONSECUTIVE WINS, win/loss outcome, not
    # order count, and is NOT reset daily -- a streak carries across a day
    # boundary same as the fund pool does). Index i = the multiplier used
    # for the order placed after i consecutive wins (index 0 = right after
    # a loss, or before any trade at all -- the "low" starting size); once
    # the streak exceeds len(tuple), the LAST value keeps being reused.
    # The two multipliers combine multiplicatively in try_open(). None
    # (default) leaves this off -- always 1x, unchanged from before this
    # field existed.
    win_streak_multipliers: Optional[Tuple[float, ...]] = None
    # Two distinct windows (StrategyNewOrderStartTime/EndTime vs. the
    # separate squareoff time): a new-order deadline earlier than the
    # square-off time means a position opened right before the deadline
    # still gets to run until squareoff_time, it just can't be FOLLOWED by
    # another new entry — these were previously a single shared window,
    # split apart 2026-09-14 once the user asked for a narrower entry
    # window (10:15-14:00) distinct from a later squareoff (14:50).
    new_order_start_time: time = time(9, 15)
    new_order_end_time: time = time(14, 0)
    squareoff_time: time = time(14, 50)
    round_trip_cost_rate: float = 0.00085
    # When True, _close computes real itemized costs (itemized_round_trip_
    # cost) instead of the flat round_trip_cost_rate above — opt-in so every
    # existing Strategy/test keeps its old flat-rate behavior unchanged.
    itemized_costs: bool = False
    # A candle's own `volume` is BOTH sides of the tape (every share traded
    # counts once as a buy and once as a sell), so real one-side liquidity is
    # volume/2 — this divisor is applied to the FULL volume (e.g. 40 = /2 for
    # double-counting, /20 further safety margin), giving a per-order cap
    # this backtest actually could have filled without moving the price more
    # than a zero-slippage fill assumes. None (default) disables this cap
    # entirely — existing runs are unaffected. See EngineConfig.max_vol_per_call
    # for the older STATIC share-count cap this supplements, never replaces.
    liquidity_safety_divisor: Optional[float] = None
    # When True (and liquidity_safety_divisor is set): instead of shrinking
    # an order down to whatever one candle's liquidity allows, SPREAD the
    # fill across consecutive candles (each contributing up to its own
    # liquidity cap) until the originally-desired quantity is reached or
    # max_fill_candles elapses — same idea applied symmetrically on exit
    # (a stop/target that can't fully clear in one candle keeps clearing
    # the remainder on later candles at the same triggered price, instead
    # of closing the whole position atomically at a price it couldn't
    # actually have traded at that size). False (default) keeps the older,
    # simpler single-candle-cap behavior every existing Strategy/test run
    # already assumes — this is purely additive.
    spread_fills: bool = False
    # Opt-in pre-entry liquidity GATE — refuses to open at all (not a
    # sizing cap like liquidity_safety_divisor above, which shrinks/spreads
    # a fill; this is a go/no-go on the signal itself) when the desired
    # order quantity exceeds min_avg_volume_multiple x the mean traded
    # volume over the last min_avg_volume_lookback candles for this symbol/
    # timeframe. e.g. multiple=1.0 means "only trade when recent mean
    # volume actually exceeds the size we'd be ordering" — explicit
    # instruction, 2026-09-18: "mean volume[last 5 candles] > order size".
    # None (default) leaves this off, unchanged from before this field
    # existed. Fewer than min_avg_volume_lookback candles seen so far (very
    # start of a run) degrades gracefully to whatever history exists rather
    # than blocking outright, same spirit as every other "None until ready"
    # indicator convention in this codebase.
    min_avg_volume_multiple: Optional[float] = None
    min_avg_volume_lookback: int = 5
    # 3, not a larger number — explicit instruction, 2026-09-17: "we can
    # make it simple also, by just limiting 3 candles" — a stale multi-
    # candle-old entry attempt is more likely chasing a level that's moved
    # on than genuinely still fillable.
    max_fill_candles: int = 3
    max_exit_candles: int = 10  # force-close an unclosed exit remainder at market after this many candles
    # Optional, tighter cutoff on the entry side: abandon the unfilled
    # remainder once price has drifted more than this fraction (e.g. 0.001
    # = 0.1%) away from the ORIGINAL signal price, even if max_fill_candles
    # hasn't been reached yet — explicit instruction: "if price moves away
    # by .0* percent then stop that". None (default) disables this check,
    # leaving max_fill_candles as the only cutoff.
    max_fill_price_drift_pct: Optional[float] = None
    sl_target: SLTargetConfig = field(default_factory=lambda: SLTargetConfig("default", "atr_neckline"))
    # Only used when sl_target.mode == "atr_neckline"; None (default) falls
    # back to the global prediction_settings values, same as before this
    # became a per-Strategy override (see engine_config_from_strategy).
    atr_multiplier: Optional[float] = None
    risk_reward_ratio: Optional[float] = None
    # Scopes simulate() to trading ONLY these pattern names (found live,
    # 2026-09-15: without this, simulate() trades EVERY registered
    # BULLISH_PATTERNS/BEARISH_PATTERNS entry blended together — hammer,
    # every crossover, every structure/graph-formation shape — with no way
    # to isolate one specific setup's own P&L, which is exactly what "test
    # a specific strategy" needs). None (default) keeps the original
    # behavior: every directional pattern trades, unchanged for every
    # Strategy row that predates this field.
    pattern_filter: Optional[Tuple[str, ...]] = None


@dataclass
class _PendingEntry:
    """A signal whose desired size exceeds what its own entry candle's
    liquidity cap allows — accumulates fills across subsequent candles
    (OrderBook.continue_entries) instead of opening undersized immediately.
    weighted_price_sum / filled_quantity gives the eventual volume-weighted
    average entry price once finalized into a real _OpenPosition."""
    symbol: str
    pattern: str
    direction: str
    signal_price: float
    stop_loss: float
    target: float
    timeframe: str
    multiplier: float
    target_quantity: int
    entry_ts: datetime
    filled_quantity: int = 0
    weighted_price_sum: float = 0.0
    margin_committed: float = 0.0
    candles_waited: int = 0


@dataclass
class _OpenPosition:
    symbol: str
    pattern: str
    direction: str
    entry_price: float
    entry_ts: datetime
    stop_loss: float
    target: float
    quantity: int
    timeframe: str
    margin_used: float
    # Partial-exit state (spread_fills only) — set once a stop/target
    # triggers but the position's full quantity can't clear in one candle;
    # None/0 for every position closing normally in a single candle (the
    # common case, and the only case when spread_fills is off).
    closing_price: Optional[float] = None
    closing_reason: Optional[str] = None
    closed_quantity: int = 0
    closed_weighted_sum: float = 0.0
    closing_candles_waited: int = 0


class OrderBook:
    """Pure order-management decision logic — no ActivityEngine dependency,
    see module docstring. One instance per (instrument, config) backtest
    run; positions/day-state are shared across every timeframe feeding it."""

    def __init__(self, config: EngineConfig):
        self.config = config
        self.open_positions: List[_OpenPosition] = []
        self.closed_trades: List[dict] = []
        self._current_day: Optional[date] = None
        self._stop_losses_today = 0
        self._orders_opened_today = 0
        # Loss and profit tracked SEPARATELY, not netted -- explicit
        # instruction, 2026-09-17: a day with a 1.5% gross loss offset by a
        # 1% gross profit still trips the breaker (net -0.5% would NOT), so
        # the cap is a real loss BUDGET for the day, not a net-P&L floor.
        # Both magnitudes, always >= 0.
        self._daily_loss_sum = 0.0
        self._daily_profit_sum = 0.0
        # NOT reset in on_new_candle_day -- see EngineConfig.win_streak_
        # multipliers' own docstring on why this persists across days.
        self._consecutive_wins = 0
        # Shared fund pool — see EngineConfig.capital_per_trade's docstring.
        # Deliberately NOT reset per trading day: it's a running portfolio
        # balance across the whole backtest (grows/shrinks with realized
        # P&L), like a real brokerage account, not a daily allowance.
        self.available_fund = config.capital_per_trade
        # spread_fills only — signals still being filled across multiple
        # candles. Each one counts as an occupied order slot (can_open),
        # same as a fully-open position, so max_orders_at_a_time can't be
        # bypassed by stacking partially-filled entries.
        self._pending_entries: List[_PendingEntry] = []
        # (timeframe, ts) -> CandleIndicators row, set via load_indicators()
        # -- _close() reads this to snapshot entry_*/exit_* indicator
        # value+state onto every closed trade (see [[backtest_column_picker_report_plan]]).
        # Empty dict (not None) when unset, so _close()'s lookup is always
        # a plain .get() with no extra branch.
        self._indicators: Dict[Tuple[str, datetime], CandleIndicators] = {}
        # timeframe -> (sorted ts list, same-order row list), derived from
        # self._indicators by load_indicators() -- lets _series_trend() find
        # the INDICATOR_TREND_LOOKBACK rows immediately before (and
        # including) a given ts via bisect, same trend definition
        # PatternOutcome.entry_rsi_trend already uses (explicit instruction,
        # 2026-09-18: keep the SAME shared lookback, not a wider one-off).
        self._indicator_series: Dict[str, Tuple[List[datetime], List[CandleIndicators]]] = {}
        # (symbol, timeframe) -> a rolling window of the most recent
        # min_avg_volume_lookback candles' own volume, fed once per candle
        # via record_volume() (simulate()'s own per-candle loop, same call
        # site as on_new_candle_day) -- powers the min_avg_volume_multiple
        # entry gate in try_open(). A bounded deque, not the whole day's
        # history, since only the trailing window is ever needed.
        self._recent_volumes: Dict[Tuple[str, str], deque] = {}

    def record_volume(self, symbol: str, timeframe: str, volume: int) -> None:
        key = (symbol, timeframe)
        window = self._recent_volumes.get(key)
        if window is None:
            window = deque(maxlen=self.config.min_avg_volume_lookback)
            self._recent_volumes[key] = window
        window.append(volume)

    def _mean_recent_volume(self, symbol: str, timeframe: str) -> Optional[float]:
        window = self._recent_volumes.get((symbol, timeframe))
        if not window:
            return None
        return sum(window) / len(window)

    def load_indicators(self, indicators: Dict[Tuple[str, datetime], CandleIndicators]) -> None:
        self._indicators = indicators
        grouped: Dict[str, List[Tuple[datetime, CandleIndicators]]] = defaultdict(list)
        for (timeframe, ts), row in indicators.items():
            grouped[timeframe].append((ts, row))
        self._indicator_series = {}
        for timeframe, pairs in grouped.items():
            pairs.sort(key=lambda p: p[0])
            self._indicator_series[timeframe] = ([p[0] for p in pairs], [p[1] for p in pairs])

    def on_new_candle_day(self, ts_utc: datetime) -> None:
        day = ist_date(ts_utc)
        if day != self._current_day:
            self._current_day = day
            self._stop_losses_today = 0
            self._orders_opened_today = 0
            self._daily_loss_sum = 0.0
            self._daily_profit_sum = 0.0

    def _next_order_margin_multiplier(self) -> float:
        multipliers = self.config.order_volume_multipliers
        idx = min(self._orders_opened_today, len(multipliers) - 1)
        return multipliers[idx]

    def _win_streak_multiplier(self) -> float:
        multipliers = self.config.win_streak_multipliers
        if multipliers is None:
            return 1.0
        idx = min(self._consecutive_wins, len(multipliers) - 1)
        return multipliers[idx]

    def _series_trend(self, timeframe: str, ts: datetime, field: str) -> Optional[str]:
        """Trend of one indicator field over the INDICATOR_TREND_LOOKBACK
        rows ending at (and including) this exact (timeframe, ts) --
        mirrors pattern_outcome_analysis.py's own _series_trend, just
        indexed via bisect over load_indicators()'s sorted series instead
        of a plain list position, since OrderBook looks up by ts rather
        than iterating in order."""
        ts_list, rows = self._indicator_series.get(timeframe, ([], []))
        idx = bisect.bisect_left(ts_list, ts)
        if idx >= len(ts_list) or ts_list[idx] != ts:
            return None
        window = rows[max(0, idx - INDICATOR_TREND_LOOKBACK + 1): idx + 1]
        values = [float(getattr(r, field)) for r in window if getattr(r, field) is not None]
        return classify_series_trend(values)

    def _indicator_snapshot(self, prefix: str, timeframe: str, ts: datetime) -> dict:
        """prefix: "entry_" or "exit_" -- returns the matching set of
        BacktestTrade columns (value + classified state + classified
        trend), all None when this run has no indicator lookup loaded or
        no row exists for this exact (timeframe, ts) (e.g. the RSI/MACD/ATR
        warm-up period), same "None until ready" convention CandleIndicators
        itself uses."""
        row = self._indicators.get((timeframe, ts))
        rsi = float(row.rsi) if row and row.rsi is not None else None
        macd_line = float(row.macd_line) if row and row.macd_line is not None else None
        macd_signal = float(row.macd_signal) if row and row.macd_signal is not None else None
        stoch_k = float(row.stoch_k) if row and row.stoch_k is not None else None
        return {
            f"{prefix}rsi": rsi,
            f"{prefix}macd_line": macd_line,
            f"{prefix}macd_signal": macd_signal,
            f"{prefix}stoch_k": stoch_k,
            f"{prefix}stoch_d": float(row.stoch_d) if row and row.stoch_d is not None else None,
            f"{prefix}vwap": float(row.vwap) if row and row.vwap is not None else None,
            f"{prefix}ma21": float(row.ma21) if row and row.ma21 is not None else None,
            f"{prefix}ma50": float(row.ma50) if row and row.ma50 is not None else None,
            f"{prefix}atr": float(row.atr) if row and row.atr is not None else None,
            f"{prefix}bb_upper": float(row.bb_upper) if row and row.bb_upper is not None else None,
            f"{prefix}bb_middle": float(row.bb_middle) if row and row.bb_middle is not None else None,
            f"{prefix}bb_lower": float(row.bb_lower) if row and row.bb_lower is not None else None,
            f"{prefix}rsi_state": classify_rsi(rsi),
            f"{prefix}macd_state": classify_macd(macd_line, macd_signal),
            f"{prefix}stoch_state": classify_stochastic(stoch_k),
            f"{prefix}rsi_trend": self._series_trend(timeframe, ts, "rsi"),
            f"{prefix}macd_trend": self._series_trend(timeframe, ts, "macd_line"),
            f"{prefix}stoch_trend": self._series_trend(timeframe, ts, "stoch_k"),
        }

    def _close(self, pos: _OpenPosition, exit_price: float, exit_ts: datetime, exit_reason: str) -> None:
        signed = 1 if pos.direction == "bull" else -1
        gross = pos.quantity * signed * (exit_price - pos.entry_price)
        if self.config.itemized_costs:
            # STT/stamp duty are side-specific (sell vs. buy), not symmetric
            # — a bull trade buys at entry and sells at exit; a bear trade
            # (short) sells at entry and buys back at exit.
            sell_price, buy_price = (exit_price, pos.entry_price) if pos.direction == "bull" else (pos.entry_price, exit_price)
            expenses = itemized_round_trip_cost(pos.quantity, sell_price, buy_price)
        else:
            expenses = pos.quantity * pos.entry_price * self.config.round_trip_cost_rate
        net = gross - expenses
        # Release this position's locked margin back to the pool, plus (or
        # minus) its realized net P&L — a losing trade shrinks the pool
        # available for the NEXT entry, a winning one grows it (explicit
        # instruction: "Margin + P&L (running portfolio value)").
        self.available_fund += pos.margin_used + net
        if net > 0:
            self._daily_profit_sum += net
        else:
            self._daily_loss_sum += -net
        trade = {
            "symbol": pos.symbol, "pattern": pos.pattern, "direction": pos.direction, "timeframe": pos.timeframe,
            "entry_ts": pos.entry_ts.isoformat(), "exit_ts": exit_ts.isoformat(),
            "entry_price": pos.entry_price, "exit_price": exit_price,
            "stop_loss": pos.stop_loss, "target": pos.target,
            "quantity": pos.quantity, "margin_used": round(pos.margin_used, 2), "exit_reason": exit_reason,
            "gross_pnl": round(gross, 2), "expenses": round(expenses, 2), "net_pnl": round(net, 2),
        }
        if self._indicators:
            trade.update(self._indicator_snapshot("entry_", pos.timeframe, pos.entry_ts))
            trade.update(self._indicator_snapshot("exit_", pos.timeframe, exit_ts))
        self.closed_trades.append(trade)
        # ANY realized loss counts toward the daily halt, not just a fixed-
        # price stop hit -- otherwise an indicator_reversal exit (or any
        # future non-stop exit reason) that closes at a loss silently
        # bypasses exit_at_loss_count, letting the day keep trading exactly
        # when this discipline rule is supposed to stop it. Found live,
        # 2026-09-17: with exit_on_macd_reversal on, trade count exploded
        # (671 -> 1795 for one combo) because reversal-exit losses never
        # incremented this counter, so the day never halted.
        if net < 0:
            self._stop_losses_today += 1
        self._consecutive_wins = self._consecutive_wins + 1 if net > 0 else 0

    def resolve_against_candle(self, candle: Candle, macd_state: Optional[str] = None) -> None:
        """Checks every open position against this candle's high/low —
        target-and-stop-both-hit resolves conservatively as a stop (matches
        this project's established same-candle-ambiguity convention).

        A level counts as hit only if the candle's own range actually
        contains it (low <= level <= high), not just "price moved past it
        in the favorable/unfavorable direction" (the old `high >= target`-
        style check). That directional check could claim a fill at the
        exact target/stop price even when the candle gapped clean over it
        — e.g. target=102 but the whole candle traded 105-110 — a price
        that was never actually traded. Direction-agnostic by construction
        (range containment doesn't care which side is target vs. stop;
        that was already baked in when the levels were computed at entry),
        so no bull/bear branch is needed here anymore.

        Positions already mid-partial-exit (pos.closing_price is not None,
        spread_fills only) are skipped here — continue_closing handles
        those on this same candle instead, since their exit level and
        direction are already decided; re-running the stop/target check on
        them would be redundant and could re-trigger against a level
        they're already in the process of clearing. Positions for a
        DIFFERENT symbol are skipped too (matters once multiple symbols
        share one OrderBook, per simulate_portfolio) — Symbol B's position
        must never be checked against Symbol A's candle range.

        macd_state ("bullish"/"bearish"/None, this candle's own value from
        indicators.classify_macd): when EngineConfig.exit_on_macd_reversal
        is on and neither stop nor target fired this candle, a position
        whose OWN direction now contradicts macd_state exits at this
        candle's close, reason "indicator_reversal" — explicit instruction,
        2026-09-17: raw intrabar stop/target hits react to a single sharp
        spike wick; MACD's own smoothing means this only fires once the
        move has genuinely turned, not on one noisy candle. Checked only
        for positions that survived the stop/target check this candle
        (stop/target still takes priority when both would fire)."""
        if not self.open_positions:
            return
        high, low = float(candle.high), float(candle.low)
        still_open = []
        for pos in self.open_positions:
            if pos.symbol != candle.symbol or pos.closing_price is not None:
                still_open.append(pos)
                continue
            hit_target = low <= pos.target <= high
            hit_stop = low <= pos.stop_loss <= high

            if not (hit_target or hit_stop):
                if (self.config.exit_on_macd_reversal and macd_state is not None
                        and ((pos.direction == "bear" and macd_state == "bullish")
                             or (pos.direction == "bull" and macd_state == "bearish"))):
                    if self._start_or_finish_close(pos, float(candle.close), "indicator_reversal", candle):
                        continue
                still_open.append(pos)
                continue
            # both hit -> conservative default is stop-first (unchanged
            # convention), which is exactly what checking hit_stop first,
            # unconditionally, already gives us
            exit_price, exit_reason = (pos.stop_loss, "stop_hit") if hit_stop else (pos.target, "target_hit")

            if self._start_or_finish_close(pos, exit_price, exit_reason, candle):
                continue  # fully closed already (fit within one candle's liquidity, or spread_fills off)
            still_open.append(pos)  # partially closed — stays open, continue_closing finishes it
        self.open_positions = still_open

    def _liquidity_cap(self, candle: Candle) -> Optional[int]:
        if self.config.liquidity_safety_divisor is None:
            return None
        return int(candle.volume // self.config.liquidity_safety_divisor)

    def _start_or_finish_close(self, pos: _OpenPosition, exit_price: float, exit_reason: str, candle: Candle) -> bool:
        """Returns True if pos is now fully closed (removed from
        open_positions by the caller) — either because it fit within this
        candle's liquidity outright, or because spread_fills/liquidity
        capping is off (unchanged single-candle behavior). Returns False
        if only a partial fill happened and the position needs continue_
        closing on later candles to finish."""
        cap = self._liquidity_cap(candle) if self.config.spread_fills else None
        if cap is None or pos.quantity <= cap:
            self._close(pos, exit_price, candle.timestamp, exit_reason)
            return True

        pos.closing_price = exit_price
        pos.closing_reason = exit_reason
        pos.closed_quantity = cap
        pos.closed_weighted_sum = cap * exit_price
        pos.closing_candles_waited = 1
        return False

    def continue_closing(self, candle: Candle) -> None:
        """Tops up every partially-closing position's remaining quantity
        using this candle's own liquidity cap, at the ALREADY-triggered
        exit price (not re-evaluated against this candle's own high/low —
        once a stop/target has fired, the goal is just to get the position
        closed, same as a real trader working a large order out of a
        position). Force-closes the remainder at this candle's own market
        close once max_exit_candles elapses, rather than leaving a position
        open forever waiting for liquidity that may never come."""
        closing = [p for p in self.open_positions if p.closing_price is not None and p.symbol == candle.symbol]
        if not closing:
            return
        cap = self._liquidity_cap(candle)
        still_open = []
        for pos in self.open_positions:
            if pos.closing_price is None or pos.symbol != candle.symbol:
                still_open.append(pos)
                continue
            pos.closing_candles_waited += 1
            remaining = pos.quantity - pos.closed_quantity
            take = min(cap or 0, remaining)
            if take > 0:
                pos.closed_quantity += take
                pos.closed_weighted_sum += take * pos.closing_price
                remaining -= take

            if remaining <= 0:
                self._finish_partial_close(pos, candle.timestamp)
            elif pos.closing_candles_waited >= self.config.max_exit_candles:
                # forced out at whatever this candle's market close is for
                # the stubborn remainder — a real trader wouldn't hold
                # forever waiting for liquidity that isn't showing up
                pos.closed_quantity += remaining
                pos.closed_weighted_sum += remaining * float(candle.close)
                self._finish_partial_close(pos, candle.timestamp)
            else:
                still_open.append(pos)
        self.open_positions = still_open

    def _finish_partial_close(self, pos: _OpenPosition, exit_ts: datetime) -> None:
        avg_exit_price = pos.closed_weighted_sum / pos.closed_quantity
        self._close(pos, avg_exit_price, exit_ts, pos.closing_reason)

    def _finalize_or_drop_entry(self, pe: _PendingEntry) -> None:
        """Converts pe into a real open position at its volume-weighted
        average fill price if it filled at least something; a zero-fill
        entry (no candle since the signal has had ANY liquidity) is
        silently dropped — nothing was ever committed for it, so there's
        nothing to unwind."""
        if pe.filled_quantity <= 0:
            return
        avg_price = pe.weighted_price_sum / pe.filled_quantity
        self.open_positions.append(_OpenPosition(
            symbol=pe.symbol, pattern=pe.pattern, direction=pe.direction, entry_price=avg_price,
            entry_ts=pe.entry_ts, stop_loss=pe.stop_loss, target=pe.target,
            quantity=pe.filled_quantity, timeframe=pe.timeframe, margin_used=pe.margin_committed,
        ))

    def continue_entries(self, candle: Candle) -> None:
        """Tops up every pending entry's fill using this candle's own
        liquidity cap, at this candle's close price — mirrors continue_
        closing's shape on the entry side. Finalizes into a real open
        position once fully filled, or once max_fill_candles elapses with
        whatever partial quantity got filled (never force-fills the
        remainder at a worse price the way exits do — an entry nobody
        could actually fill is just a smaller position, not a mandatory
        one, unlike an exit that must eventually happen). Also cuts off
        early, before max_fill_candles, if max_fill_price_drift_pct is set
        and price has moved too far from the original signal price — a
        stale chase gets abandoned rather than filled at a level the
        signal no longer describes.

        Only pending entries for THIS candle's own symbol are touched
        (matters once multiple symbols share one OrderBook, per
        simulate_portfolio) — a different symbol's candle carries neither
        the right liquidity nor the right price for anyone else's pending
        entry, and must not age its candles_waited counter either."""
        if not self._pending_entries:
            return
        cap = self._liquidity_cap(candle)
        if cap is None:
            return
        still_pending = []
        fill_price = float(candle.close)
        drift_pct = self.config.max_fill_price_drift_pct
        for pe in self._pending_entries:
            if pe.symbol != candle.symbol:
                still_pending.append(pe)
                continue
            pe.candles_waited += 1
            drifted = (
                drift_pct is not None and pe.signal_price > 0
                and abs(fill_price - pe.signal_price) / pe.signal_price > drift_pct
            )
            take = 0 if drifted else min(cap, pe.target_quantity - pe.filled_quantity)
            if take > 0:
                pe.filled_quantity += take
                pe.weighted_price_sum += take * fill_price
                margin_delta = (take * fill_price) / pe.multiplier
                pe.margin_committed += margin_delta
                self.available_fund -= margin_delta

            if pe.filled_quantity >= pe.target_quantity:
                self._finalize_or_drop_entry(pe)
                self._orders_opened_today += 1
            elif drifted or pe.candles_waited >= self.config.max_fill_candles:
                self._finalize_or_drop_entry(pe)
                if pe.filled_quantity > 0:
                    self._orders_opened_today += 1
            else:
                still_pending.append(pe)
        self._pending_entries = still_pending

    def maybe_squareoff(self, candle: Candle) -> None:
        """No 'sideways' anymore — anything still open once IST time reaches
        squareoff_time is closed at this candle's own close price. Distinct
        from the new-order window: a position can still be running here
        even after new_order_end_time has already blocked fresh entries.

        A position already mid-partial-exit (spread_fills) gets its
        REMAINDER closed at this candle's close, combined with whatever was
        already closed at the original trigger price — not a fresh full-
        quantity close at the squareoff price, which would double-count the
        portion already exited. Any still-unfilled pending entries are
        abandoned here too (finalized with whatever partial quantity
        they'd accumulated, same as a max_fill_candles cutoff) — a signal
        that never even opened by squareoff time has no position left to
        square off. Only THIS candle's own symbol is touched (matters once
        multiple symbols share one OrderBook, per simulate_portfolio) —
        Symbol A reaching squareoff time says nothing about whether Symbol
        B's own candle has too, and Symbol B's positions must never be
        closed at Symbol A's price."""
        if ist_time(candle.timestamp) < self.config.squareoff_time:
            return
        if self._pending_entries:
            due, self._pending_entries = (
                [pe for pe in self._pending_entries if pe.symbol == candle.symbol],
                [pe for pe in self._pending_entries if pe.symbol != candle.symbol],
            )
            for pe in due:
                self._finalize_or_drop_entry(pe)
        if not self.open_positions:
            return
        still_open = []
        for pos in self.open_positions:
            if pos.symbol != candle.symbol:
                still_open.append(pos)
                continue
            if pos.closing_price is not None:
                remaining = pos.quantity - pos.closed_quantity
                pos.closed_quantity += remaining
                pos.closed_weighted_sum += remaining * float(candle.close)
                self._finish_partial_close(pos, candle.timestamp)
            else:
                self._close(pos, float(candle.close), candle.timestamp, "eod_squareoff")
        self.open_positions = still_open

    def can_open(self, ts_utc: datetime) -> bool:
        t = ist_time(ts_utc)
        if not (self.config.new_order_start_time <= t < self.config.new_order_end_time):
            return False
        if len(self.open_positions) + len(self._pending_entries) >= self.config.max_orders_at_a_time:
            return False
        if self._stop_losses_today >= self.config.exit_at_loss_count:
            return False
        if (self.config.max_daily_loss_pct is not None
                and self._daily_loss_sum >= self.config.max_daily_loss_pct * self.config.capital_per_trade):
            return False
        return True

    def try_open(
        self, symbol: str, pattern: str, direction: str, entry_price: float, entry_ts: datetime,
        stop_loss: float, target: float, timeframe: str, volume: Optional[int] = None,
    ) -> bool:
        """volume: the entry candle's own real traded volume, used to derive
        a liquidity cap when EngineConfig.liquidity_safety_divisor is set —
        None (default, or when the divisor itself is unset) leaves sizing
        exactly as before this parameter existed.

        When spread_fills is also on and liquidity is the binding
        constraint (desired size > what this one candle's liquidity
        allows), this does NOT shrink the order — it opens a _PendingEntry
        for the full desired size, immediately filling whatever this
        candle allows, and lets continue_entries top up the rest on later
        candles. Returns True either way (an order really did get
        committed to, just not necessarily fully filled yet)."""
        if not self.can_open(entry_ts):
            return False
        multiplier = self._next_order_margin_multiplier() * self._win_streak_multiplier()
        liquidity_cap = None
        if volume is not None and self.config.liquidity_safety_divisor is not None:
            liquidity_cap = int(volume // self.config.liquidity_safety_divisor)

        if self._orders_opened_today == 0 and self.config.first_order_quantity is not None:
            desired_quantity = self.config.first_order_quantity
        else:
            # Sized off the REMAINING pool, not the nominal capital_per_trade
            # — if other positions are already open (or a prior loss has
            # shrunk the pool), this order gets whatever's left, not the full
            # amount. If that's not even enough for 1 share, quantity comes
            # back 0 and the order simply doesn't open (explicit
            # instruction: take whatever the remaining fund allows, never a
            # separate "skip on insufficient fund" error path — 0 quantity
            # already IS that).
            sizing_base = self.available_fund if self.config.compounding else self.config.capital_per_trade
            desired_quantity = quantity_for(sizing_base, entry_price, self.config.max_vol_per_call, multiplier)
        if desired_quantity <= 0:
            return False

        if self.config.min_avg_volume_multiple is not None:
            mean_volume = self._mean_recent_volume(symbol, timeframe)
            # No history yet (very first candles of a run) -- degrade to
            # "allow," not "block": there isn't enough data to say liquidity
            # is actually insufficient, same spirit as every other None-
            # until-ready indicator check in this codebase.
            if mean_volume is not None and mean_volume < desired_quantity * self.config.min_avg_volume_multiple:
                return False

        if self.config.spread_fills and liquidity_cap is not None and liquidity_cap < desired_quantity:
            # Deliberately NOT seeded with this candle's own liquidity here
            # — that volume is already "spent" on however this signal's
            # OWN entry_price/liquidity_cap were derived from it. Filling
            # starts from the NEXT candle's own continue_entries call
            # (simulate()'s per-candle loop), keeping this candle's
            # liquidity un-double-counted and every pending entry's fill
            # logic in exactly one place.
            self._pending_entries.append(_PendingEntry(
                symbol=symbol, pattern=pattern, direction=direction, signal_price=entry_price,
                stop_loss=stop_loss, target=target, timeframe=timeframe,
                multiplier=multiplier, target_quantity=desired_quantity, entry_ts=entry_ts,
            ))
            return True

        quantity = min(desired_quantity, liquidity_cap) if liquidity_cap is not None else desired_quantity
        if quantity <= 0:
            return False
        margin_used = (quantity * entry_price) / multiplier
        self.available_fund -= margin_used
        self.open_positions.append(_OpenPosition(
            symbol=symbol, pattern=pattern, direction=direction, entry_price=entry_price, entry_ts=entry_ts,
            stop_loss=stop_loss, target=target, quantity=quantity, timeframe=timeframe,
            margin_used=margin_used,
        ))
        self._orders_opened_today += 1
        return True

    def force_close_all(self, price: float, ts: datetime) -> None:
        """End-of-run cleanup — same double-counting concern as
        maybe_squareoff for any position mid-partial-exit (closes only the
        REMAINDER at `price`, combined with what was already closed at the
        original trigger price) and any never-fully-filled pending entries
        (finalized with whatever partial quantity they'd accumulated, or
        silently dropped if that's zero)."""
        for pe in self._pending_entries:
            self._finalize_or_drop_entry(pe)
        self._pending_entries = []
        for pos in list(self.open_positions):
            if pos.closing_price is not None:
                remaining = pos.quantity - pos.closed_quantity
                pos.closed_quantity += remaining
                pos.closed_weighted_sum += remaining * price
                self._finish_partial_close(pos, ts)
            else:
                self._close(pos, price, ts, "run_end")
        self.open_positions = []


def _sl_target_for(
    engine: ActivityEngine, symbol: str, exchange_segment: str, timeframe: str,
    pattern: str, direction: str, entry: float, config: SLTargetConfig,
    atr_multiplier: float, risk_reward_ratio: float,
) -> Optional[Tuple[float, float]]:
    if config.mode == "fixed_pct":
        return fixed_pct_levels(entry, direction, config.sl_pct, config.target_pct)

    # atr_neckline: same sourcing as PredictionTracker.on_activities
    if pattern in _GRAPH_FORMATIONS:
        points = engine.get_swing_points(symbol, exchange_segment, timeframe)
        neckline_fn, stop_fn, target_fn = FORMATION_LEVEL_FUNCS[pattern]
        needed = 5 if pattern.startswith("triple_") else 3
        if len(points) < needed:
            return None
        return stop_fn(points), target_fn(points)

    atr = engine.get_atr(symbol, exchange_segment, timeframe)
    if atr is None:
        return None
    return (
        crossover_stop_loss(entry, atr, direction, atr_multiplier),
        crossover_target(entry, atr, direction, atr_multiplier, risk_reward_ratio),
    )


def _load_candles(session_factory, symbol: str, exchange_segment: str, timeframe: str,
                   start: datetime, end: datetime) -> List[Candle]:
    with session_factory() as session:
        rows = LibCandlesHistorical.get_range(session, symbol, exchange_segment, timeframe, start, end)
    return [
        Candle(symbol=symbol, timeframe=timeframe, timestamp=_as_utc(r.ts),
               open=float(r.open_price), high=float(r.high_price),
               low=float(r.low_price), close=float(r.close_price), volume=r.volume)
        for r in rows
    ]


def _load_macd_states(session_factory, symbol: str, exchange_segment: str,
                       timeframes: List[str]) -> Dict[Tuple[str, datetime], Optional[str]]:
    """(timeframe, ts) -> classify_macd(...) for EngineConfig.exit_on_macd_
    reversal. Reads whatever CandleIndicators already has for this symbol
    (built by pattern_outcome_analysis.py's own backfill) rather than
    recomputing MACD from scratch -- same values the rest of this
    codebase's indicator-based analysis already relies on."""
    with session_factory() as session:
        row = LibSymbols.get_by_natural_key(session, symbol, "NSE", "EQUITY")
        rows = session.query(CandleIndicators).filter(
            CandleIndicators.instrument_id == row.id,
            CandleIndicators.timeframe.in_(timeframes),
        ).all()
    return {
        (r.timeframe, _as_utc(r.ts)): classify_macd(
            float(r.macd_line) if r.macd_line is not None else None,
            float(r.macd_signal) if r.macd_signal is not None else None,
        )
        for r in rows
    }


def _load_indicators(session_factory, symbol: str, exchange_segment: str,
                      timeframes: List[str]) -> Dict[Tuple[str, datetime], CandleIndicators]:
    """(timeframe, ts) -> the full CandleIndicators row, for OrderBook.
    load_indicators() -- powers the backtest result grid's entry/exit
    indicator value+state columns (BacktestTrade.entry_rsi_state etc.).
    Same source table and shape as _load_macd_states, just every column
    instead of one derived field. Always attempted (not gated behind a
    config flag, unlike macd_states, since this is reporting-only) --
    an empty dict here is the normal "no CandleIndicators for this symbol
    yet" case and OrderBook._indicator_snapshot silently no-ops on it."""
    with session_factory() as session:
        row = LibSymbols.get_by_natural_key(session, symbol, "NSE", "EQUITY")
        rows = session.query(CandleIndicators).filter(
            CandleIndicators.instrument_id == row.id,
            CandleIndicators.timeframe.in_(timeframes),
        ).all()
    return {(r.timeframe, _as_utc(r.ts)): r for r in rows}


def simulate(session_factory, symbol: str, exchange_segment: str, timeframes: List[str],
             start: datetime, end: datetime, config: EngineConfig) -> List[dict]:
    """Merges every timeframe's candles into one chronological stream (ties
    broken by the given timeframe order) and replays them through a single
    ActivityEngine + shared OrderBook, so MaxOrdersAtATime/ExitAtLossCount
    apply across timeframes, not per-timeframe."""
    merged: List[Tuple[datetime, int, Candle]] = []
    for i, tf in enumerate(timeframes):
        tf_candles = _load_candles(session_factory, symbol, exchange_segment, tf, start, end)
        print(f"  loaded {len(tf_candles)} {tf} candles", flush=True)
        for c in tf_candles:
            merged.append((c.timestamp, i, c))
    merged.sort(key=lambda item: (item[0], item[1]))
    print(f"Merged {len(merged)} candles across {timeframes}", flush=True)

    macd_states = (_load_macd_states(session_factory, symbol, exchange_segment, timeframes)
                   if config.exit_on_macd_reversal else {})

    engine = ActivityEngine(session_factory)
    settings = load_prediction_settings(session_factory)
    # config.atr_multiplier/risk_reward_ratio (from a Strategy row's own
    # sl_atr_multiplier/target_risk_reward_ratio, via
    # engine_config_from_strategy) win when set — otherwise fall back to
    # the global prediction_settings default, same as before this run
    # became DB-parameter-driven.
    atr_multiplier = config.atr_multiplier if config.atr_multiplier is not None else settings.get(
        "crossover_atr_stop_multiplier", PREDICTION_SETTING_DEFAULTS["crossover_atr_stop_multiplier"])
    risk_reward_ratio = config.risk_reward_ratio if config.risk_reward_ratio is not None else settings.get(
        "crossover_risk_reward_ratio", PREDICTION_SETTING_DEFAULTS["crossover_risk_reward_ratio"])
    book = OrderBook(config)
    book.load_indicators(_load_indicators(session_factory, symbol, exchange_segment, timeframes))

    progress_every = 5000
    loop_started = _time.monotonic()
    for idx, (ts, _, candle) in enumerate(merged):
        if idx and idx % progress_every == 0:
            elapsed = _time.monotonic() - loop_started
            print(f"  ...{idx}/{len(merged)} candles processed ({elapsed:.1f}s elapsed, "
                  f"{len(book.closed_trades)} trades closed so far)", flush=True)
        book.on_new_candle_day(ts)
        book.record_volume(symbol, candle.timeframe, candle.volume)
        book.resolve_against_candle(candle, macd_states.get((candle.timeframe, ts)))
        book.continue_closing(candle)  # spread_fills only — no-op otherwise
        book.continue_entries(candle)  # spread_fills only — no-op otherwise
        book.maybe_squareoff(candle)

        new_activities = engine.on_candle_closed(symbol, exchange_segment, candle)
        for activity in new_activities:
            pattern = activity["activity"]
            if pattern in BULLISH_PATTERNS:
                direction = "bull"
            elif pattern in BEARISH_PATTERNS:
                direction = "bear"
            else:
                continue
            if config.pattern_filter is not None and pattern not in config.pattern_filter:
                continue
            if not book.can_open(ts):
                continue
            # NOT activity["close_price"] — for structure/graph_formation
            # activities (BOS/CHoCH, double/triple top/bottom, triangles/
            # wedges), that field is the CONFIRMING SWING CANDLE's own OHLC
            # (activity_engine.py's own, correct convention for documenting
            # a formation's geometry — swing_lookback candles, 5 by default,
            # behind "now"), not the price at which the signal actually
            # became known. Found live (2026-09-14): an ascending_triangle
            # detected on a 09:24 candle carried a close_price from the
            # PRIOR DAY's 15:24 candle — an 18-hour-stale, no-longer-
            # tradeable entry price. `candle` here is always the real
            # current candle (whatever on_candle_closed was just called
            # with), correct for every pattern type — crossover/candlestick
            # activities already equal this value exactly, since those ARE
            # built off the current candle.
            entry = float(candle.close)
            levels = _sl_target_for(
                engine, symbol, exchange_segment, candle.timeframe, pattern, direction, entry,
                config.sl_target, atr_multiplier, risk_reward_ratio,
            )
            if levels is None:
                continue
            stop_loss, target = levels
            book.try_open(symbol, pattern, direction, entry, ts, stop_loss, target, candle.timeframe, candle.volume)

    print(f"  loop done ({_time.monotonic() - loop_started:.1f}s), {len(book.closed_trades)} trades, "
          f"{engine.buffered_count()} activities detected", flush=True)
    if merged:
        last_ts, _, last_candle = merged[-1]
        book.force_close_all(float(last_candle.close), last_ts)

    # Deliberately NOT calling engine.flush() here — this script's own
    # deliverable is book.closed_trades (already fully in memory), not a
    # persisted activity/indicator history. Found the hard way (2026-09-14):
    # a full run buffers tens of thousands of rows (one CandleIndicators
    # snapshot per candle, unconditionally, plus every detected pattern),
    # and bulk-inserting a batch that large over the SSH tunnel took many
    # minutes even with fast_executemany=True (db/session.py) — a real,
    # still-not-fully-explained ORM/pyodbc slowdown at this batch size that
    # this script sidesteps rather than blocks on, since it doesn't need
    # the write at all.
    return book.closed_trades


def simulate_portfolio(
    session_factory, symbols: List[Tuple[str, str]], timeframes: List[str],
    start: datetime, end: datetime, config: EngineConfig,
) -> List[dict]:
    """Multi-symbol counterpart to simulate() — N symbols share ONE
    OrderBook/fund pool, so capital goes to whichever real signal fires
    first across the whole basket (chronologically), not a static
    per-symbol split decided in advance. A low-volume symbol naturally
    can't out-consume its own share of the pool either, since liquidity_
    safety_divisor already caps every order by that SYMBOL's OWN candle
    volume — spreading capital across 20 stocks doesn't mean the 3
    illiquid ones quietly starve the other 17 of fills, or the reverse.

    Each symbol keeps its own ActivityEngine (pattern-detection state —
    swing points, VWAP, indicator history — is inherently per-instrument
    and must never mix across symbols), but max_orders_at_a_time/
    available_fund/exit_at_loss_count are genuinely portfolio-wide, via
    the one shared OrderBook every symbol's candles feed into.

    symbols: [(symbol, exchange_segment), ...]. Every symbol x timeframe's
    candles are merged into ONE chronological feed — ties broken first by
    symbol order, then timeframe order (matches simulate()'s own
    timeframe tie-break, extended one level for determinism with several
    symbols sharing an identical timestamp)."""
    merged: List[Tuple[datetime, int, int, Candle]] = []
    engines: Dict[str, ActivityEngine] = {}
    exchange_segment_by_symbol: Dict[str, str] = {}
    for sym_idx, (symbol, exchange_segment) in enumerate(symbols):
        engines[symbol] = ActivityEngine(session_factory)
        exchange_segment_by_symbol[symbol] = exchange_segment
        for tf_idx, tf in enumerate(timeframes):
            tf_candles = _load_candles(session_factory, symbol, exchange_segment, tf, start, end)
            print(f"  loaded {len(tf_candles)} {tf} candles for {symbol}", flush=True)
            for c in tf_candles:
                merged.append((c.timestamp, sym_idx, tf_idx, c))
    merged.sort(key=lambda item: (item[0], item[1], item[2]))
    print(f"Merged {len(merged)} candles across {len(symbols)} symbols x {timeframes}", flush=True)

    settings = load_prediction_settings(session_factory)
    atr_multiplier = config.atr_multiplier if config.atr_multiplier is not None else settings.get(
        "crossover_atr_stop_multiplier", PREDICTION_SETTING_DEFAULTS["crossover_atr_stop_multiplier"])
    risk_reward_ratio = config.risk_reward_ratio if config.risk_reward_ratio is not None else settings.get(
        "crossover_risk_reward_ratio", PREDICTION_SETTING_DEFAULTS["crossover_risk_reward_ratio"])
    book = OrderBook(config)

    progress_every = 5000
    loop_started = _time.monotonic()
    last_candle_by_symbol: Dict[str, Candle] = {}
    for idx, (ts, _, _, candle) in enumerate(merged):
        if idx and idx % progress_every == 0:
            elapsed = _time.monotonic() - loop_started
            print(f"  ...{idx}/{len(merged)} candles processed ({elapsed:.1f}s elapsed, "
                  f"{len(book.closed_trades)} trades closed so far, "
                  f"available_fund={book.available_fund:,.0f})", flush=True)
        symbol = candle.symbol
        last_candle_by_symbol[symbol] = candle
        exchange_segment = exchange_segment_by_symbol[symbol]
        book.on_new_candle_day(ts)
        book.record_volume(symbol, candle.timeframe, candle.volume)
        book.resolve_against_candle(candle)
        book.continue_closing(candle)  # spread_fills only — no-op otherwise
        book.continue_entries(candle)  # spread_fills only — no-op otherwise
        book.maybe_squareoff(candle)

        engine = engines[symbol]
        new_activities = engine.on_candle_closed(symbol, exchange_segment, candle)
        for activity in new_activities:
            pattern = activity["activity"]
            if pattern in BULLISH_PATTERNS:
                direction = "bull"
            elif pattern in BEARISH_PATTERNS:
                direction = "bear"
            else:
                continue
            if config.pattern_filter is not None and pattern not in config.pattern_filter:
                continue
            if not book.can_open(ts):
                continue
            entry = float(candle.close)  # see simulate()'s own comment on why NOT activity["close_price"]
            levels = _sl_target_for(
                engine, symbol, exchange_segment, candle.timeframe, pattern, direction, entry,
                config.sl_target, atr_multiplier, risk_reward_ratio,
            )
            if levels is None:
                continue
            stop_loss, target = levels
            book.try_open(symbol, pattern, direction, entry, ts, stop_loss, target, candle.timeframe, candle.volume)

    total_activities = sum(e.buffered_count() for e in engines.values())
    print(f"  loop done ({_time.monotonic() - loop_started:.1f}s), {len(book.closed_trades)} trades, "
          f"{total_activities} activities detected", flush=True)

    # Final cleanup, per symbol, at THAT symbol's own last candle — NOT
    # force_close_all (single-price/single-ts shaped, wrong for a basket
    # where every symbol's own last close differs) and NOT a maybe_squareoff
    # replay (its own IST-time gate could still be false for a range that
    # ends mid-day, leaving positions stranded open at run end).
    for symbol, last_candle in last_candle_by_symbol.items():
        for pe in [p for p in book._pending_entries if p.symbol == symbol]:
            book._finalize_or_drop_entry(pe)
        book._pending_entries = [p for p in book._pending_entries if p.symbol != symbol]
        for pos in [p for p in book.open_positions if p.symbol == symbol]:
            if pos.closing_price is not None:
                remaining = pos.quantity - pos.closed_quantity
                pos.closed_quantity += remaining
                pos.closed_weighted_sum += remaining * float(last_candle.close)
                book._finish_partial_close(pos, last_candle.timestamp)
            else:
                book._close(pos, float(last_candle.close), last_candle.timestamp, "run_end")
        book.open_positions = [p for p in book.open_positions if p.symbol != symbol]

    return book.closed_trades


def _num(value) -> Optional[float]:
    """SQL Numeric columns come back as Decimal (real SQL Server/pyodbc) or
    float (SQLite in tests) — normalize either to a plain float, passing
    None through unchanged."""
    return None if value is None else float(value)


def engine_config_from_strategy(strategy) -> EngineConfig:
    """Builds an EngineConfig entirely from a Strategy DB row — the
    database-driven replacement for hand-editing EngineConfig(...) literals
    in a script per test run (explicit instruction, 2026-09-14: "dont
    change code every time with different config values... make all params
    database driven"). Any column left null on the Strategy row falls back
    to EngineConfig's own dataclass default, so a strategy that only sets
    a few fields still works — same nullable/additive convention as every
    other column on this table.

    order1_margin_multiplier/order2_margin_multiplier/order3_margin_multiplier
    map to order_volume_multipliers[0/1/2] ("first, second and third all
    three params of strategy", explicit instruction) — a strategy that
    sets only some of the three still gets EngineConfig's own default for
    whichever is left null (NOT "5x for anything unset" — that fallback
    rule applies to constructing a NEW EngineConfig by hand from a
    partially-specified instruction, not to a persisted Strategy row,
    which should just mean "use the engine default for this slot").

    sl_formula_type == "fixed_percent" maps to SLTargetConfig's
    "fixed_pct" mode (sl_fixed_value/target_fixed_value as the two
    percentages); anything else (including null, or "atr") maps to
    "atr_neckline" — the strategy's own sl_atr_multiplier/
    target_risk_reward_ratio then flow through as EngineConfig.
    atr_multiplier/risk_reward_ratio overrides (see simulate()).

    win_streak_multipliers: same comma-string-to-tuple convention as
    pattern_filter, added alongside max_daily_loss_pct/exit_on_macd_
    reversal/spread_fills/liquidity_safety_divisor/itemized_costs/
    compounding/max_fill_candles/max_exit_candles/max_fill_price_drift_pct
    from the HINDCOPPER double_top investigation (2026-09-17) — see each
    field's own column comment on the Strategy model for what it does and
    whether it's actually recommended (max_daily_loss_pct: yes, proven;
    exit_on_macd_reversal/win_streak_multipliers: tested and NOT
    recommended, kept available off-by-default)."""
    defaults = EngineConfig()

    def _or_default(value, default):
        return default if value is None else value

    multipliers = (
        _or_default(_num(strategy.order1_margin_multiplier), defaults.order_volume_multipliers[0]),
        _or_default(_num(strategy.order2_margin_multiplier), defaults.order_volume_multipliers[1]),
        _or_default(_num(strategy.order3_margin_multiplier), defaults.order_volume_multipliers[2]),
    )

    if strategy.sl_formula_type == "fixed_percent":
        sl_target = SLTargetConfig(
            strategy.name, "fixed_pct",
            sl_pct=_num(strategy.sl_fixed_value),
            target_pct=_num(strategy.target_fixed_value),
        )
    else:
        sl_target = SLTargetConfig(strategy.name, "atr_neckline")

    pattern_filter = None
    if strategy.pattern_filter:
        pattern_filter = tuple(p.strip() for p in strategy.pattern_filter.split(",") if p.strip())

    min_avg_volume_lookback = _or_default(
        strategy.min_avg_volume_lookback, defaults.min_avg_volume_lookback)

    win_streak_multipliers = None
    if strategy.win_streak_multipliers:
        win_streak_multipliers = tuple(float(p.strip()) for p in strategy.win_streak_multipliers.split(",") if p.strip())

    return EngineConfig(
        capital_per_trade=_or_default(_num(strategy.capital_per_trade), defaults.capital_per_trade),
        max_vol_per_call=_or_default(strategy.max_vol_per_call, defaults.max_vol_per_call),
        max_orders_at_a_time=_or_default(strategy.max_orders_at_a_time, defaults.max_orders_at_a_time),
        exit_at_loss_count=_or_default(strategy.exit_at_loss_count, defaults.exit_at_loss_count),
        first_order_quantity=strategy.first_order_quantity,
        order_volume_multipliers=multipliers,
        new_order_start_time=_or_default(strategy.trading_start_time, defaults.new_order_start_time),
        new_order_end_time=_or_default(strategy.new_order_end_time, defaults.new_order_end_time),
        squareoff_time=_or_default(strategy.trading_end_time, defaults.squareoff_time),
        round_trip_cost_rate=_or_default(_num(strategy.round_trip_cost_rate), defaults.round_trip_cost_rate),
        sl_target=sl_target,
        atr_multiplier=_num(strategy.sl_atr_multiplier),
        risk_reward_ratio=_num(strategy.target_risk_reward_ratio),
        pattern_filter=pattern_filter,
        max_daily_loss_pct=_num(strategy.max_daily_loss_pct),
        exit_on_macd_reversal=_or_default(strategy.exit_on_macd_reversal, defaults.exit_on_macd_reversal),
        win_streak_multipliers=win_streak_multipliers,
        spread_fills=_or_default(strategy.spread_fills, defaults.spread_fills),
        max_fill_candles=_or_default(strategy.max_fill_candles, defaults.max_fill_candles),
        max_exit_candles=_or_default(strategy.max_exit_candles, defaults.max_exit_candles),
        max_fill_price_drift_pct=_num(strategy.max_fill_price_drift_pct),
        liquidity_safety_divisor=_or_default(strategy.liquidity_safety_divisor, defaults.liquidity_safety_divisor),
        itemized_costs=_or_default(strategy.itemized_costs, defaults.itemized_costs),
        compounding=_or_default(strategy.compounding, defaults.compounding),
        min_avg_volume_multiple=_num(strategy.min_avg_volume_multiple),
        min_avg_volume_lookback=min_avg_volume_lookback,
    )


def summarize(trades: List[dict]) -> dict:
    """No 'sideways' category — every trade is a win (net_pnl > 0) or a
    loss, decided purely by realized P&L, since every exit is a real fill
    now (target/stop/EOD-squareoff), not an arbitrary timeout label."""
    total = len(trades)
    wins = [t for t in trades if t["net_pnl"] > 0]
    win_ratio = (len(wins) / total) if total else None

    total_gross = sum(t["gross_pnl"] for t in trades)
    total_expenses = sum(t["expenses"] for t in trades)
    total_net = sum(t["net_pnl"] for t in trades)

    def _bucket_by(keyfn):
        pnl: Dict[str, float] = defaultdict(float)
        count: Dict[str, int] = defaultdict(int)
        win_count: Dict[str, int] = defaultdict(int)
        for t in trades:
            k = keyfn(t)
            pnl[k] += t["net_pnl"]
            count[k] += 1
            if t["net_pnl"] > 0:
                win_count[k] += 1
        return {
            "pnl": {k: round(v, 2) for k, v in sorted(pnl.items())},
            "trades": dict(sorted(count.items())),
            "win_ratio": {k: round(win_count[k] / count[k], 3) for k in sorted(count)},
        }

    daily = _bucket_by(lambda t: t["exit_ts"][:10])
    monthly = _bucket_by(lambda t: t["exit_ts"][:7])
    yearly = _bucket_by(lambda t: t["exit_ts"][:4])
    by_pattern = _bucket_by(lambda t: t["pattern"])
    for k in by_pattern["pnl"]:
        by_pattern.setdefault("gross_pnl", {})[k] = round(sum(t["gross_pnl"] for t in trades if t["pattern"] == k), 2)
        by_pattern.setdefault("expenses", {})[k] = round(sum(t["expenses"] for t in trades if t["pattern"] == k), 2)

    winning_days = sum(1 for v in daily["pnl"].values() if v > 0)
    losing_days = sum(1 for v in daily["pnl"].values() if v < 0)
    flat_days = sum(1 for v in daily["pnl"].values() if v == 0)

    return {
        "total_trades": total, "wins": len(wins), "losses": total - len(wins),
        "win_ratio": win_ratio,
        "total_gross_pnl": round(total_gross, 2), "total_expenses": round(total_expenses, 2),
        "total_net_pnl": round(total_net, 2),
        "winning_days": winning_days, "losing_days": losing_days, "flat_days": flat_days,
        "days_with_trades": len(daily["pnl"]),
        "daily": daily, "monthly": monthly, "yearly": yearly, "by_pattern": by_pattern,
    }


def main() -> None:
    args = sys.argv[1:]
    json_prefix = None
    if "--json-prefix" in args:
        idx = args.index("--json-prefix")
        json_prefix = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    symbol, timeframes_s, start_s, end_s = args[0], args[1], args[2], args[3]
    timeframes = timeframes_s.split(",")
    start = datetime.strptime(start_s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(end_s, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)

    engine_db = build_engine()
    session_factory = build_session_factory(engine_db)
    Base.metadata.create_all(engine_db)
    seed_pattern_definitions(session_factory)

    with session_factory() as session:
        row = LibSymbols.get_by_natural_key(session, symbol, "NSE", "EQUITY")
        exchange_segment = row.exchange_segment

    configs = [
        SLTargetConfig("SL 0.2% / Target 0.5%", "fixed_pct", sl_pct=0.002, target_pct=0.005),
        SLTargetConfig("SL 0.4% / Target 0.8%", "fixed_pct", sl_pct=0.004, target_pct=0.008),
        SLTargetConfig("ATR / neckline (own ratios)", "atr_neckline"),
    ]

    run_started = _time.monotonic()
    for sl_config in configs:
        engine_config = EngineConfig(sl_target=sl_config)
        print(f"\n{'='*70}\n{sl_config.name}\n{'='*70}")
        config_started = _time.monotonic()
        trades = simulate(session_factory, symbol, exchange_segment, timeframes, start, end, engine_config)
        config_elapsed = _time.monotonic() - config_started
        summary = summarize(trades)
        summary["run_time_seconds"] = round(config_elapsed, 2)

        print(f"Run time: {config_elapsed:.2f}s")
        print(f"Total trades: {summary['total_trades']}  (wins={summary['wins']} losses={summary['losses']})")
        wr = f"{summary['win_ratio']*100:.1f}%" if summary["win_ratio"] is not None else "n/a"
        print(f"Win ratio: {wr}")
        print(f"Gross: {summary['total_gross_pnl']:,.2f}  Expenses: {summary['total_expenses']:,.2f}  Net: {summary['total_net_pnl']:,.2f}")
        print(f"Days: {summary['days_with_trades']} (winning={summary['winning_days']} losing={summary['losing_days']} flat={summary['flat_days']})")
        print("\nBy pattern:")
        for pat, pnl in summary["by_pattern"]["pnl"].items():
            print(f"  {pat:28s} trades={summary['by_pattern']['trades'][pat]:>4}  "
                  f"win_ratio={summary['by_pattern']['win_ratio'][pat]*100:>5.1f}%  net={pnl:>12,.2f}")

        if json_prefix:
            safe_name = sl_config.name.replace(" ", "_").replace("/", "-").replace("%", "pct")
            out_path = f"{json_prefix}_{safe_name}.json"
            with open(out_path, "w") as f:
                json.dump({
                    "symbol": symbol, "timeframes": timeframes, "start": start_s, "end": end_s,
                    "config_name": sl_config.name, "sl_target": sl_config.__dict__,
                    "summary": summary, "trades": trades,
                }, f, indent=2)
            print(f"Wrote {out_path}")

    print(f"\n{'='*70}\nTotal run time (all {len(configs)} configs): {_time.monotonic() - run_started:.2f}s\n{'='*70}")


if __name__ == "__main__":
    main()
