import { createColumnHelper, type ColumnDef } from "@tanstack/react-table";
import type { NumericFilter, SortSpec, TeamRow } from "../types";
import { DataTable } from "./DataTable";

const helper = createColumnHelper<TeamRow>();

const fmt = (digits: number) => (v: number | null) => (v == null ? "-" : v.toFixed(digits));

const columns: ColumnDef<TeamRow, any>[] = [
  helper.accessor("name", { header: "Team", cell: (i) => i.getValue() }),
  helper.accessor("table_place", { header: "Place", cell: (i) => i.getValue() }),
  helper.accessor("goals_scored", { header: "Goals", cell: (i) => fmt(0)(i.getValue()) }),
  helper.accessor("expected_goals", { header: "xG", cell: (i) => fmt(2)(i.getValue()) }),
  helper.accessor("goals_conceded", { header: "Goals Against", cell: (i) => fmt(0)(i.getValue()) }),
  helper.accessor("expected_goals_conceded", { header: "xGA", cell: (i) => fmt(2)(i.getValue()) }),
  helper.accessor("goal_difference", { header: "GD", cell: (i) => fmt(0)(i.getValue()) }),
  helper.accessor("opponent_expected_goals", { header: "Opponent xG", cell: (i) => fmt(2)(i.getValue()) }),
];

const FILTERABLE_COLUMNS = [
  "table_place",
  "goals_scored",
  "expected_goals",
  "goals_conceded",
  "expected_goals_conceded",
  "goal_difference",
  "opponent_expected_goals",
];

interface Props {
  data: TeamRow[];
  sort: SortSpec | null;
  onSortChange: (sort: SortSpec) => void;
  filters: NumericFilter[];
  onFiltersChange: (filters: NumericFilter[]) => void;
}

export function TeamTable({ data, sort, onSortChange, filters, onFiltersChange }: Props) {
  return (
    <DataTable
      data={data}
      columns={columns}
      sort={sort}
      onSortChange={onSortChange}
      getRowId={(row) => row.team_code}
      filterableColumnIds={FILTERABLE_COLUMNS}
      filters={filters}
      onFiltersChange={onFiltersChange}
    />
  );
}
