"""Single place every AlgoTrading DB call lives — callers import from here
(`from db.ops import LibSymbols` etc.) instead of writing their own
`session.query(...)`. One library per concern, `Lib`-prefixed to match
the Trading project's own naming convention (LibDBBase, LibDBOps,
LibBackTest, ...) — but kept as separate small files per table/concern
rather than one giant module, since LibDBOps.py's own ~2100-line,
everything-in-one-file shape is exactly what made it hard to navigate.

Every function here takes an already-open `session` unless its own
docstring says otherwise: LibCandles.persist_bulk/persist_one and
LibPredictions.insert_bulk/insert_one/update_outcomes_bulk take a
`session_factory` instead and own their own transaction boundary, because
they need multi-statement retry/fallback behavior a single passed-in
session can't safely provide (see each one's own docstring). Session
lifecycle otherwise stays the caller's decision — a Flask route passes its
own request-scoped `g.db_session`, everything else opens `session_scope`
around the call — this module only knows how to read/write once handed a
session, never how to obtain one.
"""
from . import (
    LibActivities, LibBrokerTokens, LibCandleIndicators, LibCandles,
    LibPatternOutcomes, LibPredictions, LibSettings, LibSymbols,
)

__all__ = [
    "LibActivities", "LibBrokerTokens", "LibCandleIndicators", "LibCandles",
    "LibPatternOutcomes", "LibPredictions", "LibSettings", "LibSymbols",
]
