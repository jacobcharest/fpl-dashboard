"""Expected-points projections built from the live FPL API, for when no external model
is loaded.

FPL Review and friends need a browser session to export, so this is the always-available
fallback: a deliberately simple, explainable model over public data that lands in the
same ``player_projections`` table (source ``'fpl_api'``) and therefore the same xP /
xMins columns.

Per player and gameweek::

    xp    = base_rate * fixture_multiplier * p_play
    xmins = minutes_per_gameweek * p_play

- ``base_rate`` is points per *team gameweek* this season, shrunk toward last season's
  rate (or a positional default for newcomers) with ``PRIOR_WEIGHT`` gameweeks of prior.
  Per team gameweek rather than per appearance so rotation and benching are priced in.
- ``fixture_multiplier`` comes from the FPL Fixture Difficulty Rating (1 easy - 5 hard)
  with a small home/away tilt, normalised to 1.0 at FDR 3 so an average run of fixtures
  leaves the base rate untouched. Defenders and keepers swing more with opposition than
  attackers do.
- ``p_play`` is the availability discount: ``chance_of_playing_next_round`` for the next
  gameweek, easing back toward full fitness for a doubt, flat for a longer-term injury,
  and a one-match dip for a suspension.

A double gameweek sums both fixtures; a blank is zero. The next gameweek's official
``ep_next`` is not used directly - one consistent model across the horizon beats a
stitched one.
"""

import csv
from datetime import datetime, timezone
from pathlib import Path

from app.db import DB_PATH
from app.live_refresh import fetch_bootstrap, fetch_fixtures
from app.projections import import_projections
from app.seasons import POSITION_BY_ELEMENT_TYPE

DEFAULT_HORIZON = 8
PRIOR_WEIGHT = 4  # gameweeks of prior evidence a newcomer's/last season's rate is worth
MIN_PRIOR_MINUTES = 900  # last season must be at least this to be used as a prior

# Points per team gameweek for a player with no usable history, by position.
POSITION_DEFAULT_RATE = {"GK": 1.5, "DEF": 1.8, "MID": 2.2, "FWD": 2.2}

FDR_MULTIPLIER = {
    "attack": {1: 1.30, 2: 1.15, 3: 1.00, 4: 0.88, 5: 0.75},
    "defence": {1: 1.35, 2: 1.20, 3: 1.00, 4: 0.85, 5: 0.70},
}
HOME_TILT = 0.05


def fixture_multiplier(position: str, difficulty: int, was_home: bool) -> float:
    """Scale factor for one fixture.

    Parameters
    ----------
    position : str
        ``'GK'``, ``'DEF'``, ``'MID'`` or ``'FWD'``.
    difficulty : int
        FPL Fixture Difficulty Rating for the player's team, 1 (easiest) to 5.
    was_home : bool
        Whether the player's team is at home.

    Returns
    -------
    float
        Multiplier, 1.0 for an average away-ish fixture at FDR 3.
    """
    family = "defence" if position in ("GK", "DEF") else "attack"
    table = FDR_MULTIPLIER[family]
    return table.get(difficulty, 1.0) + (HOME_TILT if was_home else -HOME_TILT)


def availability(element: dict, gameweeks_ahead: int) -> float:
    """Probability-of-playing style discount for a gameweek ``gameweeks_ahead`` (1 =
    next).

    Parameters
    ----------
    element : dict
        A ``bootstrap-static`` element.
    gameweeks_ahead : int
        1 for the next gameweek, 2 for the one after, and so on.

    Returns
    -------
    float
        Between 0 and 1.
    """
    status = element.get("status", "a")
    chance = element.get("chance_of_playing_next_round")
    p_next = 1.0 if chance is None else chance / 100.0
    if status == "a":
        return 1.0
    if status == "s":  # suspended: the standard one-match ban
        return 0.0 if gameweeks_ahead == 1 else 1.0
    if status == "d":  # doubtful: ease back to full fitness over a couple of weeks
        return min(1.0, p_next + (1.0 - p_next) * 0.5 * (gameweeks_ahead - 1))
    # 'i' injured, 'u' unavailable, 'n' not eligible: no return date is public, so hold
    # flat.
    return p_next


def _season_rates(conn, season_id: str) -> tuple[dict, int]:
    """{player_code: (points, minutes, team_gws)} this season, plus gameweeks played."""
    rows = conn.execute(
        """SELECT s.player_code, SUM(s.total_points) AS pts, SUM(s.minutes) AS mins,
                  COUNT(DISTINCT s.round) AS team_gws
           FROM player_gw_stats s WHERE s.season_id = ? GROUP BY s.player_code""",
        (season_id,),
    ).fetchall()
    played = (
        conn.execute(
            "SELECT MAX(round) FROM player_gw_stats WHERE season_id = ?", (season_id,)
        ).fetchone()[0]
        or 0
    )
    return {
        r["player_code"]: (r["pts"], r["mins"], r["team_gws"]) for r in rows
    }, played


def _prior_rates(conn, season_id: str) -> dict:
    """{player_code: points per gameweek last season}, only where they played enough."""
    year = int(season_id[:4]) - 1
    prev = f"{year}-{(year + 1) % 100:02d}"
    rows = conn.execute(
        """SELECT player_code, SUM(total_points) AS pts, SUM(minutes) AS mins
           FROM player_gw_stats WHERE season_id = ? GROUP BY player_code HAVING mins >=
           ?""",
        (prev, MIN_PRIOR_MINUTES),
    ).fetchall()
    return {r["player_code"]: r["pts"] / 38.0 for r in rows}


def build_projection_rows(
    conn,
    season_id: str,
    bootstrap: dict,
    fixtures: list[dict],
    horizon: int = DEFAULT_HORIZON,
) -> list[dict]:
    """Project every player over the next ``horizon`` gameweeks.

    Parameters
    ----------
    conn : sqlite3.Connection
        Database with this season's (and ideally last season's) gameweek stats loaded.
    season_id : str
        Local season id.
    bootstrap : dict
        ``bootstrap-static`` payload.
    fixtures : list[dict]
        ``fixtures/`` payload.
    horizon : int
        Number of upcoming gameweeks to project.

    Returns
    -------
    list[dict]
        Rows of ``{code, name, team, gw, xp, xmins}``.
    """
    next_event = min(
        (
            e["id"]
            for e in bootstrap["events"]
            if not e.get("finished") and not e.get("is_current")
        ),
        default=None,
    )
    if next_event is None:
        return []
    gameweeks = list(range(next_event, min(next_event + horizon, 39)))

    team_short = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    by_team_gw: dict[tuple[int, int], list[tuple[int, bool]]] = {}
    for f in fixtures:
        if f["event"] is None:
            continue
        by_team_gw.setdefault((f["team_h"], f["event"]), []).append(
            (f["team_h_difficulty"], True)
        )
        by_team_gw.setdefault((f["team_a"], f["event"]), []).append(
            (f["team_a_difficulty"], False)
        )

    this_season, played = _season_rates(conn, season_id)
    prior = _prior_rates(conn, season_id)

    rows = []
    for e in bootstrap["elements"]:
        position = POSITION_BY_ELEMENT_TYPE.get(e["element_type"], "MID")
        pts, mins, _ = this_season.get(e["code"], (0, 0, 0))
        prior_rate = prior.get(e["code"], POSITION_DEFAULT_RATE[position])
        base_rate = (pts + PRIOR_WEIGHT * prior_rate) / (played + PRIOR_WEIGHT)
        mins_per_gw = mins / played if played else 0.0

        for ahead, gw in enumerate(gameweeks, start=1):
            p_play = availability(e, ahead)
            fixture_scale = sum(
                fixture_multiplier(position, d, home)
                for d, home in by_team_gw.get((e["team"], gw), [])
            )
            n_fixtures = len(by_team_gw.get((e["team"], gw), []))
            rows.append(
                {
                    "code": e["code"],
                    "name": e["web_name"],
                    "team": team_short[e["team"]],
                    "gw": gw,
                    "xp": round(base_rate * fixture_scale * p_play, 2),
                    "xmins": round(mins_per_gw * n_fixtures * p_play, 1),
                }
            )
    return rows


def generate_projections(
    conn, season_id: str, horizon: int = DEFAULT_HORIZON, source: str = "fpl_api"
) -> dict:
    """Build the FPL-API projections, write them as a CSV, and import that CSV.

    Going through the CSV keeps one write path into ``player_projections`` and leaves a
    human-readable copy in ``data/`` next to the database.

    Parameters
    ----------
    conn : sqlite3.Connection
        Open database connection.
    season_id : str
        Local season id; must be the live season.
    horizon : int
        Gameweeks ahead to project.
    source : str
        Label stored in ``player_projections.source``.

    Returns
    -------
    dict
        The import summary from ``projections.import_projections`` plus ``csv_path``.
    """
    bootstrap = fetch_bootstrap()
    rows = build_projection_rows(conn, season_id, bootstrap, fetch_fixtures(), horizon)
    if not rows:
        raise ValueError("No upcoming gameweeks to project - the season is over.")

    csv_path = Path(DB_PATH).parent / f"projections_{source}_{season_id}.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["code", "name", "team", "gw", "xp", "xmins"]
        )
        writer.writeheader()
        writer.writerows(rows)

    summary = import_projections(conn, season_id, str(csv_path), source)
    summary["csv_path"] = str(csv_path)
    summary["generated_at"] = datetime.now(timezone.utc).isoformat()
    return summary
