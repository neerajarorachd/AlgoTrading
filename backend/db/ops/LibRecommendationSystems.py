"""RecommendationSystem reads/writes — see db/models.py's RecommendationSystem
for the full rationale (RS1, the AM1-is-internal-to-RS1 relationship, the
override-vs-global-SystemSetting fallback). Pure persistence layer, same
one-way layering as LibSystemSettings.py/LibRecommendations.py:
recommendation_engine.py reads through get_by_code/resolve_settings and
calls INTO this module, never the reverse."""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from db.models import RecommendationSystem
from db.session import session_scope


def get_by_code(session, code: str) -> Optional[RecommendationSystem]:
    return session.query(RecommendationSystem).filter_by(code=code).one_or_none()


def get_by_id(session, recommendation_system_id: int) -> Optional[RecommendationSystem]:
    return session.query(RecommendationSystem).filter_by(id=recommendation_system_id).one_or_none()


def list_all(session, active_only: bool = False) -> List[RecommendationSystem]:
    query = session.query(RecommendationSystem)
    if active_only:
        query = query.filter_by(is_active=True)
    return query.order_by(RecommendationSystem.code).all()


def seed_defaults(session_factory, defaults: Sequence[dict]) -> None:
    """Idempotent: inserts a row for any `code` not already present. Leaves
    an already-existing row entirely untouched — watchlist_id/timeframe/
    top_n_per_run/*_override are meant to be tuned (presumably via a future
    admin UI) and must never be silently reset back to the seed defaults on
    the next app restart, same idempotency contract as
    LibSystemSettings.seed_defaults. Call once at app startup."""
    with session_scope(session_factory) as session:
        for fields in defaults:
            existing = session.query(RecommendationSystem).filter_by(code=fields["code"]).one_or_none()
            if existing is None:
                session.add(RecommendationSystem(**fields))


def ensure_parent_strategies(session_factory) -> None:
    """Idempotent: every RS gets a PARENT Strategy (RecommendationSystem.
    strategy_id) that its pattern rules hang off as children
    (Strategy.parent_id) -- "RS1 is the parent, the rest are child
    strategies" (user, 2026-09-19). An RS that already has one is untouched.
    Call once at app startup, after seed_defaults."""
    from db.ops import LibStrategies

    with session_scope(session_factory) as session:
        for rs in session.query(RecommendationSystem).filter(RecommendationSystem.strategy_id.is_(None)).all():
            rs.strategy_id = LibStrategies.create(session, {
                "name": f"{rs.code} (parent)", "strategy_type": "recommendation_parent",
                "description": f"Parent of {rs.code}'s pattern rules",
            }, None)


def resolve_settings(rs: Optional[RecommendationSystem], global_settings: Dict[str, float]) -> Dict[str, float]:
    """Merges one RecommendationSystem's *_override columns over the global
    SystemSetting dict — None on an override means "inherit the global
    value" (same fallback idiom the rest of this schema already uses).
    `rs=None` (no RS context, e.g. a direct/manual generate_recommendations
    call) returns global_settings unchanged. Keys match SystemSetting's own
    key names so recommendation_engine._setting() needs no translation."""
    if rs is None:
        return dict(global_settings)
    resolved = dict(global_settings)
    overrides = {
        "recommendation_win_pct_threshold": rs.win_pct_threshold_override,
        "recommendation_min_sample_size": rs.min_sample_size_override,
        "recommendation_wilson_confidence": rs.wilson_confidence_override,
        "recommendation_expiry_candles": rs.expiry_candles_override,
        "recommendation_analysis_lookback_days": rs.analysis_lookback_days_override,
        "recommendation_max_regenerations": rs.max_regenerations_override,
        "recommendation_analysis_start_time_minutes": rs.analysis_start_time_minutes_override,
    }
    for key, value in overrides.items():
        if value is not None:
            resolved[key] = float(value)
    return resolved
