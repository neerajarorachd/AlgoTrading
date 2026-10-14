"""Stock colour from patterns (ALGO-33 / 5.4), as agreed 2026-10-14:

- each pattern's score = weight x match %, counted only once FORMED (match %
  >= the formed threshold, 80%); weight = the per-stock weight when that
  setting is on and one exists, else the pattern's own (LibPatterns);
- the TOP score among formed bullish/bearish patterns sets the colour:
  >= 5 dark green, >= 4 light green, >= 3 orange, >= 2 yellow, else white;
- a formed CAUTION pattern makes it pink, a formed AVOID pattern red -- both
  override every positive colour, red over pink ("avoid the stock");
- colour is opportunity strength, not direction: direction (bull/bear, i.e.
  Buy/Sell) comes from the top pattern's type;
- every colour has a number (dark green 6 ... red 0) so sorting by colour
  always puts green at the top and red at the bottom.
"""
from __future__ import annotations

from typing import Callable, Dict, Iterable, Optional

# (key, sort value, hex, label) -- top to bottom
COLOURS = (
    ("dark_green", 6, "#1b7a3d", "Dark green: a top-weight pattern formed"),
    ("light_green", 5, "#5fb96f", "Light green: a weight-4 pattern (or a 5 at 80%) formed"),
    ("orange", 4, "#f08c00", "Orange: a weight-3 pattern (or a 5 at 60%) formed"),
    ("yellow", 3, "#e2c200", "Yellow: a weight-2 pattern formed"),
    ("white", 2, "#e9e7ef", "White: nothing meaningful formed"),
    ("pink", 1, "#e48aae", "Pink: a caution pattern is forming -- avoid"),
    ("red", 0, "#c62828", "Red: sideways or too volatile -- avoid"),
)
COLOUR_VALUE = {k: v for k, v, _, _ in COLOURS}
COLOUR_HEX = {k: h for k, _, h, _ in COLOURS}
COLOUR_LABEL = {k: label for k, _, _, label in COLOURS}
TIERS = ((5, "dark_green"), (4, "light_green"), (3, "orange"), (2, "yellow"))


def colour_for(matches: Dict[str, float], patterns: Iterable, threshold: float,
               weight_of: Optional[Callable] = None) -> dict:
    """matches: {pattern key: match %} for one stock and timeframe.
    patterns: objects with key, pattern_type, weight (PatternEvaluator's)."""
    weight_of = weight_of or (lambda p: p.weight)
    by_key = {p.key: p for p in patterns}
    formed = [(by_key[k], m) for k, m in matches.items() if k in by_key and m >= threshold]
    avoid = [p.key for p, _ in formed if p.pattern_type == "avoid"]
    caution = [p.key for p, _ in formed if p.pattern_type == "caution"]
    scored = sorted(((weight_of(p) * m, p) for p, m in formed if p.pattern_type in ("bullish", "bearish")),
                    key=lambda t: t[0], reverse=True)
    top_score, top = (scored[0] if scored else (0.0, None))
    bull = max((s for s, p in scored if p.pattern_type == "bullish"), default=0.0)
    bear = max((s for s, p in scored if p.pattern_type == "bearish"), default=0.0)
    if avoid:
        colour = "red"
    elif caution:
        colour = "pink"
    else:
        colour = next((c for minimum, c in TIERS if top_score >= minimum), "white")
    return {
        "colour": colour, "colour_value": COLOUR_VALUE[colour],
        "direction": ("bull" if top.pattern_type == "bullish" else "bear") if top is not None else None,
        "top_pattern": top.key if top is not None else None, "top_score": round(top_score, 3),
        "bull_score": round(bull, 3), "bear_score": round(bear, 3),
        "formed": sorted(p.key for p, _ in formed), "warnings": avoid + caution,
    }
