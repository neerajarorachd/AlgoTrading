"""P0 market story modules: tracker runs, anti-flicker, day close, swing
labels / leg % / ratio, setup back-off with MACD locked."""
from datetime import datetime, timedelta, timezone

import pytest

from story.setup import EvidenceIndex, age_bucket, snapshot
from story.subjects import BBWidth, LineGap, Macd, PriceVsLine, Subject
from story.swings import SwingTracker
from story.tracker import StoryTracker

T0 = datetime(2026, 10, 29, 3, 45, tzinfo=timezone.utc)  # 09:15 IST


def ts(i, day=0):
    return T0 + timedelta(days=day, minutes=5 * i)


class Scripted(Subject):
    """Reports a scripted state per candle (index = row['i'])."""
    name = "s"

    def __init__(self, states):
        self.states = states

    def observe(self, ctx):
        st = self.states[ctx.row["i"]]
        return None if st is None else (st, float(ctx.row["i"]), 1.0)


def feed(tracker, n, day=0, start=0):
    closed = []
    for k in range(n):
        closed += tracker.on_row({"i": start + k, "ts": ts(k, day), "open": 1, "high": 1, "low": 1,
                                  "close": 1, "volume": 0})
    return closed


def test_run_changes_after_confirm_and_start_is_backdated():
    states = ["a"] * 4 + ["b"] * 3
    t = StoryTracker(subjects=[Scripted(states)], settings={"confirm_candles": 2})
    closed = feed(t, 7)
    assert [(r.state, r.candles) for r in closed] == [("a", 4)]
    run = t.open_runs()["s"]
    assert (run.state, run.prev_state, run.candles, run.start_ts) == ("b", "a", 3, ts(4))


def test_one_candle_flicker_stays_in_the_current_run():
    states = ["a", "a", "b", "a", "a", "a"]
    t = StoryTracker(subjects=[Scripted(states)], settings={"confirm_candles": 2})
    assert feed(t, 6) == []
    run = t.open_runs()["s"]
    assert (run.state, run.candles) == ("a", 6)


def test_every_run_closes_at_day_end_and_prev_state_carries_over():
    t = StoryTracker(subjects=[Scripted(["a"] * 3 + ["c"] * 3)], settings={"confirm_candles": 2})
    feed(t, 3, day=0)
    closed = feed(t, 3, day=1, start=3)
    assert [(r.state, r.candles, r.is_open) for r in closed] == [("a", 3, False)]
    run = t.open_runs()["s"]
    assert (run.state, run.prev_state, run.start_ts) == ("c", "a", ts(0, 1))


def _bb_row(i, width):
    return {"i": i, "ts": ts(i), "open": 100, "high": 100, "low": 100, "close": 100, "volume": 0,
            "bb_upper": 100 + width / 2, "bb_lower": 100 - width / 2, "bb_middle": 100}


def _bb_states(widths):
    t = StoryTracker(subjects=[BBWidth()], settings={"confirm_candles": 1})
    out = []
    for i, w in enumerate(widths):
        t.on_row(_bb_row(i, w))
        run = t.open_runs().get("bb_width")
        out.append(run.state if run else None)
    return out


@pytest.mark.parametrize("w_now,state", [(1.64, "constant"), (1.36, "constant"),
                                         (1.66, "expanding"), (1.34, "squeezing")])
def test_bb_width_settled_at_1_5_stays_constant_between_1_35_and_1_65(w_now, state):
    # settles at 1.5 on the 6th candle (no change over 5 candles), then wobbles inside the band
    s = _bb_states([1.5] * 6 + [1.6, 1.42, 1.62, w_now])
    assert s[5] == "constant" and s[6:9] == ["constant"] * 3
    assert s[-1] == state


def test_bb_width_slow_drift_out_of_the_band_counts_as_expanding():
    # each step < 10% over 5 candles, but 1.70 is outside the 1.35-1.65 band of the settled 1.5
    s = _bb_states([1.5] * 6 + [1.55, 1.6, 1.64, 1.70])
    assert s[-2] == "constant" and s[-1] == "expanding"


def _gap_states(gaps_pct):
    t = StoryTracker(subjects=[LineGap("vwap_gap", "vwap")], settings={"confirm_candles": 1})
    out = []
    for i, g in enumerate(gaps_pct):
        t.on_row({"ts": ts(i), "open": 100, "high": 100 + g, "low": 100, "close": 100 + g,
                  "volume": 0, "vwap": 100.0})
        run = t.open_runs().get("vwap_gap")
        out.append(run.state if run else None)
    return out


def test_line_gap_band_is_15_percent_of_the_settled_gap():
    s = _gap_states([1.0] * 6 + [1.14, 0.86, 1.16])
    assert s[5:8] == ["constant"] * 3 and s[-1] == "expanding"


def test_line_gap_band_is_never_narrower_than_0_1_point():
    # settled at 0.2%: 15% would be +-0.03, the minimum makes it 0.1..0.3
    s = _gap_states([0.2] * 6 + [0.29, 0.11, 0.09])
    assert s[5:8] == ["constant"] * 3 and s[-1] == "narrowing"


def test_price_vs_line_counts_touches_and_rejections():
    t = StoryTracker(subjects=[PriceVsLine("price_vs_vwap", "vwap")], settings={"confirm_candles": 1})
    rows = [  # above VWAP 100; candle 1 and 2 dip to it and close back above
        {"high": 101.5, "low": 100.8, "close": 101.2},
        {"high": 101.2, "low": 99.98, "close": 100.9},
        {"high": 101.0, "low": 100.0, "close": 100.6},
    ]
    for i, r in enumerate(rows):
        t.on_row({"ts": ts(i), "open": r["close"], "volume": 0, "vwap": 100.0, **r})
    run = t.open_runs()["price_vs_vwap"]
    assert run.state == "above"
    assert run.details["touches"] == 2 and run.details["rejections"] == 2


def test_macd_state_names_side_and_widening():
    t = StoryTracker(subjects=[Macd()], settings={"confirm_candles": 1})
    for i, (line, sig) in enumerate([(0.1, 0.0), (0.3, 0.1), (0.35, 0.2)]):
        t.on_row({"ts": ts(i), "open": 100, "high": 100, "low": 100, "close": 100, "volume": 0,
                  "macd_line": line, "macd_signal": sig})
    assert t.open_runs()["macd"].state == "bull_narrowing"  # gap 0.2 -> 0.15


def _zig(prices):
    return [{"ts": ts(i), "high": p, "low": p} for i, p in enumerate(prices)]


def test_zigzag_labels_leg_and_ratio():
    # 100 -> 102 (up 2%), pullback to 100.4 (1.57% of 102 = ratio ~0.78), up to 103, down to 101
    prices = [100, 101, 102, 101, 100.4, 101.5, 103, 102, 101]
    sw = SwingTracker(mode="zigzag", reversal_pct=0.5)
    got = []
    for r in _zig(prices):
        got += sw.update(r)
    kinds = [(s.kind, s.price, s.label) for s in got]
    assert kinds == [("low", 100, None), ("high", 102, None), ("low", 100.4, "HL"), ("high", 103, "HH")]
    hl = got[2]
    assert hl.leg_pct == pytest.approx((100.4 - 102) / 102 * 100)
    assert hl.ratio == pytest.approx(abs(hl.leg_pct) / 2.0)
    assert sw.structure() == "uptrend"


def test_fixed_mode_is_lookback_candles_late_and_min_leg_filters_small_swings():
    prices = [10, 11, 12, 13, 12, 11, 10, 11, 12, 13, 14, 13]
    sw = SwingTracker(mode="fixed", lookback=2)
    got = []
    for r in _zig(prices):
        got += sw.update(r)
    high = [s for s in got if s.kind == "high"][0]
    assert (high.price, high.index, high.lag) == (13, 3, 2)
    small = SwingTracker(mode="fixed", lookback=2, min_leg_pct=50)
    for r in _zig(prices):
        small.update(r)
    # the 13 -> 10 drop (23%) and 10 -> 14 rise (40%) are under 50%: the low is ignored
    assert "low" not in [s.kind for s in small.swings]
    assert "low" in [s.kind for s in sw.swings]


def test_age_bucket():
    assert [age_bucket(n) for n in (1, 3, 4, 10, 11)] == ["1-3", "1-3", "4-10", "4-10", "10+"]


def test_backoff_keeps_macd_and_stops_at_min_cases():
    snaps, ys = [], []
    for k in range(200):
        s = {("macd", "state"): "bull_widening", ("vol", "state"): "high" if k % 2 else "normal",
             ("noise", "state"): f"n{k % 7}"}
        snaps.append(s)
        ys.append(1 if s[("vol", "state")] == "high" else 0)
    idx = EvidenceIndex(min_cases=30).fit(snaps, ys)
    assert idx.order["bull_widening"][0] == ("vol", "state")  # the useful condition ranks first
    p = idx.predict({("macd", "state"): "bull_widening", ("vol", "state"): "high", ("noise", "state"): "n3"})
    assert p["cases"] == 100 and p["prob"] == 1.0
    assert ("noise", "state") in p["dropped"] and p["used"][0] == ("macd", "state")
    assert idx.predict({("macd", "state"): "bear_widening"}) is None


def test_snapshot_from_open_runs():
    t = StoryTracker(subjects=[Scripted(["a"] * 5)], settings={"confirm_candles": 1})
    feed(t, 5)
    assert snapshot(t.open_runs()) == {("s", "state"): "a", ("s", "age"): "4-10"}
