#!/usr/bin/env python3
"""Joint multi-week transfer optimisation (mixed-integer programme).

``transfer_plan.py`` decides each week greedily with a lookahead. This solves
every week's transfers, lineup and captain *together* over the whole projection
horizon, so it will happily bank transfers now to make a two-move swing later, or
take a hit this week because it pays back over the next five.

Decision variables per (player, gameweek): in squad, in lineup, captain,
transferred in, transferred out. Per gameweek: free transfers carried, hits
taken, bank. Constraints are the FPL rules: 2/5/5/3 squad, 11 starters in a legal
formation, one captain, max three per club, non-negative bank using real selling
prices for the players you already own, free transfers rolling up to five with
four-point hits beyond them.

Objective: sum over gameweeks of ``decay**k`` times (starting xP + captain xP +
``bench_weight`` times bench xP - 4 times hits), plus ``ft_value`` per free
transfer still banked at the end so the horizon edge doesn't burn them for
nothing. Chips are not modelled.

Usage
-----
::

    node backend/scripts/fplreview_fetch.js <team id> fplreview.csv
    backend/.venv/bin/python backend/scripts/transfer_solver.py \
        --entry <team id> --projections fplreview.csv

Needs ``pulp`` (``pip install pulp``); it ships with the CBC solver.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pulp  # noqa: E402

from transfer_plan import (  # noqa: E402
    API_BASE,
    HIT_COST,
    MAX_FREE_TRANSFERS,
    MAX_PER_CLUB,
    POS_NAMES,
    SQUAD_QUOTA,
    buildProjections,
    csvHasGameweek,
    fetchJson,
    fixtureText,
    gameweekWindow,
    label,
    loadProjectionsCsv,
    loadSquad,
    opponentsByTeam,
)

FORMATION = {1: (1, 1), 2: (3, 5), 3: (2, 5), 4: (1, 3)}


def candidatePool(players, proj, squad_ids, gws, per_position, per_week):
    """Players worth putting in front of the solver.

    Parameters
    ----------
    players : dict
        element_id -> bootstrap element.
    proj : dict
        Output of ``loadProjectionsCsv`` / ``buildProjections``.
    squad_ids : list of int
    gws : list of int
    per_position : int
        Top-N by horizon xP kept per position.
    per_week : int
        Top-N per position per gameweek kept as well (fixture-swing picks).

    Returns
    -------
    list of int
    """
    pool = set(squad_ids)
    for pos_id in POS_NAMES:
        rows = [
            pid
            for pid, p in players.items()
            if p["element_type"] == pos_id and p["status"] not in ("u", "n")
        ]
        rows.sort(key=lambda pid: -sum(proj[pid]["xp"][g] for g in gws))
        pool.update(rows[:per_position])
        for g in gws:
            rows.sort(key=lambda pid: -proj[pid]["xp"][g])
            pool.update(rows[:per_week])
    return sorted(pool)


def solve(squad, players, proj, gws, opts):
    """Build and solve the MIP.

    Parameters
    ----------
    squad : dict
        Output of ``loadSquad``.
    players : dict
    proj : dict
    gws : list of int
    opts : argparse.Namespace
        decay, bench_weight, ft_value, max_hits, time_limit, pool, pool_week.

    Returns
    -------
    dict
        Per-gameweek decisions plus objective bookkeeping.
    """
    pool = candidatePool(players, proj, squad["ids"], gws, opts.pool, opts.pool_week)
    initial = set(squad["ids"])
    sale_value = {
        pid: squad["sell"][pid] if pid in initial else players[pid]["now_cost"]
        for pid in pool
    }
    buy_value = {pid: players[pid]["now_cost"] for pid in pool}
    clubs = sorted({players[pid]["team"] for pid in pool})

    m = pulp.LpProblem("fpl_transfers", pulp.LpMaximize)
    sq = pulp.LpVariable.dicts("squad", (pool, gws), cat="Binary")
    xi = pulp.LpVariable.dicts("lineup", (pool, gws), cat="Binary")
    cap = pulp.LpVariable.dicts("captain", (pool, gws), cat="Binary")
    tin = pulp.LpVariable.dicts("in", (pool, gws), cat="Binary")
    tout = pulp.LpVariable.dicts("out", (pool, gws), cat="Binary")
    ft = pulp.LpVariable.dicts("ft", gws + ["end"], 0, MAX_FREE_TRANSFERS, "Integer")
    hits = pulp.LpVariable.dicts("hits", gws, 0, opts.max_hits, "Integer")
    bank = pulp.LpVariable.dicts("bank", gws, 0)

    prev_gw = None
    for k, gw in enumerate(gws):
        # Squad composition and club limit.
        for pos_id, quota in SQUAD_QUOTA.items():
            pos_pool = [p for p in pool if players[p]["element_type"] == pos_id]
            m += pulp.lpSum(sq[p][gw] for p in pos_pool) == quota
            lo, hi = FORMATION[pos_id]
            m += pulp.lpSum(xi[p][gw] for p in pos_pool) >= lo
            m += pulp.lpSum(xi[p][gw] for p in pos_pool) <= hi
        for club in clubs:
            m += (
                pulp.lpSum(sq[p][gw] for p in pool if players[p]["team"] == club)
                <= MAX_PER_CLUB
            )
        m += pulp.lpSum(xi[p][gw] for p in pool) == 11
        m += pulp.lpSum(cap[p][gw] for p in pool) == 1
        for p in pool:
            m += xi[p][gw] <= sq[p][gw]
            m += cap[p][gw] <= xi[p][gw]
            m += tin[p][gw] + tout[p][gw] <= 1
            prev = (1 if p in initial else 0) if prev_gw is None else sq[p][prev_gw]
            m += sq[p][gw] == prev + tin[p][gw] - tout[p][gw]

        # Transfers, free-transfer rollover, hits, money.
        n_in = pulp.lpSum(tin[p][gw] for p in pool)
        m += hits[gw] >= n_in - ft[gw]
        m += ft[gw] - n_in + hits[gw] >= 0
        nxt = gws[k + 1] if k + 1 < len(gws) else "end"
        m += ft[nxt] <= ft[gw] - n_in + hits[gw] + 1
        prev_bank = squad["bank"] if prev_gw is None else bank[prev_gw]
        m += bank[gw] == prev_bank + pulp.lpSum(
            sale_value[p] * tout[p][gw] - buy_value[p] * tin[p][gw] for p in pool
        )
        prev_gw = gw
    m += ft[gws[0]] == squad["free_transfers"]

    m += pulp.lpSum(
        opts.decay**k
        * (
            pulp.lpSum(
                proj[p]["xp"][gw]
                * (
                    (1 - opts.bench_weight) * xi[p][gw]
                    + cap[p][gw]
                    + opts.bench_weight * sq[p][gw]
                )
                for p in pool
            )
            - HIT_COST * hits[gw]
        )
        for k, gw in enumerate(gws)
    ) + opts.ft_value * ft["end"]

    started = time.time()
    m.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=opts.time_limit, gapRel=0.005))
    status = pulp.LpStatus[m.status]
    if status not in ("Optimal", "Not Solved"):
        sys.exit(f"Solver status: {status}")

    weeks = []
    for gw in gws:
        weeks.append(
            {
                "gw": gw,
                "in": [p for p in pool if tin[p][gw].value() > 0.5],
                "out": [p for p in pool if tout[p][gw].value() > 0.5],
                "squad": [p for p in pool if sq[p][gw].value() > 0.5],
                "xi": [p for p in pool if xi[p][gw].value() > 0.5],
                "captain": next(p for p in pool if cap[p][gw].value() > 0.5),
                "ft": int(round(ft[gw].value())),
                "hits": int(round(hits[gw].value())),
                "bank": int(round(bank[gw].value())),
            }
        )
    return {
        "weeks": weeks,
        "ft_end": int(round(ft["end"].value())),
        "objective": pulp.value(m.objective),
        "status": status,
        "seconds": time.time() - started,
        "pool": len(pool),
    }


def weekPoints(week, proj):
    """Raw (undiscounted) xP of the starting XI with captain doubled."""
    gw = week["gw"]
    return sum(proj[p]["xp"][gw] for p in week["xi"]) + proj[week["captain"]]["xp"][gw]


def noTransferPoints(squad_ids, players, proj, gws):
    """Best-XI xP per gameweek if the squad is never touched."""
    from transfer_plan import bestXi

    return [bestXi(squad_ids, players, proj, gw)[0] for gw in gws]


def render(result, squad, bootstrap, fixtures, proj, gws, opts):
    """Markdown report."""
    players = {p["id"]: p for p in bootstrap["elements"]}
    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    opp = opponentsByTeam(fixtures, gws)
    lines = [f"# Optimised transfer plan, GW{gws[0]}-GW{gws[-1]}\n"]
    lines.append(
        f"{squad['name']} (squad as of GW{squad['gw']}), bank "
        f"{squad['bank'] / 10:.1f}m, {squad['free_transfers']} free transfers. "
        f"Solver: {result['status']} in "
        f"{result['seconds']:.0f}s over {result['pool']} candidates; decay "
        f"{opts.decay}, bench weight {opts.bench_weight}, FT value {opts.ft_value}.\n"
    )
    baseline = noTransferPoints(squad["ids"], players, proj, gws)
    planned = [weekPoints(w, proj) for w in result["weeks"]]
    total_hits = sum(w["hits"] for w in result["weeks"])
    lines.append("| | " + " | ".join(f"GW{g}" for g in gws) + " | Total |")
    lines.append("|" + "---|" * (len(gws) + 2))
    lines.append(
        "| No transfers | "
        + " | ".join(f"{x:.1f}" for x in baseline)
        + f" | {sum(baseline):.1f} |"
    )
    lines.append(
        "| This plan | "
        + " | ".join(f"{x:.1f}" for x in planned)
        + f" | {sum(planned):.1f} |"
    )
    lines.append(
        f"\nNet gain {sum(planned) - sum(baseline) - HIT_COST * total_hits:+.1f} xP "
        f"after {total_hits} hit(s); {result['ft_end']} free transfer(s) left over.\n"
    )

    for w in result["weeks"]:
        gw = w["gw"]
        lines.append(f"## GW{gw}\n")
        if w["in"]:
            outs = sorted(w["out"], key=lambda p: players[p]["element_type"])
            ins = sorted(w["in"], key=lambda p: players[p]["element_type"])
            for o, i in zip(outs, ins):
                fixture = fixtureText(players[i]["team"], gw, opp, teams)
                lines.append(
                    f"- OUT {label(players[o], teams)} -> IN "
                    f"{label(players[i], teams)} ({fixture})"
                )
            hit = ""
            if w["hits"]:
                hit = f", {w['hits']} hit(s) for -{HIT_COST * w['hits']}"
            lines.append(
                f"- {len(w['in'])} transfer(s) with {w['ft']} free{hit}. "
                f"Bank after: {w['bank'] / 10:.1f}m."
            )
        else:
            lines.append(f"- Roll ({w['ft']} free transfers banked).")
        order = lambda p: (players[p]["element_type"], -proj[p]["xp"][gw])
        xi_names = ", ".join(
            players[p]["web_name"] + (" (C)" if p == w["captain"] else "")
            for p in sorted(w["xi"], key=order)
        )
        bench = sorted((p for p in w["squad"] if p not in w["xi"]), key=order)
        lines.append(f"- XI ({weekPoints(w, proj):.1f} xP): {xi_names}")
        lines.append(f"- Bench: {', '.join(players[p]['web_name'] for p in bench)}")
        lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--entry", type=int, required=True, help="FPL team id")
    parser.add_argument("--projections", help="wide projections CSV (FPL Review)")
    parser.add_argument("--weeks", type=int, help="horizon; default = all in the CSV")
    parser.add_argument("--free-transfers", type=int, help="override FT estimate")
    parser.add_argument("--decay", type=float, default=0.9, help="per-week weight")
    parser.add_argument("--bench-weight", type=float, default=0.1)
    parser.add_argument("--ft-value", type=float, default=1.5, help="xP per FT at end")
    parser.add_argument("--max-hits", type=int, default=1, help="max hits per week")
    parser.add_argument("--pool", type=int, default=30, help="candidates per position")
    parser.add_argument("--pool-week", type=int, default=6, help="per position per GW")
    parser.add_argument("--time-limit", type=int, default=180, help="solver seconds")
    parser.add_argument("--out", help="write the markdown report here as well")
    opts = parser.parse_args()

    bootstrap = fetchJson(f"{API_BASE}/bootstrap-static/")
    fixtures = fetchJson(f"{API_BASE}/fixtures/")
    if opts.projections:
        gws = [
            g
            for g in gameweekWindow(bootstrap, opts.weeks or 38)
            if csvHasGameweek(opts.projections, g)
        ]
        proj = loadProjectionsCsv(opts.projections, bootstrap, gws)
    else:
        gws = gameweekWindow(bootstrap, opts.weeks or 6)
        proj = buildProjections(bootstrap, fixtures, gws)
    players = {p["id"]: p for p in bootstrap["elements"]}
    squad = loadSquad(opts.entry, bootstrap, opts.free_transfers)

    result = solve(squad, players, proj, gws, opts)
    report = render(result, squad, bootstrap, fixtures, proj, gws, opts)
    print(report)
    if opts.out:
        with open(opts.out, "w") as fh:
            fh.write(report + "\n")


if __name__ == "__main__":
    main()
