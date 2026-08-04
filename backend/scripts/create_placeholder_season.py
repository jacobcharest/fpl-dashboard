"""One-off: populate a not-yet-started season with the previous season's fixtures/stats as a
placeholder, overlaid with real current prices pulled from the live FPL API (revealed
pre-season, ahead of any real match data). Marks the season `is_placeholder` so the UI can show
a banner.

Once the source archive (vaastav/Fantasy-Premier-League) creates a real folder for the target
season - which happens once it actually starts - just re-run backfill_history.py for that
season. That's idempotent and will cleanly overwrite everything this script wrote, including
resetting is_placeholder to 0 (backfill_season's UPDATE only touches `backfilled`, so run
`UPDATE seasons SET is_placeholder = 0 WHERE id = '...'` once, or just delete and re-seed the
season row - see DESIGN.md).

Usage:
    backend/.venv/bin/python backend/scripts/create_placeholder_season.py 2025-26 2026-27
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_connection, init_db
from app.refresh import seed_seasons

LIVE_BOOTSTRAP_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"


def fetch_live_prices() -> dict[int, int]:
    """Returns {player_code: now_cost} from the live FPL API. now_cost is price * 10, same
    convention as player_season.start_cost / player_gw_stats.price."""
    req = urllib.request.Request(LIVE_BOOTSTRAP_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp:
        data = json.load(resp)
    return {e["code"]: e["now_cost"] for e in data["elements"]}


def create_placeholder_season(conn, source_season_id: str, target_season_id: str) -> dict:
    cur = conn.cursor()

    cur.execute(
        """INSERT INTO teams (season_id, team_code, season_team_id, name, short_name)
           SELECT ?, team_code, season_team_id, name, short_name FROM teams WHERE season_id = ?
           ON CONFLICT(season_id, team_code) DO NOTHING""",
        (target_season_id, source_season_id),
    )
    cur.execute(
        """INSERT INTO player_season (season_id, player_code, season_element_id, team_code, position, start_cost)
           SELECT ?, player_code, season_element_id, team_code, position, start_cost
           FROM player_season WHERE season_id = ?
           ON CONFLICT(season_id, player_code) DO NOTHING""",
        (target_season_id, source_season_id),
    )
    cur.execute(
        """INSERT INTO fixtures
             (season_id, fixture_id, round, kickoff_time, team_h_code, team_a_code, team_h_score, team_a_score)
           SELECT ?, fixture_id, round, kickoff_time, team_h_code, team_a_code, team_h_score, team_a_score
           FROM fixtures WHERE season_id = ?
           ON CONFLICT(season_id, fixture_id) DO NOTHING""",
        (target_season_id, source_season_id),
    )
    cur.execute("DELETE FROM player_gw_stats WHERE season_id = ?", (target_season_id,))
    cur.execute(
        """INSERT INTO player_gw_stats
             (season_id, player_code, round, fixture_id, team_code, opponent_team_code, was_home,
              minutes, starts, goals_scored, assists, clean_sheets, goals_conceded, bonus, bps,
              total_points, expected_goals, expected_assists, expected_goal_involvements,
              expected_goals_conceded, defensive_contribution, saves, yellow_cards, red_cards,
              influence, creativity, threat, ict_index, price)
           SELECT ?, player_code, round, fixture_id, team_code, opponent_team_code, was_home,
              minutes, starts, goals_scored, assists, clean_sheets, goals_conceded, bonus, bps,
              total_points, expected_goals, expected_assists, expected_goal_involvements,
              expected_goals_conceded, defensive_contribution, saves, yellow_cards, red_cards,
              influence, creativity, threat, ict_index, price
           FROM player_gw_stats WHERE season_id = ?""",
        (target_season_id, source_season_id),
    )
    conn.commit()

    live_prices = fetch_live_prices()
    players_priced = 0
    for player_code, now_cost in live_prices.items():
        cur.execute(
            "UPDATE player_season SET start_cost = ? WHERE season_id = ? AND player_code = ?",
            (now_cost, target_season_id, player_code),
        )
        if cur.rowcount:
            players_priced += 1
            cur.execute(
                "UPDATE player_gw_stats SET price = ? WHERE season_id = ? AND player_code = ?",
                (now_cost, target_season_id, player_code),
            )
    conn.commit()

    cur.execute(
        "UPDATE seasons SET backfilled = 1, is_placeholder = 1 WHERE id = ?", (target_season_id,)
    )
    conn.commit()

    return {
        "target_season_id": target_season_id,
        "source_season_id": source_season_id,
        "players_priced_from_live_data": players_priced,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source_season", help="Season to clone data from, e.g. 2025-26")
    parser.add_argument("target_season", help="Not-yet-started season to populate, e.g. 2026-27")
    args = parser.parse_args()

    init_db()
    conn = get_connection()
    seed_seasons(conn)
    summary = create_placeholder_season(conn, args.source_season, args.target_season)
    conn.close()
    print(
        f"Placeholder season {summary['target_season_id']} created from {summary['source_season_id']}: "
        f"{summary['players_priced_from_live_data']} players priced from the live FPL API."
    )


if __name__ == "__main__":
    main()
