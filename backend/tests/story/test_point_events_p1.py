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
from story.point_events import ALL_EVENT_NAMES, DayLevels, line_events
from story.swings import label_swing


def _row(close, high=None, low=None, **lines):
    return {"close": close, "high": high if high is not None else close,
            "low": low if low is not None else close, **lines}


def _names(prev, row, **kw):
    return [n for _, n, _ in line_events(prev, row, **kw) if n.startswith("vwap")]


def test_cross_up_and_down():
    assert _names(_row(99.5, vwap=100), _row(100.5, vwap=100)) == ["vwap_cross_up"]
    assert _names(_row(100.5, vwap=100), _row(99.5, vwap=100)) == ["vwap_cross_down"]


def test_bounce_needs_both_closes_clear_of_the_line():
    prev = _row(100.5, vwap=100)
    # dips to the line, closes 0.3% above: support held
    assert _names(prev, _row(100.3, high=100.6, low=100.02, vwap=100)) == ["vwap_bounce_up"]
    # same dip but closes only 0.05% above: hovering, so a touch, not a bounce
    assert _names(prev, _row(100.05, high=100.6, low=100.02, vwap=100)) == ["vwap_touch"]
    # resistance
    assert _names(_row(99.5, vwap=100), _row(99.7, high=99.98, low=99.4, vwap=100)) == ["vwap_bounce_down"]


def test_no_event_when_the_candle_stays_away_from_the_line():
    assert _names(_row(101, vwap=100), _row(101.2, high=101.4, low=100.9, vwap=100)) == []


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
    assert any(n.endswith(("_cross_up", "_cross_down")) for n in got)
    assert any(n.startswith("swing_") and n not in ("swing_high", "swing_low") for n in got)
    assert not any(n.endswith("_touch") for n in got)  # touches stay off by default
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
