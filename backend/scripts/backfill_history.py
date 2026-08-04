"""One-time (or occasional) ingestion of a past season's data from the vaastav/Fantasy-Premier-League
community archive into the local SQLite database.

Most seasons ship teams.csv + fixtures.csv directly. Three early seasons don't, and need
fallback reconstruction (see resolve_teams / resolve_fixtures below):
  - 2016-17, 2017-18: no teams.csv, no fixtures.csv, no 'team' column in the gw data.
  - 2018-19: no teams.csv (but has fixtures.csv and a raw.json bootstrap snapshot).

Usage:
    backend/.venv/bin/python backend/scripts/backfill_history.py 2025-26
    backend/.venv/bin/python backend/scripts/backfill_history.py --all
"""

import argparse
import json
import sys
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from urllib.error import HTTPError

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_connection, init_db
from app.seasons import POSITION_BY_ELEMENT_TYPE, SEASONS, TEAM_SHORT_NAME_BY_NAME

RAW_BASE = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"


def fetch_csv(season_id: str, relative_path: str) -> pd.DataFrame:
    url = f"{RAW_BASE}/{season_id}/{relative_path}"
    try:
        return pd.read_csv(url)
    except UnicodeDecodeError:
        # A few early-season files aren't UTF-8 (accented player names in a legacy encoding).
        return pd.read_csv(url, encoding="latin1")


def try_fetch_csv(season_id: str, relative_path: str):
    try:
        return fetch_csv(season_id, relative_path)
    except HTTPError as e:
        if e.code == 404:
            return None
        raise


def try_fetch_json(season_id: str, relative_path: str):
    try:
        with urllib.request.urlopen(f"{RAW_BASE}/{season_id}/{relative_path}") as resp:
            return json.load(resp)
    except HTTPError as e:
        if e.code == 404:
            return None
        raise


_master_team_list = None


def master_team_list() -> pd.DataFrame:
    global _master_team_list
    if _master_team_list is None:
        _master_team_list = pd.read_csv(f"{RAW_BASE}/master_team_list.csv")
    return _master_team_list


def resolve_teams(season_id: str, players_df: pd.DataFrame) -> list[dict]:
    """Returns [{team_code, season_team_id, name, short_name}, ...] for the season,
    trying teams.csv, then raw.json, then (players_raw.csv team_code + master_team_list.csv name)."""
    teams_df = try_fetch_csv(season_id, "teams.csv")
    if teams_df is not None:
        return [
            {
                "team_code": int(r["code"]),
                "season_team_id": int(r["id"]),
                "name": r["name"],
                "short_name": r["short_name"],
            }
            for _, r in teams_df.iterrows()
        ]

    raw_json = try_fetch_json(season_id, "raw.json")
    if raw_json is not None:
        return [
            {
                "team_code": t["code"],
                "season_team_id": t["id"],
                "name": t["name"],
                "short_name": t["short_name"],
            }
            for t in raw_json["teams"]
        ]

    # No teams.csv, no raw.json (2016-17, 2017-18 only): every player row in players_raw.csv
    # already carries their team's real stable code, so derive season_team_id -> code from
    # that directly (mode, in case of any stray inconsistency) rather than guessing. Only the
    # human-readable name has to come from master_team_list.csv, which has no code column.
    season_team_id_to_code = (
        players_df.groupby("team")["team_code"].agg(lambda s: s.mode().iat[0]).to_dict()
    )
    sub = master_team_list()
    sub = sub[sub["season"] == season_id]
    if sub.empty:
        raise RuntimeError(f"No team data source found for {season_id} (no teams.csv, raw.json, or master_team_list rows)")
    rows = []
    for _, r in sub.iterrows():
        season_team_id = int(r["team"])
        code = season_team_id_to_code.get(season_team_id)
        if code is None:
            raise ValueError(f"No players found for team id {season_team_id} in {season_id} players_raw.csv")
        name = r["team_name"]
        rows.append(
            {
                "team_code": int(code),
                "season_team_id": season_team_id,
                "name": name,
                "short_name": TEAM_SHORT_NAME_BY_NAME.get(name, name[:3].upper()),
            }
        )
    return rows


def resolve_fixtures(season_id, gws_df, season_team_id_to_code, element_id_to_team_code) -> list[dict]:
    fixtures_df = try_fetch_csv(season_id, "fixtures.csv")
    if fixtures_df is not None:
        rows = []
        for _, row in fixtures_df.iterrows():
            team_h_code = season_team_id_to_code.get(row["team_h"])
            team_a_code = season_team_id_to_code.get(row["team_a"])
            if team_h_code is None or team_a_code is None:
                continue
            rows.append(
                {
                    "fixture_id": int(row["id"]),
                    "round": int(row["event"]) if pd.notna(row["event"]) else None,
                    "kickoff_time": row.get("kickoff_time"),
                    "team_h_code": team_h_code,
                    "team_a_code": team_a_code,
                    "team_h_score": int(row["team_h_score"]) if pd.notna(row["team_h_score"]) else None,
                    "team_a_score": int(row["team_a_score"]) if pd.notna(row["team_a_score"]) else None,
                }
            )
        return rows

    # No fixtures.csv (2016-17, 2017-18 only): reconstruct from the gw data. Each player's
    # own team for a given row falls back to their season-end players_raw snapshot (there's
    # no per-round team field this far back), which is wrong for the handful of players who
    # transferred mid-season. Majority-voting across every row that references the same
    # fixture cancels that noise out.
    by_fixture = defaultdict(list)
    for _, row in gws_df.iterrows():
        own_code = element_id_to_team_code.get(row["element"])
        opp_code = season_team_id_to_code.get(row["opponent_team"])
        if own_code is None or opp_code is None:
            continue
        was_home = str(row["was_home"]).strip().lower() == "true"
        home_code, away_code = (own_code, opp_code) if was_home else (opp_code, own_code)
        by_fixture[int(row["fixture"])].append(
            (
                int(row["round"]),
                home_code,
                away_code,
                int(row["team_h_score"]) if pd.notna(row["team_h_score"]) else None,
                int(row["team_a_score"]) if pd.notna(row["team_a_score"]) else None,
            )
        )

    rows = []
    for fixture_id, entries in by_fixture.items():
        rows.append(
            {
                "fixture_id": fixture_id,
                "round": Counter(e[0] for e in entries).most_common(1)[0][0],
                "kickoff_time": None,
                "team_h_code": Counter(e[1] for e in entries).most_common(1)[0][0],
                "team_a_code": Counter(e[2] for e in entries).most_common(1)[0][0],
                "team_h_score": Counter(e[3] for e in entries).most_common(1)[0][0],
                "team_a_score": Counter(e[4] for e in entries).most_common(1)[0][0],
            }
        )
    return rows


def backfill_season(conn, season_id: str) -> None:
    print(f"[{season_id}] fetching players_raw.csv, gws/merged_gw.csv ...")
    players_df = fetch_csv(season_id, "players_raw.csv")
    gws_df = fetch_csv(season_id, "gws/merged_gw.csv")
    # The upstream archive occasionally contains exact-duplicate rows for the same
    # player/round/fixture; keep the first occurrence.
    gws_df = gws_df.drop_duplicates(subset=["element", "round", "fixture"], keep="first")

    print(f"[{season_id}] resolving team identities ...")
    team_rows = resolve_teams(season_id, players_df)
    season_team_id_to_code = {r["season_team_id"]: r["team_code"] for r in team_rows}
    team_name_to_code = {r["name"]: r["team_code"] for r in team_rows}
    element_id_to_code = dict(zip(players_df["id"], players_df["code"]))
    element_id_to_team_code = dict(zip(players_df["id"], players_df["team_code"]))

    print(f"[{season_id}] resolving fixtures ...")
    fixture_rows = resolve_fixtures(season_id, gws_df, season_team_id_to_code, element_id_to_team_code)

    cur = conn.cursor()

    # -- teams --
    for r in team_rows:
        cur.execute(
            """INSERT INTO teams (season_id, team_code, season_team_id, name, short_name)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(season_id, team_code) DO UPDATE SET
                 season_team_id=excluded.season_team_id,
                 name=excluded.name,
                 short_name=excluded.short_name""",
            (season_id, r["team_code"], r["season_team_id"], r["name"], r["short_name"]),
        )

    # -- players + player_season --
    has_cost_change = "cost_change_start" in players_df.columns
    for _, row in players_df.iterrows():
        player_code = int(row["code"])
        cur.execute(
            """INSERT INTO players (player_code, first_name, second_name, web_name)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(player_code) DO UPDATE SET
                 first_name=excluded.first_name,
                 second_name=excluded.second_name,
                 web_name=excluded.web_name""",
            (player_code, row["first_name"], row["second_name"], row["web_name"]),
        )
        now_cost = int(row["now_cost"])
        start_cost = now_cost - int(row["cost_change_start"]) if has_cost_change else now_cost
        cur.execute(
            """INSERT INTO player_season
                 (season_id, player_code, season_element_id, team_code, position, start_cost)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(season_id, player_code) DO UPDATE SET
                 season_element_id=excluded.season_element_id,
                 team_code=excluded.team_code,
                 position=excluded.position,
                 start_cost=excluded.start_cost""",
            (
                season_id,
                player_code,
                int(row["id"]),
                int(row["team_code"]),
                POSITION_BY_ELEMENT_TYPE.get(int(row["element_type"]), "UNK"),
                start_cost,
            ),
        )

    # -- fixtures --
    for r in fixture_rows:
        cur.execute(
            """INSERT INTO fixtures
                 (season_id, fixture_id, round, kickoff_time, team_h_code, team_a_code,
                  team_h_score, team_a_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(season_id, fixture_id) DO UPDATE SET
                 round=excluded.round,
                 kickoff_time=excluded.kickoff_time,
                 team_h_code=excluded.team_h_code,
                 team_a_code=excluded.team_a_code,
                 team_h_score=excluded.team_h_score,
                 team_a_score=excluded.team_a_score""",
            (
                season_id,
                r["fixture_id"],
                r["round"],
                r["kickoff_time"],
                r["team_h_code"],
                r["team_a_code"],
                r["team_h_score"],
                r["team_a_score"],
            ),
        )

    # -- player_gw_stats --
    has_team_col = "team" in gws_df.columns

    def col(row, name):
        return row[name] if name in gws_df.columns and pd.notna(row.get(name)) else None

    cur.execute("DELETE FROM player_gw_stats WHERE season_id = ?", (season_id,))
    rows_to_insert = []
    skipped_gw_rows = 0
    for _, row in gws_df.iterrows():
        player_code = element_id_to_code.get(row["element"])
        team_code = (
            team_name_to_code.get(row["team"]) if has_team_col else element_id_to_team_code.get(row["element"])
        )
        if player_code is None or team_code is None:
            skipped_gw_rows += 1
            continue
        opponent_team_code = season_team_id_to_code.get(row["opponent_team"])
        rows_to_insert.append(
            (
                season_id,
                int(player_code),
                int(row["round"]),
                int(row["fixture"]),
                int(team_code),
                int(opponent_team_code) if opponent_team_code is not None else None,
                1 if str(row["was_home"]).strip().lower() == "true" else 0,
                int(col(row, "minutes") or 0),
                int(col(row, "starts")) if col(row, "starts") is not None else None,
                int(col(row, "goals_scored") or 0),
                int(col(row, "assists") or 0),
                int(col(row, "clean_sheets") or 0),
                int(col(row, "goals_conceded") or 0),
                int(col(row, "bonus") or 0),
                int(col(row, "bps") or 0),
                int(col(row, "total_points") or 0),
                float(col(row, "expected_goals")) if col(row, "expected_goals") is not None else None,
                float(col(row, "expected_assists")) if col(row, "expected_assists") is not None else None,
                float(col(row, "expected_goal_involvements"))
                if col(row, "expected_goal_involvements") is not None
                else None,
                float(col(row, "expected_goals_conceded"))
                if col(row, "expected_goals_conceded") is not None
                else None,
                int(col(row, "defensive_contribution"))
                if col(row, "defensive_contribution") is not None
                else None,
                int(col(row, "value")) if col(row, "value") is not None else None,
            )
        )

    cur.executemany(
        """INSERT INTO player_gw_stats
             (season_id, player_code, round, fixture_id, team_code, opponent_team_code, was_home,
              minutes, starts, goals_scored, assists, clean_sheets, goals_conceded, bonus, bps,
              total_points, expected_goals, expected_assists, expected_goal_involvements,
              expected_goals_conceded, defensive_contribution, price)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows_to_insert,
    )

    cur.execute("UPDATE seasons SET backfilled = 1 WHERE id = ?", (season_id,))
    conn.commit()
    print(
        f"[{season_id}] done: {len(team_rows)} teams, {len(players_df)} players, "
        f"{len(fixture_rows)} fixtures, {len(rows_to_insert)}/{len(gws_df)} gw-stat rows "
        f"(skipped {skipped_gw_rows} gw rows due to unmapped ids)"
    )


def seed_seasons(conn) -> None:
    cur = conn.cursor()
    for s in SEASONS:
        cur.execute(
            """INSERT INTO seasons (id, label, start_date, end_date)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(id) DO NOTHING""",
            (s["id"], s["label"], s["start_date"], s["end_date"]),
        )
    conn.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("season", nargs="?", help="Season id, e.g. 2025-26")
    parser.add_argument("--all", action="store_true", help="Backfill every known season")
    args = parser.parse_args()

    if not args.season and not args.all:
        parser.error("pass a season id (e.g. 2025-26) or --all")

    init_db()
    conn = get_connection()
    seed_seasons(conn)

    targets = [s["id"] for s in SEASONS] if args.all else [args.season]
    for season_id in targets:
        backfill_season(conn, season_id)

    conn.close()


if __name__ == "__main__":
    main()
