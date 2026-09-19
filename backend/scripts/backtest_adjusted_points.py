#!/usr/bin/env python3
"""Does schedule-adjusting past points make them a better predictor of future points?

    backend/.venv/bin/python backend/scripts/backtest_adjusted_points.py

For every season with xG, at a series of cut-off gameweeks, each candidate is computed from the
matches up to the cut-off only (team ratings included - no look-ahead) and used to predict the
player's actual points per 90 over the next HORIZON gameweeks. Candidates:

    pts        actual points per 90
    xpts       xPts per 90 (finishing / clean-sheet luck removed)
    adj        schedule-adjusted xPts per 90 (past opponents neutralised)
    xpts+fix   xPts components, re-priced for the upcoming fixtures
    adj+fix    adjusted components, re-priced for the upcoming fixtures - the full idea:
               take the schedule out of the past, put the future one back in

Scored by Pearson and Spearman correlation with what happened, overall and by position, for a
long window (everything so far) and a short one (last 6 gameweeks), where schedule matters most.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from app.db import get_connection
from app.queries import load_season_frames
from app.schedule import fit_team_ratings, team_match_xg
from app.adjusted import UNADJUSTED_POSITIONS, component_frame, points_from_components

SEASONS = ["2022-23", "2023-24", "2024-25", "2025-26"]
CUTOFFS = [6, 10, 14, 18, 22, 26, 30]
HORIZON = 6
MIN_PAST_MINUTES = {"long": 360, "short": 270}
MIN_FUTURE_MINUTES = 180


def evaluate():
    conn = get_connection()
    records = []
    prior = None
    for season in SEASONS:
        player_gw, fixtures, _teams, players = load_season_frames(conn, season)
        position = players.set_index("player_code")["position"]
        all_fixtures = pd.read_sql("SELECT round, team_h_code, team_a_code FROM fixtures WHERE season_id = ?",
                                   conn, params=(season,))
        matches_all = team_match_xg(player_gw)
        for cutoff in CUTOFFS:
            matches = matches_all[matches_all["round"] <= cutoff]
            if matches["round"].nunique() < 4:
                continue  # 2022/23 has no xG before GW16
            ratings = fit_team_ratings(matches, prior)
            future_rounds = range(cutoff + 1, cutoff + HORIZON + 1)
            future = player_gw[player_gw["round"].isin(future_rounds)]
            target = future.groupby("player_code").agg(fmin=("minutes", "sum"), fpts=("total_points", "sum"))
            target = target[target["fmin"] >= MIN_FUTURE_MINUTES]
            target["y"] = target["fpts"] / target["fmin"] * 90

            # Upcoming fixtures per team, as (opponent, was_home).
            upcoming = {}
            for f in all_fixtures[all_fixtures["round"].isin(future_rounds)].itertuples():
                upcoming.setdefault(f.team_h_code, []).append((f.team_a_code, True))
                upcoming.setdefault(f.team_a_code, []).append((f.team_h_code, False))

            for window, first in (("long", 1), ("short", cutoff - 5)):
                past = player_gw[(player_gw["round"] >= first) & (player_gw["round"] <= cutoff)]
                past = past[past["expected_points"].notna()]
                comp = component_frame(past, position, ratings)
                unadjusted = comp["player_code"].map(position).isin(UNADJUSTED_POSITIONS)
                comp["adj_points"] = comp["adj_points"].where(~unadjusted, comp["expected_points"])
                g = comp.groupby("player_code")
                agg = g[["minutes", "total_points", "expected_points", "other", "xg", "xa", "xgc",
                         "xg_adj", "xa_adj", "xgc_adj", "adj_points", "cs_minutes"]].sum()
                agg["team_code"] = g["team_code"].last()
                agg = agg[agg["minutes"] >= MIN_PAST_MINUTES[window]].join(target[["y"]], how="inner")
                if agg.empty:
                    continue
                per90 = 90.0 / agg["minutes"]
                agg["pos"] = position.reindex(agg.index)
                agg["pts"] = agg["total_points"] * per90
                agg["xpts"] = agg["expected_points"] * per90
                agg["adj"] = agg["adj_points"] * per90
                for name, (gx, ax, cx) in {"xpts+fix": ("xg", "xa", "xgc"), "adj+fix": ("xg_adj", "xa_adj", "xgc_adj")}.items():
                    preds = []
                    for code, r in agg.iterrows():
                        fx = upcoming.get(r["team_code"], [])
                        if not fx:
                            preds.append(np.nan)
                            continue
                        share = r["cs_minutes"] / r["minutes"]  # how often he lasts the 60 a clean sheet needs
                        vals = [
                            points_from_components(
                                r["pos"], r["other"] * per90[code],
                                r[gx] * per90[code] * ratings.attack_multiplier(o, h),
                                r[ax] * per90[code] * ratings.attack_multiplier(o, h),
                                r[cx] * per90[code] * ratings.concede_multiplier(o, h), share)
                            for o, h in fx
                        ]
                        preds.append(float(np.mean(vals)))
                    agg[name] = preds
                agg["season"], agg["cutoff"], agg["window"] = season, cutoff, window
                records.append(agg.reset_index())
        prior = fit_team_ratings(matches_all, prior)
    return pd.concat(records, ignore_index=True)


def report(df: pd.DataFrame):
    cands = ["pts", "xpts", "adj", "xpts+fix", "adj+fix"]
    for window in ("long", "short"):
        w = df[df["window"] == window].dropna(subset=cands)
        print(f"\n=== {window} window ({'all matches so far' if window == 'long' else 'last 6 gameweeks'}) -> next {HORIZON} GWs, "
              f"n={len(w):,} player-cutoffs ===")
        print(f"{'':10s}" + "".join(f"{c:>10s}" for c in cands))
        for label, sub in [("ALL", w)] + [(p, w[w["pos"] == p]) for p in ("GK", "DEF", "MID", "FWD")]:
            print(f"{label:4s} r    " + "".join(f"{sub[c].corr(sub['y']):10.3f}" for c in cands))
            print(f"{'':4s} rho  " + "".join(f"{sub[c].rank().corr(sub['y'].rank()):10.3f}" for c in cands))
        # Correlations pooled within season-cutoff, so a season-wide scoring shift can't help.
        by = w.groupby(["season", "cutoff"])
        print("mean within-slice r " + "".join(f"{by.apply(lambda s, c=c: s[c].corr(s['y']), include_groups=False).mean():10.3f}" for c in cands))
        wins = by.apply(lambda s: pd.Series({c: s[c].corr(s["y"]) for c in cands}), include_groups=False)
        print(f"slices where adj+fix beats xpts: {(wins['adj+fix'] > wins['xpts']).sum()}/{len(wins)};"
              f"  beats pts: {(wins['adj+fix'] > wins['pts']).sum()}/{len(wins)};"
              f"  adj beats xpts: {(wins['adj'] > wins['xpts']).sum()}/{len(wins)}")


if __name__ == "__main__":
    report(evaluate())
