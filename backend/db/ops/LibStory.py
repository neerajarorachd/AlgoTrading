"""Market story DB calls (P2): write the engine's story at the 15-min flush,
move a finished day to the archive at EOD, read a day back.

The writer gets two kinds of scope per (trading_day, timeframe):
  full   - every row of these instruments for that day is replaced (the
           first flush of a day after a start / restart, which also
           de-duplicates anything a crash left behind);
  open   - only the still-open rows are replaced (every later flush:
           closed runs never change, so they are inserted once).
Deletes are batched per scope group, so a flush is a few statements, not
one per stock (the SQL Server tunnel costs ~84 ms a round trip)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import Boolean, delete, func, insert, literal, select

from db.models_story import STORY_COLUMNS, MarketStoryArchive, MarketStoryToday
from db.session import session_scope

_CHUNK = 500  # instrument ids per IN (...) list

Scope = Tuple[date, str, int]  # (trading_day, timeframe, instrument_id)


def _grouped(scopes: Iterable[Scope]) -> Dict[Tuple[date, str], List[int]]:
    g: Dict[Tuple[date, str], List[int]] = defaultdict(list)
    for d, tf, iid in scopes:
        g[(d, tf)].append(iid)
    return g


def write_story(session_factory, full: Iterable[Scope], open_only: Iterable[Scope], rows: List[dict]) -> None:
    """One transaction: clear the scopes, then insert `rows` (dicts with
    STORY_COLUMNS keys)."""
    T = MarketStoryToday
    with session_scope(session_factory) as session:
        for only_open, scopes in ((False, full), (True, open_only)):
            for (d, tf), ids in _grouped(scopes).items():
                for k in range(0, len(ids), _CHUNK):
                    q = delete(T).where(T.trading_day == d, T.timeframe == tf, T.instrument_id.in_(ids[k:k + _CHUNK]))
                    if only_open:
                        q = q.where(T.is_open)  # bare column: .is_(True) is invalid T-SQL
                    session.execute(q)
        if rows:
            session.execute(insert(T), rows)


def archive_through(session_factory, day: date) -> int:
    """Move every row with trading_day <= day from today to archive (open
    runs end at the day's close, so they are archived as closed). Safe to
    re-run. Returns rows moved."""
    T, A = MarketStoryToday, MarketStoryArchive
    cols = [c for c in STORY_COLUMNS if c != "is_open"]
    with session_scope(session_factory) as session:
        n = session.execute(select(func.count()).select_from(T).where(T.trading_day <= day)).scalar() or 0
        if not n:
            return 0
        src = select(*[getattr(T, c) for c in cols], literal(False, Boolean())).where(T.trading_day <= day)
        session.execute(insert(A).from_select(cols + ["is_open"], src))
        session.execute(delete(T).where(T.trading_day <= day))
        return int(n)


def last_archived_day(session_factory) -> Optional[date]:
    A = MarketStoryArchive
    with session_scope(session_factory) as session:
        return session.execute(select(func.max(A.trading_day))).scalar()


def get_day(session, instrument_id: int, timeframe: str, day: date) -> List[dict]:
    """One instrument's story for one day, oldest first: the today table,
    else the archive."""
    for M in (MarketStoryToday, MarketStoryArchive):
        rows = session.execute(
            select(M).where(M.instrument_id == instrument_id, M.timeframe == timeframe, M.trading_day == day)
            .order_by(M.start_ts, M.subject)
        ).scalars().all()
        if rows:
            return [{c: getattr(r, c) for c in STORY_COLUMNS} for r in rows]
    return []
