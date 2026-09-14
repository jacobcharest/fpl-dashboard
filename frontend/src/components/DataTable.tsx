import { flexRender, getCoreRowModel, useReactTable, type ColumnDef } from "@tanstack/react-table";
import { type ReactNode, useState } from "react";
import type { NumericFilter, SortSpec } from "../types";
import "./DataTable.css";

interface DataTableProps<T> {
  data: T[];
  columns: ColumnDef<T, any>[];
  sort: SortSpec | null;
  onSortChange: (sort: SortSpec) => void;
  getRowId: (row: T) => string | number;
  filterableColumnIds: string[];
  filters: NumericFilter[];
  onFiltersChange: (filters: NumericFilter[]) => void;
  customFilterColumns?: Record<string, ReactNode>;
  /** Extra class(es) for a body row - used to highlight the user's own squad. */
  getRowClassName?: (row: T) => string | undefined;
}

function FilterCell({
  columnId,
  filters,
  onFiltersChange,
}: {
  columnId: string;
  filters: NumericFilter[];
  onFiltersChange: (filters: NumericFilter[]) => void;
}) {
  const gt = filters.find((f) => f.column === columnId && f.op === "gt");
  const lt = filters.find((f) => f.column === columnId && f.op === "lt");
  const [gtText, setGtText] = useState(gt ? String(gt.value) : "");
  const [ltText, setLtText] = useState(lt ? String(lt.value) : "");

  const commit = (op: "gt" | "lt", text: string) => {
    const rest = filters.filter((f) => !(f.column === columnId && f.op === op));
    const value = text.trim() === "" ? null : Number(text);
    onFiltersChange(value === null || Number.isNaN(value) ? rest : [...rest, { column: columnId, op, value }]);
  };

  return (
    <th className="sticky-filter-row filter-cell" data-col={columnId}>
      <input
        type="number"
        placeholder=">"
        value={gtText}
        onChange={(e) => setGtText(e.target.value)}
        onBlur={() => commit("gt", gtText)}
        onKeyDown={(e) => e.key === "Enter" && commit("gt", gtText)}
      />
      <input
        type="number"
        placeholder="<"
        value={ltText}
        onChange={(e) => setLtText(e.target.value)}
        onBlur={() => commit("lt", ltText)}
        onKeyDown={(e) => e.key === "Enter" && commit("lt", ltText)}
      />
    </th>
  );
}

export function DataTable<T>({
  data,
  columns,
  sort,
  onSortChange,
  getRowId,
  filterableColumnIds,
  filters,
  onFiltersChange,
  customFilterColumns = {},
  getRowClassName,
}: DataTableProps<T>) {
  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row) => String(getRowId(row)),
  });
  // The > < filter row is collapsible. Open by default on a desktop, where it costs little;
  // collapsed on a phone (mobile.css's breakpoint), where it doubles the header's height.
  const [filtersOpen, setFiltersOpen] = useState(() => !window.matchMedia("(max-width: 720px)").matches);
  const activeFilters = filters.length;

  return (
    <div className="data-table-scroll">
      <table className="data-table">
        <thead>
          {table.getHeaderGroups().map((headerGroup) => (
            <tr key={headerGroup.id}>
              {headerGroup.headers.map((header, i) => {
                const columnId = header.column.id;
                const isSorted = sort?.column === columnId;
                const classes = [i === 0 ? "sticky-col" : "", "sticky-header", isSorted ? "col-sorted" : ""]
                  .filter(Boolean)
                  .join(" ");
                return (
                  <th
                    key={header.id}
                    className={classes}
                    data-col={columnId}
                    onClick={() =>
                      onSortChange({
                        column: columnId,
                        direction: isSorted && sort?.direction === "desc" ? "asc" : "desc",
                      })
                    }
                  >
                    {flexRender(header.column.columnDef.header, header.getContext())}
                    {isSorted ? (sort?.direction === "desc" ? " ▼" : " ▲") : ""}
                    {i === 0 && (
                      <button
                        type="button"
                        className={`filter-toggle${activeFilters > 0 ? " filter-toggle-active" : ""}`}
                        title={filtersOpen ? "Hide the filter row" : "Show the filter row"}
                        onClick={(e) => {
                          e.stopPropagation(); // the header cell itself sorts
                          setFiltersOpen((o) => !o);
                        }}
                      >
                        {filtersOpen ? "▾" : "▸"} Filters
                        {activeFilters > 0 && <span className="filter-count">{activeFilters}</span>}
                      </button>
                    )}
                  </th>
                );
              })}
            </tr>
          ))}
          {filtersOpen && (
          <tr className="filter-row">
            {table.getHeaderGroups()[0].headers.map((header, i) => {
              const columnId = header.column.id;
              if (i === 0) {
                return <th key={header.id} className="sticky-col sticky-filter-row filter-cell" data-col={columnId} />;
              }
              if (customFilterColumns[columnId]) {
                return (
                  <th key={header.id} className="sticky-filter-row filter-cell" data-col={columnId}>
                    {customFilterColumns[columnId]}
                  </th>
                );
              }
              if (!filterableColumnIds.includes(columnId)) {
                return <th key={header.id} className="sticky-filter-row filter-cell" data-col={columnId} />;
              }
              return (
                <FilterCell key={header.id} columnId={columnId} filters={filters} onFiltersChange={onFiltersChange} />
              );
            })}
          </tr>
          )}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id} className={getRowClassName?.(row.original)}>
              {row.getVisibleCells().map((cell, i) => (
                <td key={cell.id} className={i === 0 ? "sticky-col" : undefined} data-col={cell.column.id}>
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
