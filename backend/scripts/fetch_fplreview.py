#!/usr/bin/env python3
"""Fetch FPL Review's latest projections with a headless browser and import them.

    backend/.venv/bin/python backend/scripts/fetch_fplreview.py            # live season, synced team
    backend/.venv/bin/python backend/scripts/fetch_fplreview.py --no-import  # just write the CSV
    backend/.venv/bin/python backend/scripts/fetch_fplreview.py --if-due     # what the timer runs

FPL Review only ever shows upcoming gameweeks, so a gameweek's projections have to be captured
before its deadline or they're gone. systemd/fpl-projections.timer runs this with --if-due every
half hour; it does nothing except ~24h and ~2h before each deadline (see CHECKPOINTS).

This is what "Fetch New Data" runs for the live season (at most once every few hours); run it
by hand to force an update. The CSV lands in data/fplreview-latest.csv. See app/fplreview.py
for how it works and what it needs installed.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_connection, init_db
from app.fplreview import due_checkpoint, fetch_csv, last_import_time, refresh_projections
from app.my_team import fetch_bootstrap, live_season_id
from app.refresh import seed_seasons


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-import", action="store_true", help="write the CSV but don't import it")
    ap.add_argument("--if-due", action="store_true", help="only run at a pre-deadline checkpoint (for the timer)")
    args = ap.parse_args()

    init_db()
    conn = get_connection()
    seed_seasons(conn)
    bootstrap = fetch_bootstrap()
    season_id = live_season_id(bootstrap)

    if args.if_due:
        reason = due_checkpoint(bootstrap, last_import_time(conn, season_id))
        if reason is None:
            return  # silent: this is the usual outcome, 47 times out of 48
        print(f"Due: {reason}")

    if args.no_import:
        entry = conn.execute("SELECT entry_id FROM manager_entry WHERE season_id = ?", (season_id,)).fetchone()
        if entry is None:
            sys.exit('No synced team - press "Sync My Team" in the dashboard first.')
        print(f"Wrote {fetch_csv(entry['entry_id'])}")
        return

    result = refresh_projections(conn, season_id, force=True)
    conn.close()
    print(f"{season_id}: {result['message']}")
    if result.get("unmatched"):
        print(f"  unmatched: {', '.join(result['unmatched'])}")
    if result["status"] == "failed":
        sys.exit(1)


if __name__ == "__main__":
    main()
