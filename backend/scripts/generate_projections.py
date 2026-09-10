#!/usr/bin/env python3
"""Generate expected-points projections from the live FPL API and load them.

    backend/.venv/bin/python backend/scripts/generate_projections.py 2026-27
    ... --horizon 5             # gameweeks ahead (default 8)
    ... --source fpl_api        # label in the sidebar's Projections dropdown

This is the no-subscription fallback to importing an external model with
import_projections.py; see app/fpl_projections.py for what the model does and doesn't
do. Run backfill_history.py for the season first so the current form is in the database.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_connection, init_db
from app.fpl_projections import DEFAULT_HORIZON, generate_projections
from app.refresh import seed_seasons


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("season_id", help="e.g. 2026-27 (must be the live season)")
    ap.add_argument("--horizon", type=int, default=DEFAULT_HORIZON)
    ap.add_argument("--source", default="fpl_api")
    args = ap.parse_args()

    init_db()
    conn = get_connection()
    seed_seasons(conn)
    try:
        s = generate_projections(conn, args.season_id, args.horizon, args.source)
    except ValueError as e:
        sys.exit(f"generate failed: {e}")
    finally:
        conn.close()

    gw = (
        f"GW{s['gameweeks'][0]}-{s['gameweeks'][1]}"
        if s["gameweeks"]
        else "no gameweeks"
    )
    print(
        f"[{s['season_id']}] {s['source']}: {s['rows_imported']} rows, "
        f"{s['players']} players, "
        f"{gw} -> {s['csv_path']}"
    )
    for label in ("unmatched", "ambiguous"):
        if s[label]:
            print(f"  {label}: {', '.join(s[label])}")


if __name__ == "__main__":
    main()
