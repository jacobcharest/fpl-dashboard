import type { PlayerRow } from "./types";

export interface StatDef {
  key: string;
  label: string;
  seriesEligible: boolean; // can be fetched per-gameweek from /api/chart/series
}

// Player stats: the first 10 are summable per-gameweek (series-eligible). price/value are
// snapshots/derived, not per-gameweek flows, so they're aggregate-only (scatter/ranked
// bar/radar/distribution, which read off the already-fetched table rows).
export const PLAYER_STATS: StatDef[] = [
  { key: "total_points", label: "Points", seriesEligible: true },
  { key: "goals_scored", label: "Goals", seriesEligible: true },
  { key: "expected_goals", label: "xG", seriesEligible: true },
  { key: "assists", label: "Assists", seriesEligible: true },
  { key: "expected_assists", label: "xA", seriesEligible: true },
  { key: "expected_goal_involvements", label: "xGI", seriesEligible: true },
  { key: "clean_sheets", label: "Clean Sheets", seriesEligible: true },
  { key: "expected_goals_conceded", label: "xGA", seriesEligible: true },
  { key: "defensive_contribution", label: "Def. Contr.", seriesEligible: true },
  { key: "bonus", label: "Bonus", seriesEligible: true },
  { key: "bps", label: "BPS", seriesEligible: true },
  { key: "saves", label: "Saves", seriesEligible: true },
  { key: "yellow_cards", label: "Yellow Cards", seriesEligible: true },
  { key: "red_cards", label: "Red Cards", seriesEligible: true },
  { key: "influence", label: "Influence", seriesEligible: true },
  { key: "creativity", label: "Creativity", seriesEligible: true },
  { key: "threat", label: "Threat", seriesEligible: true },
  { key: "ict_index", label: "ICT Index", seriesEligible: true },
  { key: "price", label: "Price", seriesEligible: false },
  { key: "minutes", label: "Minutes", seriesEligible: false },
  { key: "value", label: "Value (pts per price step)", seriesEligible: false },
];

// Team stats: goals/xG are series-eligible (per-gameweek from fixtures/player xG sums);
// table_place/GD/opponent-strength metrics only make sense over a range, not a single GW.
export const TEAM_STATS: StatDef[] = [
  { key: "goals_scored", label: "Goals", seriesEligible: true },
  { key: "expected_goals", label: "xG", seriesEligible: true },
  { key: "goals_conceded", label: "Goals Against", seriesEligible: true },
  { key: "expected_goals_conceded", label: "xGA", seriesEligible: true },
  { key: "table_place", label: "Table Place", seriesEligible: false },
  { key: "goal_difference", label: "GD", seriesEligible: false },
  { key: "opponent_expected_goals", label: "Opponent xG", seriesEligible: false },
  { key: "opponent_expected_goals_conceded", label: "Opponent xGA", seriesEligible: false },
];

export const STACKED_BREAKDOWN_STATS = ["goals_scored", "assists", "clean_sheets", "bonus", "defensive_contribution"];

const BASE_PRICE: Record<string, number> = { GK: 4.0, DEF: 4.0, MID: 4.5, FWD: 4.5 };

// points added per price-step added above the position's lowest possible starting price,
// normalized to £0.5m steps (see DESIGN.md). Null ("N/A"/infinite) when the player is still
// at the base price - zero investment above minimum, not a real ratio.
export function computeValueStat(row: PlayerRow): number | null {
  const base = BASE_PRICE[row.position] ?? 4.5;
  const steps = Math.round((row.price - base) / 0.5);
  if (steps <= 0) return null;
  return row.total_points / steps;
}

export function statsFor(entityType: "player" | "team"): StatDef[] {
  return entityType === "player" ? PLAYER_STATS : TEAM_STATS;
}

export function statLabel(entityType: "player" | "team", key: string): string {
  return statsFor(entityType).find((s) => s.key === key)?.label ?? key;
}
