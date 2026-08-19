"""Syncing the user's own FPL squad from the live FPL API so it can be highlighted on the board.

Two things make this less trivial than it looks:

1. **Picks are private until a gameweek kicks off.** `/api/my-team/{entry}/` (the endpoint behind
   the site's own "My Team" page) needs a logged-in session, and the public
   `/api/entry/{entry}/event/{gw}/picks/` 404s until that gameweek actually starts. So before GW1
   there is no unauthenticated way to read a squad. Rather than failing outright, `sync_my_team`
   stores the entry id (validated against the public `/api/entry/{entry}/` endpoint, which *is*
   available immediately) and reports `pending_kickoff`; the same button then completes the sync
   once the gameweek starts, with no re-typing.

2. **element id -> player_code must come from the live bootstrap, not the database.** Picks
   reference `element`, a per-season numeric id. The obvious move is to join on
   `player_season.season_element_id`, but for a placeholder season those ids were cloned wholesale
   from the *previous* season (see scripts/create_placeholder_season.py) and are therefore wrong.
   The live bootstrap carries both `id` and the stable `code` for the current season, so it is the
   only trustworthy mapping here.
"""

import json
import urllib.request
from datetime import datetime, timezone
from urllib.error import HTTPError

API_BASE = "https://fantasy.premierleague.com/api"


def _get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def fetch_bootstrap() -> dict:
    return _get_json(f"{API_BASE}/bootstrap-static/")


def live_season_id(bootstrap: dict) -> str:
    """The season the live FPL API is currently serving, as a local season id ('2026-27').

    Derived from GW1's deadline rather than the wall clock: a Premier League season is named for
    the calendar year it starts in, and GW1 always falls in the back half of that year.
    """
    deadline = min(e["deadline_time"] for e in bootstrap["events"])
    year = datetime.fromisoformat(deadline.replace("Z", "+00:00")).year
    if datetime.fromisoformat(deadline.replace("Z", "+00:00")).month < 6:
        year -= 1
    return f"{year}-{(year + 1) % 100:02d}"


def latest_available_event(bootstrap: dict) -> int | None:
    """The most recent gameweek whose picks are public, or None if the season hasn't kicked off.

    A gameweek's picks become readable once it starts, so the current (in-progress) gameweek
    counts - we don't have to wait for it to finish.
    """
    started = [e["id"] for e in bootstrap["events"] if e.get("is_current") or e.get("finished")]
    return max(started) if started else None


def fetch_entry(entry_id: int) -> dict:
    """Public manager metadata. Doubles as validation that the entry id exists, and unlike picks
    it's available before the season starts."""
    try:
        return _get_json(f"{API_BASE}/entry/{entry_id}/")
    except HTTPError as e:
        if e.code == 404:
            raise ValueError(
                f"No FPL team found with id {entry_id}. It's the number in your team's URL, "
                f"e.g. fantasy.premierleague.com/entry/1234567/event/1."
            ) from e
        raise


def fetch_picks(entry_id: int, event: int) -> list[dict] | None:
    """This gameweek's 15 picks, or None if they aren't public yet."""
    try:
        return _get_json(f"{API_BASE}/entry/{entry_id}/event/{event}/picks/")["picks"]
    except HTTPError as e:
        if e.code in (403, 404):
            return None
        raise


def sync_my_team(conn, season_id: str, entry_id: int) -> dict:
    bootstrap = fetch_bootstrap()

    live_id = live_season_id(bootstrap)
    if season_id != live_id:
        raise ValueError(
            f"Your squad can only be synced for the live season ({live_id}); {season_id} is a "
            f"past season, and the FPL API doesn't serve historical squads."
        )

    entry = fetch_entry(entry_id)
    entry_name = entry.get("name")
    manager_name = " ".join(
        p for p in (entry.get("player_first_name"), entry.get("player_last_name")) if p
    ).strip() or None

    cur = conn.cursor()
    cur.execute(
        """INSERT INTO manager_entry (season_id, entry_id, entry_name, manager_name, synced_event, synced_at)
           VALUES (?, ?, ?, ?, NULL, ?)
           ON CONFLICT(season_id) DO UPDATE SET
               entry_id = excluded.entry_id,
               entry_name = excluded.entry_name,
               manager_name = excluded.manager_name,
               synced_at = excluded.synced_at""",
        (season_id, entry_id, entry_name, manager_name, datetime.now(timezone.utc).isoformat()),
    )

    base = {
        "season_id": season_id,
        "entry_id": entry_id,
        "entry_name": entry_name,
        "manager_name": manager_name,
    }

    event = latest_available_event(bootstrap)
    picks = fetch_picks(entry_id, event) if event else None
    if picks is None:
        # Keep whatever squad is already stored - a failed re-sync shouldn't wipe a good one.
        conn.commit()
        next_ev = min(
            (e for e in bootstrap["events"] if not e.get("finished")),
            key=lambda e: e["id"],
            default=None,
        )
        when = f" Picks unlock when {next_ev['name']} kicks off." if next_ev else ""
        return {
            **base,
            "status": "pending_kickoff",
            "synced_event": None,
            "picks": 0,
            "unmatched": [],
            "message": f"Team id saved ({entry_name}), but no gameweek has started yet.{when}",
        }

    code_by_element = {e["id"]: e["code"] for e in bootstrap["elements"]}
    name_by_element = {e["id"]: e["web_name"] for e in bootstrap["elements"]}
    on_board = {
        r[0] for r in cur.execute(
            "SELECT player_code FROM player_season WHERE season_id = ?", (season_id,)
        )
    }

    rows, unmatched = [], []
    for p in picks:
        code = code_by_element.get(p["element"])
        if code is None or code not in on_board:
            unmatched.append(name_by_element.get(p["element"], f"element {p['element']}"))
            continue
        rows.append(
            (season_id, code, p["position"], int(p["is_captain"]), int(p["is_vice_captain"]),
             p.get("multiplier"))
        )

    cur.execute("DELETE FROM manager_squad WHERE season_id = ?", (season_id,))
    cur.executemany(
        """INSERT INTO manager_squad
               (season_id, player_code, squad_slot, is_captain, is_vice_captain, multiplier)
           VALUES (?, ?, ?, ?, ?, ?)""",
        rows,
    )
    cur.execute(
        "UPDATE manager_entry SET synced_event = ? WHERE season_id = ?", (event, season_id)
    )
    conn.commit()

    return {
        **base,
        "status": "synced",
        "synced_event": event,
        "picks": len(rows),
        "unmatched": unmatched,
        "message": f"Synced {len(rows)} players from GW{event}.",
    }


def get_my_team(conn, season_id: str) -> dict | None:
    entry = conn.execute(
        "SELECT entry_id, entry_name, manager_name, synced_event FROM manager_entry WHERE season_id = ?",
        (season_id,),
    ).fetchone()
    if entry is None:
        return None
    picks = conn.execute(
        """SELECT player_code, squad_slot, is_captain, is_vice_captain, multiplier
           FROM manager_squad WHERE season_id = ? ORDER BY squad_slot""",
        (season_id,),
    ).fetchall()
    return {**dict(entry), "picks": [dict(p) for p in picks]}
