"""One-time (or occasional) ingestion of a past season's data from the vaastav/Fantasy-Premier-League
community archive into the local SQLite database.

Usage:
    backend/.venv/bin/python backend/scripts/backfill_history.py 2025-26
    backend/.venv/bin/python backend/scripts/backfill_history.py --all
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_connection, init_db
from app.seasons import POSITION_BY_ELEMENT_TYPE, SEASONS

RAW_BASE = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"


def fetch_csv(season_id: str, relative_path: str) -> pd.DataFrame:
    url = f"{RAW_BASE}/{season_id}/{relative_path}"
    return pd.read_csv(url)


def backfill_season(conn, season_id: str) -> None:
    print(f"[{season_id}] fetching teams.csv, players_raw.csv, fixtures.csv, gws/merged_gw.csv ...")
    teams_df = fetch_csv(season_id, "teams.csv")
    players_df = fetch_csv(season_id, "players_raw.csv")
    fixtures_df = fetch_csv(season_id, "fixtures.csv")
    gws_df = fetch_csv(season_id, "gws/merged_gw.csv")
    # The upstream archive occasionally contains exact-duplicate rows for the same
    # player/round/fixture; keep the first occurrence.
    gws_df = gws_df.drop_duplicates(subset=["element", "round", "fixture"], keep="first")

    season_team_id_to_code = dict(zip(teams_df["id"], teams_df["code"]))
    team_name_to_code = dict(zip(teams_df["name"], teams_df["code"]))
    element_id_to_code = dict(zip(players_df["id"], players_df["code"]))

    cur = conn.cursor()

    # -- teams --
    for _, row in teams_df.iterrows():
        cur.execute(
            """INSERT INTO teams (season_id, team_code, season_team_id, name, short_name)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(season_id, team_code) DO UPDATE SET
                 season_team_id=excluded.season_team_id,
                 name=excluded.name,
                 short_name=excluded.short_name""",
            (season_id, int(row["code"]), int(row["id"]), row["name"], row["short_name"]),
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
    skipped_fixtures = 0
    for _, row in fixtures_df.iterrows():
        team_h_code = season_team_id_to_code.get(row["team_h"])
        team_a_code = season_team_id_to_code.get(row["team_a"])
        if team_h_code is None or team_a_code is None:
            skipped_fixtures += 1
            continue
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
                int(row["id"]),
                int(row["event"]) if pd.notna(row["event"]) else None,
                row["kickoff_time"],
                team_h_code,
                team_a_code,
                int(row["team_h_score"]) if pd.notna(row["team_h_score"]) else None,
                int(row["team_a_score"]) if pd.notna(row["team_a_score"]) else None,
            ),
        )

    # -- player_gw_stats --
    def col(row, name):
        return row[name] if name in gws_df.columns and pd.notna(row.get(name)) else None

    cur.execute("DELETE FROM player_gw_stats WHERE season_id = ?", (season_id,))
    rows_to_insert = []
    skipped_gw_rows = 0
    for _, row in gws_df.iterrows():
        player_code = element_id_to_code.get(row["element"])
        team_code = team_name_to_code.get(row["team"])
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

    cur.execute(
        "UPDATE seasons SET backfilled = 1 WHERE id = ?",
        (season_id,),
    )
    conn.commit()
    print(
        f"[{season_id}] done: {len(teams_df)} teams, {len(players_df)} players, "
        f"{len(fixtures_df) - skipped_fixtures}/{len(fixtures_df)} fixtures, "
        f"{len(rows_to_insert)}/{len(gws_df)} gw-stat rows "
        f"(skipped {skipped_fixtures} fixtures, {skipped_gw_rows} gw rows due to unmapped ids)"
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
