from db.models import SubscribedSymbol, Watchlist
from db.ops import LibWatchlists


def _add_symbol(session_factory, symbol, exchange_segment="NSE_EQ"):
    with session_factory() as session:
        row = SubscribedSymbol(
            symbol=symbol, exchange="NSE", segment="EQUITY", exchange_segment=exchange_segment,
            security_id=f"SEC-{symbol}", previous_close=100.0, active=True,
        )
        session.add(row)
        session.commit()
        return row.id


def test_create_watchlist_with_initial_members(session_factory):
    reliance_id = _add_symbol(session_factory, "RELIANCE")
    tcs_id = _add_symbol(session_factory, "TCS")

    with session_factory() as session:
        watchlist_id = LibWatchlists.create(
            session, {"name": "My Picks", "description": "test"}, [reliance_id, tcs_id],
        )
        session.commit()

    with session_factory() as session:
        row = LibWatchlists.get_by_id(session, watchlist_id)
        members = LibWatchlists.get_members(session, watchlist_id)
    assert row.name == "My Picks"
    assert {s.symbol for _, s in members} == {"RELIANCE", "TCS"}


def test_get_all_only_returns_active_watchlists(session_factory):
    with session_factory() as session:
        active_id = LibWatchlists.create(session, {"name": "Active"}, [])
        removed_id = LibWatchlists.create(session, {"name": "Removed"}, [])
        session.commit()

    with session_factory() as session:
        LibWatchlists.delete(session, removed_id)
        session.commit()

    with session_factory() as session:
        rows = LibWatchlists.get_all(session)
    assert [r.name for r in rows] == ["Active"]


def test_add_member_is_idempotent_and_reactivates_a_removed_row(session_factory):
    reliance_id = _add_symbol(session_factory, "RELIANCE")
    with session_factory() as session:
        watchlist_id = LibWatchlists.create(session, {"name": "WL"}, [reliance_id])
        session.commit()

    with session_factory() as session:
        removed = LibWatchlists.remove_member(session, watchlist_id, reliance_id)
        session.commit()
    assert removed is True

    with session_factory() as session:
        members = LibWatchlists.get_members(session, watchlist_id)
    assert members == []  # soft-removed, not returned as active

    with session_factory() as session:
        LibWatchlists.add_member(session, watchlist_id, reliance_id)  # re-add, must not violate unique constraint
        session.commit()

    with session_factory() as session:
        members = LibWatchlists.get_members(session, watchlist_id)
        count = session.query(Watchlist).count()
    assert [s.symbol for _, s in members] == ["RELIANCE"]
    assert count == 1  # still one watchlist row, membership row was reactivated not duplicated


def test_remove_member_returns_false_when_not_an_active_member(session_factory):
    with session_factory() as session:
        watchlist_id = LibWatchlists.create(session, {"name": "WL"}, [])
        session.commit()

    with session_factory() as session:
        removed = LibWatchlists.remove_member(session, watchlist_id, 9999)
    assert removed is False


def test_get_member_counts_across_multiple_watchlists(session_factory):
    reliance_id = _add_symbol(session_factory, "RELIANCE")
    tcs_id = _add_symbol(session_factory, "TCS")

    with session_factory() as session:
        wl1 = LibWatchlists.create(session, {"name": "WL1"}, [reliance_id, tcs_id])
        wl2 = LibWatchlists.create(session, {"name": "WL2"}, [reliance_id])
        session.commit()

    with session_factory() as session:
        counts = LibWatchlists.get_member_counts(session, [wl1, wl2])
    assert counts == {wl1: 2, wl2: 1}


def test_update_fields_changes_name_and_description(session_factory):
    with session_factory() as session:
        watchlist_id = LibWatchlists.create(session, {"name": "Original"}, [])
        session.commit()

    with session_factory() as session:
        LibWatchlists.update_fields(session, watchlist_id, {"name": "Renamed", "description": "updated"})
        session.commit()

    with session_factory() as session:
        row = LibWatchlists.get_by_id(session, watchlist_id)
    assert row.name == "Renamed"
    assert row.description == "updated"


def test_delete_is_soft_and_idempotent(session_factory):
    with session_factory() as session:
        watchlist_id = LibWatchlists.create(session, {"name": "ToDelete"}, [])
        session.commit()

    with session_factory() as session:
        first = LibWatchlists.delete(session, watchlist_id)
        session.commit()
    with session_factory() as session:
        second = LibWatchlists.delete(session, watchlist_id)  # idempotent, still succeeds
        row = LibWatchlists.get_by_id(session, watchlist_id)
    assert first is True
    assert second is True
    assert row.active is False


def test_delete_unknown_watchlist_returns_false(session_factory):
    with session_factory() as session:
        assert LibWatchlists.delete(session, 9999) is False
