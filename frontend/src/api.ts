import axios from "axios";
import type {
  ChartSeriesRequest,
  PlayerRow,
  PlayerTableRequest,
  MyTeam,
  ProjectionSource,
  MyTeamSyncResult,
  Season,
  SeriesPoint,
  TableRequest,
  TeamMeta,
  TeamRow,
} from "./types";

const client = axios.create({ baseURL: "http://localhost:8000" });

export async function getSeasons(): Promise<Season[]> {
  return (await client.get("/api/seasons")).data;
}

export async function getSeasonTeams(seasonId: string): Promise<TeamMeta[]> {
  return (await client.get(`/api/seasons/${seasonId}/teams`)).data;
}

export async function getPlayerTable(req: PlayerTableRequest): Promise<PlayerRow[]> {
  return (await client.post("/api/players", req)).data;
}

export async function getTeamTable(req: TableRequest): Promise<TeamRow[]> {
  return (await client.post("/api/teams", req)).data;
}

export async function getChartSeries(req: ChartSeriesRequest): Promise<SeriesPoint[]> {
  return (await client.post("/api/chart/series", req)).data;
}

export interface RefreshSummary {
  season_id: string;
  teams: number;
  players: number;
  fixtures: number;
  gw_rows_inserted: number;
  gw_rows_total: number;
  gw_rows_skipped: number;
}

export async function refreshSeason(seasonId: string): Promise<RefreshSummary> {
  return (await client.post(`/api/refresh/${seasonId}`, null, { timeout: 120_000 })).data;
}

export async function getMyTeam(seasonId: string): Promise<MyTeam | null> {
  return (await client.get(`/api/my-team/${seasonId}`)).data;
}

export async function syncMyTeam(seasonId: string, entryId: number): Promise<MyTeamSyncResult> {
  return (await client.post(`/api/my-team/${seasonId}/sync`, { entry_id: entryId }, { timeout: 30_000 })).data;
}

export async function getProjectionSources(seasonId: string): Promise<ProjectionSource[]> {
  return (await client.get(`/api/projections/${seasonId}`)).data;
}
