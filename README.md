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

```bash
# One season:
backend/.venv/bin/python backend/scripts/backfill_history.py 2025-26

# Every known season (2016/17 - 2025/26):
backend/.venv/bin/python backend/scripts/backfill_history.py --all
```

## Run

```bash
# Backend (from backend/)
.venv/bin/uvicorn app.main:app --reload --port 8000

# Frontend (from frontend/)
npm run dev
```
