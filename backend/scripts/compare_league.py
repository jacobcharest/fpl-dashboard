#!/usr/bin/env python3
"""Compare every squad in a mini-league for one gameweek: projections, ceilings and the odds.

    backend/.venv/bin/python backend/scripts/compare_league.py --entry 3935569
    ... --csv data/fplreview-latest.csv   # projections (default: the last fetched file)
    ... --league 830675                   # default: the smallest classic league you're in
    ... --gw 10                           # default: the gameweek in play
    ... --draws 20000 --json out.json

Reads the league's standings and every entry's picks from the public FPL API (readable once the
gameweek has kicked off), scores each squad against an FPL Review export, and simulates the
gameweek jointly with scripts/simulate_upside.py so that teammates, opponents and shared picks
are correlated the way they are in real life. Per entry it reports the projected points of the
side as picked (chip and captain included), the simulated 90th and 95th percentiles shifted onto
that projection (the simulator's bonus formula runs a little hot), the chance of outscoring you
this week net of hits, the chance of sitting above you on season total afterwards, and the
players that separate the two of you. Captaincy falls to the vice when the captain doesn't
appear, and the bench comes on for non-appearing starters in bench order where the formation
allows, as FPL does. Bench Boost counts all fifteen, Triple Captain trebles.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.leagues import _original_lineup
from app.my_team import API_BASE, _get_json, fetch_bootstrap, fetch_entry, fetch_picks
from app.seasons import POSITION_BY_ELEMENT_TYPE
from simulate_upside import PlayerWeek, read_player_weeks, simulate_gameweek

MIN_BY_POS = {"GK": 1, "DEF": 3, "MID": 2, "FWD": 1}
MAX_BY_POS = {"GK": 1, "DEF": 5, "MID": 5, "FWD": 3}


def current_event(bootstrap: dict) -> int | None:
    current = [e["id"] for e in bootstrap["events"] if e.get("is_current")]
    return current[0] if current else None


def smallest_league(entry: dict) -> dict:
    private = [l for l in entry["leagues"]["classic"] if l.get("rank_count")]
    if not private:
        raise SystemExit("This entry is in no classic league with a size; pass --league.")
    return min(private, key=lambda l: l["rank_count"])


def as_picked(payload: dict) -> list[dict]:
    """The squad as the manager set it: a finished gameweek's payload has FPL's automatic
    substitutions and vice-captain swap baked in, which the simulation applies itself."""
    picks = _original_lineup(payload)
    for p in picks:
        p["multiplier"] = (2 if p["is_captain"] else 1) if p["position"] <= 11 else 0
    return picks


def squad_scores(picks: list[dict], chip: str | None, scores: dict[int, np.ndarray],
                 pos_of: dict[int, str], draws: int) -> np.ndarray:
    """The squad's simulated gameweek total per draw, with FPL's captaincy and bench rules.

    A player appeared in a draw iff his simulated score is positive (the simulator has no
    cards, so an appearance is worth at least a point). The vice doubles when the captain
    didn't appear; bench players come on, in bench order, for starters who didn't appear when
    the resulting formation is legal. Bench Boost counts everyone and skips the substitutions.
    """
    picks = sorted(picks, key=lambda p: p["position"])
    mat = np.stack([scores[p["element"]] for p in picks])  # 15 x draws
    played = mat > 0
    cap_mult = 3 if chip == "3xc" else 2
    captain = next(i for i, p in enumerate(picks) if p["is_captain"])
    vice = next(i for i, p in enumerate(picks) if p["is_vice_captain"])
    mult = np.ones((15, draws))
    mult[captain] = np.where(played[captain], cap_mult, 1)
    mult[vice] = np.where(~played[captain] & played[vice], cap_mult, 1)
    if chip == "bboost":
        return (mat * mult).sum(axis=0)

    on = np.zeros((15, draws), dtype=bool)
    on[:11] = True
    positions = [pos_of[p["element"]] for p in picks]
    needs_sub = np.nonzero(~played[:11].all(axis=0))[0]
    for d in needs_sub:
        lineup = [i for i in range(11) if played[i, d]]
        count = {pos: sum(positions[i] == pos for i in lineup) for pos in MIN_BY_POS}
        for i in range(11, 15):
            if len(lineup) == 11 or not played[i, d]:
                continue
            pos = positions[i]
            if pos == "GK":
                if count["GK"] == 0:
                    lineup.append(i)
                    count["GK"] = 1
                continue
            missing = [p for p, n in count.items() if p != "GK" and n < MIN_BY_POS[p]]
            if (missing and pos not in missing) or count[pos] >= MAX_BY_POS[pos]:
                continue
            lineup.append(i)
            count[pos] += 1
        on[:, d] = False
        on[lineup, d] = True
    return (mat * mult * on).sum(axis=0)


def projected(picks: list[dict], chip: str | None, xp: dict[int, float]) -> tuple[float, float]:
    """FPL Review's points for the side as picked (xi, bench); no fallbacks or substitutions."""
    xi = sum(xp[p["element"]] * (3 if chip == "3xc" and p["is_captain"] else p["multiplier"])
             for p in picks if p["multiplier"] > 0)
    bench = sum(xp[p["element"]] for p in picks if p["multiplier"] == 0)
    if chip == "bboost":
        return xi + bench, 0.0
    return xi, bench


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--entry", type=int, required=True, help="your FPL team id")
    ap.add_argument("--league", type=int, help="classic league id (default: your smallest)")
    ap.add_argument("--gw", type=int, help="gameweek (default: the one in play)")
    ap.add_argument("--csv", type=Path,
                    default=Path(__file__).resolve().parent.parent / "data/fplreview-latest.csv",
                    help="FPL Review projections export")
    ap.add_argument("--draws", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--haul", type=float, default=10, help="points that count as a haul")
    ap.add_argument("--independent", action="store_true",
                    help="the old model: no match-level correlation between players")
    ap.add_argument("--json", type=Path, help="also write the full result here")
    args = ap.parse_args()

    bootstrap = fetch_bootstrap()
    gw = args.gw or current_event(bootstrap)
    if gw is None:
        raise SystemExit("No gameweek is in play; pass --gw.")
    entry = fetch_entry(args.entry)
    league_id = args.league or smallest_league(entry)["id"]
    standings = _get_json(f"{API_BASE}/leagues-classic/{league_id}/standings/")
    members = standings["standings"]["results"]
    print(f"{standings['league']['name']} (league {league_id}), GW{gw}, projections from "
          f"{args.csv.name}", file=sys.stderr)

    weeks = read_player_weeks(args.csv)
    if gw not in weeks:
        raise SystemExit(f"{args.csv} has no GW{gw} projections.")
    by_code = {p.code: p for p in weeks[gw]}
    elements = {e["id"]: e for e in bootstrap["elements"]}
    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}

    entries = []
    for m in members:
        payload = fetch_picks(m["entry"], gw)
        if payload is None:
            raise SystemExit(f"GW{gw} picks for {m['player_name']} aren't public yet; the "
                             f"gameweek has to kick off first.")
        history = _get_json(f"{API_BASE}/entry/{m['entry']}/history/")
        by_event = {h["event"]: h for h in history["current"]}
        before = by_event[gw - 1]["total_points"] if gw - 1 in by_event else 0
        actual = by_event[gw]["points"] if gw in by_event and by_event[gw]["points"] else None
        entries.append(dict(
            entry=m["entry"], rank=m["rank"], manager=m["player_name"],
            team_name=m["entry_name"], total_before=before, actual=actual,
            chip=payload["active_chip"], hit=payload["entry_history"]["event_transfers_cost"],
            transfers=payload["entry_history"]["event_transfers"],
            chips_used=[f"{c['name']}@{c['event']}" for c in history["chips"]],
            picks=as_picked(payload),
        ))
    entries.sort(key=lambda e: -e["total_before"])
    for rank, e in enumerate(entries, 1):
        e["rank_before"] = rank

    # Simulate the whole gameweek once, jointly, then read each squad off the same draws.
    rng = np.random.default_rng(args.seed)
    owned = {p["element"] for e in entries for p in e["picks"]}
    players = list(weeks[gw])
    for element in owned:
        el = elements[element]
        if el["code"] not in by_code:  # not in the export: treat as not playing
            players.append(PlayerWeek(el["code"], teams[el["team"]],
                                      POSITION_BY_ELEMENT_TYPE[el["element_type"]],
                                      "", 0, 0, 0, 0, 0))
            by_code[el["code"]] = players[-1]
    sim_by_code = simulate_gameweek(players, args.draws, rng, independent=args.independent)
    sim = {element: sim_by_code[elements[element]["code"]] for element in owned}
    xp_by_code = {}
    for p in weeks[gw]:
        xp_by_code[p.code] = p.xp
    xp = {element: xp_by_code.get(elements[element]["code"], 0.0) for element in owned}
    pos_of = {element: POSITION_BY_ELEMENT_TYPE[elements[element]["element_type"]]
              for element in owned}

    me = next((e for e in entries if e["entry"] == args.entry), None)
    if me is None:
        raise SystemExit(f"Entry {args.entry} isn't in league {league_id}.")
    for e in entries:
        e["draws"] = squad_scores(e["picks"], e["chip"], sim, pos_of, args.draws)
        e["xp"], e["bench_xp"] = projected(e["picks"], e["chip"], xp)
        e["sim_mean"] = float(e["draws"].mean())
        shift = e["xp"] - e["sim_mean"]
        for q in (10, 25, 75, 90, 95):
            e[f"p{q}"] = float(np.percentile(e["draws"], q) + shift)
    for e in entries:
        mine = me["draws"] - me["hit"]
        theirs = e["draws"] - e["hit"]
        e["p_beat_you"] = float((theirs > mine).mean())
        e["p_ahead_after"] = float(
            ((e["total_before"] + theirs) > (me["total_before"] + mine)).mean())
    totals = np.stack([e["total_before"] + e["draws"] - e["hit"] for e in entries])
    my_rank = 1 + (totals > totals[entries.index(me)]).sum(axis=0)
    rank_dist = {k: float((my_rank == k).mean()) for k in range(1, len(entries) + 1)}

    def effective(e):
        return {p["element"]: xp[p["element"]]
                * (3 if e["chip"] == "3xc" and p["is_captain"] else p["multiplier"])
                for p in e["picks"] if p["multiplier"] > 0 or e["chip"] == "bboost"}

    mine_eff = effective(me)
    played = any(e["actual"] is not None for e in entries)
    print(f"\n| # | Manager | Before | Chip | Hit | xP | 90th | 95th | Bench xP | "
          f"P(beats you) | P(ahead after) |{' Actual |' if played else ''}")
    print("|---|---|---|---|---|---|---|---|---|---|---|" + ("---|" if played else ""))
    for e in entries:
        you = e is me
        actual = f" {e['actual']} |" if played else ""
        print(f"| {e['rank_before']} | {'**' if you else ''}{e['manager']}"
              f"{'**' if you else ''} | {e['total_before']} | {e['chip'] or '–'} | "
              f"{e['hit'] or 0} | {e['xp']:.1f} | {e['p90']:.0f} | {e['p95']:.0f} | "
              f"{e['bench_xp']:.1f} | {'–' if you else f'{e['p_beat_you']:.0%}'} | "
              f"{'–' if you else f'{e['p_ahead_after']:.0%}'} |{actual}")
    print("\nYour finishing rank after GW%d: " % gw
          + ", ".join(f"{k}: {v:.0%}" for k, v in rank_dist.items() if v >= 0.005))

    for e in entries:
        if e is me:
            continue
        theirs_eff = effective(e)
        shared = [k for k in mine_eff if k in theirs_eff and mine_eff[k] == theirs_eff[k]]
        mine_only = {k: v for k, v in mine_eff.items() if k not in shared}
        theirs_only = {k: v for k, v in theirs_eff.items() if k not in shared}
        name = lambda k: elements[k]["web_name"]
        e["shared"] = [name(k) for k in shared]
        e["your_edge"] = {name(k): round(v, 2) for k, v in
                          sorted(mine_only.items(), key=lambda kv: -kv[1])}
        e["their_edge"] = {name(k): round(v, 2) for k, v in
                           sorted(theirs_only.items(), key=lambda kv: -kv[1])}
        net = sum(mine_only.values()) - sum(theirs_only.values())
        gap = e["total_before"] - me["total_before"]
        print(f"\n**{e['manager']}** ({e['team_name']}, {gap:+d} on you, chip "
              f"{e['chip'] or 'none'}, hit {e['hit']}): net you {net:+.1f} xP.")
        print(f"  Shared: {', '.join(e['shared']) or 'none'}")
        print("  Your edge: " + ", ".join(f"{n} {v:.1f}" for n, v in e["your_edge"].items()))
        print("  Their edge: " + ", ".join(f"{n} {v:.1f}" for n, v in e["their_edge"].items()))

    print("\n### Squads\n")
    for e in entries:
        print(f"**{e['manager']}** ({e['team_name']}) chip={e['chip']} "
              f"transfers={e['transfers']} hit={e['hit']} chips used={e['chips_used']}")
        for p in sorted(e["picks"], key=lambda p: p["position"]):
            el = elements[p["element"]]
            pw = by_code[el["code"]]
            s = sim[p["element"]]
            tag = "C" if p["is_captain"] else ("V" if p["is_vice_captain"] else " ")
            print(f"  {'XI' if p['position'] <= 11 else 'BN'} {tag} {pw.pos:<3} "
                  f"{el['web_name']:<18} {pw.team:<4} {pw.opp or '-':<7} "
                  f"xP {xp[p['element']]:>5.2f}  p95 {np.percentile(s, 95):>4.0f}  "
                  f"P{args.haul:g}+ {(s >= args.haul).mean():.2f}")
        print()

    if args.json:
        for e in entries:
            e.pop("draws")
            e.pop("picks")
        args.json.write_text(json.dumps(dict(
            league=league_id, league_name=standings["league"]["name"], gw=gw,
            projections=str(args.csv), entries=entries, your_rank_distribution=rank_dist,
        ), indent=1))
        print(f"Wrote {args.json}", file=sys.stderr)


if __name__ == "__main__":
    main()
