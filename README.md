# FPL Dashboard

Local dashboard for visualizing Fantasy Premier League player and team data. See
[DESIGN.md](DESIGN.md) for the full design and current build status.

## Setup

```bash
# Backend
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Frontend
cd ../frontend
npm install
```

## Load data

First-time setup needs at least one season backfilled:

```bash
# One season:
backend/.venv/bin/python backend/scripts/backfill_history.py 2025-26

# Every known season (2016/17 - 2025/26):
backend/.venv/bin/python backend/scripts/backfill_history.py --all
```

After that, use the **"Fetch New Data"** button in the app (next to the season dropdown) to
pull the latest gameweek results for whichever season is selected — that's the weekly refresh,
no need to re-run the script by hand. It hits `POST /api/refresh/{season_id}`, which is safe to
call repeatedly (it re-ingests the season from scratch rather than trying to append).

The season in progress is read straight from the live FPL API (`backend/app/live_refresh.py`)
rather than the community archive, which can lag it by several gameweeks early in a season.
Past seasons still come from the archive. Loading the previous season as well is worth doing:
the built-in projection model uses it as a prior for players with only a few gameweeks of form.

### Not-yet-started seasons

A season that hasn't kicked off yet won't exist in the source archive, but FPL usually reveals
prices ahead of time. To populate one as a placeholder (previous season's results + current
season's prices, clearly flagged in the UI), run:

```bash
backend/.venv/bin/python backend/scripts/create_placeholder_season.py 2025-26 2026-27
```

No action needed once the season actually starts — the normal "Fetch New Data" button
automatically replaces the placeholder with real data and clears the flag the first time it
succeeds.

## Highlight your own team

Enter your FPL team id in the **"My team id"** box in the toolbar and press **"Sync My Team"** to
mark your 15 players on the player table (captain and vice-captain get a `C`/`V` badge; bench
players are dimmed). Your team id is the number in your team's URL, e.g.
`fantasy.premierleague.com/entry/1234567/event/1`.

This only ever reads FPL's public API - it never asks for, stores, or sends your FPL login. That
has one consequence worth knowing: **a squad only becomes readable once its gameweek kicks off.**
Syncing before then saves and validates your team id and tells you when picks unlock; press the
same button again after kickoff to pull the squad. Re-sync whenever you make transfers.

## Projections

The dashboard can show forward-looking expected points beside the historical stats. Import a
projections CSV and the player table gains **xP** and **xMins** columns, summed over a gameweek
horizon you choose in the sidebar:

```bash
backend/.venv/bin/python backend/scripts/import_projections.py 2026-27 ~/Downloads/fplreview.csv
```

Pass `--source <name>` to hold more than one model at once (they appear as separate options in
the sidebar's Projections dropdown, so you can compare them).

The parser sniffs the layout rather than requiring a fixed one - long format (a `gw` column plus
`xp`) and wide format (`1_Pts`, `2_Pts`, ... or `gw1`, `gw2`, ...) both work, with optional
`1_xMins`-style columns. Players are matched by `code` when the file has one, otherwise by name
plus team against the live FPL API. **Anything it can't match is reported, never silently
dropped** - a name that's ambiguous across two players (there are several every season) is listed
rather than guessed at.

[FPL Review](https://fplreview.com/) premium members can import their CSV download directly. On
the free tier the download is disabled, so `backend/scripts/fplreview_export.js` builds the same
file from the page - see the instructions at the top of that file.

Projections are stored per gameweek, so the sidebar horizon re-sums them without re-importing.
With no projections loaded the columns are absent entirely rather than showing empty cells.

### Built-in projections (no subscription)

If you have no external model to import, generate one from the live FPL API instead:

```bash
backend/.venv/bin/python backend/scripts/generate_projections.py 2026-27 --horizon 8
```

It appears in the sidebar as source `fpl_api` (also `POST /api/projections/{season}/generate`).
The model is deliberately simple and explainable - points per gameweek this season, shrunk
toward last season, scaled by fixture difficulty and the player's availability - and it is
documented in `backend/app/fpl_projections.py`. It's a floor, not a substitute for a real model:
import FPL Review or similar when you can, and compare the two from the dropdown.

## Review your team

With this season's stats and a projection source loaded, get a suggested XI, captain, and the
best transfers for the coming gameweeks:

```bash
backend/.venv/bin/python backend/scripts/review_team.py 2026-27 1234567
```

Pass `--horizon` to change how many gameweeks transfers are judged over (default 5),
`--source` to plan against an imported model, `--free-transfers` if the simulated count is off,
and `--json` for machine-readable output. The same report is served at
`GET /api/my-team/{season}/review?entry_id=...`. Selling prices and free transfers aren't public,
so both are reconstructed from your transfer history - see `backend/app/team_review.py`.

## Run

```bash
./run.sh
```

Starts both servers (backend on :8000, frontend on :5173) and stops both on Ctrl+C. Or run them
separately:

```bash
# Backend (from backend/)
.venv/bin/uvicorn app.main:app --reload --port 8000

# Frontend (from frontend/)
npm run dev
```
