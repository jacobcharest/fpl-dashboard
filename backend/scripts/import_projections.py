#!/usr/bin/env python3
"""Import expected-points projections from a CSV into the local database.

    backend/.venv/bin/python backend/scripts/import_projections.py 2026-27 ~/Downloads/fplreview.csv
    ... --source elevenify        # hold a second model alongside for comparison

The parser is deliberately tolerant about layout - see app/projections.py for what it accepts.
Anything it can't match to a player is reported rather than silently dropped.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_connection, init_db
from app.projections import import_projections
from app.refresh import seed_seasons


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("season_id", help="e.g. 2026-27")
    ap.add_argument("csv_path", help="projections CSV to import")
    ap.add_argument("--source", default="fplreview", help="label for this model (default: fplreview)")
    args = ap.parse_args()

    init_db()
    conn = get_connection()
    seed_seasons(conn)
    try:
        s = import_projections(conn, args.season_id, args.csv_path, args.source)
    except ValueError as e:
        sys.exit(f"import failed: {e}")
    finally:
        conn.close()

    gw = f"GW{s['gameweeks'][0]}-{s['gameweeks'][1]}" if s["gameweeks"] else "no gameweeks"
    print(f"[{s['season_id']}] {s['source']}: {s['rows_imported']} rows, {s['players']} players, {gw}")
    for label in ("unmatched", "ambiguous"):
        if s[label]:
            print(f"  {label} ({len(s[label])} shown): {', '.join(s[label])}")
    for w in s["warnings"]:
        print(f"  warning: {w}")


if __name__ == "__main__":
    main()
