#!/usr/bin/env python3
"""Record today's FPL prices, ownership and transfer counts for the live season.

    backend/.venv/bin/python backend/scripts/snapshot_prices.py

FPL's API has no price history, so the Prices page can only show movement across days that were
captured as they happened. Opening the page or pressing "Fetch New Data" captures too; this
script is what systemd/fpl-prices-snapshot.timer runs nightly so days aren't missed when the
dashboard isn't opened. One request to bootstrap-static; safe to run any number of times a day.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_connection, init_db
from app.prices import capture_snapshot
from app.refresh import seed_seasons


def main():
    init_db()
    conn = get_connection()
    seed_seasons(conn)
    summary = capture_snapshot(conn)
    conn.close()
    print(f"{summary['season_id']}: stored {summary['players']} players under price day {summary['price_day']}")


if __name__ == "__main__":
    main()
