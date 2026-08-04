import { createColumnHelper, type ColumnDef } from "@tanstack/react-table";
import { useMemo } from "react";
import type { NumericFilter, PlayerRow, SortSpec } from "../types";
import { DataTable } from "./DataTable";
import { PositionBadge } from "./PositionBadge";
import { PositionFilter } from "./PositionFilter";

const helper = createColumnHelper<PlayerRow>();

const fmt = (digits: number) => (v: number | null) => (v == null ? "-" : v.toFixed(digits));

function buildColumns(per90: boolean): ColumnDef<PlayerRow, any>[] {
  return [
    helper.accessor("web_name", { header: "Player", cell: (i) => i.getValue() }),
    helper.accessor("position", { header: "Pos", cell: (i) => <PositionBadge position={i.getValue()} /> }),
    helper.accessor("price", { header: "Price", cell: (i) => `£${i.getValue().toFixed(1)}` }),
    // Per-90 points are a fractional rate (e.g. 7.4), not a whole count, so they need a decimal
    // place to be meaningful - raw points stay integers.
    helper.accessor("total_points", { header: "Pts", cell: (i) => fmt(per90 ? 1 : 0)(i.getValue()) }),
    helper.accessor("minutes", { header: "Mins", cell: (i) => fmt(0)(i.getValue()) }),
    helper.accessor("goals_scored", { header: "Goals", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("expected_goals", { header: "xG", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("assists", { header: "Assists", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("expected_assists", { header: "xA", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("expected_goal_involvements", { header: "xGI", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("clean_sheets", { header: "CS", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("expected_goals_conceded", { header: "xGA", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("defensive_contribution", { header: "Def. Contr.", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("bonus", { header: "Bonus", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("bps", { header: "BPS", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("saves", { header: "Saves", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("yellow_cards", { header: "YC", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("red_cards", { header: "RC", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("influence", { header: "Influence", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("creativity", { header: "Creativity", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("threat", { header: "Threat", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("ict_index", { header: "ICT Index", cell: (i) => fmt(2)(i.getValue()) }),
  ];
}

const FILTERABLE_COLUMNS = [
  "price",
  "total_points",
  "minutes",
  "goals_scored",
  "expected_goals",
  "assists",
  "expected_assists",
  "expected_goal_involvements",
  "clean_sheets",
  "expected_goals_conceded",
  "defensive_contribution",
  "bonus",
  "bps",
  "saves",
  "yellow_cards",
  "red_cards",
  "influence",
  "creativity",
  "threat",
  "ict_index",
];

interface Props {
  data: PlayerRow[];
  sort: SortSpec | null;
  onSortChange: (sort: SortSpec) => void;
  filters: NumericFilter[];
  onFiltersChange: (filters: NumericFilter[]) => void;
  positions: string[] | null;
  onPositionsChange: (positions: string[] | null) => void;
  per90: boolean;
}

export function PlayerTable({
  data,
  sort,
  onSortChange,
  filters,
  onFiltersChange,
  positions,
  onPositionsChange,
  per90,
}: Props) {
  const columns = useMemo(() => buildColumns(per90), [per90]);

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
    />
  );
}
