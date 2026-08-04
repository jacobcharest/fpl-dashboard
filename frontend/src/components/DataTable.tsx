import { flexRender, getCoreRowModel, useReactTable, type ColumnDef } from "@tanstack/react-table";
import type { SortSpec } from "../types";
import "./DataTable.css";

interface DataTableProps<T> {
  data: T[];
  columns: ColumnDef<T, any>[];
  sort: SortSpec | null;
  onSortChange: (sort: SortSpec) => void;
  getRowId: (row: T) => string | number;
}

export function DataTable<T>({ data, columns, sort, onSortChange, getRowId }: DataTableProps<T>) {
  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row) => String(getRowId(row)),
  });

  return (
    <div className="data-table-scroll">
      <table className="data-table">
        <thead>
          {table.getHeaderGroups().map((headerGroup) => (
            <tr key={headerGroup.id}>
              {headerGroup.headers.map((header, i) => {
                const columnId = header.column.id;
                const isSorted = sort?.column === columnId;
                return (
                  <th
                    key={header.id}
                    className={i === 0 ? "sticky-col sticky-header" : "sticky-header"}
                    onClick={() =>
                      onSortChange({
                        column: columnId,
                        direction: isSorted && sort?.direction === "desc" ? "asc" : "desc",
                      })
                    }
                  >
                    {flexRender(header.column.columnDef.header, header.getContext())}
                    {isSorted ? (sort?.direction === "desc" ? " ▼" : " ▲") : ""}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id}>
              {row.getVisibleCells().map((cell, i) => (
                <td key={cell.id} className={i === 0 ? "sticky-col" : undefined}>
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
