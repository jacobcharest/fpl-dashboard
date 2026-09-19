import { createColumnHelper, type ColumnDef } from "@tanstack/react-table";
import { useMemo } from "react";
import type { NumericFilter, PlayerRow, SortSpec, SquadPick } from "../types";
import { DataTable } from "./DataTable";
import { PositionBadge } from "./PositionBadge";
import { PositionFilter } from "./PositionFilter";

const helper = createColumnHelper<PlayerRow>();

// Optional columns (projections) can be undefined as well as null, so both mean "no value".
const fmt = (digits: number) => (v: number | null | undefined) => (v == null ? "-" : v.toFixed(digits));
const fmtPct = (v: number | null | undefined) => (v == null ? "-" : `${v.toFixed(1)}%`);

function buildColumns(perStart: boolean, squad: Map<number, SquadPick>, projLabel: string | null): ColumnDef<PlayerRow, any>[] {
  return [
    helper.accessor("web_name", {
      header: "Player",
      cell: (i) => {
        const pick = squad.get(i.row.original.player_code);
        const armband = pick?.is_captain ? "C" : pick?.is_vice_captain ? "V" : null;
        return (
          <>
            {i.getValue()}
            {armband && <span className={`armband armband-${armband.toLowerCase()}`}>{armband}</span>}
          </>
        );
      },
    }),
    helper.accessor("position", { header: "Pos", cell: (i) => <PositionBadge position={i.getValue()} /> }),
    helper.accessor("price", { header: "Price", cell: (i) => `£${i.getValue().toFixed(1)}` }),
    helper.accessor("selected_by_percent", { header: "Own%", cell: (i) => fmtPct(i.getValue()) }),
    helper.accessor("sos", {
      header: () => (
        <span title="Past strength of schedule: the average of FPL's fixture difficulty ratings (1 easy - 5 hard) for the matches these stats come from, so it follows the gameweek ranges, the opponent filter and Per Start. Lower means the numbers were earned against kinder opposition. The Projections view has the same column for the fixtures to come.">
          SoS
        </span>
      ),
      cell: (i) => {
        const v = i.getValue() as number | null;
        if (v == null) return "-";
        return <span className={`sos ${v <= 2.6 ? "sos-easy" : v >= 3.4 ? "sos-hard" : ""}`}>{v.toFixed(2)}</span>;
      },
    }),
    // Per-start points are a fractional rate (e.g. 7.4), not a whole count, so they need a
    // decimal place to be meaningful - raw points stay integers.
    helper.accessor("total_points", { header: "Pts", cell: (i) => fmt(perStart ? 1 : 0)(i.getValue()) }),
    // Backward-looking: what the xG/xA/xGA already posted in these games was worth. Distinct
    // from the forward-looking projected "xP" at the far end of the table.
    helper.accessor("expected_points", {
      header: () => (
        <span title="Expected points from the games already played: actual points with goals, assists, clean sheets and goals conceded swapped for their xG / xA / xGA expectation. Not a projection.">
          xPts
        </span>
      ),
      cell: (i) => fmt(1)(i.getValue()),
    }),
    // xPts against an average schedule: above xPts means the numbers came the hard way.
    helper.accessor("adjusted_points", {
      header: () => (
        <span title="Schedule-adjusted points: xPts with the opposition taken out. Each match's xG and xA are scaled by how tight that opponent's defence is, and the clean-sheet maths by how dangerous their attack is (team ratings fitted from this season's xG, home advantage included), so it reads as what these performances are worth against an average side. Above xPts = a hard schedule so far; below = a kind one. Goalkeepers are left at xPts.">
          adjPts
        </span>
      ),
      cell: (i) => fmt(1)(i.getValue()),
    }),
    helper.accessor("minutes", { header: "Mins", cell: (i) => fmt(0)(i.getValue()) }),
    helper.accessor("goals_scored", { header: "Goals", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("expected_goals", { header: "xG", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("assists", { header: "Assists", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("expected_assists", { header: "xA", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("expected_goal_involvements", { header: "xGI", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("clean_sheets", { header: "CS", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("expected_goals_conceded", { header: "xGA", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("defensive_contribution", { header: "Def. Contr.", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("defensive_contribution_hit_rate", {
      header: "DC Hit Rate",
      cell: (i) => fmtPct(i.getValue()),
    }),
    helper.accessor("bonus", { header: "Bonus", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("bps", { header: "BPS", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("saves", { header: "Saves", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("yellow_cards", { header: "YC", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("red_cards", { header: "RC", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("influence", { header: "Influence", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("creativity", { header: "Creativity", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("threat", { header: "Threat", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("ict_index", { header: "ICT Index", cell: (i) => fmt(2)(i.getValue()) }),
    // Forward-looking, so they sit apart from the historical stats and are dropped entirely
    // when no projections are loaded rather than showing a column of dashes.
    ...(projLabel
      ? [
          helper.accessor("xp", {
            // The window is picked on the Projections page; the two share it so they never disagree.
            header: () => <span title="Projected points over these gameweeks - change the weeks on the Projections view">xP {projLabel}</span>,
            cell: (i) => fmt(1)(i.getValue()),
          }),
          helper.accessor("xmins", { header: "xMins", cell: (i) => fmt(0)(i.getValue()) }),
        ]
      : []),
  ];
}

const FILTERABLE_COLUMNS = [
  "price",
  "selected_by_percent",
  "sos",
  "total_points",
  "expected_points",
  "adjusted_points",
  "minutes",
  "goals_scored",
  "expected_goals",
  "assists",
  "expected_assists",
  "expected_goal_involvements",
  "clean_sheets",
  "expected_goals_conceded",
  "defensive_contribution",
  "defensive_contribution_hit_rate",
  "bonus",
  "bps",
  "saves",
  "yellow_cards",
  "red_cards",
  "influence",
  "creativity",
  "threat",
  "ict_index",
  "xp",
  "xmins",
];

interface Props {
  data: PlayerRow[];
  sort: SortSpec | null;
  onSortChange: (sort: SortSpec) => void;
  filters: NumericFilter[];
  onFiltersChange: (filters: NumericFilter[]) => void;
  positions: string[] | null;
  onPositionsChange: (positions: string[] | null) => void;
  perStart: boolean;
  /** The user's synced squad, keyed by player_code. Empty when nothing is synced. */
  squad: Map<number, SquadPick>;
  /** e.g. "GW1-4"; null hides the projection columns entirely. */
  projLabel: string | null;
}

export function PlayerTable({
  data,
  sort,
  onSortChange,
  filters,
  onFiltersChange,
  positions,
  onPositionsChange,
  perStart,
  squad,
  projLabel,
}: Props) {
  const columns = useMemo(() => buildColumns(perStart, squad, projLabel), [perStart, squad, projLabel]);

  return (
    <DataTable
      data={data}
      columns={columns}
      sort={sort}
      onSortChange={onSortChange}
      getRowId={(row) => row.player_code}
      filterableColumnIds={FILTERABLE_COLUMNS}
      filters={filters}
      onFiltersChange={onFiltersChange}
      customFilterColumns={{
        position: <PositionFilter selected={positions} onChange={onPositionsChange} />,
      }}
      getRowClassName={(row) => {
        const pick = squad.get(row.player_code);
        if (!pick) return undefined;
        // Slots 12-15 are the bench - still your squad, but dimmed so the XI stands out.
        return pick.squad_slot > 11 ? "my-team my-team-bench" : "my-team";
      }}
    />
  );
}
