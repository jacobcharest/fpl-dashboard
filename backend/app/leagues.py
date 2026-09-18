"""Mini-league tracking: every team in one of the user's private leagues, gameweek by gameweek.

Everything comes from FPL's public API - standings, each entry's season history (scores, totals,
chips) and each entry's picks per gameweek - so rivals' squads are readable exactly when the
user's own is: from the moment a gameweek's deadline passes.

Three things are less obvious than they look:

- **League rank per gameweek is computed, not fetched.** The standings endpoint only serves the
  current table. Each entry's history carries its season total after every gameweek, so ranking
  those totals among the league's members rebuilds the table as it stood any week.
- **The lineup shown is the one that was picked.** Once a gameweek ends the picks endpoint
  reports the squad *after* automatic substitutions. Those are undone for display and for the
  projection (a manager's projected score is about the eleven they chose), and flagged so it's
  still clear who came on. Actual points always use FPL's own multipliers and its own total.
- **A pick's projection is written once.** Projections get re-imported weekly and only look
  forward, so "what was this team projected to score in GW5" is unanswerable later unless it was
  kept at the time. `entry_picks.xp` is filled the first time a projection exists for the pick
  and never rewritten.

Finished-and-checked gameweeks are immutable, so they're fetched once; only the gameweek in
progress is re-read on each sync.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from app.my_team import API_BASE, _get_json, fetch_bootstrap, fetch_picks, live_season_id
from app.projections import projection_sources
from app.seasons import POSITION_BY_ELEMENT_TYPE

# The standings endpoint pages 50 at a time. This is a tool for a league of friends, not for the
# 300,000-manager "Spurs" league, and every entry costs a request per gameweek - so page one only.
MAX_ENTRIES = 50
FETCH_WORKERS = 8
SYNC_MIN_INTERVAL = timedelta(minutes=5)


def list_leagues(conn, season_id: str) -> list[dict]:
    """The user's private classic leagues, live from their entry; falls back to whatever has
    been synced before when the API can't be reached."""
    entry = conn.execute("SELECT entry_id FROM manager_entry WHERE season_id = ?", (season_id,)).fetchone()
    if entry is None:
        return []
    try:
        classic = _get_json(f"{API_BASE}/entry/{entry['entry_id']}/")["leagues"]["classic"]
        # league_type 'x' = created by a manager; 's' = FPL's own (Overall, country, club...).
        return [
            {"league_id": l["id"], "name": l["name"], "rank": l.get("entry_rank"), "size": l.get("rank_count")}
            for l in classic
            if l.get("league_type") == "x"
        ]
    except Exception:
        return [
            {"league_id": r["league_id"], "name": r["name"], "rank": None, "size": None}
            for r in conn.execute("SELECT league_id, name FROM leagues WHERE season_id = ?", (season_id,))
        ]


def _original_lineup(payload: dict) -> list[dict]:
    """Picks with automatic substitutions undone: `squad_slot` is where the manager put each
    player, `auto_sub` marks who FPL swapped. Multipliers are left as scored."""
    picks = [dict(p, auto_sub=None) for p in payload["picks"]]
    by_element = {p["element"]: p for p in picks}
    for sub in payload.get("automatic_subs") or []:
        came_on, went_off = by_element.get(sub["element_in"]), by_element.get(sub["element_out"])
        if came_on and went_off:
            came_on["position"], went_off["position"] = went_off["position"], came_on["position"]
            came_on["auto_sub"], went_off["auto_sub"] = "in", "out"
    return picks


def sync_league(conn, season_id: str, league_id: int) -> dict:
    bootstrap = fetch_bootstrap()
    if live_season_id(bootstrap) != season_id:
        raise ValueError(f"Leagues can only be synced for the live season; {season_id} is a past season.")

    standings = _get_json(f"{API_BASE}/leagues-classic/{league_id}/standings/")
    members = standings["standings"]["results"][:MAX_ENTRIES]
    truncated = standings["standings"].get("has_next", False) or len(standings["standings"]["results"]) > MAX_ENTRIES

    started = {e["id"]: e for e in bootstrap["events"] if e.get("finished") or e.get("is_current")}
    final_events = {i for i, e in started.items() if e.get("finished") and e.get("data_checked")}
    elements = {e["id"]: e for e in bootstrap["elements"]}

    cur = conn.cursor()
    cur.execute(
        """INSERT INTO leagues (season_id, league_id, name, synced_at) VALUES (?, ?, ?, ?)
           ON CONFLICT(season_id, league_id) DO UPDATE SET name = excluded.name, synced_at = excluded.synced_at""",
        (season_id, league_id, standings["league"]["name"], datetime.now(timezone.utc).isoformat()),
    )
    cur.execute("DELETE FROM league_entries WHERE season_id = ? AND league_id = ?", (season_id, league_id))
    cur.executemany(
        "INSERT INTO league_entries (season_id, league_id, entry_id, entry_name, player_name) VALUES (?, ?, ?, ?, ?)",
        [(season_id, league_id, m["entry"], m["entry_name"], m["player_name"]) for m in members],
    )

    entry_ids = [m["entry"] for m in members]
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        histories = dict(zip(entry_ids, pool.map(lambda i: _get_json(f"{API_BASE}/entry/{i}/history/"), entry_ids)))

    # Which (entry, gameweek) picks are still needed: anything not already stored as final.
    done = {
        (r["entry_id"], r["event"])
        for r in conn.execute(
            """SELECT e.entry_id, e.event FROM entry_events e
               WHERE e.season_id = ? AND e.final = 1
                 AND EXISTS (SELECT 1 FROM entry_picks p WHERE p.season_id = e.season_id
                             AND p.entry_id = e.entry_id AND p.event = e.event)""",
            (season_id,),
        )
    }
    wanted = []
    for entry_id, history in histories.items():
        chips = {c["event"]: c["name"] for c in history.get("chips", [])}
        for h in history["current"]:
            if h["event"] not in started:
                continue
            cur.execute(
                """INSERT OR REPLACE INTO entry_events
                       (season_id, entry_id, event, points, total_points, event_transfers,
                        event_transfers_cost, points_on_bench, overall_rank, bank, value, active_chip, final)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (season_id, entry_id, h["event"], h["points"], h["total_points"], h["event_transfers"],
                 h["event_transfers_cost"], h["points_on_bench"], h.get("overall_rank"), h.get("bank"),
                 h.get("value"), chips.get(h["event"]), int(h["event"] in final_events)),
            )
            if (entry_id, h["event"]) not in done:
                wanted.append((entry_id, h["event"]))

    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        payloads = list(pool.map(lambda w: fetch_picks(*w), wanted))
        live_events = sorted({event for _, event in wanted})
        live = dict(zip(live_events, pool.map(lambda e: _get_json(f"{API_BASE}/event/{e}/live/"), live_events)))
    stats = {event: {el["id"]: el["stats"] for el in data["elements"]} for event, data in live.items()}

    sources = projection_sources(conn, season_id)
    source = sources[0]["source"] if sources else None
    projected = {}
    if source and live_events:
        marks = ",".join("?" * len(live_events))
        projected = {
            (r["round"], r["player_code"]): r["xp"]
            for r in conn.execute(
                f"SELECT round, player_code, xp FROM player_projections "
                f"WHERE season_id = ? AND source = ? AND round IN ({marks})",
                (season_id, source, *live_events),
            )
        }

    for (entry_id, event), payload in zip(wanted, payloads):
        if payload is None:
            continue
        kept_xp = {
            r["player_code"]: r["xp"]
            for r in conn.execute(
                "SELECT player_code, xp FROM entry_picks WHERE season_id = ? AND entry_id = ? AND event = ?",
                (season_id, entry_id, event),
            )
        }
        rows = []
        for p in _original_lineup(payload):
            el = elements.get(p["element"])
            if el is None:
                continue
            code = int(el["code"])
            s = stats.get(event, {}).get(p["element"], {})
            xp = kept_xp.get(code)
            rows.append(
                (season_id, entry_id, event, code, el["web_name"], POSITION_BY_ELEMENT_TYPE.get(el["element_type"]),
                 p["position"], p["multiplier"], int(p["is_captain"]), int(p["is_vice_captain"]), p["auto_sub"],
                 s.get("total_points"), s.get("minutes"), xp if xp is not None else projected.get((event, code)))
            )
        cur.execute(
            "DELETE FROM entry_picks WHERE season_id = ? AND entry_id = ? AND event = ?", (season_id, entry_id, event)
        )
        cur.executemany(
            """INSERT INTO entry_picks
                   (season_id, entry_id, event, player_code, web_name, position, squad_slot, multiplier,
                    is_captain, is_vice_captain, auto_sub, points, minutes, xp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
    conn.commit()
    return {"league_id": league_id, "entries": len(members), "picks_fetched": len(wanted), "truncated": truncated}


def sync_if_stale(conn, season_id: str, league_id: int) -> str | None:
    """Syncs unless one just ran. Returns a warning for the page when the API couldn't be
    reached and there is stored data to fall back on; raises when there isn't."""
    row = conn.execute(
        "SELECT synced_at FROM leagues WHERE season_id = ? AND league_id = ?", (season_id, league_id)
    ).fetchone()
    if row and row["synced_at"]:
        if datetime.now(timezone.utc) - datetime.fromisoformat(row["synced_at"]) < SYNC_MIN_INTERVAL:
            return None
    try:
        sync_league(conn, season_id, league_id)
    except ValueError:
        if row is None:
            raise
    except Exception as e:
        conn.rollback()
        if row is None:
            raise
        return f"Couldn't reach the FPL API ({e}); showing the last synced data."
    return None


def _rank(totals: dict[int, int]) -> dict[int, int]:
    """Competition ranking (1, 2, 2, 4) of entry -> season total."""
    ordered = sorted(totals.values(), reverse=True)
    return {entry: ordered.index(total) + 1 for entry, total in totals.items()}


def query_league_week(conn, season_id: str, league_id: int, event: int | None) -> dict:
    league = conn.execute(
        "SELECT name, synced_at FROM leagues WHERE season_id = ? AND league_id = ?", (season_id, league_id)
    ).fetchone()
    if league is None:
        return {"league_id": league_id, "name": None, "events": [], "event": None, "entries": []}

    members = {
        r["entry_id"]: r
        for r in conn.execute(
            "SELECT entry_id, entry_name, player_name FROM league_entries WHERE season_id = ? AND league_id = ?",
            (season_id, league_id),
        )
    }
    marks = ",".join("?" * len(members))
    history = conn.execute(
        f"SELECT * FROM entry_events WHERE season_id = ? AND entry_id IN ({marks}) ORDER BY event",
        (season_id, *members),
    ).fetchall()
    events = sorted({h["event"] for h in history})
    if not events:
        return {"league_id": league_id, "name": league["name"], "events": [], "event": None, "entries": []}
    if event not in events:
        event = events[-1]

    by_event: dict[int, dict[int, dict]] = {}
    for h in history:
        by_event.setdefault(h["event"], {})[h["entry_id"]] = dict(h)
    # While a gameweek is in play FPL's history endpoint lags well behind its live one (it can
    # sit at 0 all weekend), so score the week from the picks' live points instead and carry
    # that into the season total - which is what the league table is ranked on.
    for r in conn.execute(
        f"""SELECT ep.entry_id, ep.event, SUM(ep.points * ep.multiplier) AS live
            FROM entry_picks ep
            JOIN entry_events ee ON ee.season_id = ep.season_id AND ee.entry_id = ep.entry_id AND ee.event = ep.event
            WHERE ep.season_id = ? AND ee.final = 0 AND ep.entry_id IN ({marks})
            GROUP BY ep.entry_id, ep.event""",
        (season_id, *members),
    ):
        h = by_event[r["event"]][r["entry_id"]]
        if r["live"] is not None:
            h["total_points"] += r["live"] - h["points"]
            h["points"] = r["live"]
    ranks = _rank({i: h["total_points"] for i, h in by_event[event].items()})
    previous = max((e for e in events if e < event), default=None)
    prev_ranks = _rank({i: h["total_points"] for i, h in by_event[previous].items()}) if previous else {}

    me = conn.execute("SELECT entry_id FROM manager_entry WHERE season_id = ?", (season_id,)).fetchone()
    picks_by_entry: dict[int, list] = {}
    for p in conn.execute(
        f"""SELECT ep.*, t.short_name AS team_name
            FROM entry_picks ep
            LEFT JOIN player_season ps ON ps.season_id = ep.season_id AND ps.player_code = ep.player_code
            LEFT JOIN teams t ON t.season_id = ps.season_id AND t.team_code = ps.team_code
            WHERE ep.season_id = ? AND ep.event = ? AND ep.entry_id IN ({marks})
            ORDER BY ep.squad_slot""",
        (season_id, event, *members),
    ):
        picks_by_entry.setdefault(p["entry_id"], []).append(p)

    entries = []
    for entry_id, m in members.items():
        h = by_event[event].get(entry_id)
        if h is None:
            continue  # joined FPL after this gameweek
        chip = h["active_chip"]
        picks = []
        for p in picks_by_entry.get(entry_id, []):
            starter = p["squad_slot"] <= 11
            # The projection's multiplier is the manager's intent, not the outcome: the captain
            # as named (FPL moves the armband to the vice only after the fact), the bench only
            # under Bench Boost.
            intent = (3 if chip == "3xc" else 2) if p["is_captain"] else 1
            if not starter and chip != "bboost":
                intent = 0
            picks.append(
                {
                    "player_code": p["player_code"],
                    "web_name": p["web_name"],
                    "position": p["position"],
                    "team_name": p["team_name"],
                    "squad_slot": p["squad_slot"],
                    "multiplier": p["multiplier"],
                    "is_captain": p["is_captain"],
                    "is_vice_captain": p["is_vice_captain"],
                    "auto_sub": p["auto_sub"],
                    "minutes": p["minutes"],
                    # Bench players show their own score and projection, flagged as not counting,
                    # so "what did I leave on the bench" is readable straight off the card.
                    "points": p["points"] * max(p["multiplier"], 1) if p["points"] is not None else None,
                    "counts": p["multiplier"] > 0,
                    "xp": round(p["xp"] * max(intent, 1), 2) if p["xp"] is not None else None,
                    "xp_counts": intent > 0,
                }
            )
        with_xp = [p["xp"] for p in picks if p["xp"] is not None and p["xp_counts"]]
        chips_used = [
            {"event": e, "chip": by_event[e][entry_id]["active_chip"]}
            for e in events
            if e <= event and entry_id in by_event[e] and by_event[e][entry_id]["active_chip"]
        ]
        entries.append(
            {
                "entry_id": entry_id,
                "entry_name": m["entry_name"],
                "player_name": m["player_name"],
                "is_me": bool(me and me["entry_id"] == entry_id),
                "rank": ranks[entry_id],
                "prev_rank": prev_ranks.get(entry_id),
                "points": h["points"],
                "transfers": h["event_transfers"],
                "hits": h["event_transfers_cost"],
                "points_on_bench": h["points_on_bench"],
                "total_points": h["total_points"],
                "overall_rank": h["overall_rank"],
                "value": h["value"] / 10.0 if h["value"] is not None else None,
                "chip": chip,
                "chips_used": chips_used,
                # Blank rather than a part-sum when the squad has no projections at all.
                "projected": round(sum(with_xp), 1) if with_xp else None,
                "projected_picks": len(with_xp),
                "final": bool(h["final"]),
                "picks": picks,
            }
        )
    entries.sort(key=lambda e: (e["rank"], -e["points"]))
    return {
        "league_id": league_id,
        "name": league["name"],
        "synced_at": league["synced_at"],
        "events": events,
        "event": event,
        "final": all(e["final"] for e in entries),
        "entries": entries,
    }
