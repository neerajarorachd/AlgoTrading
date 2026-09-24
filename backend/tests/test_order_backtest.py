import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from brokers.models import Candle  # noqa: E402
from db.models import CandleHistorical, CandleIndicators, SubscribedSymbol  # noqa: E402
from db.ops import LibCandlesHistorical, LibSymbols  # noqa: E402
from order_backtest import (  # noqa: E402
    EngineConfig, OrderBook, SLTargetConfig, _load_indicators, _OpenPosition, engine_config_from_strategy,
    fixed_pct_levels, ist_time, itemized_round_trip_cost, quantity_for, simulate, summarize,
)

SYMBOL = "RELIANCE"
_IST_OFFSET = timedelta(hours=5, minutes=30)


def _ts(hour, minute, day=16):
    """hour/minute given as IST wall-clock time; returns the equivalent UTC
    timestamp (what a Candle actually carries) — e.g. _ts(9, 15) is 09:15
    IST, stored as 03:45 UTC."""
    return datetime(2026, 6, day, hour, minute, tzinfo=timezone.utc) - _IST_OFFSET


def _candle(hour, minute, o, h, l, c, day=16, timeframe="1min", volume=100):
    return Candle(symbol=SYMBOL, timeframe=timeframe, timestamp=_ts(hour, minute, day),
                  open=o, high=h, low=l, close=c, volume=volume)


def _config(**overrides):
    kwargs = dict(max_vol_per_call=5000, max_orders_at_a_time=1, exit_at_loss_count=1,
                  new_order_start_time=time(9, 15), new_order_end_time=time(14, 0), squareoff_time=time(14, 50),
                  sl_target=SLTargetConfig("fixed", "fixed_pct", sl_pct=0.002, target_pct=0.005))
    kwargs.update(overrides)
    return EngineConfig(**kwargs)


# --------------------------------------------------------------------- pure helpers

def test_quantity_for_caps_at_max_vol_per_call():
    # capital/entry = 2,000,000/100 = 20,000 shares, but capped at 5000
    assert quantity_for(2_000_000, 100.0, 5000) == 5000


def test_quantity_for_uses_capital_when_it_is_the_tighter_limit():
    assert quantity_for(2_000_000, 500.0, 5000) == 4000


def test_quantity_for_applies_margin_multiplier():
    # 5x margin: (2,000,000 * 5) / 500 = 20,000, but still capped at 5000
    assert quantity_for(2_000_000, 500.0, 5000, margin_multiplier=5.0) == 5000
    # 5x margin with a high enough cap to actually see the multiplier's effect
    assert quantity_for(2_000_000, 500.0, 50_000, margin_multiplier=5.0) == 20_000
    # default multiplier (omitted) behaves exactly like 1x
    assert quantity_for(2_000_000, 500.0, 50_000) == 4000


def test_fixed_pct_levels_bull_and_bear():
    stop, target = fixed_pct_levels(100.0, "bull", 0.002, 0.005)
    assert stop == pytest.approx(99.8)
    assert target == pytest.approx(100.5)
    stop, target = fixed_pct_levels(100.0, "bear", 0.004, 0.008)
    assert stop == pytest.approx(100.4)
    assert target == pytest.approx(99.2)


def test_ist_time_converts_utc_correctly():
    # 03:45 UTC == 09:15 IST
    assert ist_time(datetime(2026, 6, 16, 3, 45, tzinfo=timezone.utc)) == time(9, 15)


# --------------------------------------------------------------------- OrderBook: concurrency

def test_max_orders_at_a_time_blocks_a_second_concurrent_open(monkeypatch):
    book = OrderBook(_config(max_orders_at_a_time=1))
    assert book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min") is True
    assert book.try_open(SYMBOL, "bullish_engulfing", "bull", 101.0, _ts(10, 5), 100.0, 103.0, "1min") is False
    assert len(book.open_positions) == 1


def test_a_second_order_is_allowed_once_the_first_closes():
    book = OrderBook(_config(max_orders_at_a_time=1))
    book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min")
    book.resolve_against_candle(_candle(10, 5, 100, 103, 99.5, 102.5))  # hits target, closes
    assert len(book.open_positions) == 0
    assert book.try_open(SYMBOL, "bullish_engulfing", "bull", 102.0, _ts(10, 6), 101.0, 105.0, "1min") is True


def test_max_orders_at_a_time_two_allows_two_concurrent():
    book = OrderBook(_config(max_orders_at_a_time=2))
    assert book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min") is True
    assert book.try_open(SYMBOL, "bullish_engulfing", "bull", 101.0, _ts(10, 5), 100.0, 103.0, "1min") is True
    assert book.try_open(SYMBOL, "piercing_line", "bull", 101.5, _ts(10, 6), 100.5, 103.5, "1min") is False
    assert len(book.open_positions) == 2


def test_two_concurrent_positions_resolve_independently_on_the_same_candle():
    """Each open position is checked against its OWN target/stop, not a
    shared one — two positions with different levels, hit by the same
    candle, must each get their own correct outcome (one wins, one
    loses), not have one position's result bleed into the other's."""
    book = OrderBook(_config(max_orders_at_a_time=2))
    book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min")  # target 102, stop 99
    book.try_open(SYMBOL, "bullish_engulfing", "bull", 110.0, _ts(10, 1), 108.0, 113.0, "1min")  # target 113, stop 108
    assert len(book.open_positions) == 2

    # one candle whose range hits the FIRST position's target (102) and the
    # SECOND position's stop (108), but not the first's stop or the second's target
    book.resolve_against_candle(_candle(10, 5, 105, 112, 101, 109))
    assert len(book.closed_trades) == 2
    assert len(book.open_positions) == 0
    by_pattern = {t["pattern"]: t for t in book.closed_trades}
    assert by_pattern["hammer"]["exit_reason"] == "target_hit"
    assert by_pattern["hammer"]["exit_price"] == 102.0
    assert by_pattern["bullish_engulfing"]["exit_reason"] == "stop_hit"
    assert by_pattern["bullish_engulfing"]["exit_price"] == 108.0


def test_one_of_two_concurrent_positions_can_close_while_the_other_stays_open():
    book = OrderBook(_config(max_orders_at_a_time=2))
    book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min")  # target 102
    book.try_open(SYMBOL, "bullish_engulfing", "bull", 200.0, _ts(10, 1), 198.0, 205.0, "1min")  # far away, untouched

    book.resolve_against_candle(_candle(10, 5, 101, 103, 100.5, 102.5))  # only reaches the first position's target
    assert len(book.closed_trades) == 1
    assert book.closed_trades[0]["pattern"] == "hammer"
    assert len(book.open_positions) == 1
    assert book.open_positions[0].pattern == "bullish_engulfing"


# --------------------------------------------------------------------- OrderBook: resolution

def test_target_hit_closes_with_correct_gross_and_expenses():
    book = OrderBook(_config())
    book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min")
    book.resolve_against_candle(_candle(10, 5, 101, 103, 100.5, 102.5))  # high >= target
    assert len(book.closed_trades) == 1
    t = book.closed_trades[0]
    assert t["exit_reason"] == "target_hit"
    assert t["exit_price"] == 102.0
    qty = quantity_for(2_000_000, 100.0, 5000)
    assert t["gross_pnl"] == round(qty * (102.0 - 100.0), 2)
    assert t["expenses"] == round(qty * 100.0 * 0.00085, 2)
    assert t["net_pnl"] == round(t["gross_pnl"] - t["expenses"], 2)


def test_stop_hit_closes_at_stop_price():
    book = OrderBook(_config())
    book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min")
    book.resolve_against_candle(_candle(10, 5, 99.5, 99.8, 98.5, 99.0))  # low <= stop
    t = book.closed_trades[0]
    assert t["exit_reason"] == "stop_hit"
    assert t["exit_price"] == 99.0
    assert t["net_pnl"] < 0


def test_same_candle_ambiguity_resolves_conservatively_as_stop():
    book = OrderBook(_config())
    book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min")
    book.resolve_against_candle(_candle(10, 5, 100, 103, 98, 101))  # hits both target and stop
    assert book.closed_trades[0]["exit_reason"] == "stop_hit"


def test_a_gap_through_the_target_does_not_count_as_hit():
    """A directional check (high >= target) would wrongly treat this as a
    fill at the exact target price even though the candle's actual traded
    range (105-110) never included it — the position gapped clean over
    the target, it didn't trade there. Range containment correctly leaves
    it open instead."""
    book = OrderBook(_config())
    book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min")
    book.resolve_against_candle(_candle(10, 5, 106, 110, 105, 108))  # entire range is above target=102
    assert book.closed_trades == []
    assert len(book.open_positions) == 1


def test_a_gap_through_the_stop_does_not_count_as_hit():
    book = OrderBook(_config())
    book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min")
    book.resolve_against_candle(_candle(10, 5, 96, 97, 94, 95))  # entire range is below stop=99
    assert book.closed_trades == []
    assert len(book.open_positions) == 1


def test_target_within_the_candles_range_still_hits_normally():
    """Confirms the range check isn't just stricter across the board —
    the ordinary in-range case (candle spans the target) still resolves
    exactly as before."""
    book = OrderBook(_config())
    book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min")
    book.resolve_against_candle(_candle(10, 5, 101, 103, 100.5, 102.5))  # low(100.5) <= target(102) <= high(103)
    assert book.closed_trades[0]["exit_reason"] == "target_hit"
    assert book.closed_trades[0]["exit_price"] == 102.0


# --------------------------------------------------------------------- OrderBook: MACD reversal exit

def test_macd_reversal_exit_is_off_by_default_even_when_state_opposes():
    book = OrderBook(_config())  # exit_on_macd_reversal defaults False
    book.try_open(SYMBOL, "double_top", "bear", 100.0, _ts(10, 0), 101.0, 95.0, "1min")
    # neither stop(101) nor target(95) is in this candle's range
    book.resolve_against_candle(_candle(10, 5, 99.5, 99.8, 99.0, 99.5), macd_state="bullish")
    assert book.closed_trades == []
    assert len(book.open_positions) == 1


def test_macd_reversal_exit_closes_a_bear_position_when_macd_turns_bullish():
    book = OrderBook(_config(exit_on_macd_reversal=True))
    book.try_open(SYMBOL, "double_top", "bear", 100.0, _ts(10, 0), 101.0, 95.0, "1min")
    candle = _candle(10, 5, 99.5, 99.8, 99.0, 99.5)  # neither stop nor target in range
    book.resolve_against_candle(candle, macd_state="bullish")  # opposes a bear position
    assert len(book.closed_trades) == 1
    t = book.closed_trades[0]
    assert t["exit_reason"] == "indicator_reversal"
    assert t["exit_price"] == candle.close  # exits at the candle's own close, not stop/target


def test_macd_reversal_exit_leaves_a_position_open_when_macd_agrees():
    book = OrderBook(_config(exit_on_macd_reversal=True))
    book.try_open(SYMBOL, "double_top", "bear", 100.0, _ts(10, 0), 101.0, 95.0, "1min")
    book.resolve_against_candle(_candle(10, 5, 99.5, 99.8, 99.0, 99.5), macd_state="bearish")  # agrees
    assert book.closed_trades == []
    assert len(book.open_positions) == 1


def test_stop_hit_takes_priority_over_a_macd_reversal_on_the_same_candle():
    book = OrderBook(_config(exit_on_macd_reversal=True))
    book.try_open(SYMBOL, "double_top", "bear", 100.0, _ts(10, 0), 101.0, 95.0, "1min")
    # this candle's range genuinely contains the stop(101) AND macd opposes --
    # stop/target resolution must win, not get preempted by the indicator check
    book.resolve_against_candle(_candle(10, 5, 100.5, 101.5, 100.0, 101.0), macd_state="bullish")
    assert book.closed_trades[0]["exit_reason"] == "stop_hit"


# --------------------------------------------------------------------- OrderBook: order-sequence margin multipliers

def test_first_second_third_orders_use_their_own_margin_multipliers():
    """1x, 1x, 5x by default — each order's size depends on how many
    orders have ALREADY opened today, not on its pattern or timeframe."""
    book = OrderBook(_config(max_orders_at_a_time=5, exit_at_loss_count=5,
                              order_volume_multipliers=(1.0, 1.0, 5.0)))
    book.on_new_candle_day(_ts(9, 15))

    book.try_open(SYMBOL, "a", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min")
    book.try_open(SYMBOL, "b", "bull", 100.0, _ts(10, 1), 99.0, 102.0, "1min")
    book.try_open(SYMBOL, "c", "bull", 100.0, _ts(10, 2), 99.0, 102.0, "1min")

    qty_1x = quantity_for(2_000_000, 100.0, 5000, margin_multiplier=1.0)
    qty_5x = quantity_for(2_000_000, 100.0, 5000, margin_multiplier=5.0)
    quantities = [p.quantity for p in book.open_positions]
    assert quantities == [qty_1x, qty_1x, qty_5x]


def test_fourth_and_later_orders_reuse_the_thirds_multiplier():
    book = OrderBook(_config(max_orders_at_a_time=5, exit_at_loss_count=5,
                              order_volume_multipliers=(1.0, 1.0, 5.0)))
    book.on_new_candle_day(_ts(9, 15))
    for i in range(4):
        book.try_open(SYMBOL, f"pattern{i}", "bull", 100.0, _ts(10, i), 99.0, 102.0, "1min")

    qty_5x = quantity_for(2_000_000, 100.0, 5000, margin_multiplier=5.0)
    assert book.open_positions[3].quantity == qty_5x  # 4th order, not a new/fallback multiplier


def test_order_sequence_count_resets_on_a_new_trading_day():
    book = OrderBook(_config(max_orders_at_a_time=5, exit_at_loss_count=5,
                              order_volume_multipliers=(1.0, 1.0, 5.0)))
    book.on_new_candle_day(_ts(9, 15, day=16))
    book.try_open(SYMBOL, "a", "bull", 100.0, _ts(9, 15, day=16), 99.0, 102.0, "1min")
    book.try_open(SYMBOL, "b", "bull", 100.0, _ts(9, 16, day=16), 99.0, 102.0, "1min")
    book.try_open(SYMBOL, "c", "bull", 100.0, _ts(9, 17, day=16), 99.0, 102.0, "1min")  # 3rd -> 5x today

    book.on_new_candle_day(_ts(9, 15, day=17))  # next trading day
    book.try_open(SYMBOL, "d", "bull", 100.0, _ts(9, 15, day=17), 99.0, 102.0, "1min")  # should be back to 1x

    qty_1x = quantity_for(2_000_000, 100.0, 5000, margin_multiplier=1.0)
    assert book.open_positions[-1].quantity == qty_1x


# --------------------------------------------------------------------- OrderBook: shared fund pool
# (explicit instruction, 2026-09-14: "it will impact in multitrades at a
# time" — order_volume_multipliers showed no visible effect under the old
# model because every order sized off the FULL capital_per_trade
# independently, as if it always had 100% of the fund. Now OrderBook tracks
# a running available_fund: opening a position locks its actual margin
# outlay out of the pool, closing releases margin + net P&L back to it. With
# only one position ever open at a time this is unobservable (the pool is
# always fully replenished before the next entry) — it only bites once
# MaxOrdersAtATime > 1, i.e. exactly the scenario the user pointed at.)

def test_shared_pool_shrinks_available_quantity_for_later_concurrent_orders():
    """3 concurrent 1x orders against a 1,000,000 pool, cap=400 (tighter
    than any single order's capital-only size of 1000 shares at price
    1000). Orders 1 and 2 are still cap-bound (400 each, capital not yet
    the binding constraint) — but by order 3, the pool has only 200,000
    left (1,000,000 - 400,000 - 400,000), so quantity drops to 200: the
    pool itself, not the cap, is now the binding constraint. This is the
    concrete "multiple trades at a time" impact — a later concurrent order
    gets LESS room than an earlier one, purely because the earlier ones
    are still open and holding margin."""
    book = OrderBook(_config(max_orders_at_a_time=3, exit_at_loss_count=5,
                              capital_per_trade=1_000_000.0, max_vol_per_call=400,
                              order_volume_multipliers=(1.0, 1.0, 1.0)))
    book.on_new_candle_day(_ts(9, 15))
    book.try_open(SYMBOL, "a", "bull", 1000.0, _ts(10, 0), 900.0, 1100.0, "1min")
    book.try_open(SYMBOL, "b", "bull", 1000.0, _ts(10, 1), 900.0, 1100.0, "1min")
    book.try_open(SYMBOL, "c", "bull", 1000.0, _ts(10, 2), 900.0, 1100.0, "1min")

    quantities = [p.quantity for p in book.open_positions]
    assert quantities == [400, 400, 200]
    assert book.available_fund == pytest.approx(0.0)


def test_shared_pool_can_refuse_a_concurrent_order_once_fully_committed():
    """A single 1x order that exactly exhausts the pool leaves nothing for
    a second concurrent order — it must be refused (quantity would compute
    to 0), not opened at some smaller-than-zero size."""
    book = OrderBook(_config(max_orders_at_a_time=2, exit_at_loss_count=5,
                              capital_per_trade=1_000_000.0, max_vol_per_call=100_000,
                              order_volume_multipliers=(1.0, 1.0)))
    book.on_new_candle_day(_ts(9, 15))
    assert book.try_open(SYMBOL, "a", "bull", 1000.0, _ts(10, 0), 900.0, 1100.0, "1min") is True
    assert book.open_positions[0].quantity == 1000  # fully uses the 1,000,000 pool
    assert book.available_fund == pytest.approx(0.0)
    assert book.try_open(SYMBOL, "b", "bull", 1000.0, _ts(10, 1), 900.0, 1100.0, "1min") is False
    assert len(book.open_positions) == 1


def test_closing_a_position_releases_margin_and_realized_pnl_back_to_the_pool():
    """A winning trade should leave MORE available for the next entry than
    the pool started with — margin returns plus the profit on top (the
    "running portfolio value" behavior, not a fixed per-trade allowance
    that resets to the same number every time)."""
    book = OrderBook(_config(max_orders_at_a_time=1, exit_at_loss_count=5,
                              capital_per_trade=1_000_000.0, max_vol_per_call=100_000,
                              order_volume_multipliers=(1.0, 1.0)))
    book.on_new_candle_day(_ts(9, 15))
    book.try_open(SYMBOL, "a", "bull", 1000.0, _ts(10, 0), 900.0, 1100.0, "1min")
    assert book.open_positions[0].quantity == 1000
    assert book.available_fund == pytest.approx(0.0)

    book.resolve_against_candle(_candle(10, 5, 1050, 1100, 1000, 1100))  # target hit at 1100
    trade = book.closed_trades[0]
    assert trade["exit_reason"] == "target_hit"
    assert trade["net_pnl"] > 0
    assert book.available_fund == pytest.approx(1_000_000.0 + trade["net_pnl"])

    # A second entry now has MORE than the original 1,000,000 to work with
    assert book.try_open(SYMBOL, "b", "bull", 1000.0, _ts(10, 6), 900.0, 1100.0, "1min") is True
    assert book.open_positions[0].quantity > 1000


def test_closing_a_losing_position_shrinks_the_pool_for_the_next_order():
    book = OrderBook(_config(max_orders_at_a_time=1, exit_at_loss_count=5,
                              capital_per_trade=1_000_000.0, max_vol_per_call=100_000,
                              order_volume_multipliers=(1.0, 1.0)))
    book.on_new_candle_day(_ts(9, 15))
    book.try_open(SYMBOL, "a", "bull", 1000.0, _ts(10, 0), 900.0, 1100.0, "1min")
    book.resolve_against_candle(_candle(10, 5, 950, 990, 900, 920))  # stop hit at 900
    trade = book.closed_trades[0]
    assert trade["net_pnl"] < 0
    assert book.available_fund == pytest.approx(1_000_000.0 + trade["net_pnl"])

    book.try_open(SYMBOL, "b", "bull", 1000.0, _ts(10, 6), 900.0, 1100.0, "1min")
    assert book.open_positions[0].quantity < 1000


def test_first_order_quantity_override_ignores_the_pool_formula():
    """explicit instruction: "first order qty 1" — the day's 1st order
    should use this literal quantity, not capital/pool-based sizing, while
    still deducting the correct (trivial) margin from the pool."""
    book = OrderBook(_config(max_orders_at_a_time=5, exit_at_loss_count=5,
                              capital_per_trade=1_000_000.0, max_vol_per_call=100_000,
                              order_volume_multipliers=(1.0, 1.0, 5.0),
                              first_order_quantity=1))
    book.on_new_candle_day(_ts(9, 15))
    book.try_open(SYMBOL, "a", "bull", 1000.0, _ts(10, 0), 900.0, 1100.0, "1min")
    assert book.open_positions[0].quantity == 1
    assert book.available_fund == pytest.approx(1_000_000.0 - 1000.0)

    # 2nd order is unaffected — back to normal pool-based sizing, off
    # whatever the pool has left after the trivial 1st-order deduction
    # (1,000,000 - 1,000 = 999,000 at 1x -> 999 shares)
    pool_before_second = book.available_fund
    book.try_open(SYMBOL, "b", "bull", 1000.0, _ts(10, 1), 900.0, 1100.0, "1min")
    assert book.open_positions[1].quantity == quantity_for(pool_before_second, 1000.0, 100_000, 1.0)
    assert book.open_positions[1].quantity == 999


def test_first_order_quantity_override_only_applies_to_the_very_first_order_of_the_day():
    book = OrderBook(_config(max_orders_at_a_time=1, exit_at_loss_count=5,
                              first_order_quantity=1))
    book.on_new_candle_day(_ts(9, 15))
    book.try_open(SYMBOL, "a", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min")
    assert book.open_positions[0].quantity == 1
    book.resolve_against_candle(_candle(10, 5, 101, 103, 100.5, 102.5))  # closes "a"

    book.try_open(SYMBOL, "b", "bull", 100.0, _ts(10, 6), 99.0, 102.0, "1min")  # 2nd order today
    assert book.open_positions[0].quantity != 1  # normal sizing again


def test_first_order_quantity_none_keeps_the_old_pool_based_sizing():
    book = OrderBook(_config(max_orders_at_a_time=1, exit_at_loss_count=5))  # first_order_quantity defaults to None
    book.on_new_candle_day(_ts(9, 15))
    book.try_open(SYMBOL, "a", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min")
    assert book.open_positions[0].quantity == quantity_for(2_000_000, 100.0, 5000, 1.0)


def test_only_actually_opened_orders_advance_the_sequence_count():
    """A blocked try_open (can_open() False) must not consume a slot in
    the 1st/2nd/3rd sequence — only orders that actually get placed count."""
    book = OrderBook(_config(max_orders_at_a_time=1, exit_at_loss_count=5,
                              order_volume_multipliers=(1.0, 1.0, 5.0)))
    book.on_new_candle_day(_ts(9, 15))
    book.try_open(SYMBOL, "a", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min")  # opens (1st)
    # blocked: max_orders_at_a_time=1 and "a" is still open
    assert book.try_open(SYMBOL, "b", "bull", 100.0, _ts(10, 1), 99.0, 102.0, "1min") is False

    book.resolve_against_candle(_candle(10, 5, 101, 103, 100.5, 102.5))  # closes "a" via target
    book.try_open(SYMBOL, "c", "bull", 100.0, _ts(10, 6), 99.0, 102.0, "1min")  # this is really the 2nd order, not 3rd

    qty_1x = quantity_for(2_000_000, 100.0, 5000, margin_multiplier=1.0)
    assert book.open_positions[0].quantity == qty_1x  # still 1x, not 5x


# --------------------------------------------------------------------- OrderBook: daily loss halt

def test_exit_at_loss_count_blocks_new_entries_after_n_stop_losses():
    book = OrderBook(_config(exit_at_loss_count=1, max_orders_at_a_time=5))
    book.on_new_candle_day(_ts(9, 15))
    book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min")
    book.resolve_against_candle(_candle(10, 5, 99.5, 99.8, 98.5, 99.0))  # stop hit -> 1 loss today
    assert book.try_open(SYMBOL, "bullish_engulfing", "bull", 101.0, _ts(10, 10), 100.0, 103.0, "1min") is False


def test_loss_count_resets_on_a_new_trading_day():
    book = OrderBook(_config(exit_at_loss_count=1, max_orders_at_a_time=5))
    book.on_new_candle_day(_ts(10, 0, day=16))
    book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0, day=16), 99.0, 102.0, "1min")
    book.resolve_against_candle(_candle(10, 5, 99.5, 99.8, 98.5, 99.0, day=16))  # stop hit
    assert book.try_open(SYMBOL, "x", "bull", 100.0, _ts(11, 0, day=16), 99.0, 102.0, "1min") is False

    book.on_new_candle_day(_ts(9, 15, day=17))  # next trading day
    assert book.try_open(SYMBOL, "x", "bull", 100.0, _ts(9, 20, day=17), 99.0, 102.0, "1min") is True


def test_exit_at_loss_count_of_two_allows_one_loss_before_halting():
    book = OrderBook(_config(exit_at_loss_count=2, max_orders_at_a_time=5))
    book.on_new_candle_day(_ts(9, 15))
    book.try_open(SYMBOL, "a", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min")
    book.resolve_against_candle(_candle(10, 5, 99.5, 99.8, 98.5, 99.0))  # loss 1
    assert book.try_open(SYMBOL, "b", "bull", 100.0, _ts(10, 10), 99.0, 102.0, "1min") is True  # still allowed
    book.resolve_against_candle(_candle(10, 15, 99.5, 99.8, 98.5, 99.0))  # loss 2
    assert book.try_open(SYMBOL, "c", "bull", 100.0, _ts(10, 20), 99.0, 102.0, "1min") is False  # now halted


def test_max_daily_loss_pct_blocks_new_entries_after_breach():
    # capital_per_trade defaults to 2,000,000 -- 1% = 20,000. A wide-stop
    # position that loses far more than that in one hit should halt the
    # rest of the day, same shape as exit_at_loss_count but capital-based.
    book = OrderBook(_config(exit_at_loss_count=100, max_orders_at_a_time=5, max_daily_loss_pct=0.01))
    book.on_new_candle_day(_ts(9, 15))
    book.try_open(SYMBOL, "a", "bull", 100.0, _ts(10, 0), 90.0, 110.0, "1min")
    book.resolve_against_candle(_candle(10, 5, 95.0, 96.0, 89.0, 90.5))  # stop hit, ~47,500 loss >> 20,000
    assert book.try_open(SYMBOL, "b", "bull", 100.0, _ts(10, 10), 99.0, 102.0, "1min") is False


def test_max_daily_loss_pct_resets_on_a_new_trading_day():
    book = OrderBook(_config(exit_at_loss_count=100, max_orders_at_a_time=5, max_daily_loss_pct=0.01))
    book.on_new_candle_day(_ts(9, 15, day=16))
    book.try_open(SYMBOL, "a", "bull", 100.0, _ts(10, 0, day=16), 90.0, 110.0, "1min")
    book.resolve_against_candle(_candle(10, 5, 95.0, 96.0, 89.0, 90.5, day=16))
    assert book.try_open(SYMBOL, "b", "bull", 100.0, _ts(10, 10, day=16), 99.0, 102.0, "1min") is False

    book.on_new_candle_day(_ts(9, 15, day=17))
    assert book.try_open(SYMBOL, "c", "bull", 100.0, _ts(9, 20, day=17), 99.0, 102.0, "1min") is True


def test_max_daily_loss_pct_uses_gross_loss_not_net_pnl():
    # threshold = 1% of 2,000,000 = 20,000. Loss 15,000, then win 10,000,
    # then loss 10,000: NET for the day is -15,000 (would NOT trip the OLD
    # net-P&L-based rule), but GROSS LOSS alone is 15,000 + 10,000 = 25,000
    # -- over the 20,000 budget, so this must still halt.
    book = OrderBook(_config(exit_at_loss_count=100, max_orders_at_a_time=5, max_daily_loss_pct=0.01,
                              order_volume_multipliers=(1.0,), round_trip_cost_rate=0.0))
    book.on_new_candle_day(_ts(9, 15))

    book.try_open(SYMBOL, "a", "bull", 100.0, _ts(10, 0), 97.0, 110.0, "1min")
    book.resolve_against_candle(_candle(10, 5, 97.5, 97.8, 96.5, 97.0))  # stop=97 in range -- loss 5000*3=15,000
    assert book.try_open(SYMBOL, "b", "bull", 100.0, _ts(10, 10), 90.0, 102.0, "1min") is True  # still allowed

    book.resolve_against_candle(_candle(10, 15, 101.8, 102.5, 101.5, 102.0))  # target=102 in range -- win 5000*2=10,000
    assert book.try_open(SYMBOL, "c", "bull", 100.0, _ts(10, 20), 98.0, 110.0, "1min") is True  # net so far -5,000

    book.resolve_against_candle(_candle(10, 25, 98.5, 98.8, 97.5, 98.0))  # stop=98 in range -- loss 5000*2=10,000
    # net for the day is -15,000 (under the 20,000 net threshold) but gross
    # loss is 25,000 (over it) -- must halt.
    assert book.try_open(SYMBOL, "d", "bull", 100.0, _ts(10, 30), 99.0, 102.0, "1min") is False


# --------------------------------------------------------------------- OrderBook: win-streak volume multipliers

def test_first_order_ever_uses_the_base_win_streak_multiplier():
    book = OrderBook(_config(max_orders_at_a_time=5, exit_at_loss_count=5,
                              order_volume_multipliers=(1.0,), win_streak_multipliers=(1.0, 2.0, 4.0)))
    book.on_new_candle_day(_ts(9, 15))
    book.try_open(SYMBOL, "a", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min")
    qty_1x = quantity_for(2_000_000, 100.0, 5000, margin_multiplier=1.0)
    assert book.open_positions[0].quantity == qty_1x


def test_volume_increases_after_a_win_and_resets_after_a_loss():
    book = OrderBook(_config(max_orders_at_a_time=5, exit_at_loss_count=5,
                              order_volume_multipliers=(1.0,), win_streak_multipliers=(1.0, 2.0, 4.0)))
    book.on_new_candle_day(_ts(9, 15))

    book.try_open(SYMBOL, "a", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min")
    book.resolve_against_candle(_candle(10, 5, 101.5, 102.5, 101.0, 102.0))  # target hit -> win, streak=1

    # compounding defaults True, so the pool now includes trade a's net P&L
    # -- size against the pool's REAL value at this point, not a hardcoded
    # pre-trade figure.
    pool_before_b = book.available_fund
    book.try_open(SYMBOL, "b", "bull", 100.0, _ts(10, 10), 99.0, 102.0, "1min")
    qty_2x = quantity_for(pool_before_b, 100.0, 5000, margin_multiplier=2.0)
    assert book.open_positions[0].quantity == qty_2x  # 1 prior win -> index 1 multiplier

    book.resolve_against_candle(_candle(10, 15, 99.5, 99.8, 98.5, 99.0))  # stop hit -> loss, streak resets to 0

    pool_before_c = book.available_fund
    book.try_open(SYMBOL, "c", "bull", 100.0, _ts(10, 20), 99.0, 102.0, "1min")
    qty_1x = quantity_for(pool_before_c, 100.0, 5000, margin_multiplier=1.0)
    assert book.open_positions[0].quantity == qty_1x  # back to the base multiplier after the loss


def test_win_streak_multiplier_caps_at_the_last_tuple_value():
    book = OrderBook(_config(max_orders_at_a_time=5, exit_at_loss_count=5,
                              order_volume_multipliers=(1.0,), win_streak_multipliers=(1.0, 2.0)))
    book.on_new_candle_day(_ts(9, 15))
    for i in range(3):  # 3 wins in a row -- streak exceeds the tuple's length
        book.try_open(SYMBOL, f"p{i}", "bull", 100.0, _ts(10, i), 99.0, 102.0, "1min")
        book.resolve_against_candle(_candle(10, i * 5 + 2, 101.5, 102.5, 101.0, 102.0))  # win each time

    pool_before_last = book.available_fund
    book.try_open(SYMBOL, "last", "bull", 100.0, _ts(11, 0), 99.0, 102.0, "1min")
    qty_2x = quantity_for(pool_before_last, 100.0, 5000, margin_multiplier=2.0)
    assert book.open_positions[0].quantity == qty_2x  # reuses index 1 (the last value), not a 3rd tier


# --------------------------------------------------------------------- OrderBook: trading window / squareoff

def test_cannot_open_before_new_order_start_time():
    book = OrderBook(_config(new_order_start_time=time(10, 15)))
    assert book.try_open(SYMBOL, "a", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min") is False


def test_cannot_open_at_or_after_new_order_end_time():
    book = OrderBook(_config(new_order_end_time=time(14, 0)))
    assert book.try_open(SYMBOL, "a", "bull", 100.0, _ts(14, 0), 99.0, 102.0, "1min") is False


def test_a_position_can_still_run_past_new_order_end_time_until_squareoff():
    """new_order_end_time only blocks FRESH entries — a position opened
    earlier keeps running (and can still be checked for target/stop) right
    up to the separate, later squareoff_time."""
    book = OrderBook(_config(new_order_end_time=time(14, 0), squareoff_time=time(14, 50)))
    book.try_open(SYMBOL, "a", "bull", 100.0, _ts(13, 55), 90.0, 200.0, "1min")  # opened before the new-order deadline
    book.resolve_against_candle(_candle(14, 30, 105, 106, 104, 105.5))  # after 14:00 — still open, no hit
    assert len(book.open_positions) == 1
    assert book.try_open(SYMBOL, "b", "bull", 105.0, _ts(14, 30), 100.0, 110.0, "1min") is False  # but no NEW entry now


def test_squareoff_closes_open_positions_at_the_candles_close_price():
    book = OrderBook(_config(new_order_end_time=time(14, 0), squareoff_time=time(14, 50)))
    book.try_open(SYMBOL, "a", "bull", 100.0, _ts(13, 55), 90.0, 200.0, "1min")  # far stop/target, never hit naturally
    book.maybe_squareoff(_candle(14, 49, 105, 106, 104, 105.5))  # before squareoff time — no-op
    assert len(book.open_positions) == 1
    book.maybe_squareoff(_candle(14, 50, 106, 107, 105, 106.5))  # at squareoff time — squared off
    assert len(book.open_positions) == 0
    assert book.closed_trades[0]["exit_reason"] == "eod_squareoff"
    assert book.closed_trades[0]["exit_price"] == 106.5


# --------------------------------------------------------------------- summarize()

def test_summarize_win_ratio_has_no_sideways_category():
    trades = [
        {"pattern": "hammer", "net_pnl": 100.0, "gross_pnl": 110.0, "expenses": 10.0, "exit_ts": "2026-06-16T10:00:00+00:00"},
        {"pattern": "hammer", "net_pnl": -50.0, "gross_pnl": -45.0, "expenses": 5.0, "exit_ts": "2026-06-17T10:00:00+00:00"},
        {"pattern": "shooting_star", "net_pnl": 30.0, "gross_pnl": 35.0, "expenses": 5.0, "exit_ts": "2026-07-01T10:00:00+00:00"},
    ]
    summary = summarize(trades)
    assert summary["total_trades"] == 3
    assert summary["wins"] == 2
    assert summary["losses"] == 1
    assert summary["win_ratio"] == 2 / 3
    assert summary["total_net_pnl"] == 80.0
    assert summary["total_gross_pnl"] == 100.0
    assert summary["total_expenses"] == 20.0
    assert summary["by_pattern"]["pnl"]["hammer"] == 50.0
    assert summary["by_pattern"]["trades"]["hammer"] == 2
    assert summary["by_pattern"]["win_ratio"]["hammer"] == 0.5
    assert summary["by_pattern"]["pnl"]["shooting_star"] == 30.0
    assert summary["yearly"]["pnl"] == {"2026": 80.0}
    assert summary["monthly"]["pnl"]["2026-06"] == 50.0
    assert summary["monthly"]["pnl"]["2026-07"] == 30.0


def test_summarize_handles_no_trades():
    summary = summarize([])
    assert summary["total_trades"] == 0
    assert summary["win_ratio"] is None
    assert summary["total_net_pnl"] == 0


# --------------------------------------------------------------------- simulate(): entry price for
# structure/graph_formation activities (real bug found live 2026-09-14)
#
# activity_engine.py's structure/graph_formation activities (double/triple
# top/bottom, triangles/wedges, BOS/CHoCH) correctly carry the CONFIRMING
# SWING CANDLE's own OHLC (swing_lookback candles, 5 by default, behind
# "now" — that's activity_engine.py's own documented convention, not a
# bug). simulate() used to take activity["close_price"] as the trade's
# entry price directly, which for these pattern types meant trading off a
# stale, no-longer-tradeable price instead of the current candle's real
# close — caught live via a HINDCOPPER 3-min trace where an
# ascending_triangle's "entry" turned out to be the PRIOR DAY's late-
# afternoon close. Fixed by always using the current candle's own close.

def _zigzag_highs(checkpoints):
    """checkpoints: [(index, high), ...], linearly interpolated between
    them — same helper as test_activity_engine.py's own, reproduced here
    to avoid a cross-test-file import for one small helper."""
    highs = []
    for (i0, h0), (i1, h1) in zip(checkpoints, checkpoints[1:]):
        for j in range(i0, i1):
            highs.append(h0 + (h1 - h0) * (j - i0) / (i1 - i0))
    highs.append(checkpoints[-1][1])
    return highs


def test_simulate_uses_the_current_candles_close_not_the_stale_swing_candles(session_factory):
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment="NSE_EQ",
            security_id="1333", previous_close=100.0,
        ))
        session.commit()

    # Same shape as test_activity_engine.py's own verified double_top
    # sequence: two comparable swing highs (100.3, 100.2) with a deep
    # valley (95.0) between them — double_top confirms on the swing
    # candle at the SECOND peak, 5 candles before the candle simulate()
    # is actually processing when the activity is returned.
    highs = _zigzag_highs([(0, 90.0), (10, 100.3), (20, 95.0), (30, 100.2), (40, 92.0)])
    candles = [
        (SYMBOL, "NSE_EQ", Candle(
            symbol=SYMBOL, timeframe="1min", timestamp=_ts(9, 15) + timedelta(minutes=i),
            open=h - 0.5, high=h, low=h - 1.0, close=h - 0.5, volume=100,
        ))
        for i, h in enumerate(highs)
    ]
    LibCandlesHistorical.persist_bulk(session_factory, candles)

    # max_orders_at_a_time/exit_at_loss_count wide open — this test only
    # cares about entry-price selection, not concurrency/daily-halt
    # interactions with whatever else fires during the zigzag's swings.
    config = EngineConfig(
        max_orders_at_a_time=100, exit_at_loss_count=100,
        sl_target=SLTargetConfig("fixed", "fixed_pct", sl_pct=0.01, target_pct=0.01),
    )
    start = candles[0][2].timestamp
    end = candles[-1][2].timestamp + timedelta(days=1)
    trades = simulate(session_factory, SYMBOL, "NSE_EQ", ["1min"], start, end, config)

    double_top_trades = [t for t in trades if t["pattern"] == "double_top"]
    assert len(double_top_trades) == 1
    trade = double_top_trades[0]

    # entry_price must match the close of whichever candle simulate() was
    # ACTUALLY processing at entry_ts — not the confirming swing candle
    # (index 30, the second peak, close = 100.2 - 0.5 = 99.7), which is
    # what the bug produced instead.
    entry_ts = datetime.fromisoformat(trade["entry_ts"])
    triggering_candle = next(c for _, _, c in candles if c.timestamp == entry_ts)
    assert trade["entry_price"] == pytest.approx(triggering_candle.close)
    assert trade["entry_price"] != pytest.approx(99.7)


# --------------------------------------------------------------------- simulate(): pattern_filter
# Real gap found live 2026-09-15: without this, simulate() blends EVERY
# registered pattern's trades together with no way to isolate one specific
# setup's own P&L — exactly what testing a single custom strategy needs.

def test_pattern_filter_none_trades_every_registered_pattern(session_factory):
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment="NSE_EQ",
            security_id="1333", previous_close=100.0,
        ))
        session.commit()

    highs = _zigzag_highs([(0, 90.0), (10, 100.3), (20, 95.0), (30, 100.2), (40, 92.0)])
    candles = [
        (SYMBOL, "NSE_EQ", Candle(
            symbol=SYMBOL, timeframe="1min", timestamp=_ts(9, 15) + timedelta(minutes=i),
            open=h - 0.5, high=h, low=h - 1.0, close=h - 0.5, volume=100,
        ))
        for i, h in enumerate(highs)
    ]
    LibCandlesHistorical.persist_bulk(session_factory, candles)
    start = candles[0][2].timestamp
    end = candles[-1][2].timestamp + timedelta(days=1)

    config = EngineConfig(
        max_orders_at_a_time=100, exit_at_loss_count=100,
        sl_target=SLTargetConfig("fixed", "fixed_pct", sl_pct=0.01, target_pct=0.01),
    )
    trades = simulate(session_factory, SYMBOL, "NSE_EQ", ["1min"], start, end, config)
    assert any(t["pattern"] == "double_top" for t in trades)


def test_pattern_filter_excludes_patterns_not_in_the_list(session_factory):
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment="NSE_EQ",
            security_id="1333", previous_close=100.0,
        ))
        session.commit()

    highs = _zigzag_highs([(0, 90.0), (10, 100.3), (20, 95.0), (30, 100.2), (40, 92.0)])
    candles = [
        (SYMBOL, "NSE_EQ", Candle(
            symbol=SYMBOL, timeframe="1min", timestamp=_ts(9, 15) + timedelta(minutes=i),
            open=h - 0.5, high=h, low=h - 1.0, close=h - 0.5, volume=100,
        ))
        for i, h in enumerate(highs)
    ]
    LibCandlesHistorical.persist_bulk(session_factory, candles)
    start = candles[0][2].timestamp
    end = candles[-1][2].timestamp + timedelta(days=1)

    # A pattern that never fires on this exact zigzag -> 0 trades even
    # though double_top (and possibly others) would have fired unfiltered.
    config = EngineConfig(
        max_orders_at_a_time=100, exit_at_loss_count=100,
        sl_target=SLTargetConfig("fixed", "fixed_pct", sl_pct=0.01, target_pct=0.01),
        pattern_filter=("vwap_rejection_bear", "vwap_rejection_bull"),
    )
    trades = simulate(session_factory, SYMBOL, "NSE_EQ", ["1min"], start, end, config)
    assert all(t["pattern"] in ("vwap_rejection_bear", "vwap_rejection_bull") for t in trades)
    assert not any(t["pattern"] == "double_top" for t in trades)


# --------------------------------------------------------------------- liquidity cap (real candle volume)

def test_quantity_for_applies_liquidity_cap_as_a_tighter_ceiling():
    # capital allows 4000, max_vol_per_call allows 5000, but liquidity_cap (a
    # real candle's own volume-derived ceiling) is tighter than both.
    assert quantity_for(2_000_000, 500.0, 5000, liquidity_cap=1200) == 1200
    # liquidity_cap looser than the other two limits has no effect
    assert quantity_for(2_000_000, 500.0, 5000, liquidity_cap=50_000) == 4000
    # unset (None, default) behaves exactly as before this parameter existed
    assert quantity_for(2_000_000, 500.0, 5000) == 4000


def test_try_open_caps_quantity_by_real_candle_volume_when_configured():
    # volume/40 = 2000/40 = 50 shares — far tighter than the capital-based
    # 20,000 shares (2,000,000/100) this order would otherwise get.
    book = OrderBook(_config(liquidity_safety_divisor=40))
    assert book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min", volume=2000) is True
    assert book.open_positions[0].quantity == 50


def test_try_open_ignores_volume_when_liquidity_safety_divisor_is_unset():
    book = OrderBook(_config())  # liquidity_safety_divisor defaults to None
    book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min", volume=2000)
    assert book.open_positions[0].quantity == 5000  # only max_vol_per_call applies


# --------------------------------------------------------------------- itemized real costs

def test_itemized_round_trip_cost_is_positive_and_below_a_generous_upper_bound():
    # Sanity range only — exact figures are covered by double_top_pnl_backtest.py's
    # own manual verification; this just guards against a sign error or a
    # missing GST/STT term silently zeroing out.
    cost = itemized_round_trip_cost(quantity=400, sell_price=488.5, buy_price=486.0)
    assert 0 < cost < 500.0


def test_close_uses_itemized_costs_when_configured():
    book = OrderBook(_config(itemized_costs=True))
    book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 99.0, 105.0, "1min")
    book.resolve_against_candle(_candle(10, 5, 100, 106, 99.5, 105.5))  # hits target 105
    trade = book.closed_trades[0]
    expected = itemized_round_trip_cost(trade["quantity"], sell_price=105.0, buy_price=100.0)
    assert trade["expenses"] == pytest.approx(expected, abs=0.01)
    # itemized costs differ from the flat-rate default for this same setup —
    # guards against the flag silently no-op'ing.
    flat_book = OrderBook(_config())
    flat_book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 99.0, 105.0, "1min")
    flat_book.resolve_against_candle(_candle(10, 5, 100, 106, 99.5, 105.5))
    assert flat_book.closed_trades[0]["expenses"] != trade["expenses"]


# --------------------------------------------------------------------- compounding toggle

def test_compounding_true_sizes_off_the_shrunk_pool_after_a_loss():
    book = OrderBook(_config(compounding=True, capital_per_trade=1000.0, max_vol_per_call=100, exit_at_loss_count=100))
    book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 90.0, 110.0, "1min")  # qty = 10
    book.resolve_against_candle(_candle(10, 5, 100, 100, 89.0, 90.0))  # stop hit, pool shrinks
    assert book.available_fund < 1000.0
    book.try_open(SYMBOL, "bullish_engulfing", "bull", 100.0, _ts(10, 10), 90.0, 110.0, "1min")
    # sized off the now-SHRUNK pool, not the original 1000 -> fewer than 10 shares
    assert book.open_positions[0].quantity < 10


def test_compounding_false_always_sizes_off_the_original_fixed_capital():
    book = OrderBook(_config(compounding=False, capital_per_trade=1000.0, max_vol_per_call=100, exit_at_loss_count=100))
    book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 90.0, 110.0, "1min")  # qty = 10
    book.resolve_against_candle(_candle(10, 5, 100, 100, 89.0, 90.0))  # stop hit, pool shrinks
    assert book.available_fund < 1000.0
    book.try_open(SYMBOL, "bullish_engulfing", "bull", 100.0, _ts(10, 10), 90.0, 110.0, "1min")
    # STILL sized off the original 1000, ignoring the shrunk pool
    assert book.open_positions[0].quantity == 10


# --------------------------------------------------------------------- spread_fills: entry side

def _spread_config(**overrides):
    kwargs = dict(
        liquidity_safety_divisor=40, spread_fills=True, capital_per_trade=1000.0,
        max_vol_per_call=100, max_orders_at_a_time=1, exit_at_loss_count=100,
    )
    kwargs.update(overrides)
    return _config(**kwargs)


def test_spread_fills_off_never_creates_a_pending_entry():
    # liquidity_safety_divisor alone (spread_fills still False, the
    # default) keeps the older shrink-down-immediately behavior.
    book = OrderBook(_config(liquidity_safety_divisor=40, capital_per_trade=1000.0,
                              max_vol_per_call=100, exit_at_loss_count=100))
    assert book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 90.0, 110.0, "1min", volume=200) is True
    assert len(book._pending_entries) == 0
    assert len(book.open_positions) == 1
    assert book.open_positions[0].quantity == 5  # 200 // 40, capped down immediately


def test_entry_spreads_across_candles_until_fully_filled():
    # desired = capital(1000)/entry(100) = 10 shares; each candle's own
    # liquidity (volume 200 // divisor 40) only covers 5 -> needs 2 more
    # candles beyond the signal candle itself to fully fill.
    book = OrderBook(_spread_config())
    assert book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 90.0, 110.0, "1min", volume=200) is True
    assert len(book._pending_entries) == 1
    assert len(book.open_positions) == 0

    book.continue_entries(_candle(10, 1, 101, 101, 101, 101, volume=200))  # fills 5 @ 101
    assert len(book._pending_entries) == 1
    assert book._pending_entries[0].filled_quantity == 5

    book.continue_entries(_candle(10, 2, 103, 103, 103, 103, volume=200))  # fills remaining 5 @ 103
    assert len(book._pending_entries) == 0
    assert len(book.open_positions) == 1
    pos = book.open_positions[0]
    assert pos.quantity == 10
    assert pos.entry_price == pytest.approx((5 * 101 + 5 * 103) / 10)  # 102.0, volume-weighted


def test_entry_gives_up_after_max_fill_candles_keeping_whatever_filled():
    book = OrderBook(_spread_config(max_fill_candles=2))
    # liquidity cap = 120 // 40 = 3 per candle; desired = 10 -> never fully fills
    book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 90.0, 110.0, "1min", volume=120)
    book.continue_entries(_candle(10, 1, 100, 100, 100, 100, volume=120))  # candles_waited=1, filled=3
    assert len(book._pending_entries) == 1
    book.continue_entries(_candle(10, 2, 100, 100, 100, 100, volume=120))  # candles_waited=2 -> cutoff
    assert len(book._pending_entries) == 0
    assert len(book.open_positions) == 1
    assert book.open_positions[0].quantity == 6  # 3 + 3, never reached 10


def test_entry_abandons_early_on_price_drift_even_before_candle_cutoff():
    book = OrderBook(_spread_config(max_fill_candles=10, max_fill_price_drift_pct=0.01))
    book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 90.0, 110.0, "1min", volume=120)
    book.continue_entries(_candle(10, 1, 100, 100, 100, 100.5, volume=120))  # 0.5% drift, fine, fills 3
    assert len(book._pending_entries) == 1
    book.continue_entries(_candle(10, 2, 102, 102, 102, 102.0, volume=120))  # 2% drift -> cutoff, no fill
    assert len(book._pending_entries) == 0
    assert len(book.open_positions) == 1
    assert book.open_positions[0].quantity == 3  # only candle 1's fill, candle 2 contributed nothing


def test_pending_entries_count_toward_max_orders_at_a_time():
    book = OrderBook(_spread_config(max_orders_at_a_time=1))
    assert book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 90.0, 110.0, "1min", volume=120) is True
    assert len(book._pending_entries) == 1
    # a second signal is blocked — the pending (unfilled) entry already occupies the one slot
    assert book.try_open(SYMBOL, "bullish_engulfing", "bull", 101.0, _ts(10, 1), 91.0, 111.0, "1min", volume=120) is False


def test_zero_fill_pending_entry_is_silently_dropped_at_squareoff():
    # liquidity never shows up at all (volume 0 every candle) -> nothing
    # ever gets filled; squareoff should just drop it, not open a
    # zero-quantity position.
    book = OrderBook(_spread_config(max_fill_candles=100, squareoff_time=time(10, 5)))
    book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 90.0, 110.0, "1min", volume=0)
    book.maybe_squareoff(_candle(10, 6, 100, 100, 100, 100, volume=0))
    assert len(book._pending_entries) == 0
    assert len(book.open_positions) == 0
    assert len(book.closed_trades) == 0


# --------------------------------------------------------------------- spread_fills: exit side

def test_exit_spreads_across_candles_at_the_originally_triggered_price():
    book = OrderBook(_spread_config(max_exit_candles=10))
    book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 95.0, 105.0, "1min", volume=4000)  # desired=10, fills fully at once (cap 100)
    assert book.open_positions[0].quantity == 10

    # target 105 hit, but this candle's liquidity (120//40=3) can't clear all 10
    book.resolve_against_candle(_candle(10, 5, 104, 106, 103, 105.5, volume=120))
    assert len(book.open_positions) == 1
    pos = book.open_positions[0]
    assert pos.closing_price == 105.0
    assert pos.closed_quantity == 3

    book.continue_closing(_candle(10, 6, 105, 105, 105, 105, volume=120))  # +3 = 6
    assert book.open_positions[0].closed_quantity == 6
    book.continue_closing(_candle(10, 7, 105, 105, 105, 105, volume=120))  # +3 = 9
    assert book.open_positions[0].closed_quantity == 9
    book.continue_closing(_candle(10, 8, 105, 105, 105, 105, volume=400))  # cap=10, remaining=1 -> done
    assert len(book.open_positions) == 0
    assert len(book.closed_trades) == 1
    trade = book.closed_trades[0]
    assert trade["exit_price"] == pytest.approx(105.0)  # every partial fill was at the same triggered price
    assert trade["quantity"] == 10


def test_exit_forces_out_remainder_at_market_after_max_exit_candles():
    book = OrderBook(_spread_config(max_exit_candles=2))
    book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 95.0, 105.0, "1min", volume=4000)
    book.resolve_against_candle(_candle(10, 5, 104, 106, 103, 105.5, volume=120))  # closes 3 @ 105, candles_waited=1
    book.continue_closing(_candle(10, 6, 106, 106, 106, 106, volume=120))  # closes 3 more @ 105 (6 total), candles_waited=2 -> forces remaining 4 @ market(106)
    assert len(book.open_positions) == 0
    trade = book.closed_trades[0]
    # weighted: 6 @ 105 + 4 @ 106 = 630 + 424 = 1054 / 10 = 105.4
    assert trade["exit_price"] == pytest.approx(105.4)
    assert trade["quantity"] == 10


# --------------------------------------------------------------------- multi-symbol: OrderBook isolation

def test_resolve_against_candle_never_checks_a_different_symbols_position():
    """The core correctness guarantee simulate_portfolio depends on: a
    shared OrderBook holding positions for multiple symbols must never let
    one symbol's wild candle trigger another symbol's stop/target."""
    book = OrderBook(_config(max_orders_at_a_time=100, exit_at_loss_count=100))
    book.open_positions.append(_OpenPosition(
        symbol="RELIANCE", pattern="hammer", direction="bull", entry_price=100.0,
        entry_ts=_ts(10, 0), stop_loss=99.0, target=105.0, quantity=10,
        timeframe="1min", margin_used=1000.0,
    ))
    # TCS candle whose range easily spans BOTH RELIANCE's stop and target
    # -- if resolve_against_candle didn't filter by symbol, this would
    # incorrectly close the RELIANCE position.
    tcs_candle = Candle(symbol="TCS", timeframe="1min", timestamp=_ts(10, 1),
                         open=50, high=200, low=1, close=50, volume=100)
    book.resolve_against_candle(tcs_candle)
    assert len(book.open_positions) == 1
    assert len(book.closed_trades) == 0

    # RELIANCE's OWN candle hitting its real target closes it correctly.
    reliance_candle = _candle(10, 2, 104, 106, 103, 105.5)
    book.resolve_against_candle(reliance_candle)
    assert len(book.open_positions) == 0
    assert len(book.closed_trades) == 1
    assert book.closed_trades[0]["symbol"] == "RELIANCE"
    assert book.closed_trades[0]["exit_reason"] == "target_hit"


def test_maybe_squareoff_only_closes_the_matching_symbols_positions():
    book = OrderBook(_config(max_orders_at_a_time=100, exit_at_loss_count=100, squareoff_time=time(14, 50)))
    book.open_positions.append(_OpenPosition(
        symbol="RELIANCE", pattern="hammer", direction="bull", entry_price=100.0,
        entry_ts=_ts(10, 0), stop_loss=90.0, target=110.0, quantity=10,
        timeframe="1min", margin_used=1000.0,
    ))
    book.open_positions.append(_OpenPosition(
        symbol="TCS", pattern="hammer", direction="bull", entry_price=200.0,
        entry_ts=_ts(10, 0), stop_loss=190.0, target=210.0, quantity=5,
        timeframe="1min", margin_used=1000.0,
    ))
    tcs_squareoff_candle = Candle(symbol="TCS", timeframe="1min", timestamp=_ts(15, 0),
                                   open=205, high=205, low=205, close=205, volume=100)
    book.maybe_squareoff(tcs_squareoff_candle)
    assert len(book.open_positions) == 1
    assert book.open_positions[0].symbol == "RELIANCE"  # untouched
    assert len(book.closed_trades) == 1
    assert book.closed_trades[0]["symbol"] == "TCS"
    assert book.closed_trades[0]["exit_price"] == 205.0


# --------------------------------------------------------------------- multi-symbol: simulate_portfolio

def test_simulate_portfolio_trades_across_multiple_symbols_sharing_one_pool(session_factory):
    from order_backtest import simulate_portfolio  # noqa: E402 (local import -- new symbol)

    symbol_b = "TCS"
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment="NSE_EQ",
            security_id="1333", previous_close=100.0,
        ))
        session.add(SubscribedSymbol(
            symbol=symbol_b, exchange="NSE", segment="EQUITY", exchange_segment="NSE_EQ",
            security_id="11536", previous_close=200.0,
        ))
        session.commit()

    highs_a = _zigzag_highs([(0, 90.0), (10, 100.3), (20, 95.0), (30, 100.2), (40, 92.0)])
    highs_b = _zigzag_highs([(0, 190.0), (10, 200.3), (20, 195.0), (30, 200.2), (40, 192.0)])
    candles_a = [
        (SYMBOL, "NSE_EQ", Candle(symbol=SYMBOL, timeframe="1min", timestamp=_ts(9, 15) + timedelta(minutes=i),
                                   open=h - 0.5, high=h, low=h - 1.0, close=h - 0.5, volume=100))
        for i, h in enumerate(highs_a)
    ]
    candles_b = [
        (symbol_b, "NSE_EQ", Candle(symbol=symbol_b, timeframe="1min", timestamp=_ts(9, 15) + timedelta(minutes=i),
                                     open=h - 0.5, high=h, low=h - 1.0, close=h - 0.5, volume=100))
        for i, h in enumerate(highs_b)
    ]
    LibCandlesHistorical.persist_bulk(session_factory, candles_a + candles_b)

    config = EngineConfig(
        max_orders_at_a_time=100, exit_at_loss_count=100,
        sl_target=SLTargetConfig("fixed", "fixed_pct", sl_pct=0.01, target_pct=0.01),
    )
    start = candles_a[0][2].timestamp
    end = candles_a[-1][2].timestamp + timedelta(days=1)
    trades = simulate_portfolio(
        session_factory, [(SYMBOL, "NSE_EQ"), (symbol_b, "NSE_EQ")], ["1min"], start, end, config,
    )

    symbols_traded = {t["symbol"] for t in trades}
    assert SYMBOL in symbols_traded
    assert symbol_b in symbols_traded
    # Each symbol's own signals trade at ITS OWN price level, not the
    # other's -- RELIANCE's zigzag lives in the 90-100 band, TCS's in the
    # 190-200 band; the two must never cross-contaminate.
    reliance_trades = [t for t in trades if t["symbol"] == SYMBOL]
    tcs_trades = [t for t in trades if t["symbol"] == symbol_b]
    assert reliance_trades and tcs_trades
    assert all(85 <= t["entry_price"] <= 101 for t in reliance_trades)
    assert all(185 <= t["entry_price"] <= 201 for t in tcs_trades)


def test_simulate_portfolio_shared_pool_blocks_a_second_symbols_entry(session_factory):
    """max_orders_at_a_time is genuinely portfolio-wide: one symbol
    occupying the only slot must block a signal on a DIFFERENT symbol,
    not just on the same one."""
    from order_backtest import simulate_portfolio  # noqa: E402

    symbol_b = "TCS"
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment="NSE_EQ",
            security_id="1333", previous_close=100.0,
        ))
        session.add(SubscribedSymbol(
            symbol=symbol_b, exchange="NSE", segment="EQUITY", exchange_segment="NSE_EQ",
            security_id="11536", previous_close=200.0,
        ))
        session.commit()

    highs_a = _zigzag_highs([(0, 90.0), (10, 100.3), (20, 95.0), (30, 100.2), (40, 92.0)])
    highs_b = _zigzag_highs([(0, 190.0), (10, 200.3), (20, 195.0), (30, 200.2), (40, 192.0)])
    candles_a = [
        (SYMBOL, "NSE_EQ", Candle(symbol=SYMBOL, timeframe="1min", timestamp=_ts(9, 15) + timedelta(minutes=i),
                                   open=h - 0.5, high=h, low=h - 1.0, close=h - 0.5, volume=100))
        for i, h in enumerate(highs_a)
    ]
    candles_b = [
        (symbol_b, "NSE_EQ", Candle(symbol=symbol_b, timeframe="1min", timestamp=_ts(9, 15) + timedelta(minutes=i),
                                     open=h - 0.5, high=h, low=h - 1.0, close=h - 0.5, volume=100))
        for i, h in enumerate(highs_b)
    ]
    LibCandlesHistorical.persist_bulk(session_factory, candles_a + candles_b)

    config = EngineConfig(
        max_orders_at_a_time=1, exit_at_loss_count=100,
        sl_target=SLTargetConfig("fixed", "fixed_pct", sl_pct=0.5, target_pct=0.5),
    )
    start = candles_a[0][2].timestamp
    end = candles_a[-1][2].timestamp + timedelta(days=1)
    trades = simulate_portfolio(
        session_factory, [(SYMBOL, "NSE_EQ"), (symbol_b, "NSE_EQ")], ["1min"], start, end, config,
    )

    double_tops = [t for t in trades if t["pattern"] == "double_top"]
    assert len(double_tops) <= 1


# --------------------------------------------------------------------- engine_config_from_strategy

_STRATEGY_ATTRS = (
    "name", "sl_formula_type", "sl_fixed_value", "sl_atr_multiplier",
    "target_formula_type", "target_fixed_value", "target_risk_reward_ratio",
    "order1_margin_multiplier", "order2_margin_multiplier", "order3_margin_multiplier",
    "capital_per_trade", "max_vol_per_call", "max_orders_at_a_time", "exit_at_loss_count",
    "first_order_quantity", "trading_start_time", "new_order_end_time", "trading_end_time",
    "round_trip_cost_rate", "pattern_filter",
    "max_daily_loss_pct", "exit_on_macd_reversal", "win_streak_multipliers",
    "spread_fills", "max_fill_candles", "max_exit_candles", "max_fill_price_drift_pct",
    "liquidity_safety_divisor", "itemized_costs", "compounding",
    "min_avg_volume_multiple", "min_avg_volume_lookback",
)


def _strategy(**overrides):
    fields = {a: None for a in _STRATEGY_ATTRS}
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_engine_config_from_strategy_falls_back_to_engine_defaults_when_all_null():
    defaults = EngineConfig()
    config = engine_config_from_strategy(_strategy(name="s"))
    assert config.max_daily_loss_pct is None
    assert config.exit_on_macd_reversal == defaults.exit_on_macd_reversal
    assert config.win_streak_multipliers is None
    assert config.spread_fills == defaults.spread_fills
    assert config.max_fill_candles == defaults.max_fill_candles
    assert config.max_exit_candles == defaults.max_exit_candles
    assert config.max_fill_price_drift_pct is None
    assert config.liquidity_safety_divisor == defaults.liquidity_safety_divisor
    assert config.itemized_costs == defaults.itemized_costs
    assert config.compounding == defaults.compounding
    assert config.min_avg_volume_multiple is None
    assert config.min_avg_volume_lookback == defaults.min_avg_volume_lookback


def test_engine_config_from_strategy_maps_the_new_risk_management_fields():
    strategy = _strategy(
        name="s", max_daily_loss_pct=0.01, exit_on_macd_reversal=True,
        win_streak_multipliers="0.5, 1.0, 2.0, 3.0, 4.0",
        spread_fills=True, max_fill_candles=3, max_exit_candles=10, max_fill_price_drift_pct=0.002,
        liquidity_safety_divisor=40, itemized_costs=True, compounding=False,
        min_avg_volume_multiple=1.5, min_avg_volume_lookback=8,
    )
    config = engine_config_from_strategy(strategy)
    assert config.max_daily_loss_pct == 0.01
    assert config.exit_on_macd_reversal is True
    assert config.win_streak_multipliers == (0.5, 1.0, 2.0, 3.0, 4.0)
    assert config.spread_fills is True
    assert config.max_fill_candles == 3
    assert config.max_exit_candles == 10
    assert config.max_fill_price_drift_pct == 0.002
    assert config.liquidity_safety_divisor == 40
    assert config.itemized_costs is True
    assert config.compounding is False
    assert config.min_avg_volume_multiple == 1.5
    assert config.min_avg_volume_lookback == 8


# --------------------------------------------------------------------- OrderBook: indicator snapshot

def _indicator_row(rsi=None, macd_line=None, macd_signal=None, stoch_k=None, stoch_d=None,
                    vwap=None, ma21=None, ma50=None, atr=None, bb_upper=None, bb_middle=None, bb_lower=None):
    return SimpleNamespace(rsi=rsi, macd_line=macd_line, macd_signal=macd_signal, stoch_k=stoch_k, stoch_d=stoch_d,
                            vwap=vwap, ma21=ma21, ma50=ma50, atr=atr, bb_upper=bb_upper, bb_middle=bb_middle,
                            bb_lower=bb_lower)


def test_closed_trade_has_no_indicator_keys_when_none_were_loaded():
    book = OrderBook(_config())  # load_indicators() never called
    book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min")
    book.resolve_against_candle(_candle(10, 5, 101, 103, 100.5, 102.5))
    t = book.closed_trades[0]
    assert "entry_rsi" not in t
    assert "exit_rsi_state" not in t


def test_closed_trade_gets_entry_and_exit_indicator_snapshot_when_loaded():
    entry_ts = _ts(10, 0)
    exit_ts = _ts(10, 5)
    book = OrderBook(_config())
    book.load_indicators({
        ("1min", entry_ts): _indicator_row(rsi=25.0, macd_line=1.5, macd_signal=1.0, stoch_k=15.0, stoch_d=12.0,
                                            vwap=100.2, ma21=99.5, ma50=98.0, atr=0.8,
                                            bb_upper=103.0, bb_middle=100.0, bb_lower=97.0),
        ("1min", exit_ts): _indicator_row(rsi=75.0, macd_line=0.5, macd_signal=1.0, stoch_k=85.0),
    })
    book.try_open(SYMBOL, "hammer", "bull", 100.0, entry_ts, 99.0, 102.0, "1min")
    book.resolve_against_candle(_candle(10, 5, 101, 103, 100.5, 102.5))  # exit_ts == entry of this candle
    t = book.closed_trades[0]

    assert t["entry_rsi"] == 25.0
    assert t["entry_rsi_state"] == "oversold"
    assert t["entry_macd_line"] == 1.5
    assert t["entry_macd_signal"] == 1.0
    assert t["entry_macd_state"] == "bullish"
    assert t["entry_stoch_k"] == 15.0
    assert t["entry_stoch_state"] == "oversold"
    assert t["entry_stoch_d"] == 12.0
    assert t["entry_vwap"] == 100.2
    assert t["entry_ma21"] == 99.5
    assert t["entry_ma50"] == 98.0
    assert t["entry_atr"] == 0.8
    assert t["entry_bb_upper"] == 103.0
    assert t["entry_bb_middle"] == 100.0
    assert t["entry_bb_lower"] == 97.0

    assert t["exit_rsi"] == 75.0
    assert t["exit_rsi_state"] == "overbought"
    assert t["exit_macd_state"] == "bearish"  # line(0.5) < signal(1.0)
    assert t["exit_stoch_k"] == 85.0
    assert t["exit_stoch_state"] == "overbought"
    # not set on the exit row -- must come back None, not raise
    assert t["exit_vwap"] is None
    assert t["exit_atr"] is None


def test_indicator_snapshot_is_all_none_when_no_row_matches_the_exact_timestamp():
    """Warm-up period / no CandleIndicators row yet for this candle -- same
    "None until ready" convention CandleIndicators itself uses, not an
    error."""
    book = OrderBook(_config())
    book.load_indicators({("1min", _ts(9, 0)): _indicator_row(rsi=50.0)})  # different ts -- no match
    book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min")
    book.resolve_against_candle(_candle(10, 5, 101, 103, 100.5, 102.5))
    t = book.closed_trades[0]
    assert t["entry_rsi"] is None
    assert t["entry_rsi_state"] is None
    assert t["entry_macd_state"] is None


def test_indicator_snapshot_keyed_by_timeframe_not_just_timestamp():
    """A 3min row at the same wall-clock ts as a 1min position must not
    leak into the 1min trade's snapshot -- the lookup key is
    (timeframe, ts), not ts alone."""
    entry_ts = _ts(10, 0)
    book = OrderBook(_config())
    book.load_indicators({("3min", entry_ts): _indicator_row(rsi=99.0)})
    book.try_open(SYMBOL, "hammer", "bull", 100.0, entry_ts, 99.0, 102.0, "1min")
    book.resolve_against_candle(_candle(10, 5, 101, 103, 100.5, 102.5))
    assert book.closed_trades[0]["entry_rsi"] is None


def test_indicator_trend_is_increasing_over_the_lookback_window_ending_at_entry():
    """Mirrors pattern_outcome_analysis.py's own trend convention: the last
    INDICATOR_TREND_LOOKBACK (5) rows ending at (and including) entry_ts,
    a monotonically rising RSI across all 5 -> "increasing"."""
    from order_backtest import INDICATOR_TREND_LOOKBACK
    assert INDICATOR_TREND_LOOKBACK == 5  # this test's fixture assumes exactly 5

    entry_ts = _ts(10, 0)
    rows = {
        ("1min", entry_ts - timedelta(minutes=4)): _indicator_row(rsi=30.0),
        ("1min", entry_ts - timedelta(minutes=3)): _indicator_row(rsi=35.0),
        ("1min", entry_ts - timedelta(minutes=2)): _indicator_row(rsi=40.0),
        ("1min", entry_ts - timedelta(minutes=1)): _indicator_row(rsi=45.0),
        ("1min", entry_ts): _indicator_row(rsi=50.0),
    }
    book = OrderBook(_config())
    book.load_indicators(rows)
    book.try_open(SYMBOL, "hammer", "bull", 100.0, entry_ts, 99.0, 102.0, "1min")
    book.resolve_against_candle(_candle(10, 5, 101, 103, 100.5, 102.5))
    assert book.closed_trades[0]["entry_rsi_trend"] == "increasing"


def test_indicator_trend_is_none_with_fewer_than_two_values_in_the_window():
    entry_ts = _ts(10, 0)
    book = OrderBook(_config())
    book.load_indicators({("1min", entry_ts): _indicator_row(rsi=50.0)})  # only 1 point loaded
    book.try_open(SYMBOL, "hammer", "bull", 100.0, entry_ts, 99.0, 102.0, "1min")
    book.resolve_against_candle(_candle(10, 5, 101, 103, 100.5, 102.5))
    assert book.closed_trades[0]["entry_rsi_trend"] is None


def test_indicator_trend_window_only_looks_back_not_forward():
    """A row AFTER entry_ts must never leak into entry's own trend window
    -- only pull the [ts - LOOKBACK + 1, ts] window, never past ts."""
    entry_ts = _ts(10, 0)
    rows = {
        ("1min", entry_ts): _indicator_row(rsi=50.0),
        ("1min", entry_ts + timedelta(minutes=1)): _indicator_row(rsi=999.0),  # future -- must be ignored
    }
    book = OrderBook(_config())
    book.load_indicators(rows)
    book.try_open(SYMBOL, "hammer", "bull", 100.0, entry_ts, 99.0, 102.0, "1min")
    book.resolve_against_candle(_candle(10, 5, 101, 103, 100.5, 102.5))
    # only 1 value in [entry_ts]'s own backward-looking window -> still None
    assert book.closed_trades[0]["entry_rsi_trend"] is None


# --------------------------------------------------------------------- OrderBook: min_avg_volume_multiple gate

def test_min_avg_volume_multiple_off_by_default_even_with_thin_volume():
    book = OrderBook(_config())  # min_avg_volume_multiple defaults to None
    for i in range(5):
        book.record_volume(SYMBOL, "1min", 1)  # far too thin to matter, but the gate is off
    assert book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min") is True


def test_min_avg_volume_multiple_blocks_entry_when_recent_volume_is_too_thin():
    book = OrderBook(_config(min_avg_volume_multiple=1.0, capital_per_trade=2_000_000, max_vol_per_call=100_000))
    # desired quantity at entry_price=100 with this capital/cap will be
    # capped at max_vol_per_call=100,000 shares -- feed a mean volume far
    # below that
    for i in range(5):
        book.record_volume(SYMBOL, "1min", 500)
    assert book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min") is False
    assert book.open_positions == []


def test_min_avg_volume_multiple_allows_entry_when_recent_volume_is_ample():
    book = OrderBook(_config(min_avg_volume_multiple=1.0, capital_per_trade=2_000_000, max_vol_per_call=5000))
    for i in range(5):
        book.record_volume(SYMBOL, "1min", 1_000_000)  # comfortably above any desired quantity here
    assert book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min") is True


def test_min_avg_volume_multiple_uses_the_mean_over_the_configured_lookback():
    book = OrderBook(_config(min_avg_volume_multiple=1.0, min_avg_volume_lookback=3,
                              capital_per_trade=2_000_000, max_vol_per_call=1000))
    # only the last 3 matter (lookback=3) -- an old thin reading outside the
    # window must not drag the mean down
    book.record_volume(SYMBOL, "1min", 1)
    book.record_volume(SYMBOL, "1min", 1)
    book.record_volume(SYMBOL, "1min", 5000)
    book.record_volume(SYMBOL, "1min", 5000)
    book.record_volume(SYMBOL, "1min", 5000)  # mean of last 3 = 5000, well above desired 1000
    assert book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min") is True


def test_min_avg_volume_multiple_allows_entry_with_no_volume_history_yet():
    """No recorded volume at all (very start of a run) -- degrades to
    "allow," matching the None-until-ready convention, not a block."""
    book = OrderBook(_config(min_avg_volume_multiple=1.0))
    assert book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min") is True


def test_min_avg_volume_multiple_is_keyed_by_symbol_and_timeframe():
    """A different symbol's or timeframe's recorded volume must not leak
    into this one's mean -- SYMBOL/1min gets its own genuinely thin history
    (so this isn't just the separate "no history -> allow" path), while
    TCS/1min and SYMBOL/3min get ample volume that must NOT rescue it."""
    book = OrderBook(_config(min_avg_volume_multiple=1.0, capital_per_trade=2_000_000, max_vol_per_call=100_000))
    for i in range(5):
        book.record_volume(SYMBOL, "1min", 500)
        book.record_volume("TCS", "1min", 10_000_000)
        book.record_volume(SYMBOL, "3min", 10_000_000)
    assert book.try_open(SYMBOL, "hammer", "bull", 100.0, _ts(10, 0), 99.0, 102.0, "1min") is False


# --------------------------------------------------------------------- _load_indicators

def test_load_indicators_reads_real_candle_indicators_rows_keyed_by_timeframe_and_ts(session_factory):
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment="NSE_EQ", security_id="1333",
        ))
        session.commit()
        instrument_id = LibSymbols.get_by_natural_key(session, SYMBOL, "NSE", "EQUITY").id
        session.add(CandleIndicators(instrument_id=instrument_id, timeframe="1min", ts=_ts(10, 0), rsi=42.5))
        session.add(CandleIndicators(instrument_id=instrument_id, timeframe="3min", ts=_ts(10, 0), rsi=60.0))
        session.commit()

    result = _load_indicators(session_factory, SYMBOL, "NSE_EQ", ["1min", "3min"])
    assert set(result.keys()) == {("1min", _ts(10, 0)), ("3min", _ts(10, 0))}
    assert float(result[("1min", _ts(10, 0))].rsi) == pytest.approx(42.5)
    assert float(result[("3min", _ts(10, 0))].rsi) == pytest.approx(60.0)


def test_load_indicators_only_returns_requested_timeframes(session_factory):
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment="NSE_EQ", security_id="1333",
        ))
        session.commit()
        instrument_id = LibSymbols.get_by_natural_key(session, SYMBOL, "NSE", "EQUITY").id
        session.add(CandleIndicators(instrument_id=instrument_id, timeframe="5min", ts=_ts(10, 0), rsi=10.0))
        session.commit()

    result = _load_indicators(session_factory, SYMBOL, "NSE_EQ", ["1min", "3min"])
    assert result == {}
