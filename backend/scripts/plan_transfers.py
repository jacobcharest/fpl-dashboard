#!/usr/bin/env python3
"""Plan the next few gameweeks' transfers, XI, captain and chips with the MIP in app/optimizer.py.

    backend/.venv/bin/python backend/scripts/plan_transfers.py --entry 3935569 --free-transfers 1
    ... --csv data/fplreview-latest.csv        # projections (default: the last fetched file)
    ... --upside data/upside.csv --lambda 1    # add haul upside from scripts/simulate_upside.py
    ... --bench-boost auto --triple-captain 12 # chips: a gameweek, "auto", or omit
    ... --ft-value 2,1.25,0.75,0.25            # carry value of the 2nd..5th banked transfer
    ... --ft-value 0                           # plain "maximise projections" solve
    ... --money-value 1.5 / --no-price-moves   # what £1m of team value is worth; or ignore prices
    ... --max-transfers 9=0                    # test rolling in GW9
    ... --force-in 9=12345 --force-out 9=678   # pin moves by player code
    ... --json plan.json                       # machine-readable output as well

The squad comes from the live FPL API (`--entry`), which serves the picks of the gameweek in
play: transfers already made for the coming week aren't public yet, so pass `--squad` with a
JSON file ({"codes": [...], "purchase": {"code": tenths}, "bank": tenths}) if you've moved
early. Free transfers aren't public either, hence `--free-transfers`.
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import DB_PATH, get_connection
from app.my_team import fetch_bootstrap, fetch_entry, fetch_picks, latest_available_event, purchase_prices
from app.optimizer import AUTO, Plan, Player, Settings, Squad, expected_change, solve

PTS_COL = re.compile(r"^(\d{1,2})_Pts$")


def load_projections(path: Path) -> list[Player]:
    """Players with per-gameweek projections from an FPL Review export (scripts/fplreview_export.js)."""
    players = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            if not row.get("code"):
                continue
            xp = {}
            for col, val in row.items():
                m = PTS_COL.match(col or "")
                if m and val not in (None, ""):
                    xp[int(m.group(1))] = float(val)
            progress = row.get("price_progress")
            progress = float(progress) if progress not in (None, "") else None
            players.append(
                Player(
                    code=int(float(row["code"])),
                    name=row["name"],
                    team=row["team"],
                    pos=row["pos"],
                    price=int(round(float(row["price"]) * 10)),
                    xp=xp,
                    expected_change=expected_change(progress),
                    price_progress=progress,
                )
            )
    return players


def load_upside(path: Path, players: list[Player]) -> None:
    by_code = {p.code: p for p in players}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            p = by_code.get(int(float(row["code"])))
            if p is not None and row.get("up") not in (None, ""):
                p.upside[int(row["gw"])] = float(row["up"])


def squad_from_api(entry_id: int, free_transfers: int, bank_override: int | None) -> tuple[Squad, int]:
    """The squad of the gameweek in play, with purchase prices where the transfer history allows."""
    bootstrap = fetch_bootstrap()
    event = latest_available_event(bootstrap)
    if event is None:
        raise SystemExit("The season hasn't started: picks aren't public yet, use --squad.")
    payload = fetch_picks(entry_id, event)
    if payload is None:
        raise SystemExit(f"Picks for GW{event} aren't public for entry {entry_id}.")
    picks = payload["picks"]
    code_by_element = {e["id"]: e["code"] for e in bootstrap["elements"]}
    paid = {}
    try:
        conn = get_connection() if DB_PATH.exists() else None
        entry = fetch_entry(entry_id)
        from app.my_team import live_season_id

        paid = purchase_prices(conn, live_season_id(bootstrap), entry_id, entry, picks, bootstrap)
    except Exception as e:  # purchase prices are a refinement; current prices still give a plan
        print(f"warning: purchase prices unavailable ({e}); selling at current price", file=sys.stderr)
    squad = Squad(
        codes=[code_by_element[p["element"]] for p in picks],
        purchase={
            code_by_element[el]: price for el, (price, _) in paid.items() if price is not None
        },
        bank=bank_override if bank_override is not None else int((payload.get("entry_history") or {}).get("bank") or 0),
        free_transfers=free_transfers,
    )
    return squad, event


def squad_from_json(path: Path, free_transfers: int, bank_override: int | None) -> Squad:
    data = json.loads(path.read_text())
    return Squad(
        codes=[int(c) for c in data["codes"]],
        purchase={int(k): int(v) for k, v in (data.get("purchase") or {}).items()},
        bank=bank_override if bank_override is not None else int(data.get("bank") or 0),
        free_transfers=int(data.get("free_transfers") or free_transfers),
    )


def parse_gw_map(items: list[str], many: bool) -> dict:
    out = {}
    for item in items or []:
        gw, _, val = item.partition("=")
        codes = [int(v) for v in val.split(",") if v]
        out[int(gw)] = codes if many else codes[0]
    return out


def chip_arg(val: str | None):
    if val is None:
        return None
    return AUTO if val.lower() == AUTO else int(val)


def money(tenths: int) -> str:
    return f"£{tenths / 10:.1f}m"


def timing_note(wk) -> str:
    """When to make this week's transfers: before tonight's price change, or wait for team news.

    A sale of a player about to fall, or a buy of one about to rise, is worth doing tonight;
    anything else can wait for the deadline and the news that comes before it.
    """
    now = [f"{p.name} {p.price_progress / 10:+.0f}%" for p in wk.transfers_out if (p.price_progress or 0) <= -500]
    now += [f"{p.name} {p.price_progress / 10:+.0f}%" for p in wk.transfers_in if (p.price_progress or 0) >= 500]
    if now:
        return f"   Price timing: do tonight, before the change ({', '.join(now)})"
    wait = [f"{p.name} {p.price_progress / 10:+.0f}%" for p in wk.transfers_in if (p.price_progress or 0) <= -500]
    if wait:
        return f"   Price timing: wait, buy after the fall ({', '.join(wait)})"
    return "   Price timing: no move pending, wait for team news"


def print_plan(plan: Plan, settings: Settings) -> None:
    print(f"Objective {plan.objective:.1f} = points {plan.objective - plan.team_value_points:.1f} "
          f"+ team value {plan.team_value_points:.1f} ({plan.status}), discount {settings.discount}, "
          f"hit {settings.hit_cost:g}, carry values {list(settings.ft_values)}, "
          f"upside weight {settings.upside_weight:g}, team value {settings.money_value:g} pts/£1m"
          f"{'' if settings.price_moves else ', price moves off'}\n")
    for n, wk in enumerate(plan.weeks):
        moves = ", ".join(
            f"{o.name} → {i.name} ({i.team} {money(i.price)})"
            for o, i in zip(wk.transfers_out, wk.transfers_in)
        ) or "roll"
        chip = f", {wk.chip}" if wk.chip else ""
        hit = f", hit −{wk.hits * settings.hit_cost:g}" if wk.hits else ""
        print(f"GW{wk.gameweek}: {moves}{hit}{chip}. FTs after: {wk.free_transfers_after}, "
              f"bank {money(wk.bank)}, team value {money(wk.team_value)}, projected {wk.projected:.1f}")
        if n == 0 and (wk.transfers_in or wk.transfers_out):
            print(timing_note(wk))
        xi = ", ".join(f"{p.name}{' (C)' if p is wk.captain else ''}" for p in wk.lineup)
        print(f"   XI: {xi}")
        print(f"   Bench: {', '.join(p.name for p in wk.bench)}\n")


def plan_to_dict(plan: Plan) -> dict:
    def pl(p: Player, gw: int) -> dict:
        return {"code": p.code, "name": p.name, "team": p.team, "pos": p.pos, "price": p.price, "xp": p.xp.get(gw)}

    return {
        "objective": plan.objective,
        "team_value_points": plan.team_value_points,
        "status": plan.status,
        "weeks": [
            {
                "gameweek": w.gameweek,
                "in": [pl(p, w.gameweek) for p in w.transfers_in],
                "out": [pl(p, w.gameweek) for p in w.transfers_out],
                "hits": w.hits,
                "free_transfers_after": w.free_transfers_after,
                "lineup": [pl(p, w.gameweek) for p in w.lineup],
                "bench": [pl(p, w.gameweek) for p in w.bench],
                "captain": pl(w.captain, w.gameweek),
                "chip": w.chip,
                "bank": w.bank,
                "team_value": w.team_value,
                "projected": w.projected,
            }
            for w in plan.weeks
        ],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--entry", type=int, help="FPL team id; squad read from the live API")
    src.add_argument("--squad", type=Path, help="JSON squad file (see module docstring)")
    ap.add_argument("--csv", type=Path, default=DB_PATH.parent / "fplreview-latest.csv",
                    help="FPL Review projections CSV (default: data/fplreview-latest.csv)")
    ap.add_argument("--upside", type=Path, help="upside CSV from scripts/simulate_upside.py")
    ap.add_argument("--lambda", dest="upside_weight", type=float, default=0.0,
                    help="weight of the upside term (default 0)")
    ap.add_argument("--money-value", type=float, default=1.5,
                    help="points per £1m of team value at the end of the horizon (default 1.5)")
    ap.add_argument("--no-price-moves", action="store_true",
                    help="ignore FPL Review's price progress (prices stay put)")
    ap.add_argument("--free-transfers", type=int, default=1)
    ap.add_argument("--bank", type=float, help="override the bank, in £m")
    ap.add_argument("--horizon", type=int, default=6, help="gameweeks to plan (default 6)")
    ap.add_argument("--from-gw", type=int, help="first gameweek to plan (default: first in the CSV)")
    ap.add_argument("--discount", type=float, default=0.9)
    ap.add_argument("--bench-weight", type=float, default=0.1)
    ap.add_argument("--hit-cost", type=float, default=4.0)
    ap.add_argument("--ft-cap", type=int, default=5)
    ap.add_argument("--ft-value", default="2,1.25,0.75,0.25",
                    help="marginal value of the 2nd,3rd,... banked transfer; '0' disables")
    ap.add_argument("--bench-boost", help="gameweek or 'auto'")
    ap.add_argument("--triple-captain", help="gameweek or 'auto'")
    ap.add_argument("--wildcard", type=int, help="gameweek")
    ap.add_argument("--max-transfers", action="append", metavar="GW=N")
    ap.add_argument("--force-in", action="append", metavar="GW=CODE,CODE")
    ap.add_argument("--force-out", action="append", metavar="GW=CODE,CODE")
    ap.add_argument("--ban", default="", help="player codes never to hold, comma-separated")
    ap.add_argument("--pool", type=int, default=40, help="candidates per position (default 40)")
    ap.add_argument("--time-limit", type=int, default=120, help="solver seconds")
    ap.add_argument("--json", type=Path, help="also write the plan as JSON")
    args = ap.parse_args()

    players = load_projections(args.csv)
    if args.upside:
        load_upside(args.upside, players)
    available = sorted({gw for p in players for gw in p.xp})
    start = args.from_gw or available[0]
    gameweeks = [gw for gw in available if gw >= start][: args.horizon]
    if not gameweeks:
        raise SystemExit(f"No projections from GW{start} in {args.csv} (has {available})")

    bank = None if args.bank is None else int(round(args.bank * 10))
    if args.entry:
        squad, event = squad_from_api(args.entry, args.free_transfers, bank)
        print(f"Squad from GW{event} picks of entry {args.entry}; planning GW{gameweeks[0]}-{gameweeks[-1]}")
    else:
        squad = squad_from_json(args.squad, args.free_transfers, bank)

    ft_values = tuple(float(v) for v in args.ft_value.split(",") if v)
    if ft_values == (0.0,):
        ft_values = ()
    if any(v >= args.hit_cost for v in ft_values):
        raise SystemExit("--ft-value entries must be below --hit-cost")
    settings = Settings(
        gameweeks=gameweeks,
        discount=args.discount,
        bench_weight=args.bench_weight,
        hit_cost=args.hit_cost,
        ft_cap=args.ft_cap,
        ft_values=ft_values,
        upside_weight=args.upside_weight,
        money_value=args.money_value,
        price_moves=not args.no_price_moves,
        bench_boost=chip_arg(args.bench_boost),
        triple_captain=chip_arg(args.triple_captain),
        wildcard=args.wildcard,
        max_transfers=parse_gw_map(args.max_transfers, many=False),
        force_in=parse_gw_map(args.force_in, many=True),
        force_out=parse_gw_map(args.force_out, many=True),
        banned=[int(c) for c in args.ban.split(",") if c],
        pool_per_position=args.pool,
        time_limit=args.time_limit,
    )
    plan = solve(players, squad, settings)
    print_plan(plan, settings)
    if args.json:
        args.json.write_text(json.dumps(plan_to_dict(plan), indent=2))
        print(f"Wrote {args.json}")


if __name__ == "__main__":
    main()
