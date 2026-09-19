"""Reusable filter/aggregation engine behind the player and team table endpoints.

Note: pandas silently upcasts a numeric column containing a Python `None` to NaN once it
sits alongside real float values (e.g. a team with zero counted fixtures in its window next
to teams that do have some). NaN isn't valid JSON, so every `.to_dict()` boundary here goes
through `_records()`, which converts NaN back to `None`.

Both tables share the same left-panel filter semantics (see DESIGN.md): each included team
carries its own gameweek range, an optional opponent-inclusion filter restricts which fixtures
count, and everything is computed on the fly from the season's player_gw_stats + fixtures
(nothing is pre-aggregated/stored).

Team-level "goals"/"goals against" must come from fixtures.team_h_score/team_a_score, not from
summing player_gw_stats rows: goals_conceded (like clean_sheets) is duplicated across every
player in the squad who featured that match, so summing it over-counts by a factor of ~11.
expected_goals has no such problem (it's genuinely per-shot-taker), so team xG/xGA are built by
summing player-level expected_goals grouped by (fixture, team).
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

PLAYER_STAT_COLUMNS = [
    "total_points",
    "expected_points",
    "adjusted_points",
    "goals_scored",
    "expected_goals",
    "assists",
    "expected_assists",
    "expected_goal_involvements",
    "clean_sheets",
    "expected_goals_conceded",
    "defensive_contribution",
    "bonus",
    "bps",
    "saves",
    "yellow_cards",
    "red_cards",
    "influence",
    "creativity",
    "threat",
    "ict_index",
]

# Defensive contribution points (introduced 2025/26): a defender needs 10+ combined defensive
# actions (clearances/blocks/interceptions/tackles) in a match, a midfielder or forward needs
# 12+ (same plus recoveries). Goalkeepers aren't part of this scheme at all.
DC_THRESHOLD = {"DEF": 10, "MID": 12, "FWD": 12}

# Scoring rules for the only outcomes that have an expected-stat counterpart (see
# _with_expected_points). These are the long-stable core of FPL scoring; a goalkeeper goal was
# 6 before 2024/25, but goalkeeper xG is ~0 so the single value is harmless.
GOAL_POINTS = {"GK": 10, "DEF": 6, "MID": 5, "FWD": 4}
ASSIST_POINTS = 3
# FPL credits "fantasy assists" that xA can't see (penalties/free kicks won, rebounds, forced
# own goals), so assists run a steady ~1.39x xA league-wide (1.42, 1.37, 1.38 over
# 2023/24-2025/26). Unscaled, every creator would look like a permanent over-performer.
FANTASY_ASSISTS_PER_XA = 1.39
CLEAN_SHEET_POINTS = {"GK": 4, "DEF": 4, "MID": 1, "FWD": 0}
CONCEDE_PENALTY_POSITIONS = ["GK", "DEF"]  # -1 per 2 goals conceded
# P(clean sheet | 60+ mins) = exp(-(a + b * xGC)), fitted by maximum likelihood on every 60+
# minute appearance of 2023/24-2025/26. Plain Poisson exp(-xGC) over-predicts clean sheets
# (0.35 vs 0.29 actual in 2025/26): a single match's xGC is a noisy read of the true rate and
# exp(-x) is convex, plus own goals never show up in xGC. Uncalibrated, that bias short-changed
# every defender by ~0.25 points a game.
CLEAN_SHEET_FIT = (0.09, 1.16)


@dataclass
class TeamRange:
    team_code: int
    start_gw: int
    end_gw: int


@dataclass
class NumericFilter:
    column: str
    op: str  # "gt" | "lt"
    value: float


@dataclass
class SortSpec:
    column: str
    direction: str = "desc"


@dataclass
class TableFilters:
    season_id: str
    teams: list[TeamRange]
    opponent_team_codes: list[int] | None = None
    filters: list[NumericFilter] = field(default_factory=list)
    sort: SortSpec | None = None
    positions: list[str] | None = None  # players only; None = no filter (all positions)
    # Forward-looking projections (players only). None = don't join any; see app/projections.py.
    projection_source: str | None = None
    projection_gameweeks: list[int] | None = None  # any set of rounds; None/empty = no horizon


def load_season_frames(conn, season_id: str):
    player_gw = pd.read_sql(
        "SELECT * FROM player_gw_stats WHERE season_id = ?", conn, params=(season_id,)
    )
    fixtures = pd.read_sql(
        "SELECT * FROM fixtures WHERE season_id = ? AND team_h_score IS NOT NULL",
        conn,
        params=(season_id,),
    )
    teams = pd.read_sql("SELECT * FROM teams WHERE season_id = ?", conn, params=(season_id,))
    players = pd.read_sql(
        """SELECT p.player_code, p.web_name, ps.position, ps.selected_by_percent
           FROM player_season ps JOIN players p ON p.player_code = ps.player_code
           WHERE ps.season_id = ?""",
        conn,
        params=(season_id,),
    )
    # Local import: app.adjusted builds on this module's scoring constants.
    from app.adjusted import with_adjusted_points

    player_gw = _with_expected_points(player_gw, players)
    return with_adjusted_points(conn, season_id, player_gw, players), fixtures, teams, players


def _with_expected_points(player_gw: pd.DataFrame, players: pd.DataFrame) -> pd.DataFrame:
    """Add a backward-looking `expected_points` per (player, fixture): the points the player's
    underlying numbers in that match were worth. Not a forecast - that's player_projections.xp.

    Rather than rebuilding a score from every rule (position-specific, changed across seasons,
    and several inputs like penalty misses/own goals aren't stored), start from the real
    total_points and swap only the luck-prone outcomes for their expectation:

        goals -> xG, assists -> xA (scaled to FPL's assist rate), clean sheet -> P(clean
        sheet | xGC), goals-conceded penalty -> E[floor(N/2)] with N ~ Poisson(xGC)

    Everything else (appearance, bonus, saves, defensive contribution, cards, ...) stays at its
    actual value, so total_points - expected_points is exactly the finishing/clean-sheet luck.
    FPL's expected_goals_conceded only covers the player's own time on the pitch, which is also
    what clean-sheet and goals-conceded points are judged on. NaN where there is no xG data
    (pre-2022/23, early 2022/23) or no scoring position (2024/25 assistant managers).
    """
    position = player_gw["player_code"].map(players.set_index("player_code")["position"])
    goal_pts = position.map(GOAL_POINTS)
    cs_pts = position.map(CLEAN_SHEET_POINTS)
    concedes = position.isin(CONCEDE_PENALTY_POSITIONS).astype(float)

    xgc = player_gw["expected_goals_conceded"].astype(float)
    cs_a, cs_b = CLEAN_SHEET_FIT
    actual = (
        goal_pts * player_gw["goals_scored"]
        + ASSIST_POINTS * player_gw["assists"]
        + cs_pts * player_gw["clean_sheets"]
        - concedes * (player_gw["goals_conceded"] // 2)
    )
    expected = (
        goal_pts * player_gw["expected_goals"].astype(float)
        + ASSIST_POINTS * FANTASY_ASSISTS_PER_XA * player_gw["expected_assists"].astype(float)
        # A clean sheet needs 60+ minutes.
        + cs_pts * (player_gw["minutes"] >= 60) * np.exp(-(cs_a + cs_b * xgc))
        # E[floor(N/2)] = (E[N] - P(N odd)) / 2, and P(N odd) = (1 - e^(-2*lambda)) / 2.
        - concedes * (xgc / 2 - (1 - np.exp(-2 * xgc)) / 4)
    )
    expected_points = player_gw["total_points"] - actual + expected
    # 2022/23 only has xG from GW16 on; before that the source zero-fills it rather than leaving
    # it NULL, which would read as "certain clean sheet, no attacking threat". A whole round
    # with no xG at all is missing data, not a real result.
    round_xg = player_gw.groupby("round")["expected_goals"].transform("sum")
    return player_gw.assign(expected_points=expected_points.where(round_xg > 0))


NULLABLE_SUMS = {"expected_points", "adjusted_points"}


def _stat_aggs(stats: list[str]) -> dict:
    """Named aggregations summing each stat. expected_points keeps NaN when every row is NaN -
    a plain sum would turn "no xG data" into 0, i.e. "expected to score nothing"."""
    return {s: (s, (lambda v: v.sum(min_count=1)) if s in NULLABLE_SUMS else "sum") for s in stats}


def _records(df: pd.DataFrame) -> list[dict]:
    records = df.to_dict(orient="records")
    for row in records:
        for k, v in row.items():
            if isinstance(v, float) and pd.isna(v):
                row[k] = None
    return records


def _apply_numeric_filters(df: pd.DataFrame, filters: list[NumericFilter]) -> pd.DataFrame:
    for f in filters:
        if f.column not in df.columns:
            continue
        if f.op == "gt":
            df = df[df[f.column] > f.value]
        elif f.op == "lt":
            df = df[df[f.column] < f.value]
    return df


def _apply_sort(df: pd.DataFrame, sort: SortSpec | None, default_column: str) -> pd.DataFrame:
    column = sort.column if sort and sort.column in df.columns else default_column
    ascending = sort.direction == "asc" if sort else False
    return df.sort_values(column, ascending=ascending, na_position="last")


def _rows_in_team_windows(player_gw: pd.DataFrame, teams: list[TeamRange]) -> pd.DataFrame:
    """Restrict player_gw_stats rows to each included team's own gameweek range."""
    if not teams:
        return player_gw.iloc[0:0]
    window_df = pd.DataFrame([(t.team_code, t.start_gw, t.end_gw) for t in teams],
                              columns=["team_code", "start_gw", "end_gw"])
    merged = player_gw.merge(window_df, on="team_code", how="inner")
    merged = merged[(merged["round"] >= merged["start_gw"]) & (merged["round"] <= merged["end_gw"])]
    return merged.drop(columns=["start_gw", "end_gw"])


def _defensive_contribution_hit_rate(rows: pd.DataFrame, players: pd.DataFrame) -> pd.DataFrame:
    """% of games played (minutes > 0) in which the player met their position's defensive
    contribution points threshold. Goalkeepers are excluded entirely (return NaN, not 0%),
    not just always-missing the threshold - they're not part of this scoring rule at all."""
    played = rows[rows["minutes"] > 0].merge(players[["player_code", "position"]], on="player_code", how="left")
    played = played.assign(threshold=played["position"].map(DC_THRESHOLD))
    played = played[played["threshold"].notna()]
    if played.empty:
        return pd.DataFrame(columns=["player_code", "defensive_contribution_hit_rate"])
    hit = played["defensive_contribution"] >= played["threshold"]
    result = played.assign(hit=hit).groupby("player_code")["hit"].agg(games_played="size", hits="sum").reset_index()
    result["defensive_contribution_hit_rate"] = (result["hits"] / result["games_played"] * 100).round(1)
    return result[["player_code", "defensive_contribution_hit_rate"]]


def _projection_totals(conn, filters: TableFilters) -> pd.DataFrame | None:
    """Projected points (summed) and expected minutes (averaged) over the requested horizon.

    Returns None when no horizon is requested, so the projection columns stay absent rather
    than appearing as a column of dashes."""
    if not (filters.projection_source and filters.projection_gameweeks):
        return None
    gws = _gameweek_list(filters.projection_gameweeks)
    rows = pd.read_sql_query(
        # xP sums (total points expected over the window) but xMins averages: expected minutes
        # is a per-match availability signal, and a summed 524 reads as nonsense next to the
        # 0-90 scale everyone knows it by.
        f"""SELECT player_code, SUM(xp) AS xp, AVG(xmins) AS xmins
           FROM player_projections
           WHERE season_id = ? AND source = ? AND round IN ({_placeholders(gws)})
           GROUP BY player_code""",
        conn,
        params=(filters.season_id, filters.projection_source, *gws),
    )
    return rows if not rows.empty else None


def _gameweek_list(gameweeks: list[int]) -> list[int]:
    """De-duplicated, sorted ints so the SQL placeholders line up with the params."""
    return sorted({int(g) for g in gameweeks})


def _placeholders(values: list) -> str:
    return ", ".join("?" for _ in values)


def _past_schedule_strength(rows: pd.DataFrame, fixtures: pd.DataFrame) -> pd.DataFrame:
    """Per player: `sos`, the mean FPL fixture difficulty (1 easy - 5 hard) of the fixtures
    behind `rows` - i.e. of exactly the matches the table's other numbers are drawn from, so it
    follows the gameweek windows, the opponent filter and Per Start. It's the context for a hot
    or cold run: 30 points against a 2.2 schedule and 30 against a 3.6 aren't the same form.
    NaN where the season's fixtures carry no ratings (the oldest archive seasons)."""
    if rows.empty or fixtures.empty or "team_h_difficulty" not in fixtures.columns:
        return pd.DataFrame(columns=["player_code", "sos"])
    rated = rows[["player_code", "fixture_id", "was_home"]].merge(
        fixtures[["fixture_id", "team_h_difficulty", "team_a_difficulty"]], on="fixture_id", how="left"
    )
    rated["difficulty"] = rated["team_h_difficulty"].where(rated["was_home"] == 1, rated["team_a_difficulty"])
    return rated.groupby("player_code")["difficulty"].mean().rename("sos").reset_index()


def query_players(conn, filters: TableFilters, per_start: bool) -> list[dict]:
    player_gw, fixtures, teams, players = load_season_frames(conn, filters.season_id)

    rows = _rows_in_team_windows(player_gw, filters.teams)
    if filters.opponent_team_codes is not None:
        rows = rows[rows["opponent_team_code"].isin(filters.opponent_team_codes)]
    if per_start:
        # Scope to starts only, and rate by number of starts rather than minutes: dividing by
        # minutes (the old "per 90" mode) let a player subbed off after 15 minutes with a lucky
        # goal look like a far better rate than one who played the full 90 - small-sample
        # extrapolation, not a real signal. Counting starts instead means a start is a start
        # regardless of exactly how long it lasted, so a 70-minute start and a 90-minute start
        # contribute equally to the denominator.
        rows = rows[rows["starts"] == 1]

    if rows.empty:
        return []

    agg = rows.groupby("player_code").agg(
        minutes=("minutes", "sum"),
        starts=("round", "size"),
        team_code=("team_code", "last"),
        price=("price", "last"),
        **_stat_aggs(PLAYER_STAT_COLUMNS),
    ).reset_index()

    agg = agg.merge(_defensive_contribution_hit_rate(rows, players), on="player_code", how="left")
    agg = agg.merge(_past_schedule_strength(rows, fixtures), on="player_code", how="left")

    if per_start:
        for c in PLAYER_STAT_COLUMNS:
            agg[c] = (agg[c] / agg["starts"]).where(agg["starts"] > 0)

    projections = _projection_totals(conn, filters)
    if projections is not None:
        agg = agg.merge(projections, on="player_code", how="left")

    agg["price"] = agg["price"] / 10.0
    agg = agg.merge(players, on="player_code", how="left")
    agg = agg.merge(teams[["team_code", "name"]].rename(columns={"name": "team_name"}), on="team_code", how="left")

    if filters.positions is not None:
        agg = agg[agg["position"].isin(filters.positions)]

    agg = _apply_numeric_filters(agg, filters.filters)
    agg = _apply_sort(agg, filters.sort, default_column="total_points")

    columns = (
        ["player_code", "web_name", "team_name", "position", "price", "selected_by_percent", "sos", "minutes"]
        + PLAYER_STAT_COLUMNS
        + ["defensive_contribution_hit_rate"]
    )
    if projections is not None:
        columns += ["xp", "xmins"]
    return _records(agg[columns])


def _schedule_strength(conn, season_id: str, gameweeks: list[int]) -> pd.DataFrame:
    """Per team over these gameweeks: `sos`, the mean of FPL's fixture difficulty ratings
    (1 easy - 5 hard) across the fixtures they play, and `fixtures`, those fixtures spelled out
    ("LEE (H) 2, NFO (A) 3"). A double gameweek contributes both fixtures; a blank contributes
    none and shows as "-", so the mean is over matches actually played - `fixture_count` is
    there to tell a kind run from a short one.

    FPL's own rating rather than one derived here: the bootstrap's attack/defence strength
    splits are all zero this season, and five gameweeks of xG is too little to rate opponents
    on. It is one number per fixture, so it can't tell a defender's fixture from a forward's."""
    empty = pd.DataFrame(columns=["team_code", "sos", "fixture_count", "fixtures"])
    if not gameweeks:
        return empty
    rows = conn.execute(
        f"""SELECT f.round, f.kickoff_time, f.team_h_code, f.team_a_code, f.team_h_difficulty, f.team_a_difficulty,
                   h.short_name AS h_name, a.short_name AS a_name
            FROM fixtures f
            JOIN teams h ON h.season_id = f.season_id AND h.team_code = f.team_h_code
            JOIN teams a ON a.season_id = f.season_id AND a.team_code = f.team_a_code
            WHERE f.season_id = ? AND f.round IN ({_placeholders(gameweeks)})
            ORDER BY f.round, f.kickoff_time""",
        (season_id, *gameweeks),
    ).fetchall()
    per_team: dict[int, list[tuple[int, str, int | None]]] = {}
    for r in rows:
        per_team.setdefault(r["team_h_code"], []).append((r["round"], f"{r['a_name']} (H)", r["team_h_difficulty"]))
        per_team.setdefault(r["team_a_code"], []).append((r["round"], f"{r['h_name']} (A)", r["team_a_difficulty"]))
    out = []
    for team_code, played in per_team.items():
        rated = [d for _, _, d in played if d is not None]
        by_round = {gw: [f"{label} {d}" if d is not None else label for g, label, d in played if g == gw] for gw in gameweeks}
        out.append(
            {
                "team_code": team_code,
                "sos": sum(rated) / len(rated) if rated else None,
                "fixture_count": len(played),
                "fixtures": ", ".join(" + ".join(by_round[gw]) or "-" for gw in gameweeks),
            }
        )
    return pd.DataFrame(out) if out else empty


def query_projections(conn, filters: TableFilters) -> dict:
    """One row per player for a projection source over the requested gameweek window: xP, xG,
    xA summed; xCS and xDC summed too, so they read as expected clean sheets / expected
    defensive-contribution hits over the window rather than a per-match probability; xMins
    averaged (same rule as _projection_totals, so the board's number is reproducible here);
    plus xP per projected gameweek and that rate per £m of price, for value comparisons.
    Also reports the span the source actually covers so the UI can say so.

    Rows follow the sidebar's team include/exclude and the panel's position filter, but not the
    per-team gameweek windows: those slice history, and projections are the other direction."""
    empty = {"gameweeks": [], "played_through": None, "rows": []}
    if not filters.projection_source:
        return empty
    selected = _gameweek_list(filters.projection_gameweeks or [])

    gameweeks = [int(r[0]) for r in conn.execute(
        "SELECT DISTINCT round FROM player_projections WHERE season_id = ? AND source = ? ORDER BY round",
        (filters.season_id, filters.projection_source),
    )]
    if not gameweeks:
        return empty

    player_gw, _fixtures, teams, players = load_season_frames(conn, filters.season_id)
    # Gameweeks with any stats in the DB have already been played, so the UI can flag them.
    played_through = int(player_gw["round"].max()) if not player_gw.empty else None

    totals = pd.read_sql_query(
        f"""SELECT player_code, SUM(xp) AS xp_total, AVG(xmins) AS xmins_avg, COUNT(*) AS gw_count,
                  SUM(xg) AS xg, SUM(xa) AS xa, SUM(xcs) AS xcs, SUM(xdc) AS xdc
           FROM player_projections
           WHERE season_id = ? AND source = ? AND round IN ({_placeholders(selected)})
           GROUP BY player_code""",
        conn,
        params=(filters.season_id, filters.projection_source, *selected),
    ) if selected else pd.DataFrame()
    if totals.empty:
        # Nothing selected, or the selection misses the source entirely; still list its players
        # so the panel isn't blank.
        totals = pd.read_sql_query(
            "SELECT DISTINCT player_code FROM player_projections WHERE season_id = ? AND source = ?",
            conn, params=(filters.season_id, filters.projection_source),
        )
        for c in ("xp_total", "xmins_avg", "gw_count", "xg", "xa", "xcs", "xdc"):
            totals[c] = float("nan")

    # Team and price come from the season roster, not from stats rows: a player who hasn't
    # played a minute yet (injured, new signing, benched) still has a projection worth seeing.
    # The latest gameweek price overrides the season-start one once the player has featured.
    roster = pd.read_sql_query(
        "SELECT player_code, team_code, start_cost / 10.0 AS price FROM player_season WHERE season_id = ?",
        conn, params=(filters.season_id,),
    )
    latest = (player_gw.sort_values("round").groupby("player_code")["price"].last() / 10.0).rename("latest_price")
    df = totals.merge(players, on="player_code", how="inner").merge(roster, on="player_code", how="left")
    df = df.merge(latest, left_on="player_code", right_index=True, how="left")
    df["price"] = df["latest_price"].fillna(df["price"])
    df = df.merge(teams[["team_code", "name"]].rename(columns={"name": "team_name"}), on="team_code", how="left")

    # Per-gameweek rate over the gameweeks the source actually projected inside the window (not the
    # window length, so a partially covered window isn't diluted), and that rate per £m of current
    # price. Computed before filtering/sorting so both work on them like any other column.
    df["xp_per_gw"] = df["xp_total"] / df["gw_count"]
    df["xp_per_gw_per_m"] = (df["xp_per_gw"] / df["price"]).where(df["price"] > 0)

    included = {t.team_code for t in filters.teams}
    df = df[df["team_code"].isin(included)]
    if filters.positions is not None:
        df = df[df["position"].isin(filters.positions)]
    df = df.merge(_schedule_strength(conn, filters.season_id, selected), on="team_code", how="left")
    df = _apply_numeric_filters(df, filters.filters)
    df = _apply_sort(df, filters.sort, default_column="xp_total")

    columns = ["player_code", "web_name", "team_name", "position", "price", "sos", "fixture_count", "fixtures",
               "xp_per_gw", "xp_per_gw_per_m", "xp_total", "xmins_avg", "xg", "xa", "xcs", "xdc"]
    return {"gameweeks": gameweeks, "played_through": played_through, "rows": _records(df[columns])}


def _fixture_team_xg(player_gw: pd.DataFrame) -> pd.Series:
    return player_gw.groupby(["fixture_id", "team_code"])["expected_goals"].sum()


def _team_window_stats(team_code: int, start: int, end: int, fixtures: pd.DataFrame,
                        fixture_team_xg: pd.Series, opponent_filter: set[int] | None) -> dict:
    mask = (fixtures["round"] >= start) & (fixtures["round"] <= end) & (
        (fixtures["team_h_code"] == team_code) | (fixtures["team_a_code"] == team_code)
    )
    games = fixtures[mask]
    played = 0
    goals_for = goals_against = xg_for = xg_against = 0.0
    points = 0
    for _, g in games.iterrows():
        is_home = g["team_h_code"] == team_code
        opponent = g["team_a_code"] if is_home else g["team_h_code"]
        if opponent_filter is not None and opponent not in opponent_filter:
            continue
        gf = g["team_h_score"] if is_home else g["team_a_score"]
        ga = g["team_a_score"] if is_home else g["team_h_score"]
        played += 1
        goals_for += gf
        goals_against += ga
        xg_for += fixture_team_xg.get((g["fixture_id"], team_code), 0.0)
        xg_against += fixture_team_xg.get((g["fixture_id"], opponent), 0.0)
        if gf > ga:
            points += 3
        elif gf == ga:
            points += 1
    return {
        "played": played,
        "goals_for": goals_for,
        "goals_against": goals_against,
        "xg_for": xg_for,
        "xg_against": xg_against,
        "points": points,
    }


def query_teams(conn, filters: TableFilters) -> list[dict]:
    player_gw, fixtures, teams_df, _players = load_season_frames(conn, filters.season_id)
    fixture_team_xg = _fixture_team_xg(player_gw)
    opponent_filter = set(filters.opponent_team_codes) if filters.opponent_team_codes is not None else None

    team_names = dict(zip(teams_df["team_code"], teams_df["name"]))
    results = []
    for t in filters.teams:
        own = _team_window_stats(t.team_code, t.start_gw, t.end_gw, fixtures, fixture_team_xg, opponent_filter)

        opp_xg_samples, opp_xga_samples = [], []
        mask = (fixtures["round"] >= t.start_gw) & (fixtures["round"] <= t.end_gw) & (
            (fixtures["team_h_code"] == t.team_code) | (fixtures["team_a_code"] == t.team_code)
        )
        for _, g in fixtures[mask].iterrows():
            is_home = g["team_h_code"] == t.team_code
            opponent = g["team_a_code"] if is_home else g["team_h_code"]
            if opponent_filter is not None and opponent not in opponent_filter:
                continue
            opp_stats = _team_window_stats(opponent, t.start_gw, t.end_gw, fixtures, fixture_team_xg, None)
            if opp_stats["played"] > 0:
                opp_xg_samples.append(opp_stats["xg_for"] / opp_stats["played"])
                opp_xga_samples.append(opp_stats["xg_against"] / opp_stats["played"])

        results.append(
            {
                "team_code": t.team_code,
                "name": team_names.get(t.team_code, "?"),
                "played": own["played"],
                "points": own["points"],
                "goals_scored": own["goals_for"],
                "expected_goals": round(own["xg_for"], 2),
                "goals_conceded": own["goals_against"],
                "expected_goals_conceded": round(own["xg_against"], 2),
                "goal_difference": own["goals_for"] - own["goals_against"],
                "opponent_expected_goals": round(sum(opp_xg_samples) / len(opp_xg_samples), 2) if opp_xg_samples else None,
                "opponent_expected_goals_conceded": round(sum(opp_xga_samples) / len(opp_xga_samples), 2) if opp_xga_samples else None,
            }
        )

    df = pd.DataFrame(results)
    if df.empty:
        return []
    df = df.sort_values(["points", "goal_difference", "goals_scored"], ascending=False).reset_index(drop=True)
    df["table_place"] = df.index + 1

    df = _apply_numeric_filters(df, filters.filters)
    df = _apply_sort(df, filters.sort, default_column="table_place")
    if filters.sort is None:
        df = df.sort_values("table_place", ascending=True)

    columns = [
        "team_code", "name", "table_place", "goals_scored", "expected_goals",
        "goals_conceded", "expected_goals_conceded", "goal_difference",
        "opponent_expected_goals", "opponent_expected_goals_conceded",
    ]
    return _records(df[columns])


TEAM_SERIES_STATS = ["goals_scored", "expected_goals", "goals_conceded", "expected_goals_conceded"]


def _player_series(conn, filters: TableFilters, entity_codes: list[int], stats: list[str],
                    per_start: bool) -> list[dict]:
    """Per-(player, gameweek) values for the chart builder - same filters as query_players,
    just not collapsed across rounds."""
    player_gw, _fixtures, _teams, players = load_season_frames(conn, filters.season_id)

    rows = _rows_in_team_windows(player_gw, filters.teams)
    if filters.opponent_team_codes is not None:
        rows = rows[rows["opponent_team_code"].isin(filters.opponent_team_codes)]
    if per_start:
        rows = rows[rows["starts"] == 1]
    rows = rows[rows["player_code"].isin(entity_codes)]
    if rows.empty:
        return []

    agg = rows.groupby(["player_code", "round"]).agg(
        minutes=("minutes", "sum"),
        starts=("round", "size"),
        **_stat_aggs(stats),
    ).reset_index()

    if per_start:
        for s in stats:
            agg[s] = (agg[s] / agg["starts"]).where(agg["starts"] > 0)

    agg = agg.merge(players[["player_code", "web_name"]], on="player_code", how="left")
    agg = agg.rename(columns={"player_code": "entity_code", "web_name": "name"})
    columns = ["entity_code", "name", "round"] + stats
    return _records(agg[columns].sort_values(["entity_code", "round"]))


def _team_series(conn, filters: TableFilters, entity_codes: list[int], stats: list[str]) -> list[dict]:
    """Per-(team, gameweek) values for the chart builder. Only TEAM_SERIES_STATS are supported -
    goals/goals_conceded come from fixtures (authoritative score), expected_goals(_conceded) from
    summing player-level expected_goals per fixture, same reasoning as query_teams."""
    player_gw, fixtures, teams_df, _players = load_season_frames(conn, filters.season_id)
    fixture_team_xg = _fixture_team_xg(player_gw)
    opponent_filter = set(filters.opponent_team_codes) if filters.opponent_team_codes is not None else None
    team_range = {t.team_code: (t.start_gw, t.end_gw) for t in filters.teams}
    team_names = dict(zip(teams_df["team_code"], teams_df["name"]))

    results = []
    for team_code in entity_codes:
        if team_code not in team_range:
            continue
        start, end = team_range[team_code]
        mask = (fixtures["round"] >= start) & (fixtures["round"] <= end) & (
            (fixtures["team_h_code"] == team_code) | (fixtures["team_a_code"] == team_code)
        )
        by_round: dict[int, dict] = {}
        for _, g in fixtures[mask].iterrows():
            is_home = g["team_h_code"] == team_code
            opponent = g["team_a_code"] if is_home else g["team_h_code"]
            if opponent_filter is not None and opponent not in opponent_filter:
                continue
            r = by_round.setdefault(
                int(g["round"]),
                {"goals_scored": 0.0, "goals_conceded": 0.0, "expected_goals": 0.0, "expected_goals_conceded": 0.0},
            )
            r["goals_scored"] += float(g["team_h_score"] if is_home else g["team_a_score"])
            r["goals_conceded"] += float(g["team_a_score"] if is_home else g["team_h_score"])
            r["expected_goals"] += float(fixture_team_xg.get((g["fixture_id"], team_code), 0.0))
            r["expected_goals_conceded"] += float(fixture_team_xg.get((g["fixture_id"], opponent), 0.0))
        for rnd, vals in sorted(by_round.items()):
            row = {"entity_code": team_code, "name": team_names.get(team_code, "?"), "round": rnd}
            for s in stats:
                row[s] = vals.get(s)
            results.append(row)

    return _records(pd.DataFrame(results)) if results else []


def query_series(conn, filters: TableFilters, entity_type: str, entity_codes: list[int],
                  stats: list[str], per_start: bool) -> list[dict]:
    if entity_type == "player":
        return _player_series(conn, filters, entity_codes, stats, per_start)
    return _team_series(conn, filters, entity_codes, stats)
