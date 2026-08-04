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

import pandas as pd

PLAYER_STAT_COLUMNS = [
    "total_points",
    "goals_scored",
    "expected_goals",
    "assists",
    "expected_assists",
    "expected_goal_involvements",
    "clean_sheets",
    "expected_goals_conceded",
    "defensive_contribution",
    "bonus",
]


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
        """SELECT p.player_code, p.web_name, ps.position
           FROM player_season ps JOIN players p ON p.player_code = ps.player_code
           WHERE ps.season_id = ?""",
        conn,
        params=(season_id,),
    )
    return player_gw, fixtures, teams, players


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


def query_players(conn, filters: TableFilters, per90: bool, starts_only: bool) -> list[dict]:
    player_gw, _fixtures, teams, players = load_season_frames(conn, filters.season_id)

    rows = _rows_in_team_windows(player_gw, filters.teams)
    if filters.opponent_team_codes is not None:
        rows = rows[rows["opponent_team_code"].isin(filters.opponent_team_codes)]
    if starts_only:
        rows = rows[rows["starts"] == 1]

    if rows.empty:
        return []

    agg = rows.groupby("player_code").agg(
        minutes=("minutes", "sum"),
        team_code=("team_code", "last"),
        price=("price", "last"),
        **{c: (c, "sum") for c in PLAYER_STAT_COLUMNS},
    ).reset_index()

    if per90:
        for c in PLAYER_STAT_COLUMNS:
            agg[c] = (agg[c] / agg["minutes"] * 90).where(agg["minutes"] > 0)

    agg["price"] = agg["price"] / 10.0
    agg = agg.merge(players, on="player_code", how="left")
    agg = agg.merge(teams[["team_code", "name"]].rename(columns={"name": "team_name"}), on="team_code", how="left")

    agg = _apply_numeric_filters(agg, filters.filters)
    agg = _apply_sort(agg, filters.sort, default_column="total_points")

    columns = ["player_code", "web_name", "team_name", "position", "price", "minutes"] + PLAYER_STAT_COLUMNS
    return _records(agg[columns])


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
