from __future__ import annotations

import os
from typing import Dict

DHAN_ACCOUNT_ID = "DHAN_NEERAJ"
DHAN_TOKEN_TYPE_FEED = 2   # FIXED2, per Trading's TokenType enum — AlgoTrading's live WS feed
DHAN_TOKEN_TYPE_REST = 3   # FIXED3 — AlgoTrading's REST calls (quotes, instrument resolution)


def cors_origins() -> list[str]:
    raw = os.environ.get("CORS_ORIGINS", "http://localhost:5173")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def load_dhan_tokens(session) -> Dict[int, str]:
    """Reads AlgoTrading's dedicated Dhan tokens (FIXED2/FIXED3), mirrored into
    SQL Server from Trading's own refresh cycle. Returns {token_type: access_token}
    for whichever of DHAN_TOKEN_TYPE_FEED/_REST are present — callers must handle
    a missing key (e.g. before the first mirror/backfill has run) themselves.
    """
    from db.ops import LibBrokerTokens

    return LibBrokerTokens.load_active_tokens(session, DHAN_ACCOUNT_ID, [DHAN_TOKEN_TYPE_FEED, DHAN_TOKEN_TYPE_REST])
