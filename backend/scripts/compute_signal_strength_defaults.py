"""Previews signal-strength minimums from history (ALGO-6). READ-ONLY by
default; --apply writes them as the system minimums and multipliers. Same
calibration the API runs (POST /api/signal-strength/calibrate).

    .venv/Scripts/python.exe backend/scripts/compute_signal_strength_defaults.py [--target 0.4] [--sessions 20] [--apply]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from db.session import build_engine, build_session_factory
from signal_strength import PRICE_SCALED_MEASURES
from signal_strength_calibration import apply, compute


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=float, default=0.40)
    ap.add_argument("--sessions", type=int, default=20)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    factory = build_session_factory(build_engine())
    p = compute(factory, target=args.target, sessions=args.sessions)
    mult = p["multipliers"]
    print(f"{p['stocks']} stocks, last {p['sessions']} sessions, target {p['target']:.0%} qualify; multipliers {mult}")
    for signal, mins in p["base"].items():
        info = p["signals"][signal]
        shown = "; ".join(f"{m} {v:.4f}" + (f" | {v * mult['3min']:.4f} | {v * mult['5min']:.4f}" if m in PRICE_SCALED_MEASURES else "")
                          for m, v in mins.items())
        print(f"{signal:22} {info['formations']:>6} formations, {info['qualify_share']:.0%} qualify: {shown}")
    if args.apply:
        apply(factory, p)
        print("applied")


if __name__ == "__main__":
    main()
