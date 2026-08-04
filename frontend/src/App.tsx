import { useEffect, useMemo, useState } from "react";
import { getPlayerTable, getSeasonTeams, getSeasons, getTeamTable } from "./api";
import { ChartsPanel } from "./components/ChartsPanel";
import { FilterSidebar } from "./components/FilterSidebar";
import { PlayerTable } from "./components/PlayerTable";
import { TeamTable } from "./components/TeamTable";
import type { NumericFilter, PlayerRow, Season, SortSpec, TeamFilterState, TeamRow } from "./types";
import "./App.css";

const MAX_GW = 38;

function App() {
  const [seasons, setSeasons] = useState<Season[]>([]);
  const [seasonId, setSeasonId] = useState<string>("");
  const [viewMode, setViewMode] = useState<"players" | "teams">("players");
  const [teamFilters, setTeamFilters] = useState<TeamFilterState[]>([]);
  const [playerRows, setPlayerRows] = useState<PlayerRow[]>([]);
  const [teamRows, setTeamRows] = useState<TeamRow[]>([]);
  const [playerSort, setPlayerSort] = useState<SortSpec | null>(null);
  const [teamSort, setTeamSort] = useState<SortSpec | null>(null);
  const [playerFilters, setPlayerFilters] = useState<NumericFilter[]>([]);
  const [teamNumericFilters, setTeamNumericFilters] = useState<NumericFilter[]>([]);
  const [per90, setPer90] = useState(false);
  const [startsOnly, setStartsOnly] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    getSeasons().then((data) => {
      setSeasons(data);
      const mostRecent = data[data.length - 1];
      if (mostRecent) setSeasonId(mostRecent.id);
    });
  }, []);

  useEffect(() => {
    if (!seasonId) return;
    getSeasonTeams(seasonId).then((teams) => {
      setTeamFilters(
        teams.map((t) => ({
          team_code: t.team_code,
          name: t.name,
          included: true,
          opponentIncluded: true,
          start_gw: 1,
          end_gw: MAX_GW,
        }))
      );
    });
  }, [seasonId]);

  const teamRanges = useMemo(
    () =>
      teamFilters.filter((t) => t.included).map((t) => ({ team_code: t.team_code, start_gw: t.start_gw, end_gw: t.end_gw })),
    [teamFilters]
  );
  const opponentTeamCodes = useMemo(() => {
    const allOpponentsIncluded = teamFilters.every((t) => t.opponentIncluded);
    return allOpponentsIncluded ? null : teamFilters.filter((t) => t.opponentIncluded).map((t) => t.team_code);
  }, [teamFilters]);

  useEffect(() => {
    if (!seasonId || teamFilters.length === 0) return;

    setLoading(true);
    if (viewMode === "players") {
      getPlayerTable({
        season_id: seasonId,
        teams: teamRanges,
        opponent_team_codes: opponentTeamCodes,
        filters: playerFilters,
        sort: playerSort,
        per90,
        starts_only: startsOnly,
      })
        .then(setPlayerRows)
        .finally(() => setLoading(false));
    } else {
      getTeamTable({
        season_id: seasonId,
        teams: teamRanges,
        opponent_team_codes: opponentTeamCodes,
        filters: teamNumericFilters,
        sort: teamSort,
      })
        .then(setTeamRows)
        .finally(() => setLoading(false));
    }
  }, [seasonId, teamFilters, viewMode, playerSort, teamSort, playerFilters, teamNumericFilters, per90, startsOnly]);

  return (
    <div className="app">
      <header className="toolbar">
        <h1>FPL Dashboard</h1>
        <label>
          Season
          <select value={seasonId} onChange={(e) => setSeasonId(e.target.value)}>
            {seasons.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          View
          <select value={viewMode} onChange={(e) => setViewMode(e.target.value as "players" | "teams")}>
            <option value="players">Players</option>
            <option value="teams">Teams</option>
          </select>
        </label>
        {loading && <span className="loading">Loading…</span>}
      </header>

      <div className="main-layout">
        <FilterSidebar
          teams={teamFilters}
          onChange={setTeamFilters}
          maxGw={MAX_GW}
          showPlayerToggles={viewMode === "players"}
          per90={per90}
          onPer90Change={setPer90}
          startsOnly={startsOnly}
          onStartsOnlyChange={setStartsOnly}
        />

        {viewMode === "players" ? (
          <PlayerTable
            data={playerRows}
            sort={playerSort}
            onSortChange={setPlayerSort}
            filters={playerFilters}
            onFiltersChange={setPlayerFilters}
          />
        ) : (
          <TeamTable
            data={teamRows}
            sort={teamSort}
            onSortChange={setTeamSort}
            filters={teamNumericFilters}
            onFiltersChange={setTeamNumericFilters}
          />
        )}
      </div>

      <ChartsPanel
        entityType={viewMode === "players" ? "player" : "team"}
        rows={viewMode === "players" ? playerRows : teamRows}
        seasonId={seasonId}
        teamRanges={teamRanges}
        opponentTeamCodes={opponentTeamCodes}
        per90={per90}
        startsOnly={startsOnly}
      />
    </div>
  );
}

export default App;
