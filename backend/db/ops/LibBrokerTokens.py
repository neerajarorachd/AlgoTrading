"""BrokerToken reads — AlgoTrading only ever reads this table (written by
the Trading project's own refresh cycle, mirrored in via
LibSQLServerTokenMirror.py and a one-time backfill; see BrokerToken's own
docstring in db/models.py)."""
from __future__ import annotations

from typing import Dict, Sequence

from db.models import BrokerToken


def load_active_tokens(session, account_id: str, token_types: Sequence[int]) -> Dict[int, str]:
    rows = (
        session.query(BrokerToken)
        .filter(
            BrokerToken.AccountID == account_id,
            BrokerToken.TokenType.in_(list(token_types)),
            BrokerToken.IsActive == True,  # noqa: E712 - SQLAlchemy filter, not a Python bool check
        )
        .all()
    )
    return {row.TokenType: row.AccessToken for row in rows}
