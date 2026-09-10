"""CLI for one-time (or occasional) ingestion of season data.

Past seasons come from the vaastav archive; the season in progress comes from the live FPL API.

The reusable logic lives in app/refresh.py, shared with the "Fetch new data" button
(POST /api/refresh) - see that module's docstring for why re-running this doubles as the
weekly refresh.

Usage:
    backend/.venv/bin/python backend/scripts/backfill_history.py 2025-26
    backend/.venv/bin/python backend/scripts/backfill_history.py --all
"""

import argparse
import sys
from pathlib import Path
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_connection, init_db
from app.live_refresh import fetch_bootstrap, refresh_live_season
from app.my_team import live_season_id
from app.refresh import backfill_season, seed_seasons
from app.seasons import SEASONS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("season", nargs="?", help="Season id, e.g. 2025-26")
    parser.add_argument("--all", action="store_true", help="Backfill every known season")
    args = parser.parse_args()

    if not args.season and not args.all:
        parser.error("pass a season id (e.g. 2025-26) or --all")

    init_db()
    conn = get_connection()
    seed_seasons(conn)

    targets = [s["id"] for s in SEASONS] if args.all else [args.season]
    bootstrap = fetch_bootstrap()
    live_id = live_season_id(bootstrap)
    for season_id in targets:
        if season_id == live_id:
            # The archive lags the live API (by weeks, early in a season), so the season in
            # progress is read from the API itself - see app/live_refresh.py.
            print(f"[{season_id}] fetching and ingesting from the live FPL API ...")
            summary = refresh_live_season(conn, season_id, bootstrap)
            print(
                f"[{season_id}] done: {summary['teams']} teams, {summary['players']} players, "
                f"{summary['fixtures']} fixtures, {summary['gw_rows_inserted']} gw-stat rows "
                f"over GW{summary['gameweeks'][0]}-{summary['gameweeks'][-1]}"
                if summary["gameweeks"] else f"[{season_id}] done: season not started yet"
            )
            continue
        print(f"[{season_id}] fetching and ingesting ...")
        try:
            summary = backfill_season(conn, season_id)
        except HTTPError as e:
            if e.code == 404 and args.all:
                # A not-yet-started season can be listed in SEASONS ahead of time (e.g. as a
                # placeholder-data target - see create_placeholder_season.py) before the source
                # archive has a folder for it. --all should skip it, not abort the whole run.
                print(f"[{season_id}] skipped: not in the source archive yet (404)")
                continue
            raise
        print(
            f"[{season_id}] done: {summary['teams']} teams, {summary['players']} players, "
            f"{summary['fixtures']} fixtures, {summary['gw_rows_inserted']}/{summary['gw_rows_total']} "
            f"gw-stat rows (skipped {summary['gw_rows_skipped']} due to unmapped ids)"
        )

    conn.close()


if __name__ == "__main__":
    main()
