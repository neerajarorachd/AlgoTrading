"""Reuses LibPatternOutcomes.intensity_banded_analysis (already built,
already tested 2026-09-15) to show each pattern's Low/Mid/High intensity
tercile breakdown for HINDCOPPER, 1min -- same checkpoint sign-agreement
stat as the plain checkpoint table, just split by how strong the pattern's
own formation was.

Run manually:
    .venv/Scripts/python.exe backend/scripts/hindcopper_intensity_bands.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from db.models import SubscribedSymbol
from db.ops import LibPatternOutcomes
from db.session import build_engine, build_session_factory
from prediction_tracker import BEARISH_PATTERNS, BULLISH_PATTERNS

SYMBOL = "HINDCOPPER"
TIMEFRAME = "1min"
LOOKBACK_DAYS = 730
BAND_LABELS = ["Low", "Mid", "High"]


def main() -> None:
    engine_db = build_engine()
    session_factory = build_session_factory(engine_db)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)

    with session_factory() as session:
        row = session.query(SubscribedSymbol).filter_by(symbol=SYMBOL).one()
        iid = row.id

        patterns = sorted(BULLISH_PATTERNS | BEARISH_PATTERNS)
        header = (f"{'Pattern':<28}{'Dir':<6}{'Band':<6}{'N':>6}  "
                   f"{'CP5':>7}{'CP10':>7}{'CP15':>7}{'CP20':>7}{'CP30':>7}")
        print(header)
        print("-" * len(header))
        for pattern in patterns:
            direction = "bull" if pattern in BULLISH_PATTERNS else "bear"
            bands = LibPatternOutcomes.intensity_banded_analysis(
                session, iid, [TIMEFRAME], start, end, pattern, bands=3)
            if not bands:
                print(f"{pattern:<28}{direction:<6}{'--':<6}{'no intensity data':>6}")
                continue
            for b in bands:
                label = BAND_LABELS[b["band_index"]] if b["band_index"] < len(BAND_LABELS) else str(b["band_index"])
                cps = b["checkpoints"]
                rates = []
                for n in (5, 10, 15, 20, 30):
                    pct = cps.get(n, {}).get("pct")
                    rates.append(f"{pct*100:6.1f}%" if pct is not None else "    n/a")
                print(f"{pattern:<28}{direction:<6}{label:<6}{b['count']:>6}  " + "".join(rates))
            print()


if __name__ == "__main__":
    main()
