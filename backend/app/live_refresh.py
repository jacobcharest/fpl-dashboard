"""Ingestion of the *current* season straight from the live FPL API.

The community archive that app/refresh.py reads (vaastav/Fantasy-Premier-League) is
usually a day or so behind, but early in a season it can lag by several gameweeks - at
the time of writing it held GW1 while GW3 had finished. For the live season the FPL API
itself is the source of truth the archive is derived from, so reading it directly is
both fresher and no less accurate. Past seasons keep using the archive: the FPL API only
serves the season in progress.

Endpoints used (all public, no login):

- ``bootstrap-static/``: teams, players, current prices, and which gameweeks have
  started.
- ``fixtures/``: every fixture with kickoff, scores and FDR.
- ``event/{gw}/live/``: every player's stats for one gameweek, with an ``explain`` block
  that says which fixture(s) the points came from.

The ``live`` payload aggregates a player's stats across the whole gameweek. That is
exact for the usual one-fixture round; for a double gameweek the per-fixture split is
pulled from ``element-summary/{id}/`` for just the players involved, so the request
count stays small.
"""

import json
import urllib.request
from datetime import datetime, timezone

from app.seasons import POSITION_BY_ELEMENT_TYPE

API_BASE = "https://fantasy.premierleague.com/api"

STAT_COLUMNS = [
    "minutes",
    "starts",
    "goals_scored",
    "assists",
    "clean_sheets",
    "goals_conceded",
    "bonus",
    "bps",
    "total_points",
    "expected_goals",
    "expected_assists",
    "expected_goal_involvements",
    "expected_goals_conceded",
    "defensive_contribution",
    "saves",
    "yellow_cards",
    "red_cards",
    "influence",
    "creativity",
    "threat",
    "ict_index",
]
INT_STATS = {
    "minutes",
    "starts",
    "goals_scored",
    "assists",
    "clean_sheets",
    "goals_conceded",
    "bonus",
    "bps",
    "total_points",
    "defensive_contribution",
    "saves",
    "yellow_cards",
    "red_cards",
}


def _get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def fetch_bootstrap() -> dict:
    return _get_json(f"{API_BASE}/bootstrap-static/")


def fetch_fixtures() -> list[dict]:
    return _get_json(f"{API_BASE}/fixtures/")


def fetch_event_live(event: int) -> list[dict]:
    return _get_json(f"{API_BASE}/event/{event}/live/")["elements"]


def fetch_element_history(element_id: int) -> list[dict]:
    return _get_json(f"{API_BASE}/element-summary/{element_id}/")["history"]


def started_events(bootstrap: dict) -> list[int]:
    """Gameweeks with at least one kickoff so far, in order.

    Parameters
    ----------
    bootstrap : dict
        The ``bootstrap-static`` payload.

    Returns
    -------
    list[int]
        Finished gameweeks plus the in-progress one, if any.
    """
    return sorted(
        e["id"] for e in bootstrap["events"] if e.get("finished") or e.get("is_current")
    )


def _coerce(name: str, value):
    if value is None:
        return None
    return int(value) if name in INT_STATS else float(value)


def _stat_row(
    season_id,
    player_code,
    event,
    fixture_id,
    team_code,
    opponent_code,
    was_home,
    stats: dict,
    price: int,
) -> tuple:
    values = [_coerce(c, stats.get(c)) for c in STAT_COLUMNS]
    return (
        season_id,
        player_code,
        event,
        fixture_id,
        team_code,
        opponent_code,
        was_home,
        *values,
        price,
    )


def refresh_live_season(conn, season_id: str, bootstrap: dict | None = None) -> dict:
    """Ingest (or re-ingest) the live season from the FPL API. Idempotent.

    Parameters
    ----------
    conn : sqlite3.Connection
        Open database connection; committed on success.
    season_id : str
        Local season id, e.g. ``'2026-27'``. Must be the season the API is serving.
    bootstrap : dict, optional
        An already-fetched ``bootstrap-static`` payload, to save a round trip.

    Returns
    -------
    dict
        Counts of what was written, in the same shape as ``refresh.backfill_season``.
    """
    bootstrap = bootstrap or fetch_bootstrap()
    fixtures = fetch_fixtures()
    events = started_events(bootstrap)

    teams = bootstrap["teams"]
    team_code_by_id = {t["id"]: t["code"] for t in teams}
    elements = bootstrap["elements"]
    cur = conn.cursor()

    for t in teams:
        cur.execute(
            """INSERT INTO teams (season_id, team_code, season_team_id, name,
            short_name)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(season_id, team_code) DO UPDATE SET
                 season_team_id=excluded.season_team_id,
                 name=excluded.name,
                 short_name=excluded.short_name""",
            (season_id, t["code"], t["id"], t["name"], t["short_name"]),
        )

    for e in elements:
        cur.execute(
            """INSERT INTO players (player_code, first_name, second_name, web_name)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(player_code) DO UPDATE SET
                 first_name=excluded.first_name,
                 second_name=excluded.second_name,
                 web_name=excluded.web_name""",
            (e["code"], e["first_name"], e["second_name"], e["web_name"]),
        )
        cur.execute(
            """INSERT INTO player_season
                 (season_id, player_code, season_element_id, team_code, position,
                 start_cost)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(season_id, player_code) DO UPDATE SET
                 season_element_id=excluded.season_element_id,
                 team_code=excluded.team_code,
                 position=excluded.position,
                 start_cost=excluded.start_cost""",
            (
                season_id,
                e["code"],
                e["id"],
                e["team_code"],
                POSITION_BY_ELEMENT_TYPE.get(e["element_type"], "UNK"),
                e["now_cost"] - e["cost_change_start"],
            ),
        )

    fixture_by_id = {}
    for f in fixtures:
        fixture_by_id[f["id"]] = f
        cur.execute(
            """INSERT INTO fixtures
                 (season_id, fixture_id, round, kickoff_time, team_h_code, team_a_code,
                  team_h_score, team_a_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(season_id, fixture_id) DO UPDATE SET
                 round=excluded.round, kickoff_time=excluded.kickoff_time,
                 team_h_code=excluded.team_h_code, team_a_code=excluded.team_a_code,
                 team_h_score=excluded.team_h_score,
                 team_a_score=excluded.team_a_score""",
            (
                season_id,
                f["id"],
                f["event"],
                f["kickoff_time"],
                team_code_by_id[f["team_h"]],
                team_code_by_id[f["team_a"]],
                f["team_h_score"],
                f["team_a_score"],
            ),
        )

    # -- per-gameweek player stats --
    element_by_id = {e["id"]: e for e in elements}
    rows, skipped, split_players = [], 0, set()
    for event in events:
        for item in fetch_event_live(event):
            e = element_by_id.get(item["id"])
            if e is None:
                skipped += 1
                continue
            fixture_ids = [x["fixture"] for x in item.get("explain", [])]
            if not fixture_ids:
                continue  # blank gameweek for this player's club
            if len(fixture_ids) > 1:
                split_players.add(item["id"])
                continue
            f = fixture_by_id[fixture_ids[0]]
            was_home = 1 if team_code_by_id[f["team_h"]] == e["team_code"] else 0
            opponent = team_code_by_id[f["team_a"] if was_home else f["team_h"]]
            rows.append(
                _stat_row(
                    season_id,
                    e["code"],
                    event,
                    f["id"],
                    e["team_code"],
                    opponent,
                    was_home,
                    item["stats"],
                    e["now_cost"],
                )
            )

    # Double gameweeks: the live payload only has the round total, so take the
    # per-fixture rows from each affected player's own summary instead.
    for element_id in sorted(split_players):
        e = element_by_id[element_id]
        for h in fetch_element_history(element_id):
            if h["round"] not in events:
                continue
            rows.append(
                _stat_row(
                    season_id,
                    e["code"],
                    h["round"],
                    h["fixture"],
                    e["team_code"],
                    team_code_by_id[h["opponent_team"]],
                    int(h["was_home"]),
                    h,
                    h["value"],
                )
            )

    cur.execute("DELETE FROM player_gw_stats WHERE season_id = ?", (season_id,))
    placeholders = ", ".join("?" for _ in range(7 + len(STAT_COLUMNS) + 1))
    cur.executemany(
        f"""INSERT OR REPLACE INTO player_gw_stats
              (season_id, player_code, round, fixture_id, team_code, opponent_team_code,
               was_home, {", ".join(STAT_COLUMNS)}, price)
            VALUES ({placeholders})""",
        rows,
    )
    cur.execute(
        "UPDATE seasons SET backfilled = 1, is_placeholder = 0 WHERE id = ?",
        (season_id,),
    )
    conn.commit()

    return {
        "season_id": season_id,
        "source": "fpl_api",
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
        "teams": len(teams),
        "players": len(elements),
        "fixtures": len(fixtures),
        "gameweeks": events,
        "gw_rows_inserted": len(rows),
        "gw_rows_total": len(rows) + skipped,
        "gw_rows_skipped": skipped,
    }
