import { createColumnHelper, type ColumnDef } from "@tanstack/react-table";
import { useEffect, useMemo, useState } from "react";
import { getProjectionTable } from "../api";
import { gameweekLabel } from "../types";
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
import { useFitToViewport } from "../useFitToViewport";
import { DataTable } from "./DataTable";
import { PositionBadge } from "./PositionBadge";
import { PositionFilter } from "./PositionFilter";
import "./ProjectionsPanel.css";

const helper = createColumnHelper<ProjectionRow>();

const fmt = (digits: number) => (v: number | null) => (v == null ? "-" : v.toFixed(digits));

const FILTERABLE_COLUMNS = ["price", "sos", "xp_per_gw", "xp_per_gw_per_m", "xp_total", "xmins_avg", "xg", "xa", "xcs", "xdc"];

interface Props {
  seasonId: string;
  teamRanges: TeamRange[];
  projSources: ProjectionSource[];
  /** Source + ticked gameweeks. Shared with the board, whose xP column sums the same set. */
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
    helper.accessor("sos", {
      header: () => (
        <span title="Strength of schedule: the average of FPL's fixture difficulty ratings (1 easy - 5 hard) over the ticked gameweeks. Lower is kinder. Hover a value for the fixtures. It's per club, and one rating per fixture - the same for a defender and a forward.">
          SoS
        </span>
      ),
      cell: (i) => {
        const v = i.getValue() as number | null;
        if (v == null) return "-";
        const row = i.row.original;
        const tone = v <= 2.6 ? "sos-easy" : v >= 3.4 ? "sos-hard" : "";
        return (
          <span className={`sos ${tone}`} title={`${row.fixtures ?? ""}${row.fixture_count != null ? ` · ${row.fixture_count} fixtures` : ""}`}>
            {v.toFixed(2)}
          </span>
        );
      },
    }),
    // Rates rather than totals, so players are comparable whatever the Weeks range is.
    helper.accessor("xp_per_gw", { header: "xP/GW", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("xp_per_gw_per_m", { header: "xP/£/GW", cell: (i) => fmt(2)(i.getValue()) }),
    helper.accessor("xp_total", { header: range ? `xP ${range}` : "xP", cell: (i) => fmt(1)(i.getValue()) }),
    helper.accessor("xmins_avg", { header: range ? `xMins ${range}` : "xMins", cell: (i) => fmt(0)(i.getValue()) }),
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
  projSources,
  projection,
  onProjectionChange,
  positions,
  onPositionsChange,
  squad,
  refreshNonce,
}: Props) {
  const [table, setTable] = useState<ProjectionTable>({ gameweeks: [], played_through: null, rows: [] });
  // Matches the backend's fallback, stated here so the header shows which column is sorted.
  const [sort, setSort] = useState<SortSpec | null>({ column: "xp_total", direction: "desc" });
  const [filters, setFilters] = useState<NumericFilter[]>([]);
  const [error, setError] = useState<string | null>(null);
  const fitRef = useFitToViewport<HTMLDivElement>();

  useEffect(() => {
    if (!seasonId || !projection.source) return;
    setError(null);
    getProjectionTable({
      season_id: seasonId,
      teams: teamRanges,
      opponent_team_codes: null,
      filters,
      sort,
      per_start: false,
      positions,
      projection_source: projection.source,
      projection_gameweeks: projection.gameweeks,
    })
      .then(setTable)
      .catch((err) => setError(err?.response?.data?.detail ?? err?.message ?? "Unknown error"));
  }, [seasonId, teamRanges, positions, projection, filters, sort, refreshNonce]);

  const range = gameweekLabel(projection.gameweeks) ?? "";
  const columns = useMemo(() => buildColumns(range, squad), [range, squad]);

  // One checkbox per gameweek the source covers, plus anything ticked outside that so it can
  // still be unticked. The source list is known before the first fetch, so the row doesn't pop in.
  const source = projSources.find((s) => s.source === projection.source);
  const choices = useMemo(() => {
    const gws = new Set<number>(projection.gameweeks);
    if (source) for (let g = source.first_gw; g <= source.last_gw; g++) gws.add(g);
    return [...gws].sort((a, b) => a - b);
  }, [source, projection.gameweeks]);

  if (projSources.length === 0) {
    return (
      <section className="projections-panel">
        <div className="projections-panel-header">
          <h2>Projections</h2>
        </div>
        <div className="projections-note">
          No projections loaded for this season. Import a CSV with{" "}
          <code>backend/scripts/import_projections.py</code> - see the README.
        </div>
      </section>
    );
  }

  const firstGw = table.gameweeks[0];
  const lastGw = table.gameweeks[table.gameweeks.length - 1];
  const covered = table.gameweeks.length ? `GW${firstGw}-${lastGw}` : null;
  // What's left to plan with: gameweeks the source projected that haven't been played yet.
  const upcoming = table.gameweeks.filter((g) => table.played_through == null || g > table.played_through).length;
  const selected = new Set(projection.gameweeks);
  const inside = table.gameweeks.filter((g) => selected.has(g)).length;
  const none = covered !== null && selected.size > 0 && inside === 0;
  const setGameweeks = (gameweeks: number[]) =>
    onProjectionChange({ ...projection, gameweeks: [...gameweeks].sort((a, b) => a - b) });
  const toggle = (gw: number) =>
    setGameweeks(selected.has(gw) ? projection.gameweeks.filter((g) => g !== gw) : [...projection.gameweeks, gw]);
  const isPlayed = (gw: number) => table.played_through != null && gw <= table.played_through;
  const unplayed = choices.filter((g) => !isPlayed(g));

  return (
    <section className="projections-panel">
      <div className="projections-panel-header">
        <h2>Projections</h2>
        <span className="projections-meta">
          {covered && (
            <span title={`${projection.source} has projections for ${covered}`}>
              {upcoming > 0
                ? `${projection.source} covers the next ${upcoming} ${upcoming === 1 ? "week" : "weeks"}`
                : `${projection.source} has no upcoming weeks - import a newer file`}
              {" · "}
            </span>
          )}
          {table.rows.length} players
        </span>
      </div>

      <div className="projections-layout">
        <aside className="projections-filter">
          <div className="sidebar-section">
            <div className="section-title">Filters</div>
            <div className="global-range gw-picker">
              <span>Weeks</span>
              {choices.map((gw) => (
                <label
                  key={gw}
                  className={`gw-check${selected.has(gw) ? " gw-check-on" : ""}${isPlayed(gw) ? " gw-check-played" : ""}`}
                  title={isPlayed(gw) ? `GW${gw} has already been played` : `GW${gw}`}
                >
                  <input type="checkbox" checked={selected.has(gw)} onChange={() => toggle(gw)} />
                  {gw}
                </label>
              ))}
              <button type="button" onClick={() => setGameweeks(unplayed)} title="Tick every unplayed gameweek the source covers">
                All
              </button>
              <button type="button" onClick={() => setGameweeks([])}>
                None
              </button>
            </div>
            <div className="global-range">
              <span>Position</span>
              <PositionFilter selected={positions} onChange={onPositionsChange} />
            </div>
          </div>
        </aside>

        <div className="projections-table">
          {error && <div className="fetch-error">Couldn't load projections: {error}</div>}
          {selected.size === 0 && (
            <div className="projections-note">Tick at least one gameweek to total projections over.</div>
          )}
          {none && (
            <div className="projections-note">
              {projection.source} has no projections for {range}; it covers {covered}. Import a newer file
              or tick different weeks.
            </div>
          )}
          <div className="fit-table" ref={fitRef}>
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
      </div>
    </section>
  );
}
