import { createColumnHelper, type ColumnDef } from "@tanstack/react-table";
import type { PlayerRow, SortSpec } from "../types";
import { DataTable } from "./DataTable";

const helper = createColumnHelper<PlayerRow>();

const fmt = (digits: number) => (v: number | null) => (v == null ? "-" : v.toFixed(digits));

const columns: ColumnDef<PlayerRow, any>[] = [
  helper.accessor("web_name", { header: "Player", cell: (i) => i.getValue() }),
  helper.accessor("price", { header: "Price", cell: (i) => `£${i.getValue().toFixed(1)}` }),
  helper.accessor("total_points", { header: "Pts", cell: (i) => fmt(0)(i.getValue()) }),
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
];

interface Props {
  data: PlayerRow[];
  sort: SortSpec | null;
  onSortChange: (sort: SortSpec) => void;
}

export function PlayerTable({ data, sort, onSortChange }: Props) {
  return (
    <DataTable
      data={data}
      columns={columns}
      sort={sort}
      onSortChange={onSortChange}
      getRowId={(row) => row.player_code}
    />
  );
}
