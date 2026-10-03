"""One-off, idempotent backfill for InstrumentActivityDailyCount (2026-10-01).

Going forward, this table is kept current automatically (db/ops/LibActivities.py's
persist_bulk/persist_one increment it in the same transaction as the activity
insert itself). This script only reconciles PAST instrument_activity rows —
anything detected before this feature existed, which would otherwise show as
0 in the Market Watch badges until new events happen today. Safe to re-run;
it recomputes each (instrument, day) bucket from scratch rather than adding
on top, so re-running after a partial run or a real discrepancy just
corrects it, it never double-counts.

    .venv/Scripts/python.exe backend/scripts/backfill_activity_daily_counts.py
"""
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from db.models import Base, InstrumentActivity, InstrumentActivityDailyCount
from db.ops.LibActivities import _DAILY_COUNT_CATEGORIES, _trading_date
from db.session import build_engine, build_session_factory


def main() -> None:
    engine = build_engine()
    Base.metadata.create_all(engine)  # creates instrument_activity_daily_counts if this is the first run
    session_factory = build_session_factory(engine)

    with session_factory() as session:
        rows = (
            session.query(InstrumentActivity.instrument_id, InstrumentActivity.ts, InstrumentActivity.activity_type)
            .filter(InstrumentActivity.activity_type.in_(_DAILY_COUNT_CATEGORIES))
            .all()
        )
        print(f"Scanning {len(rows)} instrument_activity rows...")

        totals = defaultdict(lambda: {"candle_pattern": 0, "indicator": 0})
        for instrument_id, ts, activity_type in rows:
            totals[(instrument_id, _trading_date(ts))][activity_type] += 1

        existing = {(r.instrument_id, r.trading_date): r for r in session.query(InstrumentActivityDailyCount).all()}
        for (instrument_id, trading_date), counts in totals.items():
            row = existing.get((instrument_id, trading_date))
            if row is None:
                row = InstrumentActivityDailyCount(instrument_id=instrument_id, trading_date=trading_date)
                session.add(row)
            row.candle_pattern_count = counts["candle_pattern"]
            row.indicator_count = counts["indicator"]
        session.commit()
        print(f"Reconciled {len(totals)} (instrument, day) buckets.")


if __name__ == "__main__":
    main()
