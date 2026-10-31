"""Market story P1: line cross/bounce/touch, day levels, swing labels in the
engine behind the point_events_v2 setting (off by default)."""
import math
from datetime import date, datetime, timedelta, timezone

import pytest

from activity_engine import PATTERN_CATALOG, ActivityEngine
from brokers.models import Candle
from db.models import InstrumentActivity
from db.ops import LibSettings
from db.session import session_scope
from story.point_events import ALL_EVENT_NAMES, DayLevels, LineTests
from story.swings import label_swing


def _run(candles, zone_pct=0.10, arm_mult=3):
    """candles: (close, high, low) with VWAP fixed at 100 -> [(event, intensity)] per candle."""
    lt = LineTests(zone_pct=zone_pct, arm_mult=arm_mult, zone_atr=None)
    out = []
    for c, h, l in candles:
        evs = lt.update({"close": c, "high": h, "low": l, "vwap": 100.0})
        out.append([(n, i) for _, n, i in evs if n.startswith("vwap")])
    return out


def test_a_test_starts_on_first_touch_stays_open_inside_the_zone_and_ends_as_a_bounce():
    got = _run([(100.5, 100.6, 100.4),     # 0.5% above: armed (needs 3 x 0.1%)
                (100.08, 100.5, 100.05),   # low reaches the zone, close inside it: test starts
                (100.05, 100.2, 99.98),    # closes inside the zone: same test
                (100.4, 100.45, 100.0)])   # close back above the zone: bounce, test lasted 3 candles
    assert got[0] == []
    assert [n for n, _ in got[1]] == ["vwap_test_from_above"]
    assert got[2] == []
    assert got[3] == [("vwap_bounce_up", 3.0)]


def test_a_close_through_the_zone_ends_the_test_as_a_break():
    got = _run([(100.5, 100.6, 100.4), (100.05, 100.3, 100.0), (99.7, 100.0, 99.6)])
    assert [n for n, _ in got[1]] == ["vwap_test_from_above"]
    assert [n for n, _ in got[2]] == ["vwap_break_down"]


def test_touch_and_close_back_out_in_one_candle_is_a_one_candle_bounce():
    got = _run([(99.5, 99.6, 99.4), (99.6, 99.95, 99.5)])
    assert got[1] == [("vwap_test_from_below", pytest.approx(0.4)), ("vwap_bounce_down", 1.0)]


def test_hovering_next_to_the_line_after_a_test_does_not_start_new_tests():
    got = _run([(100.5, 100.6, 100.4), (100.15, 100.3, 100.05),   # test + bounce (close 0.15% away)
                (100.15, 100.2, 100.05), (100.2, 100.25, 100.06),  # still near: not re-armed
                (100.5, 100.6, 100.3), (100.2, 100.5, 100.05)])    # moved away 0.5%, then a new test
    names = [n for c in got for n, _ in c]
    assert names.count("vwap_test_from_above") == 2
    assert [n for n, _ in got[2]] == [] and [n for n, _ in got[3]] == []


def test_zone_follows_the_stocks_own_atr():
    lt = LineTests(zone_atr=0.3)
    assert lt.zone_for({"atr": 0.5, "close": 100.0}) == pytest.approx(0.15)
    assert lt.zone_for({"atr": None, "close": 100.0}) == pytest.approx(0.10)  # until ATR is ready


def test_day_levels():
    d = DayLevels()
    d1, d2 = date(2026, 10, 28), date(2026, 10, 29)
    assert d.update(d1, 101, 99, 100) == []
    assert [n for _, n, _ in d.update(d1, 102, 99.5, 101)] == ["day_high_break"]
    assert [n for _, n, _ in d.update(d1, 101, 98, 99)] == ["day_low_break"]
    # next day: previous day's high 102, low 98
    assert d.update(d2, 100, 99, 99.5) == []
    assert [n for _, n, _ in d.update(d2, 103, 99.5, 102.5)] == ["day_high_break", "prev_day_high_break"]


def test_label_swing():
    assert label_swing("high", 103, 102, 100.4, -1.57) == pytest.approx(("HH", 2.589641, 2.589641 / 1.57), rel=1e-5)
    assert label_swing("low", 100.0, 100.04, 101, None)[0] == "EL"
    assert label_swing("low", 99, None, None, None) == (None, None, None)


def test_catalog_has_every_new_event_once():
    codes = [c for c, _, _ in PATTERN_CATALOG]
    assert len(codes) == len(set(codes))
    assert ALL_EVENT_NAMES <= set(codes)


def _engine(session_factory, on):
    with session_scope(session_factory) as s:
        LibSettings.set_value(s, "swing_lookback", 2)
        if on:
            LibSettings.set_value(s, "point_events_v2", 1)

    class E(ActivityEngine):
        def _lookup_instrument_id(self, sym, seg):
            return 1
    return E(session_factory)


def _feed(e, days=2, per_day=75):
    for d in range(days):
        t0 = datetime(2026, 10, 28, 3, 45, tzinfo=timezone.utc) + timedelta(days=d)
        for i in range(per_day):
            p = 100 + 3 * math.sin(i / 6) + 0.02 * i + d
            e.on_candle_closed("X", "NSE_EQ", Candle("X", "5min", t0 + timedelta(minutes=5 * i),
                                                    p, p + 0.4, p - 0.4, p + 0.1, 1000 + i))


def test_engine_records_story_events_only_when_switched_on(session_factory):
    off = _engine(session_factory, on=False)
    _feed(off)
    assert not {a["activity"] for a in off._buffer} & ALL_EVENT_NAMES

    on = _engine(session_factory, on=True)
    _feed(on)
    got = {a["activity"] for a in on._buffer}
    assert {"day_high_break", "day_low_break"} <= got
    assert any("_test_from_" in n for n in got)
    assert any(("_bounce_" in n or "_break_" in n) and not n.startswith("vwap_rejection") for n in got)
    assert any(n.startswith("swing_") and n not in ("swing_high", "swing_low") for n in got)
    # everything the engine already recorded is still there, unchanged
    assert {a["activity"] for a in off._buffer} <= got

    labels = [a for a in on._buffer if a["activity"] in ("swing_hh", "swing_lh", "swing_hl", "swing_ll")]
    assert labels and all(a["leg_pct"] is not None for a in labels)
    for a in on._buffer:
        InstrumentActivity(**a)  # every row fits the table


def test_engine_flush_writes_the_new_columns(session_factory):
    e = _engine(session_factory, on=True)
    _feed(e, days=1)
    e.flush()
    with session_scope(session_factory) as s:
        row = s.query(InstrumentActivity).filter(InstrumentActivity.activity.in_(
            ["swing_hh", "swing_lh", "swing_hl", "swing_ll"])).first()
        assert row is not None and row.leg_pct is not None
