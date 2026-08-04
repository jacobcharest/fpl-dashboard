import { useEffect, useMemo, useState } from "react";
import Plot from "react-plotly.js";
import { getChartSeries } from "../api";
import { computeValueStat, statLabel, statsFor, STACKED_BREAKDOWN_STATS } from "../chartStats";
import type { ChartType, PlayerRow, SeriesPoint, TeamRange, TeamRow } from "../types";
import { EntityPicker } from "./EntityPicker";
import { PALETTE, baseLayout, plotConfig } from "./charts/plotlyTheme";
import "./ChartCard.css";

const CHART_TYPES: { value: ChartType; label: string }[] = [
  { value: "timeseries", label: "Time Series" },
  { value: "scatter", label: "Scatter" },
  { value: "ranked_bar", label: "Ranked Bar" },
  { value: "radar", label: "Radar / Spider" },
  { value: "heatmap", label: "Heatmap" },
  { value: "distribution", label: "Distribution / Box Plot" },
  { value: "stacked", label: "Stacked Breakdown" },
  { value: "small_multiples", label: "Small Multiples" },
];

interface Props {
  entityType: "player" | "team";
  rows: (PlayerRow | TeamRow)[];
  seasonId: string;
  teamRanges: TeamRange[];
  opponentTeamCodes: number[] | null;
  per90: boolean;
  startsOnly: boolean;
  onRemove: () => void;
}

function entityCode(row: PlayerRow | TeamRow): number {
  return "player_code" in row ? row.player_code : row.team_code;
}
function entityName(row: PlayerRow | TeamRow): string {
  return "web_name" in row ? row.web_name : row.name;
}

function getStatValue(row: PlayerRow | TeamRow, entityType: "player" | "team", stat: string): number | null {
  if (entityType === "player" && stat === "value") return computeValueStat(row as PlayerRow);
  const v = (row as any)[stat];
  return v === undefined || v === null ? null : v;
}

export function ChartCard({
  entityType,
  rows,
  seasonId,
  teamRanges,
  opponentTeamCodes,
  per90,
  startsOnly,
  onRemove,
}: Props) {
  const [chartType, setChartType] = useState<ChartType>("timeseries");
  const stats = statsFor(entityType);
  const seriesStats = stats.filter((s) => s.seriesEligible);

  const [selected, setSelected] = useState<number[]>([]);
  const [stat, setStat] = useState(seriesStats[0]?.key ?? stats[0].key);
  const [statX, setStatX] = useState(stats[0].key);
  const [statY, setStatY] = useState(stats[1]?.key ?? stats[0].key);
  const [radarStats, setRadarStats] = useState<string[]>(seriesStats.slice(0, 5).map((s) => s.key));
  const [cumulative, setCumulative] = useState(true);
  const [topN, setTopN] = useState(20);
  const [sortDesc, setSortDesc] = useState(true);
  const [normalize, setNormalize] = useState(true);
  const [referenceLine, setReferenceLine] = useState(false);

  const [seriesData, setSeriesData] = useState<SeriesPoint[]>([]);
  const [loading, setLoading] = useState(false);

  const entities = useMemo(() => rows.map((r) => ({ code: entityCode(r), name: entityName(r) })), [rows]);

  // Each chart type has its own entity-selection semantics (e.g. radar caps at 5); starting
  // fresh on type switch avoids a stale selection silently blocking new picks.
  useEffect(() => {
    setSelected([]);
  }, [chartType]);

  const needsSeries = ["timeseries", "heatmap", "stacked", "small_multiples"].includes(chartType);
  const seriesStatsNeeded = chartType === "stacked" ? STACKED_BREAKDOWN_STATS : [stat];

  const depsKey = JSON.stringify({ seasonId, teamRanges, opponentTeamCodes, per90, startsOnly, selected, seriesStatsNeeded, chartType });

  useEffect(() => {
    if (!needsSeries || selected.length === 0 || !seasonId) {
      setSeriesData([]);
      return;
    }
    setLoading(true);
    getChartSeries({
      season_id: seasonId,
      teams: teamRanges,
      opponent_team_codes: opponentTeamCodes,
      filters: [],
      sort: null,
      entity_type: entityType,
      entity_codes: selected,
      stats: seriesStatsNeeded,
      per90,
      starts_only: startsOnly,
    })
      .then(setSeriesData)
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [depsKey, needsSeries]);

  const nameByCode = useMemo(() => new Map(entities.map((e) => [e.code, e.name])), [entities]);

  return (
    <div className="chart-card">
      <div className="chart-card-header">
        <select value={chartType} onChange={(e) => setChartType(e.target.value as ChartType)}>
          {CHART_TYPES.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </select>
        {loading && <span className="loading">Loading…</span>}
        <button className="remove-btn" onClick={onRemove}>
          ✕
        </button>
      </div>

      <div className="chart-card-body">
        <div className="chart-config">
          {chartType === "timeseries" && (
            <>
              <label>
                Stat
                <select value={stat} onChange={(e) => setStat(e.target.value)}>
                  {seriesStats.map((s) => (
                    <option key={s.key} value={s.key}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="toggle-row">
                <input type="checkbox" checked={cumulative} onChange={(e) => setCumulative(e.target.checked)} />
                Cumulative
              </label>
              <EntityPicker entities={entities} selected={selected} onChange={setSelected} />
            </>
          )}

          {chartType === "scatter" && (
            <>
              <label>
                Stat X
                <select value={statX} onChange={(e) => setStatX(e.target.value)}>
                  {stats.map((s) => (
                    <option key={s.key} value={s.key}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Stat Y
                <select value={statY} onChange={(e) => setStatY(e.target.value)}>
                  {stats.map((s) => (
                    <option key={s.key} value={s.key}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="toggle-row">
                <input type="checkbox" checked={referenceLine} onChange={(e) => setReferenceLine(e.target.checked)} />
                y = x reference line
              </label>
              <EntityPicker entities={entities} selected={selected} onChange={setSelected} />
            </>
          )}

          {chartType === "ranked_bar" && (
            <>
              <label>
                Stat
                <select value={stat} onChange={(e) => setStat(e.target.value)}>
                  {stats.map((s) => (
                    <option key={s.key} value={s.key}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Top N
                <input type="number" min={1} value={topN} onChange={(e) => setTopN(Number(e.target.value))} />
              </label>
              <label className="toggle-row">
                <input type="checkbox" checked={sortDesc} onChange={(e) => setSortDesc(e.target.checked)} />
                Highest first
              </label>
            </>
          )}

          {chartType === "radar" && (
            <>
              <div className="stat-multiselect">
                {stats.map((s) => (
                  <label key={s.key} className="toggle-row">
                    <input
                      type="checkbox"
                      checked={radarStats.includes(s.key)}
                      onChange={(e) =>
                        setRadarStats(
                          e.target.checked ? [...radarStats, s.key] : radarStats.filter((k) => k !== s.key)
                        )
                      }
                    />
                    {s.label}
                  </label>
                ))}
              </div>
              <label className="toggle-row">
                <input type="checkbox" checked={normalize} onChange={(e) => setNormalize(e.target.checked)} />
                Percentile-normalize
              </label>
              <EntityPicker entities={entities} selected={selected} onChange={setSelected} maxSelected={5} />
            </>
          )}

          {chartType === "heatmap" && (
            <>
              <label>
                Stat
                <select value={stat} onChange={(e) => setStat(e.target.value)}>
                  {seriesStats.map((s) => (
                    <option key={s.key} value={s.key}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </label>
              <EntityPicker entities={entities} selected={selected} onChange={setSelected} />
            </>
          )}

          {chartType === "distribution" && (
            <>
              <label>
                Stat
                <select value={stat} onChange={(e) => setStat(e.target.value)}>
                  {stats.map((s) => (
                    <option key={s.key} value={s.key}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </label>
              <div className="hint">Highlight:</div>
              <EntityPicker entities={entities} selected={selected} onChange={setSelected} />
            </>
          )}

          {chartType === "stacked" && (
            <>
              <div className="hint">Goals, assists, clean sheets, bonus, defensive contribution (raw counts).</div>
              <EntityPicker entities={entities} selected={selected} onChange={setSelected} maxSelected={1} />
            </>
          )}

          {chartType === "small_multiples" && (
            <>
              <label>
                Stat
                <select value={stat} onChange={(e) => setStat(e.target.value)}>
                  {seriesStats.map((s) => (
                    <option key={s.key} value={s.key}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </label>
              <EntityPicker entities={entities} selected={selected} onChange={setSelected} />
            </>
          )}
        </div>

        <div className="chart-render">
          {selected.length === 0 && chartType !== "ranked_bar" && (
            <div className="chart-placeholder">Select entities to plot.</div>
          )}

          {chartType === "timeseries" && selected.length > 0 && (
            <TimeSeries seriesData={seriesData} stat={stat} cumulative={cumulative} nameByCode={nameByCode} entityLabel={statLabel(entityType, stat)} />
          )}

          {chartType === "scatter" && selected.length > 0 && (
            <ScatterPlot rows={rows.filter((r) => selected.includes(entityCode(r)))} entityType={entityType} statX={statX} statY={statY} referenceLine={referenceLine} stats={stats} />
          )}

          {chartType === "ranked_bar" && (
            <RankedBar rows={rows} entityType={entityType} stat={stat} topN={topN} sortDesc={sortDesc} statLbl={statLabel(entityType, stat)} />
          )}

          {chartType === "radar" && selected.length > 0 && radarStats.length > 0 && (
            <Radar rows={rows} entityType={entityType} selected={selected} radarStats={radarStats} normalize={normalize} nameByCode={nameByCode} />
          )}

          {chartType === "heatmap" && selected.length > 0 && (
            <Heatmap seriesData={seriesData} stat={stat} nameByCode={nameByCode} />
          )}

          {chartType === "distribution" && (
            <Distribution rows={rows} entityType={entityType} stat={stat} selected={selected} statLbl={statLabel(entityType, stat)} />
          )}

          {chartType === "stacked" && selected.length > 0 && (
            <StackedBreakdown seriesData={seriesData} title={nameByCode.get(selected[0]) ?? ""} />
          )}

          {chartType === "small_multiples" && selected.length > 0 && (
            <SmallMultiples seriesData={seriesData} stat={stat} nameByCode={nameByCode} />
          )}
        </div>
      </div>
    </div>
  );
}

// ---- render helpers ----

function TimeSeries({
  seriesData,
  stat,
  cumulative,
  nameByCode,
  entityLabel,
}: {
  seriesData: SeriesPoint[];
  stat: string;
  cumulative: boolean;
  nameByCode: Map<number, string>;
  entityLabel: string;
}) {
  const byEntity = new Map<number, SeriesPoint[]>();
  for (const p of seriesData) {
    if (!byEntity.has(p.entity_code)) byEntity.set(p.entity_code, []);
    byEntity.get(p.entity_code)!.push(p);
  }
  const data = Array.from(byEntity.entries()).map(([code, points], i) => {
    const sorted = [...points].sort((a, b) => a.round - b.round);
    let running = 0;
    const y = sorted.map((p) => {
      const v = (p[stat] as number) ?? 0;
      running += v;
      return cumulative ? running : v;
    });
    return {
      x: sorted.map((p) => p.round),
      y,
      mode: "lines+markers" as const,
      name: nameByCode.get(code) ?? String(code),
      line: { color: PALETTE[i % PALETTE.length] },
    };
  });
  return (
    <Plot
      data={data}
      layout={{ ...baseLayout, xaxis: { ...baseLayout.xaxis, title: { text: "Gameweek" } }, yaxis: { ...baseLayout.yaxis, title: { text: entityLabel } } }}
      config={plotConfig}
      style={{ width: "100%", height: "360px" }}
    />
  );
}

function ScatterPlot({
  rows,
  entityType,
  statX,
  statY,
  referenceLine,
  stats,
}: {
  rows: (PlayerRow | TeamRow)[];
  entityType: "player" | "team";
  statX: string;
  statY: string;
  referenceLine: boolean;
  stats: { key: string; label: string }[];
}) {
  const x = rows.map((r) => getStatValue(r, entityType, statX) ?? 0);
  const y = rows.map((r) => getStatValue(r, entityType, statY) ?? 0);
  const shapes = referenceLine
    ? [
        {
          type: "line" as const,
          x0: Math.min(...x, ...y),
          y0: Math.min(...x, ...y),
          x1: Math.max(...x, ...y),
          y1: Math.max(...x, ...y),
          line: { color: "#666", dash: "dot" as const },
        },
      ]
    : [];
  const xLabel = stats.find((s) => s.key === statX)?.label ?? statX;
  const yLabel = stats.find((s) => s.key === statY)?.label ?? statY;
  return (
    <Plot
      data={[{ x, y, text: rows.map(entityName), mode: "markers", type: "scatter", marker: { color: PALETTE[0], size: 9 } }]}
      layout={{ ...baseLayout, shapes, xaxis: { ...baseLayout.xaxis, title: { text: xLabel } }, yaxis: { ...baseLayout.yaxis, title: { text: yLabel } } }}
      config={plotConfig}
      style={{ width: "100%", height: "360px" }}
    />
  );
}

function RankedBar({
  rows,
  entityType,
  stat,
  topN,
  sortDesc,
  statLbl,
}: {
  rows: (PlayerRow | TeamRow)[];
  entityType: "player" | "team";
  stat: string;
  topN: number;
  sortDesc: boolean;
  statLbl: string;
}) {
  const withValues = rows.map((r) => ({ name: entityName(r), value: getStatValue(r, entityType, stat) ?? 0 }));
  withValues.sort((a, b) => (sortDesc ? b.value - a.value : a.value - b.value));
  const sliced = withValues.slice(0, topN).reverse();
  return (
    <Plot
      data={[{ x: sliced.map((d) => d.value), y: sliced.map((d) => d.name), type: "bar", orientation: "h", marker: { color: PALETTE[0] } }]}
      layout={{ ...baseLayout, margin: { l: 120, r: 20, t: 20, b: 40 }, xaxis: { ...baseLayout.xaxis, title: { text: statLbl } } }}
      config={plotConfig}
      style={{ width: "100%", height: `${Math.max(300, sliced.length * 22)}px` }}
    />
  );
}

function Radar({
  rows,
  entityType,
  selected,
  radarStats,
  normalize,
  nameByCode,
}: {
  rows: (PlayerRow | TeamRow)[];
  entityType: "player" | "team";
  selected: number[];
  radarStats: string[];
  normalize: boolean;
  nameByCode: Map<number, string>;
}) {
  const labels = radarStats.map((s) => statLabel(entityType, s));
  const percentile = (statKey: string, value: number) => {
    const pool = rows.map((r) => getStatValue(r, entityType, statKey)).filter((v): v is number => v != null);
    if (pool.length === 0) return 0;
    const below = pool.filter((v) => v <= value).length;
    return (below / pool.length) * 100;
  };
  const data = selected.map((code, i) => {
    const row = rows.find((r) => entityCode(r) === code);
    const r = radarStats.map((s) => {
      const v = row ? getStatValue(row, entityType, s) ?? 0 : 0;
      return normalize ? percentile(s, v) : v;
    });
    return {
      type: "scatterpolar" as const,
      r: [...r, r[0]],
      theta: [...labels, labels[0]],
      fill: "toself" as const,
      name: nameByCode.get(code) ?? String(code),
      line: { color: PALETTE[i % PALETTE.length] },
    };
  });
  return (
    <Plot
      data={data}
      layout={{
        ...baseLayout,
        polar: { radialaxis: { visible: true, range: normalize ? [0, 100] : undefined, gridcolor: "#2a2a2a" }, bgcolor: "transparent" },
      }}
      config={plotConfig}
      style={{ width: "100%", height: "400px" }}
    />
  );
}

function Heatmap({ seriesData, stat, nameByCode }: { seriesData: SeriesPoint[]; stat: string; nameByCode: Map<number, string> }) {
  const rounds = Array.from(new Set(seriesData.map((p) => p.round))).sort((a, b) => a - b);
  const entityCodes = Array.from(new Set(seriesData.map((p) => p.entity_code)));
  const lookup = new Map(seriesData.map((p) => [`${p.entity_code}:${p.round}`, p[stat] as number]));
  const z = entityCodes.map((code) => rounds.map((r) => lookup.get(`${code}:${r}`) ?? null));
  return (
    <Plot
      data={[{ z, x: rounds, y: entityCodes.map((c) => nameByCode.get(c) ?? String(c)), type: "heatmap", colorscale: "YlOrRd" }]}
      layout={{ ...baseLayout, margin: { l: 100, r: 20, t: 20, b: 40 }, xaxis: { ...baseLayout.xaxis, title: { text: "Gameweek" } } }}
      config={plotConfig}
      style={{ width: "100%", height: `${Math.max(300, entityCodes.length * 30)}px` }}
    />
  );
}

function Distribution({
  rows,
  entityType,
  stat,
  selected,
  statLbl,
}: {
  rows: (PlayerRow | TeamRow)[];
  entityType: "player" | "team";
  stat: string;
  selected: number[];
  statLbl: string;
}) {
  const allValues = rows.map((r) => getStatValue(r, entityType, stat)).filter((v): v is number => v != null);
  const highlighted = rows.filter((r) => selected.includes(entityCode(r)));
  return (
    <Plot
      data={[
        { y: allValues, type: "box", boxpoints: "outliers", name: statLbl, marker: { color: PALETTE[0] }, fillcolor: "rgba(77,171,247,0.2)" },
        {
          y: highlighted.map((r) => getStatValue(r, entityType, stat) ?? 0),
          x: highlighted.map(() => statLbl),
          text: highlighted.map(entityName),
          mode: "markers+text",
          type: "scatter",
          textposition: "middle right",
          marker: { color: PALETTE[1], size: 10 },
        },
      ]}
      layout={{ ...baseLayout, yaxis: { ...baseLayout.yaxis, title: { text: statLbl } }, showlegend: false }}
      config={plotConfig}
      style={{ width: "100%", height: "400px" }}
    />
  );
}

function StackedBreakdown({ seriesData, title }: { seriesData: SeriesPoint[]; title: string }) {
  const rounds = Array.from(new Set(seriesData.map((p) => p.round))).sort((a, b) => a - b);
  const data = STACKED_BREAKDOWN_STATS.map((s, i) => ({
    x: rounds,
    y: rounds.map((r) => (seriesData.find((p) => p.round === r)?.[s] as number) ?? 0),
    name: statLabel("player", s),
    type: "bar" as const,
    marker: { color: PALETTE[i % PALETTE.length] },
  }));
  return (
    <Plot
      data={data}
      layout={{ ...baseLayout, barmode: "stack", title: { text: title }, xaxis: { ...baseLayout.xaxis, title: { text: "Gameweek" } } }}
      config={plotConfig}
      style={{ width: "100%", height: "360px" }}
    />
  );
}

function SmallMultiples({ seriesData, stat, nameByCode }: { seriesData: SeriesPoint[]; stat: string; nameByCode: Map<number, string> }) {
  const byEntity = new Map<number, SeriesPoint[]>();
  for (const p of seriesData) {
    if (!byEntity.has(p.entity_code)) byEntity.set(p.entity_code, []);
    byEntity.get(p.entity_code)!.push(p);
  }
  const allValues = seriesData.map((p) => (p[stat] as number) ?? 0);
  const yMax = Math.max(1, ...allValues);
  return (
    <div className="small-multiples-grid">
      {Array.from(byEntity.entries()).map(([code, points], i) => {
        const sorted = [...points].sort((a, b) => a.round - b.round);
        return (
          <div key={code} className="small-multiple">
            <div className="small-multiple-title">{nameByCode.get(code) ?? code}</div>
            <Plot
              data={[
                {
                  x: sorted.map((p) => p.round),
                  y: sorted.map((p) => (p[stat] as number) ?? 0),
                  mode: "lines",
                  line: { color: PALETTE[i % PALETTE.length], width: 1.5 },
                },
              ]}
              layout={{
                ...baseLayout,
                margin: { l: 24, r: 4, t: 4, b: 16 },
                xaxis: { visible: false },
                yaxis: { visible: false, range: [0, yMax] },
              }}
              config={{ ...plotConfig, staticPlot: true }}
              style={{ width: "160px", height: "80px" }}
            />
          </div>
        );
      })}
    </div>
  );
}
