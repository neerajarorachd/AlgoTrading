"""Market story (plan: docs/market_story_plan.md).

The walkthrough's high-level record: one row per *state run* (BB width
expanding for 10 candles, price above VWAP for 25, ...) instead of one event
per candle, plus swing labels (HH/LH/HL/LL with leg % and swing ratio) and
the setup snapshot the prediction unit reads.

P0 (2026-10-30): pure modules only, no database and no engine wiring. The
same code is run offline over the research data and, from P2, live.
"""
