"""Shared loader for the market story P0 research (plan: docs/market_story_plan.md).

Data: the same 13 stocks x 2 years of 3/5-min candles as the cross-widening
research (hist_3m_5m.pkl). Code under test: the story branch's backend/story.
"""
import pickle
import sys

STORY_BACKEND = "D:/AI Trading/AlgoTrading-story/backend"
sys.path.insert(0, STORY_BACKEND)

from story.indicator_rows import rows_from_candles  # noqa: E402
from story.setup import snapshot  # noqa: E402
from story.swings import SwingTracker  # noqa: E402
from story.tracker import StoryTracker  # noqa: E402

OLD = "D:/AI Trading/research/cross_widening_2026-10-27"
HERE = "D:/AI Trading/research/market_story_2026-10-30"
D = pickle.load(open(OLD + "/hist_3m_5m.pkl", "rb"))
SYMBOLS = sorted(D)
SEVEN = {"ASHOKLEY", "BHEL", "CUB", "FINCABLES", "HINDCOPPER", "NATIONALUM", "RELIANCE"}
IST = 19800
Y2_START = 1758067200  # 2025-09-17, same year split as the cross-widening work


def candles(sym, tf):
    return (D[sym].get(tf) or {}).get("candles") or []


def day(ts):
    return (ts + IST) // 86400


def minute_of_day(ts):
    return ((ts + IST) % 86400) // 60  # 555 = 09:15 IST


def run_tracker(rows, settings=None, swing=None, keep_snaps=False):
    """Run one StoryTracker over rows. Returns (closed runs, snapshots per
    candle or None, swings)."""
    t = StoryTracker(settings=settings, swing_tracker=SwingTracker(**(swing or {})))
    runs, snaps, swings = [], [] if keep_snaps else None, []
    for r in rows:
        runs += t.on_row(r)
        swings += t.new_swings
        if keep_snaps:
            snaps.append(snapshot(t.open_runs()))
    runs += t.close_all()
    return runs, snaps, swings
