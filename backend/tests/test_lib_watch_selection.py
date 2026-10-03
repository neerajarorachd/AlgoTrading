from db.models import SubscribedSymbol
from db.ops import LibWatchSelection


def _register(session_factory, symbol="RELIANCE") -> int:
    with session_factory() as session:
        row = SubscribedSymbol(
            symbol=symbol, exchange="NSE", segment="EQUITY", exchange_segment="NSE_EQ",
            security_id=f"SEC-{symbol}", previous_close=100.0,
        )
        session.add(row)
        session.commit()
        return row.id


def test_a_fresh_instrument_has_no_exclusions(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        assert LibWatchSelection.get_exclusions(session, instrument_id) == []


def test_set_exclusions_then_get_returns_them(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        LibWatchSelection.set_exclusions(session, instrument_id, [
            {"item_type": "pattern", "item_code": "doji"},
            {"item_type": "strategy", "item_code": "11"},
        ])
        session.commit()

    with session_factory() as session:
        rows = LibWatchSelection.get_exclusions(session, instrument_id)
    assert sorted((r["item_type"], r["item_code"]) for r in rows) == [
        ("pattern", "doji"), ("strategy", "11"),
    ]


def test_set_exclusions_replaces_the_whole_set(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        LibWatchSelection.set_exclusions(session, instrument_id, [{"item_type": "pattern", "item_code": "doji"}])
        session.commit()
    with session_factory() as session:
        LibWatchSelection.set_exclusions(session, instrument_id, [{"item_type": "pattern", "item_code": "hammer"}])
        session.commit()

    with session_factory() as session:
        rows = LibWatchSelection.get_exclusions(session, instrument_id)
    assert [(r["item_type"], r["item_code"]) for r in rows] == [("pattern", "hammer")]


def test_set_exclusions_to_an_empty_list_clears_everything(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        LibWatchSelection.set_exclusions(session, instrument_id, [{"item_type": "pattern", "item_code": "doji"}])
        session.commit()
    with session_factory() as session:
        LibWatchSelection.set_exclusions(session, instrument_id, [])
        session.commit()

    with session_factory() as session:
        assert LibWatchSelection.get_exclusions(session, instrument_id) == []


def test_set_exclusions_ignores_a_duplicate_entry_in_the_same_request(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        LibWatchSelection.set_exclusions(session, instrument_id, [
            {"item_type": "pattern", "item_code": "doji"},
            {"item_type": "pattern", "item_code": "doji"},
        ])
        session.commit()

    with session_factory() as session:
        rows = LibWatchSelection.get_exclusions(session, instrument_id)
    assert len(rows) == 1


def test_exclusions_are_scoped_to_their_own_instrument(session_factory):
    a = _register(session_factory, symbol="RELIANCE")
    b = _register(session_factory, symbol="TCS")
    with session_factory() as session:
        LibWatchSelection.set_exclusions(session, a, [{"item_type": "pattern", "item_code": "doji"}])
        session.commit()

    with session_factory() as session:
        assert len(LibWatchSelection.get_exclusions(session, a)) == 1
        assert LibWatchSelection.get_exclusions(session, b) == []


def test_get_exclusions_for_instruments_is_bulk_and_fills_in_empty_instruments(session_factory):
    a = _register(session_factory, symbol="RELIANCE")
    b = _register(session_factory, symbol="TCS")
    with session_factory() as session:
        LibWatchSelection.set_exclusions(session, a, [{"item_type": "pattern", "item_code": "doji"}])
        session.commit()

    with session_factory() as session:
        result = LibWatchSelection.get_exclusions_for_instruments(session, [a, b])
    assert [(r["item_type"], r["item_code"]) for r in result[a]] == [("pattern", "doji")]
    assert result[b] == []
