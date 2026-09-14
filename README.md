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

The season currently being played is read straight from the official FPL API (one request per
player, under a minute in total), so it is up to date the moment a gameweek's results are in.
Finished seasons come from the [vaastav](https://github.com/vaastav/Fantasy-Premier-League)
community archive, the only place their per-gameweek history still lives.

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
projections CSV and the player table gains **xP** and **xMins** columns, summed over the
gameweeks you tick in the Projections panel:

```bash
backend/.venv/bin/python backend/scripts/import_projections.py 2026-27 ~/Downloads/fplreview.csv
```

Pass `--source <name>` to hold more than one model at once. The dashboard shows the first source
loaded for the season; the others stay in the database for comparison via the API.

The parser sniffs the layout rather than requiring a fixed one - long format (a `gw` column plus
`xp`) and wide format (`1_Pts`, `2_Pts`, ... or `gw1`, `gw2`, ...) both work, with optional
`1_xMins`-style columns. Players are matched by `code` when the file has one, otherwise by name
plus team against the live FPL API. **Anything it can't match is reported, never silently
dropped** - a name that's ambiguous across two players (there are several every season) is listed
rather than guessed at.

[FPL Review](https://fplreview.com/) premium members can import their CSV download directly. On
the free tier the download is disabled, so `backend/scripts/fplreview_export.js` builds the same
file from the page - see the instructions at the top of that file.

Projections are stored per gameweek, so changing the window re-sums them without re-importing.
With no projections loaded the columns are absent entirely rather than showing empty cells.

A **Projections** panel below the player board totals each player's projections over a
gameweek window - **xP**, **xG** and **xA** summed, **xMins** averaged, and **xCS** / **xDC**
(per-match clean-sheet and defensive-contribution probabilities) summed so they read as expected
counts over the window - sortable and filterable like the main tables. Beside the price sit two
rates for comparing players across window lengths: **xP/GW** (xP over the gameweeks the source
projected inside the window) and **xP/£/GW** (that rate per £m of current price). Which of these a source
fills depends on the file: FPL Review's export carries all of them, a points-only file just xP.
Its controls sit in a bar above it: a **Weeks** row with one tick box per gameweek the source
covers (which also drives the board's xP column, so the two never disagree; any set works, so you
can skip a blank or a bad fixture) and a **Position** filter independent of the board's own. The
next six unplayed gameweeks start ticked and **All** / **None** reset the row; already-played
weeks are dimmed. When the imported file doesn't cover every ticked week the panel says so and
totals only the gameweeks it has.

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

## Or start nothing, and just open the bookmark

`systemd/` holds a socket unit and a service, the same shape as the fantasy-football draft
dashboard. systemd owns `:8767` and starts the backend on the first connection, so the page works
from cold with nothing running. The backend serves the built frontend same-origin, so that one
port is the whole app:

```bash
(cd frontend && npm run build)
ln -s "$PWD"/systemd/fpl-dashboard.{socket,service} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now fpl-dashboard.socket
```

Then, from any device on the tailnet:

- <http://fantasy-laptop:8767/> (MagicDNS), or
- <http://100.72.210.79:8767/>

(Not from this laptop itself: its tailscaled runs in userspace-networking mode, so it cannot dial
its own Tailscale address or resolve MagicDNS names. Use <http://localhost:8767/> here.)

It stops itself after `FPL_IDLE_TIMEOUT` seconds (900 in the unit) with no HTTP request and
respawns on the next visit. Nothing is lost: all state is in `data/fpl.db`.

`:8767` for the bookmark, `:8000`/`:5173` for `./run.sh`, deliberately separate, so a running
dev session and the always-on instance never fight over a port. `./run.sh` is unchanged: Vite
proxies `/api` to `:8000` (see `frontend/vite.config.ts`), which is why the API client uses
relative URLs.

**After changing the frontend**, rebuild so the systemd instance picks it up (the backend picks up
its own changes on the next idle respawn, or `systemctl --user restart fpl-dashboard.service`):

```bash
(cd frontend && npm run build)
```
