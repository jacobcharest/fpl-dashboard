"""FastAPI entrypoint.

Two behaviours are opt-in so `./run.sh` (uvicorn on :8000, Vite on :5173 proxying /api) is
unaffected:

- If `<repo_root>/frontend/dist` exists, the built SPA is served same-origin at `/`, so the
  whole app lives on one port. `run.sh` never builds, so this is dormant in development.
- If `FPL_IDLE_TIMEOUT` (seconds) is set > 0, a watchdog stops the process after that long
  without an HTTP request. With systemd socket activation (see `systemd/`) that gives
  on-demand start + idle stop: the socket keeps listening and the next visit respawns us.
"""

import asyncio
import os
import signal
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

from app.db import get_connection, init_db
from app.queries import (
    NumericFilter,
    SortSpec,
    TableFilters,
    TeamRange,
    query_players,
    query_projections,
    query_series,
    query_teams,
)
from app.my_team import get_my_team, sync_my_team
from app.projections import import_projections, projection_sources
from app.refresh import backfill_season, seed_seasons


REPO_ROOT = Path(__file__).resolve().parents[2]

# Monotonic timestamp of the last HTTP request; the idle watchdog reads this.
_last_activity = time.monotonic()


async def _idle_watchdog(timeout: float) -> None:
    """Stop the process after `timeout` seconds with no requests.

    Sends SIGTERM to ourselves so uvicorn shuts down cleanly. Under systemd socket activation
    the listening socket outlives us, so the next request transparently respawns the service.
    """
    poll = max(1.0, min(timeout, 30.0))
    while True:
        await asyncio.sleep(poll)
        if time.monotonic() - _last_activity > timeout:
            os.kill(os.getpid(), signal.SIGTERM)
            return


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    conn = get_connection()
    seed_seasons(conn)
    conn.close()
    timeout = float(os.environ.get("FPL_IDLE_TIMEOUT", "0") or "0")
    task = asyncio.create_task(_idle_watchdog(timeout)) if timeout > 0 else None
    try:
        yield
    finally:
        if task is not None:
            task.cancel()


app = FastAPI(title="FPL Dashboard API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _track_activity(request: Request, call_next):
    global _last_activity
    _last_activity = time.monotonic()
    return await call_next(request)


class MyTeamSyncRequest(BaseModel):
    entry_id: int


class TeamRangeIn(BaseModel):
    team_code: int
    start_gw: int
    end_gw: int


class NumericFilterIn(BaseModel):
    column: str
    op: Literal["gt", "lt"]
    value: float


class SortSpecIn(BaseModel):
    column: str
    direction: Literal["asc", "desc"] = "desc"


class TableRequest(BaseModel):
    season_id: str
    teams: list[TeamRangeIn]
    opponent_team_codes: list[int] | None = None
    filters: list[NumericFilterIn] = []
    sort: SortSpecIn | None = None


class PlayerTableRequest(TableRequest):
    per_start: bool = False
    positions: list[str] | None = None
    projection_source: str | None = None
    # Explicit gameweeks to total projections over - any set, not necessarily contiguous.
    projection_gameweeks: list[int] | None = None


class ChartSeriesRequest(TableRequest):
    entity_type: Literal["player", "team"]
    entity_codes: list[int]
    stats: list[str]
    per_start: bool = False


def _to_table_filters(req: TableRequest) -> TableFilters:
    return TableFilters(
        season_id=req.season_id,
        teams=[TeamRange(t.team_code, t.start_gw, t.end_gw) for t in req.teams],
        opponent_team_codes=req.opponent_team_codes,
        filters=[NumericFilter(f.column, f.op, f.value) for f in req.filters],
        sort=SortSpec(req.sort.column, req.sort.direction) if req.sort else None,
        positions=getattr(req, "positions", None),
        projection_source=getattr(req, "projection_source", None),
        projection_gameweeks=getattr(req, "projection_gameweeks", None),
    )


@app.get("/api/seasons")
def list_seasons():
    conn = get_connection()
    # played_through: latest gameweek with any stats, so the UI can default forward-looking
    # views (projections) to "next week" without a round-trip to the FPL API.
    rows = conn.execute(
        """SELECT s.id, s.label, s.backfilled, s.is_placeholder,
                  (SELECT MAX(round) FROM player_gw_stats g WHERE g.season_id = s.id) AS played_through
           FROM seasons s ORDER BY s.id"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/seasons/{season_id}/teams")
def list_teams(season_id: str):
    conn = get_connection()
    rows = conn.execute(
        "SELECT team_code, name, short_name FROM teams WHERE season_id = ? ORDER BY name",
        (season_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/players")
def players_table(req: PlayerTableRequest):
    conn = get_connection()
    result = query_players(conn, _to_table_filters(req), per_start=req.per_start)
    conn.close()
    return result


@app.post("/api/teams")
def teams_table(req: TableRequest):
    conn = get_connection()
    result = query_teams(conn, _to_table_filters(req))
    conn.close()
    return result


@app.post("/api/chart/series")
def chart_series(req: ChartSeriesRequest):
    conn = get_connection()
    result = query_series(
        conn,
        _to_table_filters(req),
        entity_type=req.entity_type,
        entity_codes=req.entity_codes,
        stats=req.stats,
        per_start=req.per_start,
    )
    conn.close()
    return result


@app.post("/api/refresh/{season_id}")
def refresh_season(season_id: str):
    """Re-fetches this season and re-ingests it (idempotent - safe to run repeatedly). This is
    the "Fetch new data" button. The season in progress comes from the live FPL API; finished
    seasons from the community archive - see app/refresh.py."""
    conn = get_connection()
    try:
        summary = backfill_season(conn, season_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"{season_id}: {e}")
    finally:
        conn.close()
    return summary


@app.get("/api/my-team/{season_id}")
def my_team(season_id: str):
    """The stored squad for this season, or null if none has been synced yet."""
    conn = get_connection()
    try:
        return get_my_team(conn, season_id)
    finally:
        conn.close()


@app.post("/api/my-team/{season_id}/sync")
def my_team_sync(season_id: str, req: MyTeamSyncRequest):
    """Pulls the user's squad from the live FPL API. Before the season's first kickoff this can
    only validate and store the team id (picks aren't public yet) - see app/my_team.py."""
    conn = get_connection()
    try:
        return sync_my_team(conn, season_id, req.entry_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Couldn't reach the FPL API: {e}")
    finally:
        conn.close()


class ProjectionImportRequest(BaseModel):
    csv_path: str
    source: str = "fplreview"


@app.get("/api/projections/{season_id}")
def list_projection_sources(season_id: str):
    """Which projection models are loaded for this season, and what horizon each covers."""
    conn = get_connection()
    try:
        return projection_sources(conn, season_id)
    finally:
        conn.close()


@app.post("/api/projections/table")
def projections_table(req: PlayerTableRequest):
    """Per-gameweek breakdown of a projection source for the Projections panel. Takes the same
    body as /api/players so the sidebar's team/position filters carry over unchanged."""
    conn = get_connection()
    try:
        return query_projections(conn, _to_table_filters(req))
    finally:
        conn.close()


@app.post("/api/projections/{season_id}/import")
def projections_import(season_id: str, req: ProjectionImportRequest):
    """Imports a projections CSV already on disk. Layout is sniffed rather than fixed - see
    app/projections.py - and anything unmatchable comes back in the response instead of being
    dropped."""
    conn = get_connection()
    try:
        return import_projections(conn, season_id, req.csv_path, req.source)
    except FileNotFoundError:
        raise HTTPException(status_code=400, detail=f"No such file: {req.csv_path}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


class SPAStaticFiles(StaticFiles):
    """Static files with single-page-app fallback: any path that isn't a real file resolves to
    index.html. Real /api routes are matched before this mount."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            # Unknown /api/* paths must stay a real JSON 404, not the HTML shell.
            if exc.status_code == 404 and not path.startswith("api/"):
                response = await super().get_response("index.html", scope)
                response.headers["cache-control"] = "no-cache"
                return response
            raise
        if path.startswith("assets/"):
            # Vite content-hashes these filenames - cache forever.
            response.headers["cache-control"] = "public, max-age=31536000, immutable"
        else:
            # index.html must revalidate on every load, or browsers keep a pre-rebuild bundle.
            response.headers["cache-control"] = "no-cache"
        return response


# Serve the built SPA same-origin when it exists (the systemd instance). Mounted last so
# /api/* keeps priority. Absent in dev -> no-op.
_dist = REPO_ROOT / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/", SPAStaticFiles(directory=_dist, html=True), name="spa")
