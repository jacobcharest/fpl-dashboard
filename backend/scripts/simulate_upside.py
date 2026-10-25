#!/usr/bin/env python3
"""Simulate each player's gameweek score distribution and measure his haul upside.

    backend/.venv/bin/python backend/scripts/simulate_upside.py data/fplreview-latest.csv data/upside.csv
    ... --draws 20000 --haul 10

For every player and gameweek in an FPL Review export, draws a score from the projection's
components: an appearance from expected minutes, Poisson goals and assists from xG and xA, a
clean sheet at the projected probability (60+ minutes only), a defensive-contribution hit at its
probability, and a bonus formula (2 per goal, 1 per assist, 1 for a defender's clean sheet,
capped at 3). The output's `up` column is the expected points above the haul line, which
scripts/plan_transfers.py adds to the objective at weight --lambda.

Rough by design: goals and assists are independent, teammates aren't correlated, cards and saves
are ignored and bonus is a formula rather than BPS. Good for ranking ceilings, not exact odds.
Columns: code, gw, mean, p10 (chance of 10+), p15, p95 (95th percentile score), up.
"""
import argparse
import csv
import re
import sys
from pathlib import Path

import numpy as np

GW_COL = re.compile(r"^(\d{1,2})_(Pts|xMins|xG|xA|xCS|xDC)$")
GOAL_PTS = {"GK": 6, "DEF": 6, "MID": 5, "FWD": 4}
CS_PTS = {"GK": 4, "DEF": 4, "MID": 1, "FWD": 0}


def simulate(pos: str, xmins: float, xg: float, xa: float, xcs: float, xdc: float,
             draws: int, rng: np.random.Generator) -> np.ndarray:
    """One array of `draws` possible scores for a player-gameweek."""
    p_app = min(1.0, xmins / 70) if xmins > 0 else 0.0
    if p_app == 0:
        return np.zeros(draws)
    p_60 = min(1.0, xmins / 88)
    played = rng.random(draws) < p_app
    full = played & (rng.random(draws) < p_60 / p_app)
    # xG / xA already average over the appearance chance; condition them on playing.
    goals = rng.poisson(xg / p_app, draws) * played
    assists = rng.poisson(xa / p_app, draws) * played
    cs = full & (rng.random(draws) < min(1.0, xcs / max(p_60, 1e-9)))
    dc = played & (rng.random(draws) < min(1.0, xdc / p_app))
    bonus = np.minimum(3, 2 * goals + assists + (cs if pos in ("GK", "DEF") else 0))
    return (
        played * 1 + full * 1
        + goals * GOAL_PTS[pos] + assists * 3
        + cs * CS_PTS[pos] + dc * 2 + bonus
    ).astype(float)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path", type=Path, help="FPL Review projections export")
    ap.add_argument("out_path", type=Path, help="where to write the upside CSV")
    ap.add_argument("--draws", type=int, default=20000)
    ap.add_argument("--haul", type=float, default=10, help="points that count as a haul (default 10)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    rows_out = []
    with open(args.csv_path, newline="") as f:
        for row in csv.DictReader(f):
            if not row.get("code"):
                continue
            per_gw: dict[int, dict[str, float]] = {}
            for col, val in row.items():
                m = GW_COL.match(col or "")
                if m and val not in (None, ""):
                    per_gw.setdefault(int(m.group(1)), {})[m.group(2)] = float(val)
            for gw, s in sorted(per_gw.items()):
                scores = simulate(row["pos"], s.get("xMins", 0), s.get("xG", 0), s.get("xA", 0),
                                  s.get("xCS", 0), s.get("xDC", 0), args.draws, rng)
                rows_out.append({
                    "code": int(float(row["code"])),
                    "gw": gw,
                    "mean": round(float(scores.mean()), 2),
                    "p10": round(float((scores >= args.haul).mean()), 4),
                    "p15": round(float((scores >= 15).mean()), 4),
                    "p95": float(np.percentile(scores, 95)),
                    "up": round(float(np.maximum(scores - args.haul, 0).mean()), 3),
                })
    with open(args.out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["code", "gw", "mean", "p10", "p15", "p95", "up"])
        w.writeheader()
        w.writerows(rows_out)
    print(f"Wrote {len(rows_out)} player-gameweeks to {args.out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
