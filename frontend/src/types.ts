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
  per_start: boolean;
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
  selected_by_percent: number | null;
  // Mean FPL fixture difficulty (1 easy - 5 hard) of the matches behind this row's stats.
  sos: number | null; // ownership %; null until the season is (re)fetched
  minutes: number;
  total_points: number;
  expected_points: number | null; // backward-looking; null where the season/round has no xG data
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
  per_start: boolean;
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
  // Schedule over the ticked gameweeks, shared by everyone at the club: the mean of FPL's
  // fixture difficulty ratings (1 easy - 5 hard), how many fixtures that covers (doubles add,
  // blanks don't), and the fixtures spelled out for the tooltip.
  sos: number | null;
  fixture_count: number | null;
  fixtures: string | null;
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

// One player on the Prices page. Money is in £m; the `change_*` columns are price movement
// and the `net_*` columns transfer traffic (in minus out). Anything that needs an earlier
// snapshot to compare against is null until one exists - see backend/app/prices.py.
export interface PriceRow {
  player_code: number;
  web_name: string;
  position: string;
  team_name: string | null;
  status: string | null; // FPL availability flag: a(vailable) d(oubt) i(njured) s(uspended) u(navailable) n(ot eligible)
  price: number;
  change_day: number | null; // vs the previous stored day
  change_week: number | null; // vs the latest stored day at least 7 days back
  change_event: number | null; // this gameweek, as FPL reports it
  change_start: number | null; // since the season began
  selected_by_percent: number | null;
  transfers_in_event: number | null;
  transfers_out_event: number | null;
  net_event: number | null;
  net_day: number | null;
  net_since_change: number | null;
  // What net_since_change is counted from: an observed price change, FPL's own gameweek
  // counter, or just the first day tracked (a lower bound).
  since_basis: "change" | "gameweek" | "tracking";
  since_day: string | null;
  pressure: number | null; // net_since_change as a % of the managers who own the player
  // FPL Review's estimate of progress to the next change: -100 (about to fall) .. +100 (about
  // to rise). Theirs, not ours - refreshed whenever projections are fetched (see progress_day).
  fplreview_progress: number | null;
  squad_slot: number | null;
  purchase_price: number | null;
  purchase_estimated: boolean;
  selling_price: number | null;
  profit: number | null; // selling - purchase: the half of the rise you'd actually keep
}

export interface SquadValue {
  players: number;
  bank: number | null;
  market_value: number;
  selling_value: number | null; // null until every pick has a purchase price (re-sync)
  purchase_value: number | null;
}

export interface PriceTable {
  season_id: string;
  price_day: string | null; // null = nothing captured for this season
  captured_at: string | null;
  event: number | null;
  prev_day: string | null;
  week_day: string | null;
  first_day: string | null;
  snapshot_days: number;
  total_players: number | null;
  progress_day: string | null; // price day of the newest FPL Review progress reading
  unlisted: number; // in the live API but not ingested yet - "Fetch New Data" picks them up
  squad: SquadValue | null;
  rows: PriceRow[];
  warning: string | null;
}

export interface LeagueSummary {
  league_id: number;
  name: string;
  rank: number | null;
  size: number | null;
}

// One player in a league rival's gameweek squad. `squad_slot` is where the manager put them
// (automatic substitutions undone); `points` / `xp` already carry the captaincy multiplier.
export interface LeaguePick {
  player_code: number;
  web_name: string;
  position: string;
  team_name: string | null;
  squad_slot: number;
  multiplier: number;
  is_captain: number;
  is_vice_captain: number;
  auto_sub: "in" | "out" | null;
  minutes: number | null;
  points: number | null;
  counts: boolean; // false = benched, shown but not in the team's score
  xp: number | null;
  xp_counts: boolean;
}

export interface LeagueEntry {
  entry_id: number;
  entry_name: string;
  player_name: string;
  is_me: boolean;
  rank: number;
  prev_rank: number | null;
  points: number; // before hits, as FPL shows a gameweek score
  transfers: number;
  hits: number;
  points_on_bench: number;
  total_points: number;
  overall_rank: number | null;
  value: number | null;
  chip: string | null;
  chips_used: { event: number; chip: string }[];
  projected: number | null;
  projected_picks: number;
  final: boolean;
  picks: LeaguePick[];
}

export interface LeagueWeek {
  league_id: number;
  name: string | null;
  synced_at?: string | null;
  events: number[];
  event: number | null;
  final?: boolean;
  entries: LeagueEntry[];
  warning: string | null;
}
