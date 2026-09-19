"""Schedule-adjusted points ("adjPts"): xPts with the opposition taken out.

xPts already swaps the luck-prone outcomes for their expectation (goals -> xG, assists -> xA,
clean sheets and goals conceded -> functions of xGC). This goes one step further and asks what
those underlying numbers would have been against an *average* opponent at a neutral venue:
0.6 xG away at the league's best defence is worth more than 0.6 at home to the worst. Each
match's xG and xA are divided by how generous that opponent's defence was (there), its xGC by
how dangerous that opponent's attack was, and the same points formula is applied. Appearance,
bonus, saves, defensive contribution and cards are left at their actual values, exactly as xPts
leaves them. Ratings come from app/schedule.py.
"""

import numpy as np
import pandas as pd

from app.queries import (
    ASSIST_POINTS,
    CLEAN_SHEET_FIT,
    CLEAN_SHEET_POINTS,
    CONCEDE_PENALTY_POSITIONS,
    FANTASY_ASSISTS_PER_XA,
    GOAL_POINTS,
)
from app.schedule import TeamRatings, fit_team_ratings, team_match_xg, with_schedule_multipliers

# Goalkeepers are left at their xPts. Their points are clean sheets *and* saves, which move in
# opposite directions with the opponent's strength, and in the backtest adjusting them made the
# number a worse predictor under every setting tried (scripts/backtest_adjusted_points.py).
UNADJUSTED_POSITIONS = ["GK"]

# Fitting is cheap but every table/chart request reloads the season; key on how much completed
# football the fit would see, so a refresh that adds matches refits and nothing else does.
_ratings_cache: dict[tuple[str, int], TeamRatings] = {}


def season_ratings(conn, season_id: str, player_gw: pd.DataFrame) -> TeamRatings:
    """This season's team ratings, shrunk toward the previous season's (the prior that makes
    the first weeks usable). Uses every completed match of the season, not just the rows a table
    is filtered to: the question is how good the opponent *is*, best answered with all we know."""
    matches = team_match_xg(player_gw)
    key = (season_id, len(matches))
    if key not in _ratings_cache:
        previous = conn.execute("SELECT MAX(id) FROM seasons WHERE id < ?", (season_id,)).fetchone()[0]
        prior = None
        if previous:
            prev_gw = pd.read_sql(
                """SELECT round, fixture_id, team_code, opponent_team_code, was_home, minutes, expected_goals
                   FROM player_gw_stats WHERE season_id = ?""",
                conn, params=(previous,),
            )
            prev_matches = team_match_xg(prev_gw)
            prior = fit_team_ratings(prev_matches) if not prev_matches.empty else None
        _ratings_cache[key] = fit_team_ratings(matches, prior)
    return _ratings_cache[key]


def with_adjusted_points(conn, season_id: str, player_gw: pd.DataFrame, players: pd.DataFrame) -> pd.DataFrame:
    """Adds `adjusted_points` per (player, fixture). NaN wherever xPts is (no xG data)."""
    if player_gw.empty:
        return player_gw.assign(adjusted_points=np.nan)
    position = players.set_index("player_code")["position"]
    comp = component_frame(player_gw, position, season_ratings(conn, season_id, player_gw))
    keep = comp["player_code"].map(position).isin(UNADJUSTED_POSITIONS)
    return player_gw.assign(adjusted_points=comp["adj_points"].where(~keep, comp["expected_points"]))


def _concede_penalty(xgc):
    """E[floor(N/2)], N ~ Poisson(xgc) - FPL's -1 per two goals conceded."""
    return xgc / 2 - (1 - np.exp(-2 * xgc)) / 4


def _clean_sheet_probability(xgc):
    a, b = CLEAN_SHEET_FIT
    return np.exp(-(a + b * xgc))


def component_frame(player_gw: pd.DataFrame, position: pd.Series, ratings: TeamRatings) -> pd.DataFrame:
    """`player_gw` rows plus the pieces adjusted points are built from. `other` is everything
    xPts leaves at its actual value; `adj_points` is the per-match schedule-adjusted total."""
    df = with_schedule_multipliers(player_gw, ratings)
    pos = df["player_code"].map(position)
    goal_pts, cs_pts = pos.map(GOAL_POINTS), pos.map(CLEAN_SHEET_POINTS)
    concedes = pos.isin(CONCEDE_PENALTY_POSITIONS).astype(float)
    xg, xa = df["expected_goals"].astype(float), df["expected_assists"].astype(float)
    xgc = df["expected_goals_conceded"].astype(float)
    played60 = (df["minutes"] >= 60).astype(float)

    def points(g, a, c):
        return (goal_pts * g + ASSIST_POINTS * FANTASY_ASSISTS_PER_XA * a
                + cs_pts * played60 * _clean_sheet_probability(c) - concedes * _concede_penalty(c))

    other = df["expected_points"] - points(xg, xa, xgc)
    xg_adj, xa_adj, xgc_adj = xg / df["att_mult"], xa / df["att_mult"], xgc / df["def_mult"]
    return df.assign(
        other=other, xg=xg, xa=xa, xgc=xgc, xg_adj=xg_adj, xa_adj=xa_adj, xgc_adj=xgc_adj,
        cs_minutes=df["minutes"] * played60,
        adj_points=other + points(xg_adj, xa_adj, xgc_adj),
    )


def points_from_components(position: str, other: float, xg: float, xa: float, xgc: float,
                           sixty_share: float = 1.0) -> float:
    """Points per 90 implied by per-90 components - used to re-price a player for a fixture."""
    concedes = 1.0 if position in CONCEDE_PENALTY_POSITIONS else 0.0
    return float(
        other + GOAL_POINTS.get(position, 0) * xg + ASSIST_POINTS * FANTASY_ASSISTS_PER_XA * xa
        + CLEAN_SHEET_POINTS.get(position, 0) * sixty_share * _clean_sheet_probability(xgc)
        - concedes * _concede_penalty(xgc)
    )
