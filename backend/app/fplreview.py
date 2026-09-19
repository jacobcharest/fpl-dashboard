"""Fetching FPL Review's projections by driving a real (headless) browser.

FPL Review has no public API and its free tier disables the CSV download, but the projections
are in the page once it renders - which is what scripts/fplreview_export.js reads when pasted
into the console. This module does the same thing unattended: open the free planner, connect a
team id, open the PROJECTIONS tab, run that very script, and catch the CSV it would have
downloaded. The export script stays the single source of truth for the file's layout; nothing
here knows about columns.

It is one page load, as a person would make it, and it's rate-limited (FETCH_MIN_INTERVAL)
because Fetch New Data calls it and projections only change a few times a day. It depends on
FPL Review's page structure, so expect it to need a fix when they redesign; every failure says
which step broke, and the manual console route in fplreview_export.js always remains.

Needs Chrome or Chromium plus a matching chromedriver. Both are auto-detected; set
FPL_CHROME_BINARY / FPL_CHROMEDRIVER to override.
"""

import csv
import os
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.db import DB_PATH
from app.prices import store_fplreview_progress
from app.projections import import_projections

PLANNER_URL = "https://app.fplreview.com/free"
EXPORT_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "fplreview_export.js"
CSV_PATH = DB_PATH.parent / "fplreview-latest.csv"  # data/*.csv is gitignored
SOURCE = "fplreview"
FETCH_MIN_INTERVAL = timedelta(hours=3)
# When the scheduled job fetches, as time before a gameweek's deadline. FPL Review drops a
# gameweek once its deadline passes, so the last one is the capture that matters - late enough
# to carry the final team news, early enough to leave retries. The earlier one is insurance for
# a laptop that's asleep at the end.
CHECKPOINTS = (timedelta(hours=24), timedelta(hours=2))
STEP_TIMEOUT = 45  # seconds to wait for each page state

# A headless browser announces itself in its user agent; present as the desktop Chrome it is.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)

CHROME_CANDIDATES = [
    "/snap/chromium/current/usr/lib/chromium-browser/chrome",
    "google-chrome",
    "chromium",
    "chromium-browser",
]

# Runs the export script with the download intercepted: the script builds a Blob and clicks an
# <a download>; we keep the Blob and swallow the click, then hand its text back.
CAPTURE_JS = """
const done = arguments[arguments.length - 1];
const source = arguments[0];
let blob = null;
const realCreate = URL.createObjectURL, realClick = HTMLAnchorElement.prototype.click;
URL.createObjectURL = (b) => { blob = b; return "blob:captured"; };
HTMLAnchorElement.prototype.click = function () {};
try {
  (0, eval)(source);
  if (!blob) throw new Error("the export script produced no file");
  blob.text().then((text) => done({ csv: text }), (e) => done({ error: String(e) }));
} catch (e) {
  done({ error: e.message || String(e) });
} finally {
  URL.createObjectURL = realCreate;
  HTMLAnchorElement.prototype.click = realClick;
}
"""


class FplReviewError(RuntimeError):
    pass


def _find(candidates: list[str], env: str, what: str) -> str:
    override = os.environ.get(env)
    for c in [override] if override else candidates:
        path = c if os.path.isabs(c) else shutil.which(c)
        if path and os.path.exists(path):
            return path
    raise FplReviewError(f"No {what} found (set {env} to its path).")


def _wait_for(driver, description: str, probe):
    """Polls `probe(driver)` until it returns something truthy."""
    deadline = time.monotonic() + STEP_TIMEOUT
    while time.monotonic() < deadline:
        found = probe(driver)
        if found:
            return found
        time.sleep(0.5)
    raise FplReviewError(f"Timed out waiting for {description} - FPL Review's page may have changed.")


def fetch_csv(entry_id: int, dest: Path = CSV_PATH) -> Path:
    """Drives the browser and writes the projections CSV to `dest`."""
    # Imported here so the rest of the app runs without selenium installed.
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By

    options = Options()
    options.binary_location = _find(CHROME_CANDIDATES, "FPL_CHROME_BINARY", "Chrome/Chromium browser")
    for arg in ("--headless=new", "--window-size=1600,1200", "--no-sandbox", f"--user-agent={USER_AGENT}"):
        options.add_argument(arg)
    service = Service(_find(["chromedriver"], "FPL_CHROMEDRIVER", "chromedriver"))

    driver = webdriver.Chrome(options=options, service=service)
    try:
        driver.set_script_timeout(STEP_TIMEOUT)
        driver.get(PLANNER_URL)

        box = _wait_for(driver, "the team id box", lambda d: d.find_elements(By.CSS_SELECTOR, "input[type=text]"))[0]
        box.send_keys(str(entry_id))
        connect = [b for b in driver.find_elements(By.TAG_NAME, "button") if b.text.strip() == "Connect"]
        if not connect:
            raise FplReviewError("No Connect button - FPL Review's page may have changed.")
        connect[0].click()

        tab = _wait_for(
            driver,
            f"the planner to load for team {entry_id}",
            lambda d: d.find_elements(By.XPATH, "//*[translate(normalize-space(text()),'projections','PROJECTIONS')='PROJECTIONS']"),
        )[0]
        tab.click()
        # The table rendering is what mounts the player data the export script reads.
        _wait_for(driver, "the projections table", lambda d: len(d.find_elements(By.TAG_NAME, "tr")) > 10)

        result = driver.execute_async_script(CAPTURE_JS, EXPORT_SCRIPT.read_text())
    finally:
        driver.quit()

    if not result or result.get("error"):
        raise FplReviewError(f"Export failed: {(result or {}).get('error', 'no result')}")
    csv_text = result["csv"]
    if csv_text.count("\n") < 100:
        raise FplReviewError("Export returned too few players to be a real projections file.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(csv_text)
    return dest


def due_checkpoint(bootstrap: dict, last_import: datetime | None, now: datetime | None = None) -> str | None:
    """Why a scheduled fetch should run now, or None if it shouldn't.

    Due when a checkpoint before the next deadline has passed and nothing has been imported
    since it. A failed fetch leaves `last_import` where it was, so the next tick simply tries
    again - and a machine that wakes up inside the window catches up at once."""
    now = now or datetime.now(timezone.utc)
    deadlines = sorted(
        (datetime.fromisoformat(e["deadline_time"].replace("Z", "+00:00")), e["id"]) for e in bootstrap["events"]
    )
    upcoming = next(((d, gw) for d, gw in deadlines if d > now), None)
    if upcoming is None:
        return None
    deadline, gw = upcoming
    passed = [deadline - c for c in CHECKPOINTS if deadline - c <= now]
    if not passed:
        return None
    latest = max(passed)
    if last_import is not None and last_import >= latest:
        return None
    hours = (deadline - now).total_seconds() / 3600
    return f"GW{gw} deadline in {hours:.1f}h"


def last_import_time(conn, season_id: str) -> datetime | None:
    last = conn.execute(
        "SELECT MAX(imported_at) FROM player_projections WHERE season_id = ? AND source = ?", (season_id, SOURCE)
    ).fetchone()[0]
    return datetime.fromisoformat(last) if last else None


def read_price_progress(path: Path) -> dict[int, float]:
    """player_code -> FPL Review's progress to the next price change, as a percentage
    (-100 about to fall .. +100 about to rise). The page carries it in tenths of a percent."""
    out = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            raw, code = row.get("price_progress"), row.get("code")
            if raw not in (None, "") and code:
                out[int(float(code))] = float(raw) / 10.0
    return out


def refresh_projections(conn, season_id: str, force: bool = False) -> dict:
    """Fetch + import, for Fetch New Data. Never raises: a projections problem must not fail the
    stats refresh it rides along with. Returns {status, message, ...} for the UI."""
    entry = conn.execute("SELECT entry_id FROM manager_entry WHERE season_id = ?", (season_id,)).fetchone()
    if entry is None:
        return {"status": "skipped", "message": "projections need a synced team id (FPL Review asks for one)"}

    last = conn.execute(
        "SELECT MAX(imported_at) FROM player_projections WHERE season_id = ? AND source = ?", (season_id, SOURCE)
    ).fetchone()[0]
    if last and not force:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(last)
        if age < FETCH_MIN_INTERVAL:
            hours = age.total_seconds() / 3600
            return {"status": "fresh", "message": f"projections already updated {hours:.1f}h ago"}

    try:
        path = fetch_csv(entry["entry_id"])
        summary = import_projections(conn, season_id, str(path), SOURCE)
        store_fplreview_progress(conn, season_id, read_price_progress(path))
    except Exception as e:
        return {"status": "failed", "message": f"projections not updated: {e}"}
    gws = summary["gameweeks"]
    return {
        "status": "imported",
        "message": f"projections GW{gws[0]}-{gws[1]} ({summary['players']} players)",
        "gameweeks": gws,
        "players": summary["players"],
        "unmatched": summary["unmatched"],
    }
