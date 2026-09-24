"""Per-pattern order defaults (RecommendationSystemPatternDefault) turned into
what a recommendation carries: a suggested quantity and direction-aware SL /
target prices. Sizing here is the DEFAULT-size cap only; the full
Min(strategy funds, default size, broker funds, trade-count) rule
(strategy_accounts_ledger_plan) is applied when the Trade popup / accounts
exist. "available_fund" sizing uses the RS's fallback_capital until then.
"""
from __future__ import annotations

from typing import Optional

from db.models import RecommendationSystem, RecommendationSystemPatternDefault
from db.session import session_scope

FALLBACK_PATTERN = "*"
# Visible placeholders for the mandatory fallback row -- meant to be edited.
FALLBACK_SEED = {"size_mode": "max_qty", "max_qty": 1, "fund_pct": None, "sl_pct": 0.40, "target_pct": 0.80}


def ensure_fallback_defaults(session_factory) -> None:
    """Idempotent: every RS gets its mandatory '(all others)' row."""
    with session_scope(session_factory) as session:
        for rs in session.query(RecommendationSystem).all():
            exists = session.query(RecommendationSystemPatternDefault).filter_by(
                recommendation_system_id=rs.id, pattern=FALLBACK_PATTERN).first()
            if exists is None:
                session.add(RecommendationSystemPatternDefault(
                    recommendation_system_id=rs.id, pattern=FALLBACK_PATTERN, **FALLBACK_SEED))


def find_default(session, recommendation_system_id: int, pattern: str):
    """The pattern's own row, else the '*' fallback, else None."""
    rows = {
        r.pattern: r for r in session.query(RecommendationSystemPatternDefault).filter(
            RecommendationSystemPatternDefault.recommendation_system_id == recommendation_system_id,
            RecommendationSystemPatternDefault.pattern.in_([pattern, FALLBACK_PATTERN]),
        )
    }
    return rows.get(pattern) or rows.get(FALLBACK_PATTERN)


def suggest_order(default, direction: str, entry_price: float,
                  fallback_capital: Optional[float]) -> dict:
    """{suggested_quantity, suggested_sl_price, suggested_target_price}.
    SL/target are direction-aware (bull: SL below, target above; bear: the
    reverse). Quantity is None when it can't be sized (available_fund mode
    with no capital to size against)."""
    sl_pct, tg_pct = float(default.sl_pct) / 100, float(default.target_pct) / 100
    sign = 1 if direction == "bull" else -1
    quantity = None
    if default.size_mode == "available_fund":
        if fallback_capital and default.fund_pct and entry_price > 0:
            quantity = int(float(fallback_capital) * float(default.fund_pct) / 100 // entry_price)
    elif default.max_qty:
        quantity = int(default.max_qty)
    return {
        "suggested_quantity": quantity,
        "suggested_sl_price": round(entry_price * (1 - sign * sl_pct), 4),
        "suggested_target_price": round(entry_price * (1 + sign * tg_pct), 4),
    }


def suggestion_for(session_factory, recommendation_system_id: Optional[int], pattern: str,
                   direction: str, entry_price: float) -> dict:
    """Suggested fields for a new recommendation ({} when the RS has no
    defaults row at all). Never raises into the live path."""
    if recommendation_system_id is None:
        return {}
    with session_scope(session_factory) as session:
        default = find_default(session, recommendation_system_id, pattern)
        if default is None:
            return {}
        rs = session.query(RecommendationSystem).filter_by(id=recommendation_system_id).one_or_none()
        return suggest_order(default, direction, entry_price, rs.fallback_capital if rs else None)
