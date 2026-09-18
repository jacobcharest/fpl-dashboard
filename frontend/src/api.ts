import axios from "axios";
import type {
  LeagueSummary,
  LeagueWeek,
  ChartSeriesRequest,
  PlayerRow,
  PlayerTableRequest,
  PriceTable,
  MyTeam,
  ProjectionSource,
  ProjectionTable,
  MyTeamSyncResult,
  Season,
  SeriesPoint,
  TableRequest,
  TeamMeta,
  TeamRow,
} from "./types";

// Relative URLs: in dev the Vite proxy forwards /api -> :8000 (vite.config.ts); in the
// systemd instance the backend serves the built app itself, so /api is same-origin.
const client = axios.create({ baseURL: "" });

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

export async function getProjectionTable(req: PlayerTableRequest): Promise<ProjectionTable> {
  return (await client.post("/api/projections/table", req)).data;
}

export async function getPriceTable(seasonId: string): Promise<PriceTable> {
  return (await client.get(`/api/prices/${seasonId}`, { timeout: 30_000 })).data;
}

export async function getLeagues(seasonId: string): Promise<LeagueSummary[]> {
  return (await client.get(`/api/leagues/${seasonId}`, { timeout: 30_000 })).data;
}

// The first load of a league fetches every team's every gameweek, hence the long timeout;
// after that only the gameweek in progress is re-read.
export async function getLeagueWeek(seasonId: string, leagueId: number, event: number | null): Promise<LeagueWeek> {
  return (await client.get(`/api/leagues/${seasonId}/${leagueId}`, { params: event ? { event } : {}, timeout: 120_000 })).data;
}
