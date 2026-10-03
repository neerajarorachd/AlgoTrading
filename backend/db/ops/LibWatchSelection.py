"""InstrumentWatchExclusion reads/writes -- per-instrument "what to watch"
on Market Watch. See InstrumentWatchExclusion's own docstring in
db/models.py: this table stores only EXCLUSIONS, so "watches everything"
is the zero-row default, not something that needs seeding."""
from __future__ import annotations

from typing import Dict, List, Sequence

from db.models import InstrumentWatchExclusion


def get_exclusions(session, instrument_id: int) -> List[Dict[str, str]]:
    rows = session.query(InstrumentWatchExclusion).filter_by(instrument_id=instrument_id).all()
    return [{"item_type": r.item_type, "item_code": r.item_code} for r in rows]


def get_exclusions_for_instruments(session, instrument_ids: Sequence[int]) -> Dict[int, List[Dict[str, str]]]:
    """Bulk form of get_exclusions -- the Market Watch table needs every
    visible row's exclusions in one query, not one query per row."""
    rows = (
        session.query(InstrumentWatchExclusion)
        .filter(InstrumentWatchExclusion.instrument_id.in_(list(instrument_ids)))
        .all()
    )
    out: Dict[int, List[Dict[str, str]]] = {iid: [] for iid in instrument_ids}
    for r in rows:
        out.setdefault(r.instrument_id, []).append({"item_type": r.item_type, "item_code": r.item_code})
    return out


def set_exclusions(session, instrument_id: int, exclusions: List[Dict[str, str]]) -> None:
    """Replaces the full exclusion set for one instrument -- same
    replace-the-whole-list semantics as a watchlist's members or a
    Strategy's pattern_filter, simpler than diffing adds/removes."""
    session.query(InstrumentWatchExclusion).filter_by(instrument_id=instrument_id).delete()
    seen = set()
    for item in exclusions:
        key = (item["item_type"], item["item_code"])
        if key in seen:  # a duplicate in the request body -- same row, don't violate the unique constraint
            continue
        seen.add(key)
        session.add(InstrumentWatchExclusion(
            instrument_id=instrument_id, item_type=item["item_type"], item_code=item["item_code"],
        ))
