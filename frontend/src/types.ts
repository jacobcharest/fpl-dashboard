export interface Season {
  id: string;
  label: string;
  backfilled: number;
  is_placeholder: number;
  played_through: number | null; // latest gameweek with stats; null before the season starts
}

export interface TeamMeta {
  team_code: number;
  name: string;
  short_name: string;
}

export interface TeamFilterState {
  team_code: number;
  name: string;
  included: boolean;
  opponentIncluded: boolean;
  start_gw: number;
  end_gw: number;
}

export interface TeamRange {
  team_code: number;
  start_gw: number;
  end_gw: number;
}

export interface NumericFilter {
  column: string;
  op: "gt" | "lt";
  value: number;
}

export interface SortSpec {
  column: string;
  direction: "asc" | "desc";
}

export interface TableRequest {
  season_id: string;
  teams: TeamRange[];
  opponent_team_codes: number[] | null;
  filters: NumericFilter[];
  sort: SortSpec | null;
}

export interface PlayerTableRequest extends TableRequest {
  per90: boolean;
  starts_only: boolean;
  positions: string[] | null;
  projection_source?: string | null;
  projection_gameweeks?: number[] | null;
}

export const POSITIONS = ["GK", "DEF", "MID", "FWD"] as const;

export interface PlayerRow {
  player_code: number;
  web_name: string;
  team_name: string;
  position: string;
  price: number;
  selected_by_percent: number | null; // ownership %; null until the season is (re)fetched
  minutes: number;
  total_points: number;
  goals_scored: number;
  expected_goals: number;
  assists: number;
  expected_assists: number;
  expected_goal_involvements: number;
  clean_sheets: number;
  expected_goals_conceded: number;
  defensive_contribution: number | null;
  defensive_contribution_hit_rate: number | null;
  bonus: number;
  bps: number;
  saves: number;
  yellow_cards: number;
  red_cards: number;
  influence: number;
  creativity: number;
  threat: number;
  ict_index: number;
  xp?: number | null;
  xmins?: number | null;
}

export interface TeamRow {
  team_code: number;
  name: string;
  table_place: number;
  goals_scored: number;
  expected_goals: number;
  goals_conceded: number;
  expected_goals_conceded: number;
  goal_difference: number;
  opponent_expected_goals: number | null;
  opponent_expected_goals_conceded: number | null;
}

export type ChartType =
  | "timeseries"
  | "scatter"
  | "ranked_bar"
  | "radar"
  | "heatmap"
  | "distribution"
  | "stacked"
  | "small_multiples";

export interface ChartSeriesRequest extends TableRequest {
  entity_type: "player" | "team";
  entity_codes: number[];
  stats: string[];
  per90: boolean;
  starts_only: boolean;
}

export interface SeriesPoint {
  entity_code: number;
  name: string;
  round: number;
  [stat: string]: number | string | null;
}

export interface ProjectionSource {
  source: string;
  players: number;
  first_gw: number;
  last_gw: number;
  imported_at: string | null;
}

/** One player in the Projections panel, totalled over the panel's Weeks range. */
export interface ProjectionRow {
  player_code: number;
  web_name: string;
  team_name: string;
  position: string;
  price: number | null;
  xp_per_gw: number | null; // xp_total / gameweeks the source projected inside the range
  xp_per_gw_per_m: number | null; // xp_per_gw / price; null when the price is unknown
  xp_total: number | null; // summed over the range
  xmins_avg: number | null; // averaged over the range
  xg: number | null; // summed over the range
  xa: number | null;
  xcs: number | null; // expected clean sheets over the range (sum of per-match probabilities)
  xdc: number | null; // expected defensive-contribution hits over the range
}

export interface ProjectionTable {
  gameweeks: number[]; // every gameweek the source covers, for the coverage note
  played_through: number | null; // latest gameweek with stats in the DB; earlier GWs are history
  rows: ProjectionRow[];
}

/** Horizon the player table sums projections over. null source = projections off. The
 * gameweeks are an explicit set (ticked in the Projections panel), not necessarily contiguous. */
export interface ProjectionSpec {
  source: string | null;
  gameweeks: number[];
}

/** "GW5-10" for a contiguous set, "GW5, 7, 8" otherwise; null when nothing is selected. */
export function gameweekLabel(gameweeks: number[]): string | null {
  if (gameweeks.length === 0) return null;
  const sorted = [...gameweeks].sort((a, b) => a - b);
  const first = sorted[0];
  const last = sorted[sorted.length - 1];
  const contiguous = last - first + 1 === sorted.length;
  if (sorted.length === 1) return `GW${first}`;
  return contiguous ? `GW${first}-${last}` : `GW${sorted.join(", ")}`;
}

export interface SquadPick {
  player_code: number;
  squad_slot: number; // FPL's 1-15 ordering; 1-11 start, 12-15 bench
  is_captain: number;
  is_vice_captain: number;
  multiplier: number | null;
}

export interface MyTeam {
  entry_id: number;
  entry_name: string | null;
  manager_name: string | null;
  synced_event: number | null; // null until a gameweek has started and picks became public
  picks: SquadPick[];
}

// The sync endpoint reports on what it did (a pick *count*), rather than echoing the squad -
// the squad itself is read back via getMyTeam.
export interface MyTeamSyncResult {
  season_id: string;
  entry_id: number;
  entry_name: string | null;
  manager_name: string | null;
  status: "synced" | "pending_kickoff";
  synced_event: number | null;
  picks: number;
  unmatched: string[]; // squad members with no row on this season's board
  message: string;
}
