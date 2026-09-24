from datetime import datetime, timedelta, timezone

from db.models import GuidingScenario, Recommendation, SubscribedSymbol

SYMBOL = "RELIANCE"
TIMEFRAME = "3min"


def _dt(days_ago=0):
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


def _register(session_factory, symbol=SYMBOL) -> int:
    with session_factory() as session:
        row = SubscribedSymbol(
            symbol=symbol, exchange="NSE", segment="EQUITY", exchange_segment="NSE_EQ",
            security_id=f"SEC-{symbol}", previous_close=100.0,
        )
        session.add(row)
        session.commit()
        return row.id


def _recommendation(instrument_id, status="queued", wilson_score=0.6, pattern="hammer", expires_in_minutes=30):
    now = _dt()
    return Recommendation(
        source="pattern_outcome_bands", instrument_id=instrument_id, timeframe=TIMEFRAME,
        pattern=pattern, direction="bull", entry_price=101.0, detected_ts=now,
        band_kind="intensity", band_label="guiding_scenario", band_min=0.5, band_max=1.5,
        qualifying_checkpoint=20, sample_count=10, win_count=8, win_pct=0.8, wilson_score=wilson_score,
        win_pct_threshold_applied=0.70, min_sample_size_applied=7, wilson_confidence_applied=0.95,
        status=status, generated_at=now, expires_at=now + timedelta(minutes=expires_in_minutes),
        window_kind="2y",
    )


def test_get_pending_returns_queued_recommendations(session_factory, client):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_recommendation(instrument_id))
        session.commit()

    resp = client.get("/api/recommendations/pending", query_string={"instrument_id": instrument_id})
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body) == 1
    row = body[0]
    assert row["pattern"] == "hammer"
    assert row["status"] == "queued"
    assert row["window_kind"] == "2y"
    assert row["win_pct"] == 0.8
    assert row["wilson_score"] == 0.6


def test_get_pending_excludes_expired_and_non_queued(session_factory, client):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_recommendation(instrument_id, status="rejected"))
        session.add(_recommendation(instrument_id, status="queued", expires_in_minutes=-5))  # already expired
        session.commit()

    resp = client.get("/api/recommendations/pending", query_string={"instrument_id": instrument_id})
    assert resp.get_json() == []


def test_get_best_preview_ranks_by_wilson_score(session_factory, client):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_recommendation(instrument_id, wilson_score=0.3, pattern="hammer"))
        session.add(_recommendation(instrument_id, wilson_score=0.9, pattern="triple_top"))
        session.commit()

    resp = client.get("/api/recommendations/best-preview", query_string={"slots": 1, "instrument_id": instrument_id})
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body) == 1
    assert body[0]["pattern"] == "triple_top"  # higher wilson_score wins


def test_best_preview_never_mutates_status(session_factory, client):
    """Dry-run only -- calling this repeatedly must never call mark_selected."""
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_recommendation(instrument_id))
        session.commit()

    client.get("/api/recommendations/best-preview", query_string={"slots": 1, "instrument_id": instrument_id})
    client.get("/api/recommendations/best-preview", query_string={"slots": 1, "instrument_id": instrument_id})

    with session_factory() as session:
        row = session.query(Recommendation).filter_by(instrument_id=instrument_id).one()
    assert row.status == "queued"
