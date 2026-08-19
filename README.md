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
