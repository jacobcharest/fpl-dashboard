import axios from "axios";
import type { PlayerRow, PlayerTableRequest, Season, TableRequest, TeamMeta, TeamRow } from "./types";

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
