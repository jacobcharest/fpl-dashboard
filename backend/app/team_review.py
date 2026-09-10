"""Review of the user's own squad: best lineup, captain, and transfer suggestions.

Everything here is built on data the app already holds - the synced squad
(app/my_team.py), this season's gameweek stats, and whichever projection source is
loaded (app/projections.py, or the built-in app/fpl_projections.py model) - plus three
public FPL endpoints for the manager's bank, transfer history and chips. No login is
needed.

Two FPL rules the suggestions have to respect, and how they're approximated here:

- **Selling price is not current price.** Profit on a player is halved (rounded down)
  when selling. The public picks endpoint doesn't expose purchase prices, so they're
  reconstructed: the GW1 price for the original squad, and the recorded
  ``element_in_cost`` for anyone bought since. Wildcard rebuilds are recorded as
  ordinary transfers, so this holds through them too.
- **Free transfers roll over, up to five.** They aren't public either, so they're
  simulated from the transfer history (one added per gameweek, a chip week costs none, a
  hit resets to zero carried). Pass ``free_transfers`` to override when the estimate is
  off.

Transfer suggestions maximise projected points over the horizon, net of the 4-point hit
for each transfer beyond the free ones, subject to budget, the three-per-club limit, and
the position counts (2 GK, 5 DEF, 5 MID, 3 FWD), which swapping like-for-like preserves.
"""

from itertools import combinations

from app.my_team import (
    API_BASE,
    _get_json,
    fetch_bootstrap,
    fetch_picks,
    latest_available_event,
    sync_my_team,
)
from app.seasons import POSITION_BY_ELEMENT_TYPE

HIT_COST = 4
MAX_FREE_TRANSFERS = 5
MAX_PER_CLUB = 3
# Candidates are only worth considering if they're actually expected on the pitch.
MIN_CANDIDATE_XMINS = 60
# Every valid FPL formation as (DEF, MID, FWD); always exactly one GK.
FORMATIONS = [
    (d, m, f)
    for d in range(3, 6)
    for m in range(2, 6)
    for f in range(1, 4)
    if d + m + f == 10
]


def fetch_entry_history(entry_id: int) -> dict:
    return _get_json(f"{API_BASE}/entry/{entry_id}/history/")


def fetch_transfers(entry_id: int) -> list[dict]:
    return _get_json(f"{API_BASE}/entry/{entry_id}/transfers/")


def estimate_free_transfers(
    transfers: list[dict], chips: list[dict], next_event: int
) -> int:
    """Simulate the free-transfer count going into ``next_event``.

    Parameters
    ----------
    transfers : list[dict]
        The manager's transfer history (``entry/{id}/transfers/``).
    chips : list[dict]
        Chips played, from ``entry/{id}/history/``.
    next_event : int
        The gameweek being planned for.

    Returns
    -------
    int
        Estimated free transfers available, 1 to ``MAX_FREE_TRANSFERS``.
    """
    made = {}
    for t in transfers:
        made[t["event"]] = made.get(t["event"], 0) + 1
    chip_weeks = {c["event"] for c in chips if c["name"] in ("wildcard", "freehit")}
    free = 1
    for gw in range(2, next_event):
        if gw not in chip_weeks:
            free = max(0, free - made.get(gw, 0))
        free = min(MAX_FREE_TRANSFERS, free + 1)
    return free


def purchase_prices(picks: list[dict], transfers: list[dict], elements: dict) -> dict:
    """{element_id: purchase price (tenths)} for the current squad."""
    bought = {}
    for t in sorted(transfers, key=lambda t: t["time"]):
        bought[t["element_in"]] = t["element_in_cost"]
    out = {}
    for p in picks:
        e = elements[p["element"]]
        out[p["element"]] = bought.get(
            p["element"], e["now_cost"] - e["cost_change_start"]
        )
    return out


def selling_price(now_cost: int, purchase: int) -> int:
    profit = now_cost - purchase
    return purchase + profit // 2 if profit > 0 else now_cost


def _projection_map(
    conn, season_id: str, source: str, first_gw: int, last_gw: int
) -> dict:
    """{player_code: {gw: (xp, xmins)}} over the horizon."""
    rows = conn.execute(
        """SELECT player_code, round, xp, xmins FROM player_projections
           WHERE season_id = ? AND source = ? AND round BETWEEN ? AND ?""",
        (season_id, source, first_gw, last_gw),
    ).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["player_code"], {})[r["round"]] = (
            r["xp"] or 0.0,
            r["xmins"] or 0.0,
        )
    return out


def _season_totals(conn, season_id: str) -> dict:
    rows = conn.execute(
        """SELECT player_code, SUM(total_points) AS pts, SUM(minutes) AS mins
           FROM player_gw_stats WHERE season_id = ? GROUP BY player_code""",
        (season_id,),
    ).fetchall()
    return {r["player_code"]: (r["pts"], r["mins"]) for r in rows}


def _player_row(e, teams, proj, totals, gws):
    per_gw = proj.get(e["code"], {})
    xp = [per_gw.get(g, (0.0, 0.0))[0] for g in gws]
    xm = [per_gw.get(g, (0.0, 0.0))[1] for g in gws]
    pts, mins = totals.get(e["code"], (0, 0))
    return {
        "element": e["id"],
        "code": e["code"],
        "name": e["web_name"],
        "team": teams[e["team"]],
        "position": POSITION_BY_ELEMENT_TYPE.get(e["element_type"], "UNK"),
        "price": e["now_cost"] / 10,
        "status": e["status"],
        "news": e.get("news") or "",
        "chance": e.get("chance_of_playing_next_round"),
        "ep_next": float(e.get("ep_next") or 0),
        "form": float(e.get("form") or 0),
        "selected_by": float(e.get("selected_by_percent") or 0),
        "season_points": pts,
        "season_minutes": mins,
        "xp_next": round(xp[0], 2) if xp else 0.0,
        "xp_horizon": round(sum(xp), 2),
        "xmins_avg": round(sum(xm) / len(xm), 1) if xm else 0.0,
        "xp_by_gw": dict(zip(gws, [round(v, 2) for v in xp])),
    }


def best_lineup(squad: list[dict]) -> dict:
    """Pick the XI, captain, vice-captain and bench order that maximise next-gameweek
    xP.

    Parameters
    ----------
    squad : list[dict]
        The 15 player rows from ``_player_row``.

    Returns
    -------
    dict
        ``starting`` (11 rows), ``bench`` (4 rows, GK first), ``captain``,
        ``vice_captain``, ``formation`` and the XI's ``xp_next`` total.
    """
    by_pos = {
        p: sorted((s for s in squad if s["position"] == p), key=lambda s: -s["xp_next"])
        for p in ("GK", "DEF", "MID", "FWD")
    }
    best, best_total, best_formation = None, -1.0, None
    for d, m, f in FORMATIONS:
        if len(by_pos["DEF"]) < d or len(by_pos["MID"]) < m or len(by_pos["FWD"]) < f:
            continue
        xi = (
            by_pos["GK"][:1] + by_pos["DEF"][:d] + by_pos["MID"][:m] + by_pos["FWD"][:f]
        )
        total = sum(s["xp_next"] for s in xi)
        if total > best_total:
            best, best_total, best_formation = xi, total, f"{d}-{m}-{f}"
    starting_ids = {s["element"] for s in best}
    bench_gk = [s for s in by_pos["GK"] if s["element"] not in starting_ids]
    bench_out = sorted(
        (
            s
            for s in squad
            if s["element"] not in starting_ids and s["position"] != "GK"
        ),
        key=lambda s: -s["xp_next"],
    )
    ranked = sorted(best, key=lambda s: -s["xp_next"])
    return {
        "formation": best_formation,
        "starting": best,
        "bench": bench_gk + bench_out,
        "captain": ranked[0],
        "vice_captain": ranked[1],
        "xp_next": round(best_total, 2),
    }


def _club_counts(squad: list[dict]) -> dict:
    counts = {}
    for s in squad:
        counts[s["team"]] = counts.get(s["team"], 0) + 1
    return counts


def suggest_transfers(
    squad: list[dict],
    pool: list[dict],
    sell: dict,
    bank: int,
    free_transfers: int,
    max_moves: int = 2,
    top_n: int = 6,
) -> dict:
    """Rank single and double transfers by projected gain over the horizon, net of hits.

    Parameters
    ----------
    squad : list[dict]
        Current 15 player rows.
    pool : list[dict]
        Every other player row, already filtered for availability.
    sell : dict
        ``{element: selling price in tenths}`` for the squad.
    bank : int
        Money in the bank, in tenths.
    free_transfers : int
        Free transfers available.
    max_moves : int
        Consider plans of up to this many transfers.
    top_n : int
        How many plans of each size to return.

    Returns
    -------
    dict
        ``singles`` and ``doubles``, each sorted by ``net_gain`` descending; every plan
        has its ``moves``, ``hit`` and resulting ``bank_after``.
    """
    clubs = _club_counts(squad)
    squad_codes = {s["code"] for s in squad}
    pool = [p for p in pool if p["code"] not in squad_codes]

    singles = []
    for out in squad:
        for inc in pool:
            if inc["position"] != out["position"]:
                continue
            cost = inc["now_cost_tenths"] - sell[out["element"]]
            if cost > bank:
                continue
            if inc["team"] != out["team"] and clubs.get(inc["team"], 0) >= MAX_PER_CLUB:
                continue
            gain = inc["xp_horizon"] - out["xp_horizon"]
            if gain <= 0:
                continue
            singles.append({"out": out, "in": inc, "cost": cost, "gain": gain})
    singles.sort(key=lambda m: -m["gain"])

    def plan(moves):
        hit = max(0, len(moves) - free_transfers) * HIT_COST
        gain = sum(m["gain"] for m in moves)
        spend = sum(m["cost"] for m in moves)
        return {
            "moves": moves,
            "transfers": len(moves),
            "hit": hit,
            "gross_gain": round(gain, 2),
            "net_gain": round(gain - hit, 2),
            "bank_after": (bank - spend) / 10,
        }

    single_plans = [plan([m]) for m in singles[:top_n]]
    double_plans = []
    if max_moves >= 2:
        shortlist = singles[:40]
        for a, b in combinations(shortlist, 2):
            if (
                a["out"]["element"] == b["out"]["element"]
                or a["in"]["code"] == b["in"]["code"]
            ):
                continue
            if a["cost"] + b["cost"] > bank:
                continue
            after = dict(clubs)
            for m in (a, b):
                after[m["out"]["team"]] -= 1
                after[m["in"]["team"]] = after.get(m["in"]["team"], 0) + 1
            if max(after.values()) > MAX_PER_CLUB:
                continue
            double_plans.append(plan([a, b]))
        double_plans.sort(key=lambda p: -p["net_gain"])
    return {"singles": single_plans, "doubles": double_plans[:top_n]}


def review_team(
    conn,
    season_id: str,
    entry_id: int,
    source: str = "fpl_api",
    horizon: int = 5,
    free_transfers: int | None = None,
) -> dict:
    """Pull the squad, score it against the loaded projections, and suggest moves.

    Parameters
    ----------
    conn : sqlite3.Connection
        Open database connection (the squad sync is stored as a side effect).
    season_id : str
        Local season id; must be the live season.
    entry_id : int
        The manager's FPL team id.
    source : str
        Projection source to plan against.
    horizon : int
        Gameweeks ahead to sum projections over when ranking transfers.
    free_transfers : int, optional
        Override for the simulated free-transfer count.

    Returns
    -------
    dict
        Manager summary, next gameweek, the annotated squad, ``lineup`` and
        ``transfer_plans``.
    """
    sync = sync_my_team(conn, season_id, entry_id)
    if sync["status"] != "synced":
        raise ValueError(sync["message"])

    bootstrap = fetch_bootstrap()
    elements = {e["id"]: e for e in bootstrap["elements"]}
    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    event = latest_available_event(bootstrap)
    next_event = min(
        (
            e["id"]
            for e in bootstrap["events"]
            if not e.get("finished") and not e.get("is_current")
        ),
        default=None,
    )
    if next_event is None:
        raise ValueError("The season is over - nothing left to plan for.")
    gws = list(range(next_event, min(next_event + horizon, 39)))

    picks = fetch_picks(entry_id, event)
    history = fetch_entry_history(entry_id)
    transfers = fetch_transfers(entry_id)
    current = history["current"][-1] if history.get("current") else {}
    bank = current.get("bank", 0)

    loaded = conn.execute(
        "SELECT COUNT(*) FROM player_projections WHERE season_id = ? AND source = ?",
        (season_id, source),
    ).fetchone()[0]
    if not loaded:
        raise ValueError(
            f"No '{source}' projections loaded for {season_id}. Run "
            f"scripts/generate_projections.py or import a CSV first."
        )
    proj = _projection_map(conn, season_id, source, gws[0], gws[-1])
    totals = _season_totals(conn, season_id)

    rows = {}
    for e in bootstrap["elements"]:
        row = _player_row(e, teams, proj, totals, gws)
        row["now_cost_tenths"] = e["now_cost"]
        rows[e["id"]] = row

    purchase = purchase_prices(picks, transfers, elements)
    sell = {
        el: selling_price(elements[el]["now_cost"], purchase[el]) for el in purchase
    }
    squad = []
    for p in sorted(picks, key=lambda p: p["position"]):
        row = dict(rows[p["element"]])
        row.update(
            {
                "squad_slot": p["position"],
                "is_captain": bool(p["is_captain"]),
                "is_vice_captain": bool(p["is_vice_captain"]),
                "selling_price": sell[p["element"]] / 10,
            }
        )
        squad.append(row)

    pool = [
        r
        for r in rows.values()
        if r["status"] == "a" and r["xmins_avg"] >= MIN_CANDIDATE_XMINS
    ]
    ft = (
        free_transfers
        if free_transfers is not None
        else estimate_free_transfers(transfers, history.get("chips", []), next_event)
    )
    lineup = best_lineup(squad)
    plans = suggest_transfers(squad, pool, sell, bank, ft)

    next_ev = next(e for e in bootstrap["events"] if e["id"] == next_event)
    return {
        "entry_id": entry_id,
        "entry_name": sync["entry_name"],
        "manager_name": sync["manager_name"],
        "season_id": season_id,
        "source": source,
        "synced_event": event,
        "next_event": next_event,
        "deadline": next_ev["deadline_time"],
        "horizon_gws": gws,
        "overall_points": current.get("total_points"),
        "overall_rank": current.get("overall_rank"),
        "last_event_points": current.get("points"),
        "bank": bank / 10,
        "squad_value": current.get("value", 0) / 10,
        "free_transfers": ft,
        "free_transfers_estimated": free_transfers is None,
        "chips_used": [c["name"] for c in history.get("chips", [])],
        "squad": squad,
        "lineup": lineup,
        "transfer_plans": plans,
    }
