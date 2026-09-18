import { useEffect, useMemo, useState } from "react";
import { getMyTeam, getPlayerTable, getProjectionSources, getSeasonTeams, getSeasons, getTeamTable, refreshSeason, syncMyTeam } from "./api";
import { ChartsPanel } from "./components/ChartsPanel";
import { FilterSidebar } from "./components/FilterSidebar";
import { LeaguePage } from "./components/LeaguePage";
import { PlayerTable } from "./components/PlayerTable";
import { PricesPage } from "./components/PricesPage";
import { ProjectionsPanel } from "./components/ProjectionsPanel";
import { TeamTable } from "./components/TeamTable";
import { gameweekLabel } from "./types";
import type { MyTeam, NumericFilter, PlayerRow, ProjectionSource, ProjectionSpec, Season, SortSpec, SquadPick, TeamFilterState, TeamRow } from "./types";
import "./App.css";

const MAX_GW = 38;
// How many gameweeks ahead the projections window defaults to.
const PROJECTION_WEEKS = 6;

type ViewMode = "players" | "teams" | "projections" | "prices" | "league";

function errorMessage(err: any): string {
  return err?.response?.data?.detail ?? err?.message ?? "Unknown error";
}

function App() {
  const [seasons, setSeasons] = useState<Season[]>([]);
  const [seasonId, setSeasonId] = useState<string>("");
  const [viewMode, setViewMode] = useState<ViewMode>("players");
  const [teamFilters, setTeamFilters] = useState<TeamFilterState[]>([]);
  const [playerRows, setPlayerRows] = useState<PlayerRow[]>([]);
  const [teamRows, setTeamRows] = useState<TeamRow[]>([]);
  // The backend's own fallbacks, stated up front so the header marks the sorted column from the start.
  const [playerSort, setPlayerSort] = useState<SortSpec | null>({ column: "total_points", direction: "desc" });
  const [teamSort, setTeamSort] = useState<SortSpec | null>({ column: "table_place", direction: "asc" });
  const [playerFilters, setPlayerFilters] = useState<NumericFilter[]>([]);
  const [teamNumericFilters, setTeamNumericFilters] = useState<NumericFilter[]>([]);
  const [positions, setPositions] = useState<string[] | null>(null);
  const [perStart, setPerStart] = useState(false);
  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshMessage, setRefreshMessage] = useState<string | null>(null);
  const [refreshNonce, setRefreshNonce] = useState(0);
  const [myTeam, setMyTeam] = useState<MyTeam | null>(null);
  const [entryIdText, setEntryIdText] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);
  const [projSources, setProjSources] = useState<ProjectionSource[]>([]);
  const [projection, setProjection] = useState<ProjectionSpec>({ source: null, gameweeks: [] });
  const [projPositions, setProjPositions] = useState<string[] | null>(null);

  useEffect(() => {
    getSeasons().then((data) => {
      setSeasons(data);
      const mostRecent = data[data.length - 1];
      if (mostRecent) setSeasonId(mostRecent.id);
    });
  }, []);

  // The last gameweek the season actually has stats for. Finished seasons run to 38; the live
  // one stops at the latest played round, so the filters describe the data the table holds
  // rather than a nominal 1-38.
  const lastPlayedGw = useMemo(() => {
    const played = seasons.find((s) => s.id === seasonId)?.played_through;
    return played ? Math.min(played, MAX_GW) : MAX_GW;
  }, [seasons, seasonId]);

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
          end_gw: lastPlayedGw,
        }))
      );
    });
  }, [seasonId, lastPlayedGw]);

  useEffect(() => {
    if (!seasonId) return;
    setSyncMessage(null);
    getMyTeam(seasonId)
      .then((team) => {
        setMyTeam(team);
        setEntryIdText(team ? String(team.entry_id) : "");
      })
      .catch(() => setMyTeam(null));
  }, [seasonId]);

  // Default the projection window to the next six unplayed gameweeks - what you'd actually plan
  // transfers around - but never past what the imported file covers: a ticked week with no data
  // behind it just makes the totals look like they span more than they do.
  const nextGw = useMemo(() => {
    const played = seasons.find((s) => s.id === seasonId)?.played_through ?? 0;
    return Math.min(played + 1, MAX_GW);
  }, [seasons, seasonId]);

  useEffect(() => {
    if (!seasonId) return;
    getProjectionSources(seasonId)
      .then((sources) => {
        setProjSources(sources);
        const first = sources[0];
        const start = Math.max(nextGw, first?.first_gw ?? nextGw);
        const end = Math.min(nextGw + PROJECTION_WEEKS - 1, first?.last_gw ?? MAX_GW, MAX_GW);
        const gameweeks = Array.from({ length: Math.max(end - start + 1, 0) }, (_, i) => start + i);
        setProjection({ source: first?.source ?? null, gameweeks });
      })
      .catch(() => setProjSources([]));
    // refreshNonce: Fetch New Data may have imported newer projections, moving the coverage.
  }, [seasonId, nextGw, refreshNonce]);

  // Keyed by player_code so the table can look a row up in O(1); memoized so PlayerTable's
  // column defs aren't rebuilt on every render.
  const squad = useMemo(
    () => new Map<number, SquadPick>((myTeam?.picks ?? []).map((p) => [p.player_code, p])),
    [myTeam]
  );

  const teamRanges = useMemo(
    () =>
      teamFilters.filter((t) => t.included).map((t) => ({ team_code: t.team_code, start_gw: t.start_gw, end_gw: t.end_gw })),
    [teamFilters]
  );
  // The Projections page has no team sidebar, so it isn't narrowed by one it can't show.
  const allTeamRanges = useMemo(
    () => teamFilters.map((t) => ({ team_code: t.team_code, start_gw: t.start_gw, end_gw: t.end_gw })),
    [teamFilters]
  );
  const opponentTeamCodes = useMemo(() => {
    const allOpponentsIncluded = teamFilters.every((t) => t.opponentIncluded);
    return allOpponentsIncluded ? null : teamFilters.filter((t) => t.opponentIncluded).map((t) => t.team_code);
  }, [teamFilters]);

  useEffect(() => {
    // Only the two stats tables are fed from here; the other pages fetch for themselves.
    if (!seasonId || teamFilters.length === 0 || (viewMode !== "players" && viewMode !== "teams")) return;

    setLoading(true);
    setFetchError(null);
    if (viewMode === "players") {
      getPlayerTable({
        season_id: seasonId,
        teams: teamRanges,
        opponent_team_codes: opponentTeamCodes,
        filters: playerFilters,
        sort: playerSort,
        per_start: perStart,
        positions,
        projection_source: projection.source,
        projection_gameweeks: projection.gameweeks,
      })
        .then(setPlayerRows)
        .catch((err) => setFetchError(errorMessage(err)))
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
        .catch((err) => setFetchError(errorMessage(err)))
        .finally(() => setLoading(false));
    }
  }, [
    seasonId,
    teamFilters,
    viewMode,
    playerSort,
    teamSort,
    playerFilters,
    teamNumericFilters,
    perStart,
    positions,
    projection,
    refreshNonce,
  ]);

  const currentSeason = useMemo(() => seasons.find((s) => s.id === seasonId), [seasons, seasonId]);

  const handleRefresh = () => {
    if (!seasonId || refreshing) return;
    setRefreshing(true);
    setRefreshMessage(null);
    refreshSeason(seasonId)
      .then((summary) => {
        const proj = summary.projections ? ` · ${summary.projections.message}` : "";
        setRefreshMessage(`Updated: ${summary.gw_rows_inserted} gameweek rows, ${summary.players} players${proj}.`);
        setRefreshNonce((n) => n + 1);
        // New results move played_through, which the gameweek filters and projection default follow.
        return getSeasons().then(setSeasons);
      })
      .catch((err) => setRefreshMessage(`Refresh failed: ${errorMessage(err)}`))
      .finally(() => setRefreshing(false));
  };

  const handleSyncMyTeam = () => {
    const entryId = Number(entryIdText.trim());
    if (!seasonId || syncing) return;
    if (!Number.isInteger(entryId) || entryId <= 0) {
      setSyncMessage("Enter your numeric FPL team id.");
      return;
    }
    setSyncing(true);
    setSyncMessage(null);
    syncMyTeam(seasonId, entryId)
      .then((res) => {
        const missed = res.unmatched.length
          ? ` ${res.unmatched.length} not on this board yet (${res.unmatched.join(", ")}).`
          : "";
        setSyncMessage(res.message + missed);
        return getMyTeam(seasonId).then(setMyTeam);
      })
      .catch((err) => setSyncMessage(`Sync failed: ${errorMessage(err)}`))
      .finally(() => setSyncing(false));
  };

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
          <select value={viewMode} onChange={(e) => setViewMode(e.target.value as ViewMode)}>
            <option value="players">Players</option>
            <option value="teams">Teams</option>
            <option value="projections">Projections</option>
            <option value="prices">Prices</option>
            <option value="league">League</option>
          </select>
        </label>
        <button className="refresh-btn" onClick={handleRefresh} disabled={refreshing || !seasonId}>
          {refreshing ? "Fetching…" : "Fetch New Data"}
        </button>
        <label className="my-team-control">
          My team id
          <input
            type="number"
            placeholder="e.g. 1234567"
            value={entryIdText}
            onChange={(e) => setEntryIdText(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSyncMyTeam()}
          />
        </label>
        <button className="sync-btn" onClick={handleSyncMyTeam} disabled={syncing || !seasonId}>
          {syncing ? "Syncing…" : "Sync My Team"}
        </button>
        {refreshMessage && <span className="refresh-message">{refreshMessage}</span>}
        {syncMessage && <span className="refresh-message">{syncMessage}</span>}
        {loading && <span className="loading">Loading…</span>}
      </header>

      {currentSeason?.is_placeholder === 1 && (
        <div className="placeholder-banner">
          {currentSeason.label} hasn't started yet — showing last season's results with current{" "}
          {currentSeason.label} prices as a stand-in. Use "Fetch New Data" once real fixtures have been
          played to replace this.
        </div>
      )}

      {fetchError && (
        <div className="fetch-error">
          Couldn't load data: {fetchError}. Is the backend running (<code>./run.sh</code>)?
        </div>
      )}

      {viewMode === "prices" ? (
        <PricesPage seasonId={seasonId} squad={squad} refreshNonce={refreshNonce} />
      ) : viewMode === "projections" ? (
        <ProjectionsPanel
          seasonId={seasonId}
          teamRanges={allTeamRanges}
          projSources={projSources}
          projection={projection}
          onProjectionChange={setProjection}
          positions={projPositions}
          onPositionsChange={setProjPositions}
          squad={squad}
          refreshNonce={refreshNonce}
        />
      ) : viewMode === "league" ? (
        <LeaguePage seasonId={seasonId} hasTeam={myTeam !== null} refreshNonce={refreshNonce} />
      ) : (
        <>
          <div className="main-layout">
            <FilterSidebar
              teams={teamFilters}
              onChange={setTeamFilters}
              maxGw={lastPlayedGw}
              showPlayerToggles={viewMode === "players"}
              perStart={perStart}
              onPerStartChange={setPerStart}
            />

            {viewMode === "players" ? (
              <PlayerTable
                data={playerRows}
                sort={playerSort}
                onSortChange={setPlayerSort}
                filters={playerFilters}
                onFiltersChange={setPlayerFilters}
                positions={positions}
                onPositionsChange={setPositions}
                perStart={perStart}
                squad={squad}
                projLabel={projection.source ? gameweekLabel(projection.gameweeks) : null}
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
            perStart={perStart}
          />
        </>
      )}
    </div>
  );
}

export default App;
