#!/usr/bin/env python3
"""Simulate each player's gameweek score distribution and measure his haul upside.

    backend/.venv/bin/python backend/scripts/simulate_upside.py data/fplreview-latest.csv data/upside.csv
    ... --draws 20000 --haul 10

For every player and gameweek in an FPL Review export, draws a score from the projection's
components: an appearance from expected minutes, goals and assists from xG and xA, a clean
sheet at the projected probability (60+ minutes only), a defensive-contribution hit at its
probability, and a bonus formula (2 per goal, 1 per assist, 1 for a defender's clean sheet,
capped at 3). The output's `up` column is the expected points above the haul line, which
scripts/plan_transfers.py adds to the objective at weight --lambda.

Players in the same match are correlated through the match itself. Each side's goal count is
one Poisson draw (mean: the side's summed xG) and is shared out among the teammates who appear,
in proportion to their xG, so a side that scores three has three scorers, not three independent
chances. Every goal carries an assist with the side's xA/xG probability, shared out by xA. The
same draw decides the other side's clean sheets: the uniform behind a side's goal count also
sits under its opponents' clean-sheet thresholds, so a shut-out is one event the whole back
line shares, and it can't coincide with the other side scoring. Marginals per player are
unchanged (mean goals still xG, mean clean sheets still xCS); only the joint distribution
moves, which is what a squad's total, or a head-to-head, depends on.

Rough by design: teammates aren't correlated beyond the match score, cards and saves are
ignored and bonus is a formula rather than BPS. Good for ranking ceilings, not exact odds.
Columns: code, gw, mean, p10 (chance of 10+), p15, p95 (95th percentile score), up.
"""
import argparse
import csv
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

GW_COL = re.compile(r"^(\d{1,2})_(Pts|xMins|xG|xA|xCS|xDC|opp)$")
OPP_COL = re.compile(r"^([A-Z]{3})")
GOAL_PTS = {"GK": 6, "DEF": 6, "MID": 5, "FWD": 4}
CS_PTS = {"GK": 4, "DEF": 4, "MID": 1, "FWD": 0}
MAX_GOALS = 12


@dataclass
class PlayerWeek:
    """One player's projection for one gameweek, as the simulator consumes it."""

    code: int
    team: str
    pos: str
    opp: str  # FPL Review's fixture string, e.g. "HUL(H)"; "" for a blank
    xmins: float
    xg: float
    xa: float
    xcs: float
    xdc: float
    xp: float = 0.0  # FPL Review's own expected points, carried along for reporting

    @property
    def opponent(self) -> str | None:
        m = OPP_COL.match(self.opp or "")
        return m.group(1) if m else None


def read_player_weeks(csv_path: Path) -> dict[int, list[PlayerWeek]]:
    """Every player-gameweek in an FPL Review export, keyed by gameweek."""
    weeks: dict[int, list[PlayerWeek]] = defaultdict(list)
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            if not row.get("code"):
                continue
            per_gw: dict[int, dict[str, str]] = {}
            for col, val in row.items():
                m = GW_COL.match(col or "")
                if m and val not in (None, ""):
                    per_gw.setdefault(int(m.group(1)), {})[m.group(2)] = val
            for gw, s in per_gw.items():
                weeks[gw].append(PlayerWeek(
                    code=int(float(row["code"])), team=row["team"], pos=row["pos"],
                    opp=s.get("opp", ""), xmins=float(s.get("xMins", 0)),
                    xg=float(s.get("xG", 0)), xa=float(s.get("xA", 0)),
                    xcs=float(s.get("xCS", 0)), xdc=float(s.get("xDC", 0)),
                    xp=float(s.get("Pts", 0)),
                ))
    return weeks


def _poisson_ppf(lam: float, u: np.ndarray) -> np.ndarray:
    """Poisson quantiles of the uniforms `u`, so the count is monotone in `u`."""
    k = np.arange(MAX_GOALS + 1)
    pmf = np.exp(-lam) * np.cumprod(np.concatenate([[1.0], np.full(MAX_GOALS, lam) / k[1:]]))
    cdf = np.cumsum(pmf)
    return np.searchsorted(cdf, u, side="right").clip(max=MAX_GOALS)


def _share_out(count: np.ndarray, weights: np.ndarray, rng: np.random.Generator,
               exclude: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Hand `count[d]` events in each draw d to players in proportion to `weights[d, i]`.

    Returns the per-player event counts (draws x players) and, for each event slot
    (draws x max count), the index of the player who got it (-1 for none), so assists can
    avoid the scorer.
    """
    draws, n = weights.shape
    counts = np.zeros((draws, n), dtype=np.int16)
    top = int(count.max()) if count.size else 0
    who = np.full((draws, max(top, 1)), -1, dtype=np.int32)
    cum = np.cumsum(weights, axis=1)
    total = cum[:, -1]
    for g in range(top):
        live = (count > g) & (total > 0)
        if not live.any():
            break
        if exclude is not None:
            w = weights.copy()
            w[np.arange(draws), exclude[:, g].clip(min=0)] *= (exclude[:, g] < 0)
            cum_g = np.cumsum(w, axis=1)
            total_g = cum_g[:, -1]
            live &= total_g > 0
        else:
            cum_g, total_g = cum, total
        u = rng.random(draws) * total_g
        idx = (cum_g < u[:, None]).sum(axis=1).clip(max=n - 1)
        rows = np.nonzero(live)[0]
        counts[rows, idx[rows]] += 1
        who[rows, g] = idx[rows]
    return counts, who


def simulate_gameweek(players: list[PlayerWeek], draws: int, rng: np.random.Generator,
                      independent: bool = False) -> dict[int, np.ndarray]:
    """Joint score draws for every player in one gameweek, keyed by player code.

    Players on the same side share one goal count and one assist count; the uniform that
    drives a side's goals also decides the opposing side's clean sheets. A side whose
    opponent isn't in the data, or a player with no fixture, falls back to independent draws.
    `independent=True` puts every player on a side of his own (the pre-correlation model).
    """
    out: dict[int, np.ndarray] = {}
    sides: dict[tuple, list[PlayerWeek]] = defaultdict(list)
    for p in players:
        if p.xmins <= 0 or p.opponent is None:
            out[p.code] = np.zeros(draws, dtype=np.float32)
        elif independent:
            sides[(p.team, p.opponent, p.code)].append(p)
        else:
            sides[(p.team, p.opponent)].append(p)

    # One uniform per side drives that side's goals and its opponents' clean sheets.
    side_u = {key: rng.random(draws) for key in sides}

    for key, group in sides.items():
        team, opp = key[0], key[1]
        n = len(group)
        xmins = np.array([p.xmins for p in group])
        p_app = np.minimum(1.0, xmins / 70)
        p_60 = np.minimum(1.0, xmins / 88)
        played = rng.random((draws, n)) < p_app
        full = played & (rng.random((draws, n)) < p_60 / p_app)
        xg = np.array([p.xg for p in group])
        xa = np.array([p.xa for p in group])

        # Goals: a Poisson count for the side, shared out by xG among those who appear.
        # xG already averages over the appearance chance, so condition it on playing.
        lam = float(xg.sum())
        side_goals = _poisson_ppf(lam, side_u[key])
        goals, scorers = _share_out(side_goals, played * (xg / p_app), rng)

        # Assists: one per goal with the side's xA/xG probability, shared out by xA,
        # never to the scorer.
        assist_p = min(1.0, xa.sum() / lam) if lam > 0 else 0.0
        assisted = (rng.random(scorers.shape) < assist_p) & (scorers >= 0)
        # Pack the assisted goals' scorers to the left so slot g lines up with assist g.
        order = np.argsort(~assisted, axis=1, kind="stable")
        excluded = np.where(np.take_along_axis(assisted, order, axis=1),
                            np.take_along_axis(scorers, order, axis=1), -1)
        assists, _ = _share_out(assisted.sum(axis=1), played * (xa / p_app), rng,
                                exclude=excluded)

        # Clean sheets ride on the opponents' goal draw when they're in the data.
        xcs = np.array([p.xcs for p in group])
        u_cs = side_u.get((opp, team))
        if u_cs is None:
            u_cs = rng.random((draws, 1))
        else:
            u_cs = u_cs[:, None]
        cs = full & (u_cs < np.minimum(1.0, xcs / np.maximum(p_60, 1e-9)))

        xdc = np.array([p.xdc for p in group])
        dc = played & (rng.random((draws, n)) < np.minimum(1.0, xdc / p_app))

        goal_pts = np.array([GOAL_PTS[p.pos] for p in group])
        cs_pts = np.array([CS_PTS[p.pos] for p in group])
        is_def = np.array([p.pos in ("GK", "DEF") for p in group])
        bonus = np.minimum(3, 2 * goals + assists + (cs & is_def))
        scores = (
            played * 1 + full * 1
            + goals * goal_pts + assists * 3
            + cs * cs_pts + dc * 2 + bonus
        ).astype(np.float32)
        for i, p in enumerate(group):
            out[p.code] = scores[:, i]
    return out


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
    for gw, players in sorted(read_player_weeks(args.csv_path).items()):
        scores_by_code = simulate_gameweek(players, args.draws, rng)
        for p in players:
            scores = scores_by_code[p.code]
            rows_out.append({
                "code": p.code,
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
