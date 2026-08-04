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
