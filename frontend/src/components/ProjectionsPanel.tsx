import { createColumnHelper, type ColumnDef } from "@tanstack/react-table";
import { useEffect, useMemo, useState } from "react";
import { getProjectionTable } from "../api";
import type {
  NumericFilter,
  ProjectionRow,
  ProjectionSource,
  ProjectionSpec,
  ProjectionTable,
  SortSpec,
  SquadPick,
  TeamRange,
} from "../types";
import { DataTable } from "./DataTable";
import { PositionBadge } from "./PositionBadge";
import { PositionFilter } from "./PositionFilter";
import "./ProjectionsPanel.css";

const helper = createColumnHelper<ProjectionRow>();

const fmt = (digits: number) => (v: number | null) => (v == null ? "-" : v.toFixed(digits));

const FILTERABLE_COLUMNS = ["price", "xp_per_gw", "xp_per_gw_per_m", "xp_total", "xmins_avg", "xg", "xa", "xcs", "xdc"];

interface Props {
  seasonId: string;
  teamRanges: TeamRange[];
  maxGw: number;
  projSources: ProjectionSource[];
  /** Source + Weeks range. Shared with the board, whose xP column sums the same range. */
  projection: ProjectionSpec;
  onProjectionChange: (p: ProjectionSpec) => void;
  /** The panel's own position filter - separate from the board's. */
  positions: string[] | null;
  onPositionsChange: (positions: string[] | null) => void;
  squad: Map<number, SquadPick>;
  refreshNonce: number;
}

function buildColumns(range: string, squad: Map<number, SquadPick>): ColumnDef<ProjectionRow, any>[] {
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
    helper.accessor("team_name", { header: "Team", cell: (i) => i.getValue() }),
    helper.accessor("price", { header: "Price", cell: (i) => (i.getValue() == null ? "-" : `£${i.getValue()!.toFixed(1)}`) }),
    // Rates rather than totals, so players are comparable whatever the Weeks range is.
    helper.accessor("xp_per_gw", { header: "xP/GW", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("xp_per_gw_per_m", { header: "xP/£/GW", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("xp_total", { header: `xP ${range}`, cell: (i) => fmt(1)(i.getValue()) }),
    helper.accessor("xmins_avg", { header: `xMins ${range}`, cell: (i) => fmt(0)(i.getValue()) }),
    helper.accessor("xg", { header: "xG", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("xa", { header: "xA", cell: (i) => fmt(2)(i.getValue()) }),
    // Per-match probabilities summed over the window, so they read as an expected count.
    helper.accessor("xcs", { header: "xCS", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("xdc", { header: "xDC", cell: (i) => fmt(2)(i.getValue()) }),
  ];
}

export function ProjectionsPanel({
  seasonId,
  teamRanges,
  maxGw,
  projSources,
  projection,
  onProjectionChange,
  positions,
  onPositionsChange,
  squad,
  refreshNonce,
}: Props) {
  const [table, setTable] = useState<ProjectionTable>({ gameweeks: [], played_through: null, rows: [] });
  const [sort, setSort] = useState<SortSpec | null>(null);
  const [filters, setFilters] = useState<NumericFilter[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!seasonId || !projection.source) return;
    setError(null);
    getProjectionTable({
      season_id: seasonId,
      teams: teamRanges,
      opponent_team_codes: null,
      filters,
      sort,
      per90: false,
      starts_only: false,
      positions,
      projection_source: projection.source,
      projection_start_gw: projection.start_gw,
      projection_end_gw: projection.end_gw,
    })
      .then(setTable)
      .catch((err) => setError(err?.response?.data?.detail ?? err?.message ?? "Unknown error"));
  }, [seasonId, teamRanges, positions, projection, filters, sort, refreshNonce]);

  const range = `GW${projection.start_gw}-${projection.end_gw}`;
  const columns = useMemo(() => buildColumns(range, squad), [range, squad]);

  if (projSources.length === 0) return null;

  const firstGw = table.gameweeks[0];
  const lastGw = table.gameweeks[table.gameweeks.length - 1];
  const covered = table.gameweeks.length ? `GW${firstGw}-${lastGw}` : null;
  // The chosen range reaches past what the source has projected - the totals only cover the overlap.
  const partial = covered !== null && (projection.start_gw < firstGw || projection.end_gw > lastGw);
  const none = covered !== null && (projection.end_gw < firstGw || projection.start_gw > lastGw);
  const setWeek = (key: "start_gw" | "end_gw", raw: string) => {
    const n = Number(raw);
    if (Number.isInteger(n)) onProjectionChange({ ...projection, [key]: n });
  };

  return (
    <section className="projections-panel">
      <div className="projections-panel-header">
        <h2>Projections</h2>
        <span className="projections-meta">
          {covered && (
            <>
              {projection.source} covers {covered}
              {table.played_through != null && firstGw <= table.played_through && (
                <> (GW{firstGw}-{Math.min(table.played_through, lastGw)} already played)</>
              )}
              {" · "}
            </>
          )}
          {table.rows.length} players
        </span>
      </div>

      <div className="projections-layout">
        <aside className="projections-filter">
          <div className="sidebar-section">
            <div className="section-title">Source</div>
            <select
              className="proj-source"
              value={projection.source ?? ""}
              onChange={(e) => onProjectionChange({ ...projection, source: e.target.value })}
            >
              {projSources.map((s) => (
                <option key={s.source} value={s.source}>
                  {s.source} (GW{s.first_gw}-{s.last_gw})
                </option>
              ))}
            </select>
          </div>
          <div className="sidebar-section">
            <div className="section-title">Filters</div>
            <div className="global-range">
              <span>Weeks</span>
              <input
                type="number"
                min={1}
                max={maxGw}
                value={projection.start_gw}
                onChange={(e) => setWeek("start_gw", e.target.value)}
              />
              <span>to</span>
              <input
                type="number"
                min={1}
                max={maxGw}
                value={projection.end_gw}
                onChange={(e) => setWeek("end_gw", e.target.value)}
              />
            </div>
            <div className="global-range">
              <span>Position</span>
              <PositionFilter selected={positions} onChange={onPositionsChange} />
            </div>
          </div>
        </aside>

        <div className="projections-table">
          {error && <div className="fetch-error">Couldn't load projections: {error}</div>}
          {none && (
            <div className="projections-note">
              {projection.source} has no projections for {range}; it covers {covered}. Import a newer file
              or move the Weeks range.
            </div>
          )}
          {partial && !none && (
            <div className="projections-note">
              {projection.source} only covers {covered}, so these totals include just the gameweeks inside
              {" "}
              {range} that it projected.
            </div>
          )}
          <DataTable
            data={table.rows}
            columns={columns}
            sort={sort}
            onSortChange={setSort}
            getRowId={(row) => row.player_code}
            filterableColumnIds={FILTERABLE_COLUMNS}
            filters={filters}
            onFiltersChange={setFilters}
            customFilterColumns={{
              position: <PositionFilter selected={positions} onChange={onPositionsChange} />,
            }}
            getRowClassName={(row) => {
              const pick = squad.get(row.player_code);
              if (!pick) return undefined;
              return pick.squad_slot > 11 ? "my-team my-team-bench" : "my-team";
            }}
          />
        </div>
      </div>
    </section>
  );
}
