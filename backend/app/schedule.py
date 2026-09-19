"""Schedule adjustment: team attack/defence ratings fitted from xG, and the multipliers that
turn "what a player did against these opponents" into "what that is worth against an average
one" (and back again, for fixtures still to come).

Why not FPL's fixture difficulty rating: it is a hand-set 1-5 integer, one per fixture, the same
for a centre-back and a striker. What a forward's numbers should be discounted by is the
opponent's *defence*; what a defender's clean sheets should be discounted by is the opponent's
*attack*. Both are measurable from the xG the dashboard already stores.

The model is the usual multiplicative one, on xG rather than goals (far less noisy):

    E[xG of team i against team j] = mu * att_i * def_j * (hfa if i is at home else 1 / hfa)

att > 1 is a better-than-average attack; def > 1 is a *leakier*-than-average defence. Ratings
are fitted jointly by iterative scaling, which is what stops a side that has only met strong
attacks from being rated a bad defence. Every rating is shrunk toward a prior (last season's
rating for the club, or the typical promoted side, else 1.0) with the weight of PRIOR_MATCHES
matches - without that, five gameweeks of xG rates teams on noise.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

PRIOR_MATCHES = 6.0
# Newly promoted sides, from the nine that came up in 2023/24-2025/26 (final attack ratings
# 0.65-1.01, defence 1.05-1.54): weaker going forward, leakier at the back.
PROMOTED_PRIOR = (0.78, 1.25)
# Ratings and multipliers are clipped so one freak xG line can't produce a 4x adjustment.
RATING_BOUNDS = (0.5, 2.0)


@dataclass
class TeamRatings:
    mu: float = 1.35                      # league-average xG per team per match
    hfa: float = 1.0                      # home multiplier (away is 1 / hfa)
    attack: dict[int, float] = field(default_factory=dict)
    defence: dict[int, float] = field(default_factory=dict)

    def attack_multiplier(self, opponent: int, was_home) -> float:
        """How much easier than average it was to create xG against this opponent here."""
        venue = self.hfa if was_home else 1.0 / self.hfa
        return float(np.clip(self.defence.get(opponent, 1.0) * venue, *RATING_BOUNDS))

    def concede_multiplier(self, opponent: int, was_home) -> float:
        """How much more xG than average this opponent was expected to create here."""
        venue = 1.0 / self.hfa if was_home else self.hfa
        return float(np.clip(self.attack.get(opponent, 1.0) * venue, *RATING_BOUNDS))


def team_match_xg(player_gw: pd.DataFrame) -> pd.DataFrame:
    """One row per (fixture, team): the xG that team created, summed over its players, with the
    opponent and venue. Rounds with no xG at all (pre-2022/23, early 2022/23) are dropped."""
    cols = ["round", "fixture_id", "team_code", "opponent_team_code", "was_home"]
    if player_gw.empty:
        return pd.DataFrame(columns=cols + ["xg"])
    rows = player_gw[player_gw.groupby("round")["expected_goals"].transform("sum") > 0]
    rows = rows.dropna(subset=["opponent_team_code"])
    # Completed matches only. The live season carries rows for fixtures still to be played in
    # the current round (all zeros), which would read as "created nothing"; a finished match
    # has ~990 player-minutes a side.
    rows = rows[rows.groupby(["fixture_id", "team_code"])["minutes"].transform("sum") >= 900]
    out = rows.groupby(cols, as_index=False)["expected_goals"].sum().rename(columns={"expected_goals": "xg"})
    out["opponent_team_code"] = out["opponent_team_code"].astype(int)
    return out


def fit_team_ratings(matches: pd.DataFrame, prior: TeamRatings | None = None,
                     promoted: tuple[float, float] = PROMOTED_PRIOR, iterations: int = 40) -> TeamRatings:
    """Fit att/def/hfa to `matches` (from team_match_xg). `prior` is last season's ratings."""
    if matches.empty:
        return prior or TeamRatings()
    teams = sorted(set(matches["team_code"]) | set(matches["opponent_team_code"]))
    known = prior is not None and bool(prior.attack)

    def prior_for(team: int) -> tuple[float, float]:
        if known and team in prior.attack:
            return prior.attack[team], prior.defence[team]
        return promoted if known else (1.0, 1.0)

    p_att = {t: prior_for(t)[0] for t in teams}
    p_def = {t: prior_for(t)[1] for t in teams}
    att, dfn = dict(p_att), dict(p_def)
    mu = float(matches["xg"].mean())
    hfa = 1.0
    team = matches["team_code"].to_numpy()
    opp = matches["opponent_team_code"].to_numpy()
    home = matches["was_home"].to_numpy().astype(bool)
    xg = matches["xg"].to_numpy(dtype=float)

    for _ in range(iterations):
        venue = np.where(home, hfa, 1.0 / hfa)
        a = np.array([att[t] for t in team])
        d = np.array([dfn[t] for t in opp])
        # Each rating = observed / expected-without-it, with PRIOR_MATCHES pseudo-matches at the
        # prior rating mixed in (a ratio estimator's version of shrinkage).
        base_att = mu * d * venue
        base_def = mu * a * venue
        for t in teams:
            m = team == t
            att[t] = (xg[m].sum() + PRIOR_MATCHES * mu * p_att[t]) / (base_att[m].sum() + PRIOR_MATCHES * mu)
            m = opp == t
            dfn[t] = (xg[m].sum() + PRIOR_MATCHES * mu * p_def[t]) / (base_def[m].sum() + PRIOR_MATCHES * mu)
        # Keep the scale in mu: ratings average 1 (geometrically), so "1.0" means average.
        ga = np.exp(np.mean([np.log(att[t]) for t in teams]))
        gd = np.exp(np.mean([np.log(dfn[t]) for t in teams]))
        att = {t: v / ga for t, v in att.items()}
        dfn = {t: v / gd for t, v in dfn.items()}
        a = np.array([att[t] for t in team])
        d = np.array([dfn[t] for t in opp])
        mu = xg.sum() / (a * d * np.where(home, hfa, 1.0 / hfa)).sum()
        expected_neutral = mu * a * d
        # hfa solves sum(home xG) / sum(away xG) = hfa^2 * (neutral home / neutral away).
        ratio = (xg[home].sum() / max(xg[~home].sum(), 1e-9)) / (
            expected_neutral[home].sum() / max(expected_neutral[~home].sum(), 1e-9))
        hfa = float(np.clip(np.sqrt(ratio), 0.9, 1.5))

    clip = lambda v: float(np.clip(v, *RATING_BOUNDS))
    return TeamRatings(mu=float(mu), hfa=hfa, attack={t: clip(v) for t, v in att.items()},
                       defence={t: clip(v) for t, v in dfn.items()})


def with_schedule_multipliers(player_gw: pd.DataFrame, ratings: TeamRatings) -> pd.DataFrame:
    """Adds `att_mult` / `def_mult` per row: the factors the row's xG+xA and xGC were inflated
    by, relative to meeting an average side at a neutral venue."""
    opp = player_gw["opponent_team_code"]
    home = player_gw["was_home"] == 1
    hfa = ratings.hfa
    att = opp.map(ratings.defence).fillna(1.0) * np.where(home, hfa, 1.0 / hfa)
    dfn = opp.map(ratings.attack).fillna(1.0) * np.where(home, 1.0 / hfa, hfa)
    return player_gw.assign(att_mult=att.clip(*RATING_BOUNDS), def_mult=dfn.clip(*RATING_BOUNDS))
