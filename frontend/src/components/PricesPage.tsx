import { createColumnHelper, type ColumnDef } from "@tanstack/react-table";
import { useEffect, useMemo, useState } from "react";
import { getPriceTable } from "../api";
import type { NumericFilter, PriceRow, PriceTable, SortSpec, SquadPick } from "../types";
import { DataTable } from "./DataTable";
import { PositionBadge } from "./PositionBadge";
import { PositionFilter } from "./PositionFilter";
import "./PricesPage.css";

const helper = createColumnHelper<PriceRow>();

const EMPTY: PriceTable = {
  season_id: "",
  price_day: null,
  captured_at: null,
  event: null,
  prev_day: null,
  week_day: null,
  first_day: null,
  snapshot_days: 0,
  total_players: null,
  unlisted: 0,
  squad: null,
  rows: [],
  warning: null,
};

const STATUS_LABEL: Record<string, string> = {
  d: "Doubtful",
  i: "Injured",
  s: "Suspended",
  u: "Unavailable",
  n: "Not eligible",
};

const money = (v: number | null) => (v == null ? "-" : `£${v.toFixed(1)}`);
const fmtPct = (v: number | null) => (v == null ? "-" : `${v.toFixed(1)}%`);
const fmtCount = (v: number | null) => (v == null ? "-" : v.toLocaleString());
const shortDay = (day: string | null) =>
  day ? new Date(`${day}T00:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "";

/** A signed number, coloured by direction. Zero is dimmed: on this page "didn't move" is the
    common case and shouldn't compete with the rows that did. */
function Delta({ value, format }: { value: number | null; format: (abs: number) => string }) {
  if (value == null) return <span className="delta-none">-</span>;
  if (value === 0) return <span className="delta-flat">{format(0)}</span>;
  return (
    <span className={value > 0 ? "delta-up" : "delta-down"}>
      {value > 0 ? "+" : "−"}
      {format(Math.abs(value))}
    </span>
  );
}

const priceDelta = (v: number | null) => <Delta value={v} format={(a) => a.toFixed(1)} />;
const countDelta = (v: number | null) => <Delta value={v} format={(a) => a.toLocaleString()} />;

const head = (label: string, title: string) => () => <span title={title}>{label}</span>;

const BASIS_NOTE: Record<PriceRow["since_basis"], string> = {
  change: "since the day before their last price change",
  gameweek: "this gameweek (no price change in it)",
  tracking: "since tracking began - no price change seen yet, so a lower bound",
};

// Left to right: who, what they cost, how close they look to moving, then the detail behind it.
// The signal columns sit beside Price because the table is wider than most screens and the far
// right is a scroll away.
function buildColumns(table: PriceTable, squad: Map<number, SquadPick>, squadFirst: boolean): ColumnDef<PriceRow, any>[] {
  const columns: ColumnDef<PriceRow, any>[] = [
    helper.accessor("web_name", {
      header: "Player",
      cell: (i) => {
        const row = i.row.original;
        const pick = squad.get(row.player_code);
        const armband = pick?.is_captain ? "C" : pick?.is_vice_captain ? "V" : null;
        const flag = row.status ? STATUS_LABEL[row.status] : undefined;
        return (
          <>
            {i.getValue()}
            {armband && <span className={`armband armband-${armband.toLowerCase()}`}>{armband}</span>}
            {flag && (
              <span className={`status-flag status-${row.status}`} title={flag}>
                !
              </span>
            )}
          </>
        );
      },
    }),
    helper.accessor("position", { header: "Pos", cell: (i) => <PositionBadge position={i.getValue()} /> }),
    helper.accessor("team_name", { header: "Team", cell: (i) => i.getValue() ?? "-" }),
    helper.accessor("price", { header: "Price", cell: (i) => money(i.getValue()) }),
    helper.accessor("net_since_change", {
      header: head("Net since Δ", "Net transfers since the player's price last changed - the counter FPL's next change is decided on. Hover a cell for what it's counted from."),
      cell: (i) => <span title={`Counted ${BASIS_NOTE[i.row.original.since_basis]}`}>{countDelta(i.getValue())}</span>,
    }),
    helper.accessor("pressure", {
      header: head("Pressure", "Net transfers since the last price change, as a % of the managers who own the player. A ranking signal, not a prediction: FPL's thresholds aren't public. Blank under ~1,000 owners, where it's noise."),
      cell: (i) => <Delta value={i.getValue() == null ? null : Math.round(i.getValue() * 100) / 100} format={(a) => `${a.toFixed(2)}%`} />,
    }),
    helper.accessor("change_day", {
      header: head("Δ Day", table.prev_day ? `Price change since the ${shortDay(table.prev_day)} snapshot` : "Needs a second day of snapshots"),
      cell: (i) => priceDelta(i.getValue()),
    }),
    helper.accessor("change_week", {
      header: head("Δ Week", table.week_day ? `Price change since the ${shortDay(table.week_day)} snapshot` : "Needs a snapshot at least 7 days old"),
      cell: (i) => priceDelta(i.getValue()),
    }),
    helper.accessor("change_event", {
      header: head("Δ GW", "Price change this gameweek, as FPL reports it"),
      cell: (i) => priceDelta(i.getValue()),
    }),
    helper.accessor("change_start", {
      header: head("Δ Season", "Price change since the season started"),
      cell: (i) => priceDelta(i.getValue()),
    }),
    helper.accessor("selected_by_percent", { header: "Own%", cell: (i) => fmtPct(i.getValue()) }),
    helper.accessor("transfers_in_event", { header: head("In", "Transfers in this gameweek"), cell: (i) => fmtCount(i.getValue()) }),
    helper.accessor("transfers_out_event", { header: head("Out", "Transfers out this gameweek"), cell: (i) => fmtCount(i.getValue()) }),
    helper.accessor("net_event", { header: head("Net GW", "Transfers in minus out, this gameweek"), cell: (i) => countDelta(i.getValue()) }),
    helper.accessor("net_day", {
      header: head("Net Day", table.prev_day ? `Net transfers since the ${shortDay(table.prev_day)} snapshot` : "Needs a second day of snapshots"),
      cell: (i) => countDelta(i.getValue()),
    }),
  ];
  if (table.squad) {
    // Paid / Sell / Profit only mean something for your own 15, so they trail the table - until
    // it's filtered to the squad, when they're the reason you're looking.
    columns.splice(
      squadFirst ? 4 : columns.length,
      0,
      helper.accessor("purchase_price", {
        header: head("Paid", "What you bought them for (from your public transfer history)"),
        cell: (i) => (
          <span title={i.row.original.purchase_estimated ? "Estimated: your entry joined after GW1, so this is that gameweek's price" : undefined}>
            {money(i.getValue())}
            {i.row.original.purchase_estimated && i.getValue() != null ? "*" : ""}
          </span>
        ),
      }),
      helper.accessor("selling_price", {
        header: head("Sell", "What you'd get today: you keep half of any rise (rounded down to 0.1) and all of any fall"),
        cell: (i) => money(i.getValue()),
      }),
      helper.accessor("profit", { header: head("Profit", "Sell minus Paid - the value you've actually banked"), cell: (i) => priceDelta(i.getValue()) })
    );
  }
  return columns;
}

const FILTERABLE_COLUMNS = [
  "price",
  "change_day",
  "change_week",
  "change_event",
  "change_start",
  "selected_by_percent",
  "transfers_in_event",
  "transfers_out_event",
  "net_event",
  "net_day",
  "net_since_change",
  "pressure",
  "purchase_price",
  "selling_price",
  "profit",
];

interface Props {
  seasonId: string;
  squad: Map<number, SquadPick>;
  refreshNonce: number;
}

export function PricesPage({ seasonId, squad, refreshNonce }: Props) {
  const [table, setTable] = useState<PriceTable>(EMPTY);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The whole season's players arrive in one payload, so sorting and filtering happen here
  // rather than round-tripping like the stats tables (whose rows are aggregated server-side).
  const [sort, setSort] = useState<SortSpec | null>({ column: "pressure", direction: "desc" });
  const [filters, setFilters] = useState<NumericFilter[]>([]);
  const [positions, setPositions] = useState<string[] | null>(null);
  const [search, setSearch] = useState("");
  const [squadOnly, setSquadOnly] = useState(false);

  // `squad` is a dependency so a re-sync (new purchase prices) reloads the table.
  useEffect(() => {
    if (!seasonId) return;
    setLoading(true);
    setError(null);
    getPriceTable(seasonId)
      .then(setTable)
      .catch((err) => setError(err?.response?.data?.detail ?? err?.message ?? "Unknown error"))
      .finally(() => setLoading(false));
  }, [seasonId, squad, refreshNonce]);

  const columns = useMemo(() => buildColumns(table, squad, squadOnly), [table, squad, squadOnly]);

  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const kept = table.rows.filter((r) => {
      if (squadOnly && r.squad_slot == null) return false;
      if (positions && !positions.includes(r.position)) return false;
      if (needle && !`${r.web_name} ${r.team_name ?? ""}`.toLowerCase().includes(needle)) return false;
      return filters.every((f) => {
        const v = r[f.column as keyof PriceRow];
        if (typeof v !== "number") return false;
        return f.op === "gt" ? v > f.value : v < f.value;
      });
    });
    if (!sort) return kept;
    const key = sort.column as keyof PriceRow;
    const dir = sort.direction === "desc" ? -1 : 1;
    // Blanks sink whichever way the column is sorted - they're "unknown", not "lowest".
    return [...kept].sort((a, b) => {
      const x = a[key];
      const y = b[key];
      if (x == null || y == null) return x == null ? (y == null ? 0 : 1) : -1;
      return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y))) * dir;
    });
  }, [table.rows, sort, filters, positions, search, squadOnly]);

  const sq = table.squad;
  const bank = sq?.bank ?? 0;

  return (
    <section className="prices-page">
      <div className="prices-header">
        <h2>Prices</h2>
        <span className="prices-meta">
          {table.price_day ? (
            <>
              Price day {shortDay(table.price_day)}
              {table.captured_at && <> · read {new Date(table.captured_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</>}
              {" · "}
              {table.snapshot_days} {table.snapshot_days === 1 ? "day" : "days"} tracked
              {table.first_day && table.snapshot_days > 1 && <> since {shortDay(table.first_day)}</>}
              {" · "}
              {rows.length} of {table.rows.length} players
            </>
          ) : loading ? (
            "Loading…"
          ) : (
            "No snapshots"
          )}
        </span>
        {loading && table.price_day && <span className="loading">Updating…</span>}
      </div>

      {sq && (
        <div className="value-tiles">
          <div className="value-tile" title="Squad at today's prices plus the bank - the figure FPL shows as your team value">
            <span className="value-tile-label">Team value</span>
            <span className="value-tile-number">{money(sq.market_value + bank)}</span>
          </div>
          <div className="value-tile" title="What you could actually spend: every player at their selling price, plus the bank">
            <span className="value-tile-label">Sale value</span>
            <span className="value-tile-number">{sq.selling_value == null ? "-" : money(sq.selling_value + bank)}</span>
          </div>
          <div className="value-tile" title="Selling prices minus what you paid: rises you've banked, less falls">
            <span className="value-tile-label">Banked profit</span>
            <span className="value-tile-number">
              {sq.selling_value == null || sq.purchase_value == null
                ? "-"
                : priceDelta(Math.round((sq.selling_value - sq.purchase_value) * 10) / 10)}
            </span>
          </div>
          <div className="value-tile" title="Rises FPL would keep if you sold today: team value minus sale value">
            <span className="value-tile-label">Lost to the 50% rule</span>
            <span className="value-tile-number">
              {sq.selling_value == null ? "-" : money(Math.round((sq.market_value - sq.selling_value) * 10) / 10)}
            </span>
          </div>
          <div className="value-tile">
            <span className="value-tile-label">Bank</span>
            <span className="value-tile-number">{money(sq.bank)}</span>
          </div>
        </div>
      )}

      <div className="prices-controls">
        <input
          type="search"
          className="prices-search"
          placeholder="Search player or team…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <div className="global-range">
          <span>Position</span>
          <PositionFilter selected={positions} onChange={setPositions} />
        </div>
        {sq && (
          <label className={`gw-check${squadOnly ? " gw-check-on" : ""}`}>
            <input type="checkbox" checked={squadOnly} onChange={(e) => setSquadOnly(e.target.checked)} />
            My squad only
          </label>
        )}
      </div>

      {error && <div className="fetch-error">Couldn't load prices: {error}</div>}
      {table.warning && <div className="prices-note prices-note-warning">{table.warning}</div>}
      {!loading && !error && table.price_day === null && (
        <div className="prices-note">
          No price snapshots for this season. FPL's API has no price history, so movement can only be
          shown for days that were captured while the season was live - switch to the current season.
        </div>
      )}
      {table.price_day !== null && table.snapshot_days < 2 && (
        <div className="prices-note">
          Tracking started today, so <b>Δ Day</b>, <b>Δ Week</b> and <b>Net Day</b> are blank until there's
          an earlier snapshot to compare with. Everything else is live now.
        </div>
      )}
      {sq && sq.selling_value == null && (
        <div className="prices-note">Press "Sync My Team" again to load what you paid for each player.</div>
      )}
      {table.unlisted > 0 && (
        <div className="prices-note">
          {table.unlisted} {table.unlisted === 1 ? "player is" : "players are"} new to FPL since the last
          data fetch - "Fetch New Data" adds them.
        </div>
      )}

      <DataTable
        data={rows}
        columns={columns}
        sort={sort}
        onSortChange={setSort}
        getRowId={(row) => row.player_code}
        filterableColumnIds={FILTERABLE_COLUMNS}
        filters={filters}
        onFiltersChange={setFilters}
        customFilterColumns={{ position: <PositionFilter selected={positions} onChange={setPositions} /> }}
        getRowClassName={(row) => {
          if (row.squad_slot == null) return undefined;
          return row.squad_slot > 11 ? "my-team my-team-bench" : "my-team";
        }}
      />
    </section>
  );
}
