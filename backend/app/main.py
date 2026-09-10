from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.db import get_connection, init_db
from app.queries import (
    NumericFilter,
    SortSpec,
    TableFilters,
    TeamRange,
    query_players,
    query_series,
    query_teams,
)
from app.fpl_projections import DEFAULT_HORIZON, generate_projections
from app.live_refresh import fetch_bootstrap, refresh_live_season
from app.my_team import get_my_team, live_season_id, sync_my_team
from app.projections import import_projections, projection_sources
from app.refresh import backfill_season, seed_seasons
from app.team_review import review_team


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    conn = get_connection()
    seed_seasons(conn)
    conn.close()
    yield


app = FastAPI(title="FPL Dashboard API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    per90: bool = False
    starts_only: bool = False
    positions: list[str] | None = None
    projection_source: str | None = None
    projection_start_gw: int | None = None
    projection_end_gw: int | None = None


class ChartSeriesRequest(TableRequest):
    entity_type: Literal["player", "team"]
    entity_codes: list[int]
    stats: list[str]
    per90: bool = False
    starts_only: bool = False


def _to_table_filters(req: TableRequest) -> TableFilters:
    return TableFilters(
        season_id=req.season_id,
        teams=[TeamRange(t.team_code, t.start_gw, t.end_gw) for t in req.teams],
        opponent_team_codes=req.opponent_team_codes,
        filters=[NumericFilter(f.column, f.op, f.value) for f in req.filters],
        sort=SortSpec(req.sort.column, req.sort.direction) if req.sort else None,
        positions=getattr(req, "positions", None),
        projection_source=getattr(req, "projection_source", None),
        projection_start_gw=getattr(req, "projection_start_gw", None),
        projection_end_gw=getattr(req, "projection_end_gw", None),
    )


@app.get("/api/seasons")
def list_seasons():
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, label, backfilled, is_placeholder FROM seasons ORDER BY id"
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
    result = query_players(conn, _to_table_filters(req), per90=req.per90, starts_only=req.starts_only)
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
        per90=req.per90,
        starts_only=req.starts_only,
    )
    conn.close()
    return result


@app.post("/api/refresh/{season_id}")
def refresh_season(season_id: str):
    """Re-fetches this season and re-ingests it (idempotent - safe to run repeatedly). This is
    the "Fetch new data" button. The live season comes straight from the FPL API (see
    app/live_refresh.py - the community archive can lag it by weeks); past seasons come from
    the archive (see app/refresh.py)."""
    conn = get_connection()
    try:
        bootstrap = fetch_bootstrap()
        if season_id == live_season_id(bootstrap):
            summary = refresh_live_season(conn, season_id, bootstrap)
        else:
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


class ProjectionGenerateRequest(BaseModel):
    horizon: int = DEFAULT_HORIZON
    source: str = "fpl_api"


@app.get("/api/my-team/{season_id}/review")
def my_team_review(season_id: str, entry_id: int, source: str = "fpl_api", horizon: int = 5,
                   free_transfers: int | None = None):
    """Suggested XI, captain and transfers for the coming gameweeks, against the loaded
    projections. Syncs the squad as a side effect - see app/team_review.py."""
    conn = get_connection()
    try:
        return review_team(conn, season_id, entry_id, source, horizon, free_transfers)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Couldn't reach the FPL API: {e}")
    finally:
        conn.close()


@app.get("/api/projections/{season_id}")
def list_projection_sources(season_id: str):
    """Which projection models are loaded for this season, and what horizon each covers."""
    conn = get_connection()
    try:
        return projection_sources(conn, season_id)
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


@app.post("/api/projections/{season_id}/generate")
def projections_generate(season_id: str, req: ProjectionGenerateRequest):
    """Builds the built-in FPL-API projection model over the next `horizon` gameweeks and
    loads it under `source` - the fallback when no external model has been imported. See
    app/fpl_projections.py for the model."""
    conn = get_connection()
    try:
        return generate_projections(conn, season_id, req.horizon, req.source)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Couldn't reach the FPL API: {e}")
    finally:
        conn.close()
