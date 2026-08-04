export interface Season {
  id: string;
  label: string;
  backfilled: number;
  is_placeholder: number;
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
}

export const POSITIONS = ["GK", "DEF", "MID", "FWD"] as const;

export interface PlayerRow {
  player_code: number;
  web_name: string;
  team_name: string;
  position: string;
  price: number;
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
  bonus: number;
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
