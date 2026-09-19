"""Ingestion of a season's data into the local SQLite database. Used both by the one-time
historical backfill script (scripts/backfill_history.py) and by the app's "Fetch new data"
button (POST /api/refresh); re-running it is idempotent.

Two sources, chosen by `backfill_season`:

- **The live season comes straight from the official FPL API** (bootstrap-static, fixtures,
  and one element-summary call per player for the per-gameweek rows). The community archive
  below used to track the live API within a day, but in 2026/27 it stalled after gameweek 1,
  which left the dashboard silently frozen on round 1 while "Fetch new data" re-ingested the
  same stale file. The API is authoritative for the season in progress, so it is now the only
  source for it.
- **Finished seasons come from the vaastav/Fantasy-Premier-League community archive**, which
  is the only place per-gameweek history for past seasons still exists (the FPL API only
  serves the current season's rounds).

Both paths produce the same rows and share one writer (`_write_season`), so the query layer
sees no difference.

Most archive seasons ship teams.csv + fixtures.csv directly. Three early seasons don't, and need
fallback reconstruction (see resolve_teams / resolve_fixtures below):
  - 2016-17, 2017-18: no teams.csv, no fixtures.csv, no 'team' column in the gw data.
  - 2018-19: no teams.csv (but has fixtures.csv and a raw.json bootstrap snapshot).
"""

import json
import time
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError

import pandas as pd

from app.my_team import fetch_bootstrap, live_season_id
from app.prices import capture_snapshot
from app.seasons import POSITION_BY_ELEMENT_TYPE, SEASONS, TEAM_SHORT_NAME_BY_NAME

RAW_BASE = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"
FPL_API = "https://fantasy.premierleague.com/api"
# element-summary is one request per player (~650 per refresh); a small pool keeps the
# whole refresh to a handful of seconds without hammering the API.
LIVE_FETCH_WORKERS = 8


def fetch_live_json(path: str):
    req = urllib.request.Request(f"{FPL_API}/{path}", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


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
                    # FPL's fixture difficulty ratings; in the archive's fixtures.csv from 2018-19 on.
                    "team_h_difficulty": _num(row.get("team_h_difficulty"), int),
                    "team_a_difficulty": _num(row.get("team_a_difficulty"), int),
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


def _num(v, cast):
    """`cast(v)` or None for the API's/archive's assorted empties ('', None, NaN)."""
    if v is None or v == "":
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return cast(v)


def backfill_season(conn, season_id: str) -> dict:
    """Ingests (or re-ingests) one season. Idempotent - safe to call repeatedly, e.g. as
    a weekly refresh once new gameweeks have been played.

    The season currently being played is read from the live FPL API; every other season
    from the community archive (see the module docstring for why)."""
    bootstrap = fetch_bootstrap()
    if season_id == live_season_id(bootstrap):
        return backfill_live_season(conn, season_id, bootstrap)
    return backfill_archive_season(conn, season_id)


def backfill_live_season(conn, season_id: str, bootstrap: dict | None = None) -> dict:
    """The in-progress season, straight from the official API.

    bootstrap-static gives teams and players, fixtures/ gives the schedule, and
    element-summary/{id}/ gives each player's per-round history with every column
    player_gw_stats stores (the archive's merged_gw.csv is itself built from the same
    endpoint, so the two sources agree column-for-column)."""
    bootstrap = bootstrap or fetch_bootstrap()
    fixtures = fetch_live_json("fixtures/")
    elements = bootstrap["elements"]

    team_rows = [
        {
            "team_code": int(t["code"]),
            "season_team_id": int(t["id"]),
            "name": t["name"],
            "short_name": t["short_name"],
        }
        for t in bootstrap["teams"]
    ]
    season_team_id_to_code = {r["season_team_id"]: r["team_code"] for r in team_rows}

    player_rows = [
        {
            "code": int(e["code"]),
            "id": int(e["id"]),
            "first_name": e["first_name"],
            "second_name": e["second_name"],
            "web_name": e["web_name"],
            "team_code": int(e["team_code"]),
            "element_type": int(e["element_type"]),
            "now_cost": int(e["now_cost"]),
            "start_cost": int(e["now_cost"]) - int(e.get("cost_change_start") or 0),
            "selected_by_percent": _num(e.get("selected_by_percent"), float),
        }
        for e in elements
    ]
    element_id_to_code = {r["id"]: r["code"] for r in player_rows}

    fixture_rows = []
    for f in fixtures:
        team_h_code = season_team_id_to_code.get(f["team_h"])
        team_a_code = season_team_id_to_code.get(f["team_a"])
        if team_h_code is None or team_a_code is None:
            continue
        fixture_rows.append(
            {
                "fixture_id": int(f["id"]),
                "round": _num(f.get("event"), int),
                "kickoff_time": f.get("kickoff_time"),
                "team_h_code": team_h_code,
                "team_a_code": team_a_code,
                "team_h_score": _num(f.get("team_h_score"), int),
                "team_a_score": _num(f.get("team_a_score"), int),
                "team_h_difficulty": _num(f.get("team_h_difficulty"), int),
                "team_a_difficulty": _num(f.get("team_a_difficulty"), int),
            }
        )

    def fetch_history(element_id: int) -> list[dict] | None:
        """A player's per-round history; [] if FPL has none for them, None if the request
        failed - the two must not be confused, since gameweek rows are delete-and-replace and
        a failure read as "no history" would silently erase that player's season."""
        for attempt in (1, 2):
            try:
                return fetch_live_json(f"element-summary/{element_id}/")["history"]
            except HTTPError as e:
                if e.code == 404:
                    return []
            except OSError:
                pass
        return None

    ids = [r["id"] for r in player_rows]
    with ThreadPoolExecutor(max_workers=LIVE_FETCH_WORKERS) as pool:
        histories = list(pool.map(fetch_history, ids))
    # FPL rate-limits bursts (two refreshes back to back is enough). Go back for the failures
    # one at a time, slowly; if any still won't come, stop before anything is written.
    for pause in (2, 5):
        failed = [i for i, h in enumerate(histories) if h is None]
        if not failed:
            break
        time.sleep(pause)
        for i in failed:
            histories[i] = fetch_history(ids[i])
            time.sleep(0.1)
    failed = sum(1 for h in histories if h is None)
    if failed:
        raise RuntimeError(
            f"the FPL API refused {failed} of {len(ids)} player histories (it rate-limits bursts). "
            f"Nothing was changed - try again in a minute"
        )

    gw_rows = []
    skipped = 0
    seen = set()
    for player, history in zip(player_rows, histories):
        for h in history:
            key = (player["code"], int(h["round"]), int(h["fixture"]))
            if key in seen:
                skipped += 1
                continue
            seen.add(key)
            opponent_team_code = season_team_id_to_code.get(h.get("opponent_team"))
            gw_rows.append(
                (
                    season_id,
                    player["code"],
                    int(h["round"]),
                    int(h["fixture"]),
                    player["team_code"],
                    opponent_team_code,
                    1 if h.get("was_home") else 0,
                    int(h.get("minutes") or 0),
                    _num(h.get("starts"), int),
                    int(h.get("goals_scored") or 0),
                    int(h.get("assists") or 0),
                    int(h.get("clean_sheets") or 0),
                    int(h.get("goals_conceded") or 0),
                    int(h.get("bonus") or 0),
                    int(h.get("bps") or 0),
                    int(h.get("total_points") or 0),
                    _num(h.get("expected_goals"), float),
                    _num(h.get("expected_assists"), float),
                    _num(h.get("expected_goal_involvements"), float),
                    _num(h.get("expected_goals_conceded"), float),
                    _num(h.get("defensive_contribution"), int),
                    int(h.get("saves") or 0),
                    int(h.get("yellow_cards") or 0),
                    int(h.get("red_cards") or 0),
                    float(h.get("influence") or 0),
                    float(h.get("creativity") or 0),
                    float(h.get("threat") or 0),
                    float(h.get("ict_index") or 0),
                    _num(h.get("value"), int),
                )
            )
    players_without_history = sum(1 for hist in histories if not hist)

    summary = _write_season(
        conn, season_id, team_rows, player_rows, fixture_rows, gw_rows
    )
    # The bootstrap is already in hand, so a refresh doubles as a price snapshot for free.
    capture_snapshot(conn, bootstrap)
    summary.update(
        {
            "source": "fpl-api",
            "gw_rows_total": len(gw_rows),
            "gw_rows_skipped": skipped,
            "players_without_history": players_without_history,
            "rounds": sorted({r[2] for r in gw_rows}),
        }
    )
    return summary


def backfill_archive_season(conn, season_id: str) -> dict:
    """A finished season, from the community archive."""
    players_df = fetch_csv(season_id, "players_raw.csv")
    gws_df = fetch_csv(season_id, "gws/merged_gw.csv")
    # The upstream archive occasionally contains exact-duplicate rows for the same
    # player/round/fixture; keep the first occurrence.
    gws_df = gws_df.drop_duplicates(subset=["element", "round", "fixture"], keep="first")

    team_rows = resolve_teams(season_id, players_df)
    season_team_id_to_code = {r["season_team_id"]: r["team_code"] for r in team_rows}
    team_name_to_code = {r["name"]: r["team_code"] for r in team_rows}
    element_id_to_code = dict(zip(players_df["id"], players_df["code"]))
    element_id_to_team_code = dict(zip(players_df["id"], players_df["team_code"]))

    fixture_rows = resolve_fixtures(season_id, gws_df, season_team_id_to_code, element_id_to_team_code)

    has_cost_change = "cost_change_start" in players_df.columns
    player_rows = []
    for _, row in players_df.iterrows():
        now_cost = int(row["now_cost"])
        player_rows.append(
            {
                "code": int(row["code"]),
                "id": int(row["id"]),
                "first_name": row["first_name"],
                "second_name": row["second_name"],
                "web_name": row["web_name"],
                "team_code": int(row["team_code"]),
                "element_type": int(row["element_type"]),
                "now_cost": now_cost,
                "start_cost": now_cost - int(row["cost_change_start"]) if has_cost_change else now_cost,
                # The archive's players_raw.csv is a season-end snapshot, so this is final ownership.
                "selected_by_percent": _num(row.get("selected_by_percent"), float),
            }
        )

    has_team_col = "team" in gws_df.columns

    def col(row, name):
        return row[name] if name in gws_df.columns and pd.notna(row.get(name)) else None

    gw_rows = []
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
        gw_rows.append(
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
                int(col(row, "saves") or 0),
                int(col(row, "yellow_cards") or 0),
                int(col(row, "red_cards") or 0),
                float(col(row, "influence") or 0),
                float(col(row, "creativity") or 0),
                float(col(row, "threat") or 0),
                float(col(row, "ict_index") or 0),
                int(col(row, "value")) if col(row, "value") is not None else None,
            )
        )

    summary = _write_season(conn, season_id, team_rows, player_rows, fixture_rows, gw_rows)
    summary.update(
        {"source": "archive", "gw_rows_total": len(gws_df), "gw_rows_skipped": skipped_gw_rows}
    )
    return summary


def _write_season(conn, season_id, team_rows, player_rows, fixture_rows, gw_rows) -> dict:
    """Upserts teams/players/fixtures and replaces the season's per-gameweek rows.
    Shared by both sources so they can't drift apart."""
    cur = conn.cursor()

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

    for p in player_rows:
        cur.execute(
            """INSERT INTO players (player_code, first_name, second_name, web_name)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(player_code) DO UPDATE SET
                 first_name=excluded.first_name,
                 second_name=excluded.second_name,
                 web_name=excluded.web_name""",
            (p["code"], p["first_name"], p["second_name"], p["web_name"]),
        )
        cur.execute(
            """INSERT INTO player_season
                 (season_id, player_code, season_element_id, team_code, position, start_cost,
                  selected_by_percent)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(season_id, player_code) DO UPDATE SET
                 season_element_id=excluded.season_element_id,
                 team_code=excluded.team_code,
                 position=excluded.position,
                 start_cost=excluded.start_cost,
                 selected_by_percent=excluded.selected_by_percent""",
            (
                season_id,
                p["code"],
                p["id"],
                p["team_code"],
                POSITION_BY_ELEMENT_TYPE.get(p["element_type"], "UNK"),
                p["start_cost"],
                p.get("selected_by_percent"),
            ),
        )

    for r in fixture_rows:
        cur.execute(
            """INSERT INTO fixtures
                 (season_id, fixture_id, round, kickoff_time, team_h_code, team_a_code,
                  team_h_score, team_a_score, team_h_difficulty, team_a_difficulty)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(season_id, fixture_id) DO UPDATE SET
                 round=excluded.round,
                 kickoff_time=excluded.kickoff_time,
                 team_h_code=excluded.team_h_code,
                 team_a_code=excluded.team_a_code,
                 team_h_score=excluded.team_h_score,
                 team_a_score=excluded.team_a_score,
                 team_h_difficulty=excluded.team_h_difficulty,
                 team_a_difficulty=excluded.team_a_difficulty""",
            (
                season_id,
                r["fixture_id"],
                r["round"],
                r["kickoff_time"],
                r["team_h_code"],
                r["team_a_code"],
                r["team_h_score"],
                r["team_a_score"],
                r.get("team_h_difficulty"),
                r.get("team_a_difficulty"),
            ),
        )

    cur.execute("DELETE FROM player_gw_stats WHERE season_id = ?", (season_id,))
    cur.executemany(
        """INSERT INTO player_gw_stats
             (season_id, player_code, round, fixture_id, team_code, opponent_team_code, was_home,
              minutes, starts, goals_scored, assists, clean_sheets, goals_conceded, bonus, bps,
              total_points, expected_goals, expected_assists, expected_goal_involvements,
              expected_goals_conceded, defensive_contribution, saves, yellow_cards, red_cards,
              influence, creativity, threat, ict_index, price)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        gw_rows,
    )

    # Reaching here means real data was successfully fetched from the source, so any earlier
    # placeholder (see create_placeholder_season.py) is no longer one - this is what closes the
    # loop once a not-yet-started season actually begins and "Fetch New Data" is clicked again.
    cur.execute("UPDATE seasons SET backfilled = 1, is_placeholder = 0 WHERE id = ?", (season_id,))
    conn.commit()

    return {
        "season_id": season_id,
        "teams": len(team_rows),
        "players": len(player_rows),
        "fixtures": len(fixture_rows),
        "gw_rows_inserted": len(gw_rows),
    }

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
