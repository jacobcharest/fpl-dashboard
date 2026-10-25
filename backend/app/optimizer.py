"""Multi-week transfer planner: a mixed-integer program over the projection horizon.

Given a squad, its purchase prices and the bank, plus per-gameweek projections, the solver picks
for every week of the horizon which players to hold, which to buy and sell, the starting XI, the
captain and (optionally) which week to play a chip, maximising discounted projected points.

Decisions it understands, and the rules behind them:

- **Transfers.** Each week starts with a number of free transfers; unused ones roll over, capped
  at `ft_cap` (5 under the 2024/25+ rules). Every transfer beyond the free ones costs `hit_cost`.
- **Carry value.** A banked transfer is worth something in itself: next week's projections will
  have moved, and a spare move covers an injury without a hit. `ft_values` gives the marginal
  value of the 2nd, 3rd, ... banked transfer, so a move must beat *rolling* by that margin rather
  than by any fraction of a point. Set it to zeros for the plain "maximise projections" solve.
- **Discount.** Week w is weighted `discount ** w`, both for how much later projections drift
  and because the plan is re-solved every week, so later weeks matter mainly through this week's
  move.
- **Bench.** Bench players count at `bench_weight` of their projection (an auto-sub chance),
  fully in a Bench Boost week.
- **Chips.** Bench Boost and Triple Captain can be pinned to a week or left to the solver;
  Wildcard makes a week's transfers free and keeps the bank of free transfers intact. Free Hit is
  not modelled (its squad reverts, which needs a second squad track).
- **Upside.** An optional per-player-week "points above a haul" term, weighted by
  `upside_weight`, so the plan can favour ceilings as well as averages (see
  scripts/simulate_upside.py).
- **Budget.** Selling an existing player raises FPL's selling price (half of any rise, see
  prices.selling_price); a player bought inside the horizon is assumed to sell for what he cost.

The pool is trimmed to the squad plus the best projected players per position, which keeps the
model small enough for CBC/HiGHS to solve in seconds.
"""

from dataclasses import dataclass, field

import pulp

from app.prices import selling_price

POSITIONS = ("GK", "DEF", "MID", "FWD")
SQUAD_SIZE = {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}
LINEUP_MIN = {"GK": 1, "DEF": 3, "MID": 2, "FWD": 1}
LINEUP_MAX = {"GK": 1, "DEF": 5, "MID": 5, "FWD": 3}
MAX_PER_TEAM = 3
AUTO = "auto"  # chip setting: let the solver choose the week


@dataclass
class Player:
    code: int
    name: str
    team: str
    pos: str
    price: int  # tenths of a million, as the FPL API stores it
    xp: dict[int, float] = field(default_factory=dict)  # gameweek -> projected points
    upside: dict[int, float] = field(default_factory=dict)  # gameweek -> points above a haul


@dataclass
class Squad:
    codes: list[int]
    purchase: dict[int, int]  # code -> price paid, tenths; missing = current price
    bank: int  # tenths
    free_transfers: int = 1


@dataclass
class Settings:
    gameweeks: list[int]
    discount: float = 0.9
    bench_weight: float = 0.1
    hit_cost: float = 4.0
    ft_cap: int = 5
    # Marginal value of holding a 2nd, 3rd, 4th, 5th free transfer into next week.
    ft_values: tuple[float, ...] = (2.0, 1.25, 0.75, 0.25)
    upside_weight: float = 0.0
    bench_boost: int | str | None = None  # gameweek, AUTO, or None (not in window)
    triple_captain: int | str | None = None
    wildcard: int | None = None
    max_transfers: dict[int, int] = field(default_factory=dict)  # gameweek -> cap
    force_in: dict[int, list[int]] = field(default_factory=dict)  # gameweek -> codes held from then on
    force_out: dict[int, list[int]] = field(default_factory=dict)  # gameweek -> codes gone from then on
    banned: list[int] = field(default_factory=list)
    pool_per_position: int = 40
    time_limit: int = 120  # seconds


@dataclass
class WeekPlan:
    gameweek: int
    transfers_in: list[Player]
    transfers_out: list[Player]
    hits: int
    free_transfers_after: int
    lineup: list[Player]
    bench: list[Player]
    captain: Player
    chip: str | None
    bank: int
    projected: float  # expected points this week, chips and hits applied


@dataclass
class Plan:
    weeks: list[WeekPlan]
    objective: float
    status: str


def build_pool(players: list[Player], squad: Squad, settings: Settings) -> list[Player]:
    """The squad, plus the top `pool_per_position` by horizon projection in each position."""
    held = set(squad.codes)
    out = [p for p in players if p.code in held]
    for pos in POSITIONS:
        ranked = sorted(
            (p for p in players if p.pos == pos and p.code not in held and p.code not in settings.banned),
            key=lambda p: -sum(p.xp.get(gw, 0.0) for gw in settings.gameweeks),
        )
        out.extend(ranked[: settings.pool_per_position])
    forced = {c for codes in settings.force_in.values() for c in codes}
    have = {p.code for p in out}
    out.extend(p for p in players if p.code in forced and p.code not in have)
    return out


def _vars(name: str, keys, **kw) -> dict:
    """{key: LpVariable} over a key list, without LpVariable.dicts (dropped in PuLP 4)."""
    return {k: pulp.LpVariable(f"{name}_{k}", **kw) for k in keys}


def _vars2(name: str, outer, inner, **kw) -> dict:
    return {a: {b: pulp.LpVariable(f"{name}_{a}_{b}", **kw) for b in inner} for a in outer}


def _chip_weeks(setting, gameweeks: list[int]) -> list[int]:
    if setting == AUTO:
        return list(gameweeks)
    return [setting] if setting in gameweeks else []


def solve(players: list[Player], squad: Squad, settings: Settings) -> Plan:
    """Solve the plan.

    Parameters
    ----------
    players : list[Player]
        Every player with projections, at current prices.
    squad : Squad
        The squad going into the first gameweek of the horizon.
    settings : Settings
        Horizon and objective weights.

    Returns
    -------
    Plan
        Per-week transfers, XI, captain and chip, plus the objective value.
    """
    gws = settings.gameweeks
    pool = build_pool(players, squad, settings)
    by_code = {p.code: p for p in pool}
    missing = [c for c in squad.codes if c not in by_code]
    if missing:
        raise ValueError(f"No projections for squad players {missing}")
    codes = list(by_code)
    held = set(squad.codes)

    def sell_value(p: Player) -> int:
        return selling_price(squad.purchase[p.code], p.price) if p.code in squad.purchase else p.price

    m = pulp.LpProblem("transfer_plan", pulp.LpMaximize)
    B = pulp.LpBinary
    sq = _vars2("squad", codes, gws, cat=B)
    st = _vars2("start", codes, gws, cat=B)
    cap = _vars2("captain", codes, gws, cat=B)
    buy = _vars2("buy", codes, gws, cat=B)
    sell = _vars2("sell", codes, gws, cat=B)
    hits = _vars("hits", gws, lowBound=0, cat=pulp.LpInteger)
    # Free transfers available at the start of each week, and after the week's transfers.
    ft = _vars("ft", gws, lowBound=1, upBound=settings.ft_cap, cat=pulp.LpInteger)
    ft_after = _vars("ft_after", gws, lowBound=0, cat=pulp.LpInteger)
    ft_next = _vars("ft_next", gws, lowBound=1, upBound=settings.ft_cap, cat=pulp.LpInteger)
    bank = _vars("bank", gws, lowBound=0)
    # Carry value: z[k][w] fills the k-th slot of next week's bank beyond the first transfer.
    # Values decrease with k, so the LP fills them in order without needing binaries.
    slots = list(range(len(settings.ft_values)))
    z = _vars2("carry", slots, gws, lowBound=0, upBound=1)

    bb_weeks = _chip_weeks(settings.bench_boost, gws)
    tc_weeks = _chip_weeks(settings.triple_captain, gws)
    bb = _vars("bboost", gws, cat=B)
    tc = _vars("tcap", gws, cat=B)
    bb_pts = _vars2("bb_pts", codes, gws, lowBound=0, upBound=1)
    tc_pts = _vars2("tc_pts", codes, gws, lowBound=0, upBound=1)
    for w in gws:
        if w not in bb_weeks:
            m += bb[w] == 0
        if w not in tc_weeks:
            m += tc[w] == 0
    if settings.bench_boost == AUTO:
        m += pulp.lpSum(bb[w] for w in gws) <= 1
    elif bb_weeks:
        m += bb[bb_weeks[0]] == 1
    if settings.triple_captain == AUTO:
        m += pulp.lpSum(tc[w] for w in gws) <= 1
    elif tc_weeks:
        m += tc[tc_weeks[0]] == 1

    objective = []
    prev_sq = {c: (1 if c in held else 0) for c in codes}
    prev_bank = squad.bank
    for i, w in enumerate(gws):
        weight = settings.discount**i
        # Squad evolution.
        for c in codes:
            m += sq[c][w] == prev_sq[c] + buy[c][w] - sell[c][w]
            m += buy[c][w] + sell[c][w] <= 1
            m += sell[c][w] <= prev_sq[c]
            m += buy[c][w] <= 1 - prev_sq[c]
            m += st[c][w] <= sq[c][w]
            m += cap[c][w] <= st[c][w]
            m += bb_pts[c][w] <= bb[w]
            m += bb_pts[c][w] <= sq[c][w] - st[c][w]
            m += tc_pts[c][w] <= tc[w]
            m += tc_pts[c][w] <= cap[c][w]
        for c in settings.banned:
            if c in by_code:
                m += sq[c][w] == 0
        for gw, forced in settings.force_in.items():
            if w >= gw:
                for c in forced:
                    m += sq[c][w] == 1
        for gw, forced in settings.force_out.items():
            if w >= gw:
                for c in forced:
                    if c in by_code:
                        m += sq[c][w] == 0
        # Squad shape.
        m += pulp.lpSum(sq[c][w] for c in codes) == 15
        m += pulp.lpSum(st[c][w] for c in codes) == 11
        m += pulp.lpSum(cap[c][w] for c in codes) == 1
        for pos in POSITIONS:
            in_pos = [c for c in codes if by_code[c].pos == pos]
            m += pulp.lpSum(sq[c][w] for c in in_pos) == SQUAD_SIZE[pos]
            m += pulp.lpSum(st[c][w] for c in in_pos) >= LINEUP_MIN[pos]
            m += pulp.lpSum(st[c][w] for c in in_pos) <= LINEUP_MAX[pos]
        for team in {by_code[c].team for c in codes}:
            m += pulp.lpSum(sq[c][w] for c in codes if by_code[c].team == team) <= MAX_PER_TEAM
        # Transfers, hits and the rolling bank of free transfers.
        n_transfers = pulp.lpSum(buy[c][w] for c in codes)
        if w in settings.max_transfers:
            m += n_transfers <= settings.max_transfers[w]
        if i == 0:
            m += ft[w] == min(max(squad.free_transfers, 1), settings.ft_cap)
        else:
            m += ft[w] == ft_next[gws[i - 1]]
        if w == settings.wildcard:
            m += hits[w] == 0
            m += ft_after[w] == ft[w]
        else:
            m += hits[w] >= n_transfers - ft[w]
            m += ft_after[w] == ft[w] - n_transfers + hits[w]
        m += ft_next[w] <= ft_after[w] + 1
        m += pulp.lpSum(z[k][w] for k in slots) <= ft_next[w] - 1
        # Money.
        m += bank[w] == prev_bank + pulp.lpSum(
            sell_value(by_code[c]) * sell[c][w] - by_code[c].price * buy[c][w] for c in codes
        )
        # Points.
        week_pts = []
        for c in codes:
            p = by_code[c]
            xp = p.xp.get(w, 0.0)
            up = settings.upside_weight * p.upside.get(w, 0.0)
            week_pts.append(
                (xp + up) * st[c][w]
                + (xp + up) * cap[c][w]
                + xp * tc_pts[c][w]
                + settings.bench_weight * xp * (sq[c][w] - st[c][w])
                + (1 - settings.bench_weight) * xp * bb_pts[c][w]
            )
        objective.append(
            weight * (pulp.lpSum(week_pts) - settings.hit_cost * hits[w]
                      + pulp.lpSum(settings.ft_values[k] * z[k][w] for k in slots))
        )
        prev_sq = {c: sq[c][w] for c in codes}
        prev_bank = bank[w]

    m += pulp.lpSum(objective)
    solver = _solver(settings.time_limit)
    m.solve(solver)
    status = pulp.LpStatus[m.status]
    if status not in ("Optimal", "Not Solved"):
        raise RuntimeError(f"Solver finished with status {status}")

    def on(var) -> bool:
        return var.value() is not None and var.value() > 0.5

    weeks = []
    for w in gws:
        lineup = [by_code[c] for c in codes if on(st[c][w])]
        bench = [by_code[c] for c in codes if on(sq[c][w]) and not on(st[c][w])]
        captain = next(by_code[c] for c in codes if on(cap[c][w]))
        chip = "wildcard" if w == settings.wildcard else "bboost" if on(bb[w]) else "3xc" if on(tc[w]) else None
        n_hits = int(round(hits[w].value() or 0))
        pts = sum(p.xp.get(w, 0.0) for p in lineup) + captain.xp.get(w, 0.0)
        if chip == "3xc":
            pts += captain.xp.get(w, 0.0)
        if chip == "bboost":
            pts += sum(p.xp.get(w, 0.0) for p in bench)
        weeks.append(
            WeekPlan(
                gameweek=w,
                transfers_in=[by_code[c] for c in codes if on(buy[c][w])],
                transfers_out=[by_code[c] for c in codes if on(sell[c][w])],
                hits=n_hits,
                free_transfers_after=int(round(ft_next[w].value())),
                lineup=sorted(lineup, key=lambda p: (POSITIONS.index(p.pos), -p.xp.get(w, 0.0))),
                bench=sorted(bench, key=lambda p: (p.pos != "GK", -p.xp.get(w, 0.0))),
                captain=captain,
                chip=chip,
                bank=int(round(bank[w].value())),
                projected=pts - settings.hit_cost * n_hits,
            )
        )
    return Plan(weeks=weeks, objective=pulp.value(m.objective), status=status)


def _solver(time_limit: int):
    """CBC ships with PuLP's wheels; HiGHS is the fallback when it doesn't (e.g. some ARM builds)."""
    cbc = pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit)
    if cbc.available():
        return cbc
    highs = pulp.HiGHS(msg=False, timeLimit=time_limit)
    if highs.available():
        return highs
    raise RuntimeError("No MIP solver found: reinstall PuLP (bundles CBC) or pip install highspy")
