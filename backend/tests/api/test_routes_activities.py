from datetime import datetime, timezone

import pytest

from db.models import InstrumentActivity, PatternOutcome, SubscribedSymbol

SYMBOL = "RELIANCE"
SEG = "NSE_EQ"


def _dt(d, h=9, mi=15):
    return datetime(2026, 1, d, h, mi, tzinfo=timezone.utc)


def _register(client) -> int:
    with client.application.extensions["db_session_factory"]() as session:
        row = SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment=SEG,
            security_id="1333", previous_close=100.0,
        )
        session.add(row)
        session.commit()
        return row.id


def _seed(client, instrument_id):
    with client.application.extensions["db_session_factory"]() as session:
        session.add_all([
            InstrumentActivity(
                instrument_id=instrument_id, timeframe="1min", ts=_dt(1),
                activity_type="candle_pattern", activity="hammer", intensity=2.0,
                open_price=100, high_price=101, low_price=99, close_price=100.5,
            ),
            InstrumentActivity(
                instrument_id=instrument_id, timeframe="1min", ts=_dt(2),
                activity_type="candle_pattern", activity="hammer", intensity=4.0,
                open_price=100, high_price=101, low_price=99, close_price=100.5,
            ),
            PatternOutcome(
                instrument_id=instrument_id, timeframe="1min", pattern="hammer",
                activity_type="candle_pattern", direction="bull", detected_ts=_dt(1),
                entry_price=100.0, pct_change_20=0.02, max_favorable_pct=0.03,
                max_adverse_pct=-0.01, window_candles=20,
            ),
            PatternOutcome(
                instrument_id=instrument_id, timeframe="1min", pattern="hammer",
                activity_type="candle_pattern", direction="bull", detected_ts=_dt(2),
                entry_price=100.0, pct_change_20=-0.01, max_favorable_pct=0.015,
                max_adverse_pct=-0.02, window_candles=20,
            ),
        ])
        session.commit()


def test_occurrences_includes_intensity_median(client):
    instrument_id = _register(client)
    _seed(client, instrument_id)

    resp = client.get(
        "/api/activities/occurrences",
        query_string={"instrument_id": instrument_id, "timeframes": "1min", "from": "2026-01-01", "to": "2026-01-28"},
    )
    assert resp.status_code == 200
    hammer = next(p for p in resp.get_json()["patterns"] if p["pattern"] == "hammer")
    assert hammer["intensity_median"] == 3.0  # median of [2.0, 4.0]
    assert hammer["intensity_count"] == 2


def test_occurrences_includes_expected_and_actual_result(client):
    instrument_id = _register(client)
    _seed(client, instrument_id)

    resp = client.get(
        "/api/activities/occurrences",
        query_string={"instrument_id": instrument_id, "timeframes": "1min", "from": "2026-01-01", "to": "2026-01-28"},
    )
    hammer = next(p for p in resp.get_json()["patterns"] if p["pattern"] == "hammer")
    assert hammer["expected_result"] == "bullish"
    # one occurrence closed up after 20 candles (0.02 > 0, matched), one closed down (-0.01, not matched)
    assert hammer["actual_result_matched"] == 1
    assert hammer["actual_result_total"] == 2
    assert hammer["actual_result_pct"] == 0.5


def test_occurrences_includes_price_range_medians(client):
    instrument_id = _register(client)
    _seed(client, instrument_id)

    resp = client.get(
        "/api/activities/occurrences",
        query_string={"instrument_id": instrument_id, "timeframes": "1min", "from": "2026-01-01", "to": "2026-01-28"},
    )
    hammer = next(p for p in resp.get_json()["patterns"] if p["pattern"] == "hammer")
    assert hammer["up_median_pct"] == 0.0225  # median of [0.03, 0.015]
    assert hammer["down_median_pct"] == -0.015  # median of [-0.01, -0.02]
    assert hammer["range_median_pct"] == pytest.approx((0.03 - (-0.01) + 0.015 - (-0.02)) / 2)


def test_occurrences_pattern_with_no_outcome_analysis_yet_has_null_stats(client):
    """No pattern_outcome_analysis.py run has happened for this pattern —
    the endpoint should degrade gracefully, not error."""
    instrument_id = _register(client)
    with client.application.extensions["db_session_factory"]() as session:
        session.add(InstrumentActivity(
            instrument_id=instrument_id, timeframe="1min", ts=_dt(1),
            activity_type="candle_pattern", activity="doji", intensity=None,
            open_price=100, high_price=101, low_price=99, close_price=100.5,
        ))
        session.commit()

    resp = client.get(
        "/api/activities/occurrences",
        query_string={"instrument_id": instrument_id, "timeframes": "1min", "from": "2026-01-01", "to": "2026-01-28"},
    )
    doji = next(p for p in resp.get_json()["patterns"] if p["pattern"] == "doji")
    assert doji["intensity_median"] is None
    assert doji["up_median_pct"] is None
    assert doji["actual_result_total"] == 0
    assert doji["actual_result_pct"] is None
    assert doji["expected_result"] == "neutral"


# --------------------------------------------------------------------- /api/activities/intensity-analysis

def test_intensity_analysis_bands_by_intensity_and_reports_all_checkpoints(client):
    instrument_id = _register(client)
    _seed(client, instrument_id)  # 2 occurrences: intensity 2.0 (down) and 4.0 (up)

    resp = client.get(
        "/api/activities/intensity-analysis",
        query_string={
            "instrument_id": instrument_id, "timeframes": "1min", "from": "2026-01-01",
            "to": "2026-01-28", "pattern": "hammer", "bands": 2,
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["pattern"] == "hammer"
    assert body["checkpoints"] == [5, 10, 15, 20, 30]
    assert len(body["bands"]) == 2

    low_band, high_band = body["bands"]
    assert low_band["intensity_min"] == 2.0
    assert high_band["intensity_min"] == 4.0
    # low-intensity occurrence (detected_ts=_dt(1)) closed UP after 20 candles (matched, bull expected)
    assert low_band["checkpoints"]["20"]["matched"] == 1
    assert low_band["checkpoints"]["20"]["pct"] == 1.0
    # high-intensity occurrence (detected_ts=_dt(2)) closed DOWN after 20 candles (not matched)
    assert high_band["checkpoints"]["20"]["matched"] == 0
    assert high_band["checkpoints"]["20"]["total"] == 1
    # neither fixture sets pct_change_5/10/15/30 — those checkpoints have 0 total
    assert low_band["checkpoints"]["5"]["total"] == 0


def test_intensity_analysis_requires_pattern_param(client):
    instrument_id = _register(client)
    resp = client.get(
        "/api/activities/intensity-analysis",
        query_string={"instrument_id": instrument_id, "timeframes": "1min", "from": "2026-01-01", "to": "2026-01-28"},
    )
    assert resp.status_code == 400


def test_intensity_analysis_unknown_instrument_returns_404(client):
    resp = client.get(
        "/api/activities/intensity-analysis",
        query_string={
            "instrument_id": 999999, "timeframes": "1min", "from": "2026-01-01",
            "to": "2026-01-28", "pattern": "hammer",
        },
    )
    assert resp.status_code == 404


def test_intensity_analysis_returns_empty_bands_when_nothing_analyzed(client):
    instrument_id = _register(client)
    resp = client.get(
        "/api/activities/intensity-analysis",
        query_string={
            "instrument_id": instrument_id, "timeframes": "1min", "from": "2026-01-01",
            "to": "2026-01-28", "pattern": "hammer",
        },
    )
    assert resp.status_code == 200
    assert resp.get_json()["bands"] == []


def test_intensity_analysis_includes_indicator_states(client):
    instrument_id = _register(client)
    _seed(client, instrument_id)
    with client.application.extensions["db_session_factory"]() as session:
        session.query(PatternOutcome).filter_by(instrument_id=instrument_id, detected_ts=_dt(1)).update({
            "entry_rsi": 25.0, "entry_rsi_state": "oversold",
            "entry_macd_line": 0.5, "entry_macd_signal": 1.0, "entry_macd_state": "bearish",
            "entry_stoch_k": 10.0, "entry_stoch_state": "oversold",
        })
        session.query(PatternOutcome).filter_by(instrument_id=instrument_id, detected_ts=_dt(2)).update({
            "entry_rsi": 75.0, "entry_rsi_state": "overbought",
            "entry_macd_line": 1.5, "entry_macd_signal": 1.0, "entry_macd_state": "bullish",
            "entry_stoch_k": 90.0, "entry_stoch_state": "overbought",
        })
        session.commit()

    resp = client.get(
        "/api/activities/intensity-analysis",
        query_string={
            "instrument_id": instrument_id, "timeframes": "1min", "from": "2026-01-01",
            "to": "2026-01-28", "pattern": "hammer",
        },
    )
    assert resp.status_code == 200
    states = resp.get_json()["indicator_states"]
    assert [b["label"] for b in states["rsi"]["state"]] == ["oversold", "overbought"]
    assert [b["label"] for b in states["macd"]["state"]] == ["bearish", "bullish"]
    assert [b["label"] for b in states["stoch"]["state"]] == ["oversold", "overbought"]
    oversold_rsi = states["rsi"]["state"][0]
    assert oversold_rsi["count"] == 1
    assert oversold_rsi["checkpoints"]["20"]["matched"] == 1  # pct_change_20=0.02 for detected_ts=_dt(1)


def test_intensity_analysis_indicator_states_empty_without_states(client):
    instrument_id = _register(client)
    _seed(client, instrument_id)  # no entry_*_state set on either PatternOutcome row

    resp = client.get(
        "/api/activities/intensity-analysis",
        query_string={
            "instrument_id": instrument_id, "timeframes": "1min", "from": "2026-01-01",
            "to": "2026-01-28", "pattern": "hammer",
        },
    )
    assert resp.status_code == 200
    empty = {"state": [], "trend": []}
    assert resp.get_json()["indicator_states"] == {"rsi": empty, "macd": empty, "stoch": empty}
