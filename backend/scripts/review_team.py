#!/usr/bin/env python3
"""Review your squad: suggested XI, captain, and the best transfers for the coming
gameweeks.

    backend/.venv/bin/python backend/scripts/review_team.py 2026-27 1234567 ...
    --horizon 5          # gameweeks to sum projections over when ranking transfers ...
    --source fplreview   # plan against an imported model instead of the built-in one
    ... --free-transfers 2   # override the simulated free-transfer count ... --json
    # machine-readable output

Requires this season's stats (backfill_history.py) and a projection source
(generate_projections.py or import_projections.py) to be loaded. Reads only public FPL
endpoints; see app/team_review.py for the rules it approximates and how.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_connection, init_db
from app.refresh import seed_seasons
from app.team_review import review_team


def _flag(p):
    if p["status"] == "a":
        return ""
    chance = f"{p['chance']}%" if p["chance"] is not None else "?"
    return f"  [{p['status'].upper()} {chance}] {p['news']}"


def print_report(r: dict) -> None:
    gws = r["horizon_gws"]
    print(f"{r['entry_name']} ({r['manager_name']}) - team {r['entry_id']}")
    print(
        f"GW{r['synced_event']} done: {r['last_event_points']} pts | overall "
        f"{r['overall_points']} pts, rank {r['overall_rank']:,} | "
        f"bank £{r['bank']:.1f}m, "
        f"value £{r['squad_value']:.1f}m"
    )
    est = " (estimated)" if r["free_transfers_estimated"] else ""
    print(
        f"Next: GW{r['next_event']} deadline {r['deadline']} | free transfers "
        f"{r['free_transfers']}{est} | chips used: "
        f"{', '.join(r['chips_used']) or 'none'}"
    )
    print(f"Projections: {r['source']}, horizon GW{gws[0]}-{gws[-1]}\n")

    hdr = (
        f"{'':2}{'Player':<16}{'Pos':<4}{'Team':<5}{'£':>5}{'Sell':>5}{'Pts':>5}"
        f"{'Min':>5}{'Form':>5}{'FPL':>5}{'xP1':>5}{'xP' + str(len(gws)):>6}{'xMin':>5}"
    )
    print("SQUAD (current order)")
    print(hdr)
    for p in r["squad"]:
        badge = "C" if p["is_captain"] else "V" if p["is_vice_captain"] else ""
        bench = "-" if p["squad_slot"] > 11 else " "
        print(
            f"{badge:<1}{bench}{p['name']:<16}{p['position']:<4}{p['team']:<5}"
            f"{p['price']:>5.1f}{p['selling_price']:>5.1f}{p['season_points']:>5}"
            f"{p['season_minutes']:>5}{p['form']:>5.1f}{p['ep_next']:>5.1f}"
            f"{p['xp_next']:>5.1f}{p['xp_horizon']:>6.1f}{p['xmins_avg']:>5.0f}{_flag(p)}"
        )
    print(
        "  (FPL = official ep_next; xP1 = model next GW; "
        "xMin = avg projected minutes)\n"
    )

    lu = r["lineup"]
    print(
        f"SUGGESTED XI ({lu['formation']}, {lu['xp_next']:.1f} xP "
        f"for GW{r['next_event']})"
    )
    for p in lu["starting"]:
        tag = (
            " (C)" if p is lu["captain"] else " (V)" if p is lu["vice_captain"] else ""
        )
        print(f"  {p['position']:<4}{p['name']:<16}{p['xp_next']:>5.1f}{tag}")
    print(
        "  Bench: " + ", ".join(f"{p['name']} {p['xp_next']:.1f}" for p in lu["bench"])
    )
    print()

    print(f"TRANSFER PLANS (gain = xP over GW{gws[0]}-{gws[-1]}, net of hits)")
    plans = r["transfer_plans"]
    if not plans["singles"]:
        print(
            "  No affordable transfer improves on the current squad over this horizon."
        )
    for label, key in (("One transfer", "singles"), ("Two transfers", "doubles")):
        if not plans[key]:
            continue
        print(f"  {label}:")
        for i, plan in enumerate(plans[key], 1):
            moves = "; ".join(
                f"{m['out']['name']} ({m['out']['xp_horizon']:.1f}) -> "
                f"{m['in']['name']} {m['in']['team']} £{m['in']['price']:.1f} "
                f"({m['in']['xp_horizon']:.1f})"
                for m in plan["moves"]
            )
            hit = f", -{plan['hit']} hit" if plan["hit"] else ""
            print(
                f"    {i}. {moves}  => +{plan['net_gain']:.1f}{hit}, "
                f"bank £{plan['bank_after']:.1f}m"
            )


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("season_id", help="e.g. 2026-27 (must be the live season)")
    ap.add_argument("entry_id", type=int, help="your FPL team id (from your team URL)")
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--source", default="fpl_api")
    ap.add_argument("--free-transfers", type=int, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    init_db()
    conn = get_connection()
    seed_seasons(conn)
    try:
        r = review_team(
            conn,
            args.season_id,
            args.entry_id,
            args.source,
            args.horizon,
            args.free_transfers,
        )
    except ValueError as e:
        sys.exit(f"review failed: {e}")
    finally:
        conn.close()

    if args.json:
        print(json.dumps(r, indent=2, default=str))
    else:
        print_report(r)


if __name__ == "__main__":
    main()
