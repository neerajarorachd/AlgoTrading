from db.ops import LibSettings


def test_set_value_inserts_a_new_row(session_factory):
    with session_factory() as session:
        LibSettings.set_value(session, "swing_lookback", 7, description="test")
        session.commit()

    with session_factory() as session:
        assert LibSettings.load_all(session) == {"swing_lookback": 7.0}


def test_set_value_updates_an_existing_row_without_requiring_a_description(session_factory):
    with session_factory() as session:
        LibSettings.set_value(session, "swing_lookback", 5, description="original")
        session.commit()
    with session_factory() as session:
        LibSettings.set_value(session, "swing_lookback", 9)  # no description this time
        session.commit()

    with session_factory() as session:
        assert LibSettings.load_all(session) == {"swing_lookback": 9.0}


def test_delete_removes_the_row_and_reports_whether_one_existed(session_factory):
    with session_factory() as session:
        LibSettings.set_value(session, "swing_lookback", 7)
        session.commit()

    with session_factory() as session:
        assert LibSettings.delete(session, "swing_lookback") is True
        session.commit()
    with session_factory() as session:
        assert LibSettings.load_all(session) == {}

    with session_factory() as session:
        assert LibSettings.delete(session, "swing_lookback") is False  # already gone
