"""One-off analysis: grades every Recommendation detected today against real
subsequent candles_today price action, using the SAME win rule already used
throughout the system (LibPatternOutcomes._band_stats: a checkpoint "matches"
when price simply moved in the predicted direction, sign-only, no magnitude
threshold) — so results are directly comparable to the win_pct/wilson_score
already stored on each row.

Not part of the app; run manually:
    .venv/Scripts/python.exe backend/scripts/grade_todays_recommendations.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from db.models import CandleToday, Recommendation, SubscribedSymbol
from db.session import build_engine, build_session_factory

TODAY_UTC_START = datetime(2026, 9, 17, 0, 0, 0)


def main() -> None:
    engine = build_engine()
    session_factory = build_session_factory(engine)

    with session_factory() as session:
        recs = (
            session.query(Recommendation)
            .filter(Recommendation.detected_ts >= TODAY_UTC_START)
            .order_by(Recommendation.detected_ts.asc())
            .all()
        )
        symbols_by_id = {row.id: row for row in session.query(SubscribedSymbol).all()}

        rows_by_key: dict[tuple, list] = {}
        for r in recs:
            symbol = symbols_by_id[r.instrument_id]
            key = (r.instrument_id, r.timeframe)
            if key not in rows_by_key:
                candles = (
                    session.query(CandleToday)
                    .filter_by(symbol=symbol.symbol, exchange_segment=symbol.exchange_segment, timeframe=r.timeframe)
                    .order_by(CandleToday.ts.asc())
                    .all()
                )
                rows_by_key[key] = candles

        wins = losses = pending = 0
        print(f"{'Symbol':<12}{'TF':<6}{'Pattern':<20}{'Dir':<5}{'Detected(UTC)':<18}"
              f"{'Entry':>9}{'AtCkpt':>9}{'Pct':>8}  {'Result':<8}{'Wilson':>7}")
        print("-" * 110)
        for r in recs:
            symbol = symbols_by_id[r.instrument_id]
            candles = rows_by_key[(r.instrument_id, r.timeframe)]
            after = [c for c in candles if c.ts > r.detected_ts]
            n = r.qualifying_checkpoint or 5
            entry = float(r.entry_price)

            if len(after) >= n:
                at_ckpt = float(after[n - 1].close_price)
                pct = (at_ckpt - entry) / entry
                is_win = (r.direction == "bull" and pct > 0) or (r.direction == "bear" and pct < 0)
                result = "WIN" if is_win else "LOSS"
                if is_win:
                    wins += 1
                else:
                    losses += 1
            elif after:
                at_ckpt = float(after[-1].close_price)
                pct = (at_ckpt - entry) / entry
                result = f"partial({len(after)}/{n})"
                pending += 1
            else:
                at_ckpt = entry
                pct = 0.0
                result = "no data yet"
                pending += 1

            print(
                f"{symbol.symbol:<12}{r.timeframe:<6}{r.pattern:<20}{r.direction:<5}"
                f"{str(r.detected_ts):<18}{entry:>9.2f}{at_ckpt:>9.2f}{pct*100:>7.2f}%  "
                f"{result:<8}{float(r.wilson_score):>7.3f}"
            )

        total_graded = wins + losses
        print("-" * 110)
        print(f"Graded: {total_graded} ({wins} win / {losses} loss"
              f" = {wins/total_graded*100:.1f}% win rate)" if total_graded else "Graded: 0")
        print(f"Still pending/insufficient data: {pending}")


if __name__ == "__main__":
    main()
