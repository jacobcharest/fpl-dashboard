"""Import of external expected-points projections (FPL Review's Massive Data model and similar).

Two things make a tolerant parser worth the effort here:

1. **Every provider uses a different CSV layout.** FPL Review, community exports and ad-hoc
   scrapes all disagree on column names, and most ship a *wide* layout (one column per
   gameweek, e.g. `1_Pts`, `2_Pts`) rather than one row per gameweek. Rather than pin to one
   vendor's format and break on the next, `parse_csv` sniffs the identifier column and the
   gameweek columns, accepting long or wide input.

2. **Projections identify players by name, not by a stable id.** Names are ambiguous
   ("Gomez", "Anderson", "Muñoz" all collide in a single season), so resolution goes through
   the live bootstrap on `web_name` + team where a team column exists. Anything that stays
   ambiguous or unmatched is *reported*, never silently dropped - a projection import that
   quietly loses a third of the squad is worse than one that fails loudly.

Storage is per (player, gameweek, source) so any horizon can be summed at query time and two
models can sit side by side for comparison.
"""

import csv
import json
import re
import urllib.request
from datetime import datetime, timezone

BOOTSTRAP_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"

# Column-name candidates, lowercased.
NAME_COLS = ("name", "web_name", "player", "player_name", "full_name")
CODE_COLS = ("code", "player_code")
TEAM_COLS = ("team", "team_short", "club", "team_name")
# Wide gameweek columns: "1_Pts", "gw1", "gw_1", "1_xMins", ...
GW_PTS_RE = re.compile(r"^(?:gw[_ ]?)?(\d{1,2})(?:_)?(?:pts|xp|points)?$|^(\d{1,2})_(?:pts|xp|points)$", re.I)
GW_MINS_RE = re.compile(r"^(?:gw[_ ]?)?(\d{1,2})_(?:xmins|mins|minutes)$", re.I)


def fetch_code_index() -> dict:
    """{(web_name_lower, team_short_lower): code} plus {web_name_lower: [codes]} for fallback."""
    req = urllib.request.Request(BOOTSTRAP_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp:
        data = json.load(resp)
    teams = {t["id"]: t["short_name"].lower() for t in data["teams"]}
    by_name_team, by_name = {}, {}
    for e in data["elements"]:
        n = e["web_name"].strip().lower()
        by_name_team[(n, teams[e["team"]])] = e["code"]
        by_name.setdefault(n, []).append(e["code"])
    return {"by_name_team": by_name_team, "by_name": by_name}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_csv(path: str) -> tuple[list[dict], list[str]]:
    """Returns (rows, warnings). Each row: {name|code, team, gw, xp, xmins}."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        raw = list(reader)
        headers = reader.fieldnames or []
    if not raw:
        return [], ["file has no data rows"]

    lower = {h.lower().strip(): h for h in headers}
    name_col = next((lower[c] for c in NAME_COLS if c in lower), None)
    code_col = next((lower[c] for c in CODE_COLS if c in lower), None)
    team_col = next((lower[c] for c in TEAM_COLS if c in lower), None)
    if not name_col and not code_col:
        return [], [f"no player name or code column found; headers were: {headers}"]

    # Long format: an explicit gameweek column plus a points column.
    gw_col = next((lower[c] for c in ("gw", "round", "gameweek", "event") if c in lower), None)
    pts_col = next((lower[c] for c in ("xp", "pts", "points", "xpts") if c in lower), None)
    mins_col = next((lower[c] for c in ("xmins", "mins", "minutes") if c in lower), None)

    out, warnings = [], []
    if gw_col and pts_col:
        for r in raw:
            gw, xp = _num(r.get(gw_col)), _num(r.get(pts_col))
            if gw is None or xp is None:
                continue
            out.append(dict(name=r.get(name_col), code=r.get(code_col), team=r.get(team_col),
                            gw=int(gw), xp=xp, xmins=_num(r.get(mins_col)) if mins_col else None))
        return out, warnings

    # Wide format: sniff per-gameweek columns out of the header.
    pts_by_gw, mins_by_gw = {}, {}
    for h in headers:
        hs = h.strip()
        if hs in (name_col, code_col, team_col):
            continue
        m = GW_MINS_RE.match(hs)
        if m:
            mins_by_gw[int(m.group(1))] = h
            continue
        m = GW_PTS_RE.match(hs)
        if m:
            gw = int(next(g for g in m.groups() if g))
            pts_by_gw[gw] = h
    if not pts_by_gw:
        return [], [f"could not identify any gameweek columns; headers were: {headers}"]

    for r in raw:
        for gw, col in sorted(pts_by_gw.items()):
            xp = _num(r.get(col))
            if xp is None:
                continue
            mins = _num(r.get(mins_by_gw[gw])) if gw in mins_by_gw else None
            out.append(dict(name=r.get(name_col), code=r.get(code_col), team=r.get(team_col),
                            gw=gw, xp=xp, xmins=mins))
    return out, warnings


def import_projections(conn, season_id: str, csv_path: str, source: str = "fplreview") -> dict:
    rows, warnings = parse_csv(csv_path)
    if not rows:
        raise ValueError("; ".join(warnings) or "nothing parsed from that file")

    idx = fetch_code_index()
    resolved, unmatched, ambiguous = [], set(), set()
    for r in rows:
        code = None
        if r.get("code"):
            code = int(float(r["code"]))
        else:
            n = (r.get("name") or "").strip().lower()
            t = (r.get("team") or "").strip().lower()
            if t and (n, t) in idx["by_name_team"]:
                code = idx["by_name_team"][(n, t)]
            else:
                hits = idx["by_name"].get(n, [])
                if len(hits) == 1:
                    code = hits[0]
                elif len(hits) > 1:
                    ambiguous.add(r.get("name"))
                else:
                    unmatched.add(r.get("name"))
        if code is not None:
            resolved.append((season_id, code, r["gw"], source, r["xp"], r["xmins"]))

    now = datetime.now(timezone.utc).isoformat()
    cur = conn.cursor()
    cur.execute("DELETE FROM player_projections WHERE season_id = ? AND source = ?", (season_id, source))
    cur.executemany(
        """INSERT OR REPLACE INTO player_projections
               (season_id, player_code, round, source, xp, xmins, imported_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [row + (now,) for row in resolved],
    )
    conn.commit()

    gws = sorted({r[2] for r in resolved})
    return {
        "season_id": season_id, "source": source,
        "rows_imported": len(resolved),
        "players": len({r[1] for r in resolved}),
        "gameweeks": [gws[0], gws[-1]] if gws else [],
        "unmatched": sorted(x for x in unmatched if x)[:20],
        "ambiguous": sorted(x for x in ambiguous if x)[:20],
        "warnings": warnings,
    }


def projection_sources(conn, season_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT source, COUNT(DISTINCT player_code) AS players,
                  MIN(round) AS first_gw, MAX(round) AS last_gw, MAX(imported_at) AS imported_at
           FROM player_projections WHERE season_id = ? GROUP BY source ORDER BY source""",
        (season_id,),
    ).fetchall()
    return [dict(r) for r in rows]
