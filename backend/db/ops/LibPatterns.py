"""Pattern reads/writes (ALGO-31) -- see db/models.py's Pattern.

System patterns (origin "system") are seeded from patterns_catalog.py and are
read-only through this module's update/replace functions: a user changes a
COPY (copy_pattern). Every formula is validated with the condition language's
own validator before it is stored, so a stored pattern always evaluates."""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from condition_evaluator import KNOWN_CONTEXT_NAMES, validate_condition
from db.models import Pattern, PatternCondition, PatternStockWeight
from db.session import session_scope

PATTERN_TYPES = ("bullish", "bearish", "caution", "avoid")
PATTERN_CONTEXT_NAMES = tuple(KNOWN_CONTEXT_NAMES) + ("lookback",)
EDITABLE_FIELDS = (
    "name", "description", "pattern_type", "category", "enabled", "lookback", "cooldown",
    "active_for", "manual_weight", "use_manual_weight",
)


class PatternError(ValueError):
    """Invalid pattern data, or an attempt to edit a system pattern."""


def _num(v):
    return float(v) if v is not None else None


def to_dict(p: Pattern, conditions: Sequence[PatternCondition]) -> dict:
    return {
        "id": p.id, "key": p.key, "name": p.name, "description": p.description,
        "pattern_type": p.pattern_type, "category": p.category, "origin": p.origin,
        "owner_id": p.owner_id, "parent_id": p.parent_id, "enabled": p.enabled,
        "lookback": p.lookback, "cooldown": p.cooldown, "active_for": p.active_for,
        "manual_weight": _num(p.manual_weight), "backtest_weight": _num(p.backtest_weight),
        "use_manual_weight": p.use_manual_weight, "weight": default_weight(p),
        "win_rate": _num(p.win_rate), "avg_move_pct": _num(p.avg_move_pct), "sample_size": p.sample_size,
        "last_backtested_at": p.last_backtested_at.isoformat() + "Z" if p.last_backtested_at else None,
        "conditions": [{"id": c.id, "formula": c.formula, "note": c.note, "weight": _num(c.weight)}
                       for c in sorted(conditions, key=lambda c: c.position)],
    }


def default_weight(p: Pattern) -> float:
    """The pattern's own weight: manual, or the backtest one when chosen and present."""
    if not p.use_manual_weight and p.backtest_weight is not None:
        return float(p.backtest_weight)
    return float(p.manual_weight)


def effective_weight(p: Pattern, instrument_id: Optional[int], stock_weights: Dict[tuple, float],
                     use_per_stock: bool) -> float:
    """Per-stock weight when the setting is on and one exists for this
    stock; otherwise the pattern's default -- always the fallback."""
    if use_per_stock and instrument_id is not None:
        w = stock_weights.get((p.id, instrument_id))
        if w is not None:
            return float(w)
    return default_weight(p)


def validate(data: dict, partial: bool = False) -> None:
    """Raises PatternError with a message a person can act on."""
    if not partial or "key" in data:
        key = (data.get("key") or "").strip()
        if not key or not key.replace("_", "").isalnum() or not key[0].isalpha():
            raise PatternError("key must start with a letter and use only letters, digits and _ (e.g. bullish_momentum)")
    if not partial or "name" in data:
        if not (data.get("name") or "").strip():
            raise PatternError("name is required")
    if not partial or "pattern_type" in data:
        if data.get("pattern_type") not in PATTERN_TYPES:
            raise PatternError(f"pattern_type must be one of {', '.join(PATTERN_TYPES)}")
    for field in ("lookback", "cooldown", "active_for"):
        if field in data and (not isinstance(data[field], int) or data[field] < 0 or (field != "cooldown" and data[field] < 1)):
            raise PatternError(f"{field} must be a whole number (lookback and active_for at least 1)")
    if "manual_weight" in data:
        w = data["manual_weight"]
        if not isinstance(w, (int, float)) or not 0 <= w <= 5:
            raise PatternError("manual_weight must be between 0 and 5")
    if not partial or "conditions" in data:
        conds = data.get("conditions") or []
        if not conds:
            raise PatternError("a pattern needs at least one condition")
        for i, c in enumerate(conds, 1):
            formula = (c.get("formula") if isinstance(c, dict) else c) or ""
            err = validate_condition(formula.strip(), None, None, PATTERN_CONTEXT_NAMES)
            if err:
                raise PatternError(f"condition {i} ({formula!r}): {err}")


def _conditions_by_pattern(session, ids) -> Dict[int, List[PatternCondition]]:
    out: Dict[int, List[PatternCondition]] = {}
    if ids:
        for c in session.query(PatternCondition).filter(PatternCondition.pattern_id.in_(list(ids))):
            out.setdefault(c.pattern_id, []).append(c)
    return out


def list_patterns(session, owner_id: Optional[int] = None, enabled_only: bool = False,
                  pattern_type: Optional[str] = None, category: Optional[str] = None) -> List[dict]:
    """System patterns plus this owner's own (owner_id None = system only)."""
    q = session.query(Pattern)
    q = q.filter((Pattern.origin == "system") | (Pattern.owner_id == owner_id)) if owner_id is not None else q.filter(Pattern.origin == "system")
    if enabled_only:
        q = q.filter(Pattern.enabled.is_(True))
    if pattern_type:
        q = q.filter(Pattern.pattern_type == pattern_type)
    if category:
        q = q.filter(Pattern.category == category)
    rows = q.order_by(Pattern.origin, Pattern.pattern_type, Pattern.name).all()
    conds = _conditions_by_pattern(session, [p.id for p in rows])
    return [to_dict(p, conds.get(p.id, [])) for p in rows]


def pattern_direction(pattern_type: Optional[str]) -> Optional[str]:
    """bullish -> "bull", bearish -> "bear"; caution/avoid have no direction."""
    return {"bullish": "bull", "bearish": "bear"}.get(pattern_type)


def pattern_meta(session, owner_id: Optional[int] = None) -> Dict[str, dict]:
    """{key: {"name", "pattern_type"}} -- labels pattern hits (activity_type
    "pattern") in the chart markers and event lists. The owner's own pattern
    wins over a system one with the same key."""
    q = session.query(Pattern.key, Pattern.name, Pattern.pattern_type, Pattern.origin)
    q = q.filter((Pattern.origin == "system") | (Pattern.owner_id == owner_id)) if owner_id is not None else q.filter(Pattern.origin == "system")
    out: Dict[str, dict] = {}
    for key, name, pattern_type, origin in sorted(q.all(), key=lambda r: r[3] != "system"):
        out[key] = {"name": name, "pattern_type": pattern_type}
    return out


def get_pattern(session, pattern_id: int) -> Optional[dict]:
    p = session.get(Pattern, pattern_id)
    if p is None:
        return None
    return to_dict(p, _conditions_by_pattern(session, [p.id]).get(p.id, []))


def _write_conditions(session, pattern_id: int, conditions) -> None:
    session.query(PatternCondition).filter_by(pattern_id=pattern_id).delete()
    for i, c in enumerate(conditions):
        c = c if isinstance(c, dict) else {"formula": c}
        session.add(PatternCondition(pattern_id=pattern_id, position=i, formula=c["formula"].strip(),
                                     note=c.get("note", "") or "", weight=c.get("weight", 1) or 1))


def create_pattern(session_factory, data: dict, owner_id: Optional[int] = None, origin: str = "user") -> int:
    validate(data)
    with session_scope(session_factory) as session:
        if session.query(Pattern).filter_by(key=data["key"].strip(), owner_id=owner_id).first() is not None:
            raise PatternError(f"a pattern with key {data['key']!r} already exists")
        p = Pattern(key=data["key"].strip(), origin=origin, owner_id=owner_id, parent_id=data.get("parent_id"),
                    **{k: data[k] for k in EDITABLE_FIELDS if k in data})
        session.add(p)
        session.flush()
        _write_conditions(session, p.id, data["conditions"])
        return p.id


def update_pattern(session_factory, pattern_id: int, data: dict) -> None:
    validate(data, partial=True)
    with session_scope(session_factory) as session:
        p = session.get(Pattern, pattern_id)
        if p is None:
            raise PatternError("pattern not found")
        if p.origin == "system" and set(data) - {"enabled"}:
            raise PatternError("system patterns are read-only -- copy it to make your own version")
        for k in EDITABLE_FIELDS:
            if k in data:
                setattr(p, k, data[k])
        if "conditions" in data:
            _write_conditions(session, p.id, data["conditions"])


def copy_pattern(session_factory, pattern_id: int, owner_id: Optional[int] = None, key: Optional[str] = None) -> int:
    """A user's editable copy of any pattern (typically a system one)."""
    with session_scope(session_factory) as session:
        src = session.get(Pattern, pattern_id)
        if src is None:
            raise PatternError("pattern not found")
        data = to_dict(src, _conditions_by_pattern(session, [src.id]).get(src.id, []))
    base = key or f"{data['key']}_copy"
    new_key, n = base, 2
    with session_scope(session_factory) as session:
        while session.query(Pattern).filter_by(key=new_key, owner_id=owner_id).first() is not None:
            new_key, n = f"{base}_{n}", n + 1
    data.update(key=new_key, name=f"{data['name']} (copy)" if not key else data["name"], parent_id=pattern_id)
    return create_pattern(session_factory, data, owner_id=owner_id, origin="user")


def set_stock_weight(session_factory, pattern_id: int, instrument_id: int, weight: float,
                     source: str = "backtest", owner_id: Optional[int] = None) -> None:
    if not 0 <= weight <= 5:
        raise PatternError("weight must be between 0 and 5")
    with session_scope(session_factory) as session:
        row = session.query(PatternStockWeight).filter_by(
            pattern_id=pattern_id, instrument_id=instrument_id, owner_id=owner_id).one_or_none()
        if row is None:
            session.add(PatternStockWeight(pattern_id=pattern_id, instrument_id=instrument_id,
                                           owner_id=owner_id, weight=weight, source=source))
        else:
            row.weight, row.source = weight, source


def load_stock_weights(session, owner_id: Optional[int] = None) -> Dict[tuple, float]:
    rows = session.query(PatternStockWeight).filter(PatternStockWeight.owner_id == owner_id) if owner_id is not None \
        else session.query(PatternStockWeight).filter(PatternStockWeight.owner_id.is_(None))
    return {(r.pattern_id, r.instrument_id): float(r.weight) for r in rows}


def seed_system_patterns(session_factory, catalog) -> None:
    """Idempotent: inserts any system pattern not already present (by key).
    An existing system pattern is left alone, so a weight set by a backtest
    or an enable/disable is never reset on restart."""
    with session_scope(session_factory) as session:
        existing = {p.key for p in session.query(Pattern.key).filter(Pattern.origin == "system")}
    for key, name, ptype, category, weight, lookback, cooldown, description, conditions in catalog:
        if key in existing:
            continue
        create_pattern(session_factory, {
            "key": key, "name": name, "pattern_type": ptype, "category": category, "manual_weight": weight,
            "lookback": lookback, "cooldown": cooldown, "description": description, "conditions": conditions,
        }, owner_id=None, origin="system")
