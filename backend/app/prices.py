"""Player price tracking: a nightly snapshot of the live FPL API, and the Prices page's table.

FPL reprices players once a night (~01:30 UK) on net transfers, and its API only ever serves the
current state - there is no price-history endpoint. So the history is *recorded*: every capture
stores each player's price, ownership and transfer counters under a **price day**, and everything
the page shows beyond "now" (yesterday's movers, the week's drift, transfers since a player last
changed price) is a difference between two stored days.

Two conventions are worth knowing before reading the code:

- A price day is the UK date of the overnight change a capture follows, so a reading taken at
  00:40 UK - before that night's change - still belongs to the previous day. PRICE_DAY_ROLLOVER
  is set a little after FPL's usual 01:30 so a late change doesn't land on the wrong day.
- Captures within a day overwrite. The day therefore ends up holding its *last* reading, which
  is the closest we get to the transfer counts FPL actually decided that night's change on.

Nothing here predicts a price change. FPL's thresholds are unpublished and have been retuned
more than once; "pressure" is a transparent ranking signal (net transfers since the player last
moved, as a share of the managers who own them), not a forecast. DESIGN.md covers what would be
needed to calibrate a real one from the snapshots this module accumulates.
"""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.my_team import fetch_bootstrap, live_season_id

UK = ZoneInfo("Europe/London")
PRICE_DAY_ROLLOVER = timedelta(hours=2)
# Opening the page captures too, so the table is live rather than as-of-last-night. Reloads
# inside this window reuse the stored reading instead of hitting the FPL API again.
CAPTURE_MIN_INTERVAL = timedelta(minutes=10)


def price_day(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return (now.astimezone(UK) - PRICE_DAY_ROLLOVER).date().isoformat()


def selling_price(purchase: int, now: int) -> int:
    """FPL's sell-on rule, in tenths: you keep half of any rise, rounded down to 0.1, and wear
    the whole of any fall."""
    return purchase + (now - purchase) // 2 if now >= purchase else now


def capture_snapshot(conn, bootstrap: dict | None = None, now: datetime | None = None) -> dict:
    """Stores the live API's current prices under today's price day. Idempotent within a day."""
    now = now or datetime.now(timezone.utc)
    bootstrap = bootstrap or fetch_bootstrap()
    season_id = live_season_id(bootstrap)
    day = price_day(now)

    events = bootstrap["events"]
    event = next((e["id"] for e in events if e.get("is_current")), None) or next(
        (e["id"] for e in events if e.get("is_next")), None
    )

    def _int(v):
        return None if v is None else int(v)

    rows = [
        (
            season_id,
            int(e["code"]),
            day,
            int(e["now_cost"]),
            _int(e.get("cost_change_event")),
            _int(e.get("cost_change_start")),
            _int(e.get("transfers_in")),
            _int(e.get("transfers_out")),
            _int(e.get("transfers_in_event")),
            _int(e.get("transfers_out_event")),
            float(e["selected_by_percent"]) if e.get("selected_by_percent") not in (None, "") else None,
            e.get("status"),
        )
        for e in bootstrap["elements"]
    ]

    cur = conn.cursor()
    # seed_seasons normally guarantees the season row; the standalone script may run first.
    known = cur.execute("SELECT 1 FROM seasons WHERE id = ?", (season_id,)).fetchone()
    if not known:
        raise ValueError(f"Live season {season_id} isn't in the seasons table yet (see app/seasons.py).")
    cur.execute(
        """INSERT INTO price_snapshot_days (season_id, price_day, captured_at, event, total_players)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(season_id, price_day) DO UPDATE SET
               captured_at = excluded.captured_at,
               event = excluded.event,
               total_players = excluded.total_players""",
        (season_id, day, now.isoformat(), event, bootstrap.get("total_players")),
    )
    cur.executemany(
        """INSERT OR REPLACE INTO player_price_snapshots
               (season_id, player_code, price_day, now_cost, cost_change_event, cost_change_start,
                transfers_in, transfers_out, transfers_in_event, transfers_out_event,
                selected_by_percent, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    return {"season_id": season_id, "price_day": day, "captured_at": now.isoformat(), "players": len(rows)}


def capture_if_stale(conn, season_id: str, now: datetime | None = None) -> str | None:
    """Captures unless this season isn't live or a reading was just taken. Returns a warning to
    show beside the table when the live API couldn't be reached, else None."""
    now = now or datetime.now(timezone.utc)
    last = conn.execute(
        "SELECT MAX(captured_at) FROM price_snapshot_days WHERE season_id = ?", (season_id,)
    ).fetchone()[0]
    if last and now - datetime.fromisoformat(last) < CAPTURE_MIN_INTERVAL:
        return None
    try:
        bootstrap = fetch_bootstrap()
        if live_season_id(bootstrap) != season_id:
            return None
        capture_snapshot(conn, bootstrap, now)
    except Exception as e:  # offline, FPL down mid-update: the stored days are still worth showing
        return f"Couldn't reach the FPL API ({e}); showing the last stored snapshot."
    return None


def _net(row) -> int | None:
    if row is None or row["transfers_in"] is None or row["transfers_out"] is None:
        return None
    return row["transfers_in"] - row["transfers_out"]


def query_prices(conn, season_id: str) -> dict:
    days = [
        dict(r)
        for r in conn.execute(
            "SELECT price_day, captured_at, event, total_players FROM price_snapshot_days "
            "WHERE season_id = ? ORDER BY price_day",
            (season_id,),
        )
    ]
    result = {
        "season_id": season_id,
        "price_day": None,
        "captured_at": None,
        "event": None,
        "prev_day": None,
        "week_day": None,
        "first_day": None,
        "snapshot_days": len(days),
        "total_players": None,
        "unlisted": 0,
        "squad": None,
        "rows": [],
    }
    if not days:
        return result

    today = days[-1]
    day_event = {d["price_day"]: d["event"] for d in days}
    day_names = [d["price_day"] for d in days]
    prev_day = day_names[-2] if len(days) > 1 else None
    week_cutoff = (date.fromisoformat(today["price_day"]) - timedelta(days=7)).isoformat()
    week_day = max((d for d in day_names if d <= week_cutoff), default=None)

    def load_day(day: str | None) -> dict:
        if day is None:
            return {}
        return {
            r["player_code"]: r
            for r in conn.execute(
                "SELECT * FROM player_price_snapshots WHERE season_id = ? AND price_day = ?",
                (season_id, day),
            )
        }

    current, previous, week_ago = load_day(today["price_day"]), load_day(prev_day), load_day(week_day)

    # For each player, the last stored day on which their price differed from today's: the day
    # before their most recent change, whose counters are the baseline for "since last change".
    before_change = {
        r["player_code"]: r
        for r in conn.execute(
            """SELECT s.* FROM player_price_snapshots s
               JOIN (
                   SELECT o.player_code, MAX(o.price_day) AS day
                   FROM player_price_snapshots o
                   JOIN player_price_snapshots c
                     ON c.season_id = o.season_id AND c.player_code = o.player_code AND c.price_day = ?
                   WHERE o.season_id = ? AND o.now_cost != c.now_cost
                   GROUP BY o.player_code
               ) last ON last.player_code = s.player_code AND last.day = s.price_day
               WHERE s.season_id = ?""",
            (today["price_day"], season_id, season_id),
        )
    }
    # Players who haven't moved since tracking began fall back to their first stored day.
    first_seen = {
        r["player_code"]: r
        for r in conn.execute(
            """SELECT s.* FROM player_price_snapshots s
               JOIN (
                   SELECT player_code, MIN(price_day) AS day FROM player_price_snapshots
                   WHERE season_id = ? GROUP BY player_code
               ) f ON f.player_code = s.player_code AND f.day = s.price_day
               WHERE s.season_id = ?""",
            (season_id, season_id),
        )
    }

    meta = {
        r["player_code"]: r
        for r in conn.execute(
            """SELECT ps.player_code, p.web_name, ps.position, t.short_name AS team_name,
                      ms.squad_slot, ms.purchase_price, ms.purchase_estimated
               FROM player_season ps
               JOIN players p ON p.player_code = ps.player_code
               LEFT JOIN teams t ON t.season_id = ps.season_id AND t.team_code = ps.team_code
               LEFT JOIN manager_squad ms ON ms.season_id = ps.season_id AND ms.player_code = ps.player_code
               WHERE ps.season_id = ?""",
            (season_id,),
        )
    }

    total_players = today["total_players"]
    rows = []
    for code, c in current.items():
        m = meta.get(code)
        if m is None:
            continue  # in the live API but not ingested yet; counted below
        net_now = _net(c)
        net_event = (
            c["transfers_in_event"] - c["transfers_out_event"]
            if c["transfers_in_event"] is not None and c["transfers_out_event"] is not None
            else None
        )
        # Transfers since the player last changed price, from the best baseline available:
        #   change    - a change was seen in the snapshots; count from the day before it.
        #   gameweek  - no change seen and tracking began inside this gameweek, but FPL says the
        #               price hasn't moved this gameweek either, so its own per-gameweek counter
        #               is a clean (and longer) window. This is what makes day one useful.
        #   tracking  - otherwise, count from the first stored day: a lower bound.
        base = before_change.get(code)
        basis = "change"
        if base is None:
            base = first_seen.get(code)
            basis = "tracking"
        net_since = net_now - _net(base) if net_now is not None and _net(base) is not None else None
        if (
            basis == "tracking"
            and net_event is not None
            and c["cost_change_event"] == 0
            and day_event.get(base["price_day"]) == today["event"]
        ):
            net_since, basis = net_event, "gameweek"
        prev, week = previous.get(code), week_ago.get(code)

        owners = None
        if total_players and c["selected_by_percent"] is not None:
            owners = c["selected_by_percent"] / 100.0 * total_players
        # Below ~1,000 owners the ratio is all noise (one mini-league moving a 4.0 defender).
        pressure = net_since / owners * 100.0 if net_since is not None and owners and owners >= 1000 else None

        purchase = m["purchase_price"]
        sell = selling_price(purchase, c["now_cost"]) if purchase is not None else None
        rows.append(
            {
                "player_code": code,
                "web_name": m["web_name"],
                "position": m["position"],
                "team_name": m["team_name"],
                "status": c["status"],
                "price": c["now_cost"] / 10.0,
                "change_day": (c["now_cost"] - prev["now_cost"]) / 10.0 if prev else None,
                "change_week": (c["now_cost"] - week["now_cost"]) / 10.0 if week else None,
                "change_event": c["cost_change_event"] / 10.0 if c["cost_change_event"] is not None else None,
                "change_start": c["cost_change_start"] / 10.0 if c["cost_change_start"] is not None else None,
                "selected_by_percent": c["selected_by_percent"],
                "transfers_in_event": c["transfers_in_event"],
                "transfers_out_event": c["transfers_out_event"],
                "net_event": net_event,
                "net_day": net_now - _net(prev) if net_now is not None and _net(prev) is not None else None,
                "net_since_change": net_since,
                "since_basis": basis,
                "since_day": base["price_day"] if base is not None else None,
                "pressure": pressure,
                "squad_slot": m["squad_slot"],
                "purchase_price": purchase / 10.0 if purchase is not None else None,
                "purchase_estimated": bool(m["purchase_estimated"]),
                "selling_price": sell / 10.0 if sell is not None else None,
                "profit": (sell - purchase) / 10.0 if sell is not None else None,
            }
        )
    rows.sort(key=lambda r: (r["pressure"] is None, -(r["pressure"] or 0)))

    owned = [r for r in rows if r["squad_slot"] is not None]
    squad = None
    if owned:
        entry = conn.execute("SELECT bank FROM manager_entry WHERE season_id = ?", (season_id,)).fetchone()
        priced = [r for r in owned if r["selling_price"] is not None]
        squad = {
            "players": len(owned),
            "bank": entry["bank"] / 10.0 if entry and entry["bank"] is not None else None,
            "market_value": round(sum(r["price"] for r in owned), 1),
            # Only meaningful once every pick has a purchase price (i.e. after a re-sync).
            "selling_value": round(sum(r["selling_price"] for r in priced), 1) if len(priced) == len(owned) else None,
            "purchase_value": round(sum(r["purchase_price"] for r in priced), 1) if len(priced) == len(owned) else None,
        }

    result.update(
        {
            "price_day": today["price_day"],
            "captured_at": today["captured_at"],
            "event": today["event"],
            "prev_day": prev_day,
            "week_day": week_day,
            "first_day": day_names[0],
            "total_players": total_players,
            "unlisted": sum(1 for code in current if code not in meta),
            "squad": squad,
            "rows": rows,
        }
    )
    return result
