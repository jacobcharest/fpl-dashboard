#!/usr/bin/env python3
"""Multi-week FPL transfer plan from FPL's own projections plus a fixture model.

Two sources feed the per-gameweek expected points (xP) used here, blended 50/50:

1. **FPL's official projection** (`ep_next` / `form` in the bootstrap). It is a
   30-day points average with a light fixture adjustment - honest about who is
   actually scoring, blind to who is about to play Arsenal away.
2. **A fixture-adjusted event model**: xG/xA/defensive-contribution/save rates per 90,
   scaled by expected minutes and by the opponent's FPL difficulty rating for every
   fixture in the gameweek (so blanks score zero and doubles count twice).

Availability (`status`, `chance_of_playing_next_round`) gates both. Nothing here is
as good as a dedicated model like FPL Review's; if a projections CSV is available,
import it with ``import_projections.py`` instead and use the dashboard. This script
is the fallback for when it isn't.

Usage
-----
Generic targets only (no squad)::

    backend/.venv/bin/python backend/scripts/transfer_plan.py

Plan for your own squad (team id is the number in your FPL URL)::

    backend/.venv/bin/python backend/scripts/transfer_plan.py --entry 1234567
    backend/.venv/bin/python backend/scripts/transfer_plan.py --entry 1234567 \
        --free-transfers 2 --weeks 4 --out plan.md

With FPL Review projections (see ``fplreview_fetch.js``) instead of the model::

    backend/.venv/bin/python backend/scripts/transfer_plan.py --entry 1234567 \
        --projections fplreview.csv

Picks are read from the public API, which only serves them once a gameweek has
kicked off (see README "Highlight your own team"), so the squad used is the one
from the most recent started gameweek plus nothing else - make the plan before
you make the transfers.
"""

import argparse
import csv
import itertools
import json
import re
import sys
import urllib.request
from collections import defaultdict
from urllib.error import HTTPError

GW_PTS_RE = re.compile(r"^(\d{1,2})_pts$", re.I)
GW_MINS_RE = re.compile(r"^(\d{1,2})_xmins$", re.I)

API_BASE = "https://fantasy.premierleague.com/api"

POS_NAMES = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
SQUAD_QUOTA = {1: 2, 2: 5, 3: 5, 4: 3}
MAX_PER_CLUB = 3
HIT_COST = 4
MAX_FREE_TRANSFERS = 5
# A transfer has to beat rolling it by this much xP; rolling keeps options open.
MIN_GAIN = 1.5
# Weight on FPL's own projection vs the fixture model.
FPL_WEIGHT = 0.5
# Discount applied per gameweek of lookahead when scoring a plan.
DISCOUNT = 0.9
# Early-season form is a handful of games; shrink it toward the positional average
# for regular starters with this many pseudo-games of prior weight.
FORM_PRIOR_GAMES = 3
# Opponent difficulty (FPL FDR, 1-5) -> attacking multiplier, clean-sheet odds,
# expected goals conceded.
FDR_ATTACK = {1: 1.30, 2: 1.20, 3: 1.00, 4: 0.85, 5: 0.70}
FDR_CLEAN_SHEET = {1: 0.50, 2: 0.45, 3: 0.33, 4: 0.25, 5: 0.17}
FDR_GOALS_CONCEDED = {1: 0.70, 2: 0.90, 3: 1.20, 4: 1.50, 5: 1.80}
HOME_ATTACK_BONUS = 0.05
HOME_CLEAN_SHEET_BONUS = 0.03
DC_THRESHOLD = {2: 10, 3: 12, 4: 12}


def fetchJson(url):
    """GET a JSON document from the FPL API.

    Parameters
    ----------
    url : str
        Absolute URL.

    Returns
    -------
    object
        Decoded JSON.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def gameweekWindow(bootstrap, weeks):
    """The next `weeks` gameweek ids starting from the first unfinished one.

    Parameters
    ----------
    bootstrap : dict
        ``bootstrap-static`` payload.
    weeks : int
        Horizon length.

    Returns
    -------
    list of int
    """
    upcoming = [e["id"] for e in bootstrap["events"] if not e["finished"]]
    return upcoming[:weeks]


def fixturesByTeam(fixtures, gws):
    """Map (team_id, gw) -> list of (opponent_fdr, is_home).

    Parameters
    ----------
    fixtures : list of dict
        ``fixtures/`` payload.
    gws : list of int
        Gameweeks of interest.

    Returns
    -------
    dict
    """
    wanted = set(gws)
    out = defaultdict(list)
    for f in fixtures:
        if f["event"] not in wanted:
            continue
        out[(f["team_h"], f["event"])].append((f["team_h_difficulty"], True))
        out[(f["team_a"], f["event"])].append((f["team_a_difficulty"], False))
    return out


def playChance(player):
    """Probability the player is available to play next round.

    Parameters
    ----------
    player : dict
        Bootstrap element.

    Returns
    -------
    float
    """
    chance = player.get("chance_of_playing_next_round")
    if chance is not None:
        return chance / 100.0
    return 1.0 if player["status"] == "a" else 0.0


def modelFixturePoints(player, scoring, gws_elapsed, fdr, is_home):
    """Expected points for one fixture from the event-rate model.

    Parameters
    ----------
    player : dict
        Bootstrap element.
    scoring : dict
        ``game_config.scoring`` from the bootstrap.
    gws_elapsed : int
        Gameweeks already played this season (for minutes-per-week).
    fdr : int
        Opponent difficulty rating, 1-5.
    is_home : bool

    Returns
    -------
    float
    """
    pos = POS_NAMES[player["element_type"]]
    minutes = player["minutes"]
    mins_est = minutes / max(gws_elapsed, 1)
    if mins_est <= 0:
        return 0.0
    m = min(mins_est / 90.0, 1.0)
    attack_mult = FDR_ATTACK[fdr] + (HOME_ATTACK_BONUS if is_home else 0.0)
    cs_prob = FDR_CLEAN_SHEET[fdr] + (HOME_CLEAN_SHEET_BONUS if is_home else 0.0)
    goals_conceded = FDR_GOALS_CONCEDED[fdr]

    appearance = 1.0 + min(mins_est / 60.0, 1.0)
    attack = (
        float(player["expected_goals_per_90"]) * scoring["goals_scored"][pos]
        + float(player["expected_assists_per_90"]) * scoring["assists"]
    ) * attack_mult
    clean_sheet = scoring["clean_sheets"][pos] * cs_prob * min(mins_est / 60.0, 1.0)
    conceded = scoring["goals_conceded"][pos] * goals_conceded / 2.0
    saves = 0.0
    if pos == "GKP":
        saves = float(player["saves_per_90"]) * scoring["saves"] * goals_conceded / 1.2
    dc = 0.0
    if player["element_type"] in DC_THRESHOLD:
        rate = float(player["defensive_contribution_per_90"])
        threshold = DC_THRESHOLD[player["element_type"]]
        p_hit = min(max((rate - (threshold - 3)) / 6.0, 0), 0.9)
        dc = scoring["defensive_contribution"][pos] * p_hit
    bonus = player["bonus"] / minutes * 90.0 if minutes else 0.0
    cards = -0.1 if pos != "GKP" else -0.03

    events = attack + clean_sheet + conceded + saves + dc + bonus + cards
    return appearance + m * events


def positionPriors(bootstrap, gws_elapsed):
    """Mean points per game of regular starters, per position.

    Parameters
    ----------
    bootstrap : dict
    gws_elapsed : int

    Returns
    -------
    dict
        element_type -> float
    """
    priors = {}
    for pos_id in POS_NAMES:
        ppg = [
            float(p["points_per_game"])
            for p in bootstrap["elements"]
            if p["element_type"] == pos_id
            and p["minutes"] >= 60 * max(gws_elapsed, 1)
        ]
        priors[pos_id] = sum(ppg) / len(ppg) if ppg else 2.0
    return priors


def buildProjections(bootstrap, fixtures, gws):
    """Per-player, per-gameweek expected points and minutes.

    Parameters
    ----------
    bootstrap : dict
    fixtures : list of dict
    gws : list of int

    Returns
    -------
    dict
        element_id -> {"xp": {gw: float}, "xmins": float, "p_play": float}
    """
    scoring = bootstrap["game_config"]["scoring"]
    gws_elapsed = sum(1 for e in bootstrap["events"] if e["finished"])
    team_fixtures = fixturesByTeam(fixtures, gws)
    prior = positionPriors(bootstrap, gws_elapsed)
    out = {}
    for p in bootstrap["elements"]:
        p_play = playChance(p)
        mins_est = p["minutes"] / max(gws_elapsed, 1)
        n = gws_elapsed
        k = FORM_PRIOR_GAMES
        form = (n * float(p["form"] or 0.0) + k * prior[p["element_type"]]) / (n + k)
        ep_next = float(p["ep_next"] or 0.0)
        ep_next = (n * ep_next + k * prior[p["element_type"]]) / (n + k)
        xp = {}
        for i, gw in enumerate(gws):
            model = 0.0
            fpl = 0.0
            for fdr, is_home in team_fixtures.get((p["team"], gw), []):
                model += modelFixturePoints(p, scoring, gws_elapsed, fdr, is_home)
                # FPL's number already carries its own (mild) fixture view for the
                # next round only; beyond that, tilt the form average by fixture.
                tilt = 0.7 + 0.3 * FDR_ATTACK[fdr]
                fpl += ep_next if i == 0 else form * tilt
            blended = FPL_WEIGHT * fpl + (1.0 - FPL_WEIGHT) * model
            xp[gw] = max(p_play * blended, 0.0)
        out[p["id"]] = {"xp": xp, "xmins": p_play * mins_est, "p_play": p_play}
    return out


def csvHasGameweek(path, gw):
    """Whether a wide projections CSV carries a ``<gw>_Pts`` column."""
    with open(path, newline="", encoding="utf-8") as fh:
        header = next(csv.reader(fh))
    return any(
        (m := GW_PTS_RE.match(c.strip())) and int(m.group(1)) == gw for c in header
    )


def loadProjectionsCsv(path, bootstrap, gws):
    """Per-player projections from a wide CSV (FPL Review export layout).

    Columns ``<gw>_Pts`` and optional ``<gw>_xMins``; players matched by ``id``
    (FPL element id), then ``code``, then ``name`` + ``team``. Players absent from
    the file project to zero, so a stale file quietly benches everyone new -
    check the reported match count.

    Parameters
    ----------
    path : str
    bootstrap : dict
    gws : list of int

    Returns
    -------
    dict
        Same shape as ``buildProjections``.
    """
    teams = {t["id"]: t["short_name"].lower() for t in bootstrap["teams"]}
    by_id = {p["id"]: p for p in bootstrap["elements"]}
    by_code = {p["code"]: p for p in bootstrap["elements"]}
    by_name = {}
    for p in bootstrap["elements"]:
        by_name[(p["web_name"].lower(), teams[p["team"]])] = p

    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        sys.exit(f"{path} is empty.")
    pts_cols, mins_cols = {}, {}
    for col in rows[0]:
        m = GW_PTS_RE.match(col.strip())
        if m:
            pts_cols[int(m.group(1))] = col
        m = GW_MINS_RE.match(col.strip())
        if m:
            mins_cols[int(m.group(1))] = col
    missing = [g for g in gws if g not in pts_cols]
    if missing:
        sys.exit(
            f"{path} has no projections for GW{missing} (has GW"
            f"{sorted(pts_cols)}); shorten --weeks."
        )

    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    out = {
        pid: {"xp": {g: 0.0 for g in gws}, "xmins": 0.0, "p_play": 0.0}
        for pid in by_id
    }
    matched = 0
    for row in rows:
        player = None
        if row.get("id"):
            player = by_id.get(int(num(row["id"])))
        if player is None and row.get("code"):
            player = by_code.get(int(num(row["code"])))
        if player is None:
            key = (row.get("name", "").strip().lower(), row.get("team", "").lower())
            player = by_name.get(key)
        if player is None:
            continue
        matched += 1
        xp = {g: max(num(row[pts_cols[g]]), 0.0) for g in gws}
        mins = [num(row[mins_cols[g]]) for g in gws if g in mins_cols]
        xmins = sum(mins) / len(mins) if mins else (90.0 if any(xp.values()) else 0)
        out[player["id"]] = {"xp": xp, "xmins": xmins, "p_play": 1.0}
    print(
        f"[projections] {matched}/{len(rows)} rows matched from {path}",
        file=sys.stderr,
    )
    return out


def bestXi(squad_ids, players, proj, gw):
    """Optimal starting XI expected points for a gameweek.

    Parameters
    ----------
    squad_ids : iterable of int
    players : dict
        element_id -> bootstrap element.
    proj : dict
        Output of ``buildProjections``.
    gw : int

    Returns
    -------
    tuple
        (total_xp, xi_ids, captain_id)
    """
    by_pos = defaultdict(list)
    for pid in squad_ids:
        by_pos[players[pid]["element_type"]].append((proj[pid]["xp"][gw], pid))
    for lst in by_pos.values():
        lst.sort(reverse=True)
    best = (-1.0, [], None)
    for n_def, n_mid, n_fwd in itertools.product((3, 4, 5), (2, 3, 4, 5), (1, 2, 3)):
        if n_def + n_mid + n_fwd != 10:
            continue
        picks = by_pos[1][:1] + by_pos[2][:n_def]
        picks += by_pos[3][:n_mid] + by_pos[4][:n_fwd]
        if len(picks) != 11:
            continue
        total = sum(x for x, _ in picks)
        captain = max(picks[1:])  # nobody captains a keeper
        total += captain[0]  # captain doubles
        if total > best[0]:
            best = (total, [pid for _, pid in picks], captain[1])
    return best


def horizonScore(squad_ids, players, proj, gws):
    """Discounted best-XI expected points over a list of gameweeks.

    Parameters
    ----------
    squad_ids : iterable of int
    players : dict
    proj : dict
    gws : list of int

    Returns
    -------
    float
    """
    return sum(
        DISCOUNT**i * bestXi(squad_ids, players, proj, gw)[0]
        for i, gw in enumerate(gws)
    )


def sellPrice(now_cost, purchase_cost):
    """FPL selling price: purchase price plus half of any rise, rounded down.

    Parameters
    ----------
    now_cost : int
        Current price in tenths.
    purchase_cost : int
        Price paid in tenths.

    Returns
    -------
    int
    """
    if now_cost <= purchase_cost:
        return now_cost
    return purchase_cost + (now_cost - purchase_cost) // 2


def loadSquad(entry_id, bootstrap, free_transfers_override=None):
    """Current squad, bank, selling prices and an estimate of free transfers.

    Parameters
    ----------
    entry_id : int
    bootstrap : dict
    free_transfers_override : int or None

    Returns
    -------
    dict
        {"ids": [...], "bank": int, "sell": {id: int}, "free_transfers": int,
         "gw": int, "name": str}
    """
    started = [e["id"] for e in bootstrap["events"] if e["is_current"] or e["finished"]]
    if not started:
        sys.exit("Season hasn't kicked off - picks aren't public yet.")
    gw = max(started)
    try:
        entry = fetchJson(f"{API_BASE}/entry/{entry_id}/")
        picks = fetchJson(f"{API_BASE}/entry/{entry_id}/event/{gw}/picks/")
        history = fetchJson(f"{API_BASE}/entry/{entry_id}/history/")
        transfers = fetchJson(f"{API_BASE}/entry/{entry_id}/transfers/")
    except HTTPError as e:
        sys.exit(f"Could not read team {entry_id} (HTTP {e.code}).")

    players = {p["id"]: p for p in bootstrap["elements"]}
    ids = [p["element"] for p in picks["picks"]]
    bank = picks["entry_history"]["bank"]

    purchase = {}
    for t in sorted(transfers, key=lambda t: t["time"]):
        purchase[t["element_in"]] = t["element_in_cost"]
    sell = {}
    for pid in ids:
        p = players[pid]
        paid = purchase.get(pid, p["now_cost"] - p["cost_change_start"])
        sell[pid] = sellPrice(p["now_cost"], paid)

    # Free transfers aren't public; replay the rules over the season's history.
    ft = 1
    for row in history["current"]:
        if row["event"] == 1:
            continue
        made = row["event_transfers"]
        ft = min(max(ft - made, 0) + 1, MAX_FREE_TRANSFERS)
    if free_transfers_override is not None:
        ft = free_transfers_override

    return {
        "ids": ids,
        "bank": bank,
        "sell": sell,
        "free_transfers": ft,
        "gw": gw,
        "name": entry.get("name", str(entry_id)),
        "captain": next(p["element"] for p in picks["picks"] if p["is_captain"]),
    }


def clubCounts(squad_ids, players):
    """Number of squad players per club."""
    counts = defaultdict(int)
    for pid in squad_ids:
        counts[players[pid]["team"]] += 1
    return counts


def candidateSwaps(squad_ids, bank, sell, players, proj, gws, top_n=12):
    """Rank single transfers by horizon gain.

    Parameters
    ----------
    squad_ids : list of int
    bank : int
    sell : dict
    players : dict
    proj : dict
    gws : list of int
    top_n : int
        Candidates to keep per outgoing player.

    Returns
    -------
    list of tuple
        (gain, out_id, in_id, new_bank), best first.
    """
    base = horizonScore(squad_ids, players, proj, gws)
    counts = clubCounts(squad_ids, players)
    squad_set = set(squad_ids)
    results = []
    for out_id in squad_ids:
        out_p = players[out_id]
        budget = bank + sell[out_id]
        per_out = []
        for in_id, in_p in players.items():
            if in_id in squad_set or in_p["element_type"] != out_p["element_type"]:
                continue
            if in_p["now_cost"] > budget or in_p["status"] in ("u", "n"):
                continue
            same_club = in_p["team"] == out_p["team"]
            if not same_club and counts[in_p["team"]] >= MAX_PER_CLUB:
                continue
            new_ids = [in_id if pid == out_id else pid for pid in squad_ids]
            gain = horizonScore(new_ids, players, proj, gws) - base
            per_out.append((gain, out_id, in_id, budget - in_p["now_cost"]))
        per_out.sort(reverse=True)
        results.extend(per_out[:top_n])
    results.sort(reverse=True)
    return results


def planWeek(squad_ids, bank, sell, free_transfers, players, proj, gws):
    """Choose the best 0/1/2-transfer move for the first gameweek in `gws`.

    Parameters
    ----------
    squad_ids : list of int
    bank : int
    sell : dict
    free_transfers : int
    players : dict
    proj : dict
    gws : list of int
        Remaining horizon; the move is made before ``gws[0]``.

    Returns
    -------
    dict
        {"moves": [(out_id, in_id)], "gain": float, "hit": int, "bank": int}
    """
    singles = candidateSwaps(squad_ids, bank, sell, players, proj, gws)
    base = horizonScore(squad_ids, players, proj, gws)
    best = {"moves": [], "gain": MIN_GAIN, "hit": 0, "bank": bank}

    def consider(moves, hit):
        new_ids = list(squad_ids)
        new_bank = bank
        for out_id, in_id in moves:
            new_ids = [in_id if pid == out_id else pid for pid in new_ids]
            new_bank += sell[out_id] - players[in_id]["now_cost"]
        if new_bank < 0:
            return
        counts = clubCounts(new_ids, players)
        if any(c > MAX_PER_CLUB for c in counts.values()):
            return
        gain = horizonScore(new_ids, players, proj, gws) - base - hit
        if gain > best["gain"] + 1e-9:
            best.update({"moves": moves, "gain": gain, "hit": hit, "bank": new_bank})

    for gain, out_id, in_id, _ in singles[:40]:
        consider([(out_id, in_id)], 0 if free_transfers >= 1 else HIT_COST)
    pool = singles[:25]
    for a, b in itertools.combinations(pool, 2):
        if a[1] == b[1] or a[2] == b[2]:
            continue
        hit = max(0, 2 - free_transfers) * HIT_COST
        consider([(a[1], a[2]), (b[1], b[2])], hit)
    if not best["moves"]:
        best["gain"] = 0.0
    return best


def applyMoves(squad_ids, sell, bank, moves, players):
    """Return (new_squad_ids, new_sell, new_bank) after transfers."""
    new_ids = list(squad_ids)
    new_sell = dict(sell)
    for out_id, in_id in moves:
        new_ids = [in_id if pid == out_id else pid for pid in new_ids]
        bank += new_sell.pop(out_id) - players[in_id]["now_cost"]
        new_sell[in_id] = players[in_id]["now_cost"]
    return new_ids, new_sell, bank


def label(p, teams):
    """'Saka (ARS, MID, 9.5)'."""
    return (
        f"{p['web_name']} ({teams[p['team']]}, {POS_NAMES[p['element_type']]}, "
        f"{p['now_cost'] / 10:.1f})"
    )


def fixtureText(team_id, gw, team_fixtures, teams):
    """'ARS (H)' / 'LIV (A), MCI (H)' / 'BLANK'."""
    parts = []
    for opp, is_home in team_fixtures.get((team_id, gw), []):
        parts.append(f"{teams[opp]} ({'H' if is_home else 'A'})")
    return ", ".join(parts) if parts else "BLANK"


def opponentsByTeam(fixtures, gws):
    """(team_id, gw) -> [(opponent_id, is_home)]."""
    out = defaultdict(list)
    for f in fixtures:
        if f["event"] in gws:
            out[(f["team_h"], f["event"])].append((f["team_a"], True))
            out[(f["team_a"], f["event"])].append((f["team_h"], False))
    return out


def renderTargets(bootstrap, fixtures, proj, gws, lines, per_pos=10):
    """Append the top projected players per position to `lines`."""
    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    opp = opponentsByTeam(fixtures, gws)
    lines.append(f"## Top projected players, GW{gws[0]}-GW{gws[-1]}\n")
    for pos_id, pos in POS_NAMES.items():
        rows = [
            p
            for p in bootstrap["elements"]
            if p["element_type"] == pos_id and proj[p["id"]]["xmins"] >= 45
        ]
        rows.sort(key=lambda p: -sum(proj[p["id"]]["xp"][g] for g in gws))
        gw_cols = " | ".join(f"GW{g}" for g in gws)
        head = f"| Player | Price | {gw_cols} | Total | Fixtures |"
        lines.append(f"### {pos}\n")
        lines.append(head)
        lines.append("|" + "---|" * (len(gws) + 4))
        for p in rows[:per_pos]:
            xp = proj[p["id"]]["xp"]
            fx = "; ".join(fixtureText(p["team"], g, opp, teams) for g in gws)
            cells = " | ".join(f"{xp[g]:.1f}" for g in gws)
            total = sum(xp[g] for g in gws)
            lines.append(
                f"| {p['web_name']} ({teams[p['team']]}) | {p['now_cost'] / 10:.1f} | "
                f"{cells} | {total:.1f} | {fx} |"
            )
        lines.append("")


def renderPlan(squad, bootstrap, fixtures, proj, gws, lines, eval_gws=None):
    """Append a week-by-week transfer plan for `squad` to `lines`.

    Parameters
    ----------
    squad : dict
        Output of ``loadSquad``.
    bootstrap, fixtures, proj : see ``buildProjections``.
    gws : list of int
        Gameweeks to plan moves for.
    lines : list of str
    eval_gws : list of int, optional
        Longer window the moves are scored over, so the final planned week
        doesn't sell players for a one-week bump. Defaults to ``gws``.
    """
    eval_gws = eval_gws or gws
    players = {p["id"]: p for p in bootstrap["elements"]}
    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    opp = opponentsByTeam(fixtures, gws)
    ids, sell, bank = squad["ids"], squad["sell"], squad["bank"]
    ft = squad["free_transfers"]

    lines.append(
        f"## Transfer plan for {squad['name']} (squad as of GW{squad['gw']})\n"
    )
    lines.append(
        f"Bank {bank / 10:.1f}m, estimated free transfers {ft} "
        f"(override with --free-transfers if wrong).\n"
    )
    lines.append("### Current squad\n")
    gw_cols = " | ".join(f"GW{g}" for g in gws)
    lines.append(f"| Player | Sell | {gw_cols} | Total |")
    lines.append("|" + "---|" * (len(gws) + 3))
    order = lambda i: (players[i]["element_type"], -sum(proj[i]["xp"][g] for g in gws))
    for pid in sorted(ids, key=order):
        p = players[pid]
        xp = proj[pid]["xp"]
        cells = " | ".join(f"{xp[g]:.1f}" for g in gws)
        flag = "" if p["status"] == "a" else f" [{p['status']}: {p['news'] or 'doubt'}]"
        lines.append(
            f"| {label(p, teams)}{flag} | {sell[pid] / 10:.1f} | {cells} | "
            f"{sum(xp[g] for g in gws):.1f} |"
        )
    lines.append("")

    for i, gw in enumerate(gws):
        horizon = eval_gws[i:]
        move = planWeek(ids, bank, sell, ft, players, proj, horizon)
        lines.append(f"### GW{gw}\n")
        if move["moves"]:
            for out_id, in_id in move["moves"]:
                lines.append(
                    f"- OUT {label(players[out_id], teams)} -> IN "
                    f"{label(players[in_id], teams)} "
                    f"({fixtureText(players[in_id]['team'], gw, opp, teams)})"
                )
            hit = f", -{move['hit']} hit" if move["hit"] else ""
            lines.append(
                f"- Gain over GW{horizon[0]}-GW{horizon[-1]}: +{move['gain']:.1f} "
                f"xP{hit}. Bank after: {move['bank'] / 10:.1f}m."
            )
            ids, sell, bank = applyMoves(ids, sell, bank, move["moves"], players)
            ft = min(max(ft - len(move["moves"]), 0) + 1, MAX_FREE_TRANSFERS)
        else:
            lines.append("- Roll the transfer (no move clears the bar).")
            ft = min(ft + 1, MAX_FREE_TRANSFERS)
        total, xi, cap = bestXi(ids, players, proj, gw)
        xi_order = lambda i: (players[i]["element_type"], -proj[i]["xp"][gw])
        xi_names = ", ".join(
            players[pid]["web_name"] + (" (C)" if pid == cap else "")
            for pid in sorted(xi, key=xi_order)
        )
        bench = [pid for pid in ids if pid not in xi]
        bench_names = ", ".join(players[pid]["web_name"] for pid in bench)
        lines.append(f"- XI ({total:.1f} xP incl. captain): {xi_names}")
        lines.append(f"- Bench: {bench_names}")
        lines.append("")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--entry", type=int, help="FPL team id for a squad plan")
    parser.add_argument("--weeks", type=int, default=4, help="horizon in gameweeks")
    parser.add_argument(
        "--lookahead",
        type=int,
        default=2,
        help="extra gameweeks the moves are scored over beyond --weeks",
    )
    parser.add_argument("--free-transfers", type=int, help="override FT estimate")
    parser.add_argument("--out", help="write the markdown report here as well")
    parser.add_argument("--targets", type=int, default=10, help="rows per position")
    parser.add_argument(
        "--projections",
        help="wide projections CSV (e.g. from fplreview_fetch.js) instead of the "
        "built-in FPL-API model",
    )
    args = parser.parse_args()

    bootstrap = fetchJson(f"{API_BASE}/bootstrap-static/")
    fixtures = fetchJson(f"{API_BASE}/fixtures/")
    gws = gameweekWindow(bootstrap, args.weeks)
    eval_gws = gameweekWindow(bootstrap, args.weeks + args.lookahead)
    if args.projections:
        eval_gws = eval_gws[: len(gws)] + [
            g for g in eval_gws[len(gws):] if csvHasGameweek(args.projections, g)
        ]
        proj = loadProjectionsCsv(args.projections, bootstrap, eval_gws)
        source = f"projections CSV {args.projections}"
    else:
        proj = buildProjections(bootstrap, fixtures, eval_gws)
        source = (
            "FPL API ep_next/form blended 50/50 with a fixture-adjusted "
            "xG/xA/DC model; see script docstring"
        )

    lines = [f"# FPL transfer plan, GW{gws[0]}-GW{gws[-1]}\n"]
    events = {e["id"]: e for e in bootstrap["events"]}
    deadline = events[gws[0]]["deadline_time"]
    lines.append(f"GW{gws[0]} deadline: {deadline}. Source: {source}.\n")
    if args.entry:
        squad = loadSquad(args.entry, bootstrap, args.free_transfers)
        renderPlan(squad, bootstrap, fixtures, proj, gws, lines, eval_gws)
    renderTargets(bootstrap, fixtures, proj, gws, lines, args.targets)

    report = "\n".join(lines)
    print(report)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(report + "\n")


if __name__ == "__main__":
    main()
