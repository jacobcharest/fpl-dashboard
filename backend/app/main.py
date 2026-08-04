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
from app.refresh import backfill_season, seed_seasons


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
    """Re-fetches this season from the source archive and re-ingests it (idempotent - safe to
    run repeatedly). This is the "Fetch new data" button; see app/refresh.py for why this
    doubles as the current season's live-data refresh."""
    conn = get_connection()
    try:
        summary = backfill_season(conn, season_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"{season_id}: {e}")
    finally:
        conn.close()
    return summary
