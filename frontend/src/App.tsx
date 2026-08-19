import { useEffect, useMemo, useState } from "react";
import { getMyTeam, getPlayerTable, getSeasonTeams, getSeasons, getTeamTable, refreshSeason, syncMyTeam } from "./api";
import { ChartsPanel } from "./components/ChartsPanel";
import { FilterSidebar } from "./components/FilterSidebar";
import { PlayerTable } from "./components/PlayerTable";
import { TeamTable } from "./components/TeamTable";
import type { MyTeam, NumericFilter, PlayerRow, Season, SortSpec, SquadPick, TeamFilterState, TeamRow } from "./types";
import "./App.css";

const MAX_GW = 38;

function errorMessage(err: any): string {
  return err?.response?.data?.detail ?? err?.message ?? "Unknown error";
}

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
  const [positions, setPositions] = useState<string[] | null>(null);
  const [per90, setPer90] = useState(false);
  const [startsOnly, setStartsOnly] = useState(false);
  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshMessage, setRefreshMessage] = useState<string | null>(null);
  const [refreshNonce, setRefreshNonce] = useState(0);
  const [myTeam, setMyTeam] = useState<MyTeam | null>(null);
  const [entryIdText, setEntryIdText] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);

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
  const opponentTeamCodes = useMemo(() => {
    const allOpponentsIncluded = teamFilters.every((t) => t.opponentIncluded);
    return allOpponentsIncluded ? null : teamFilters.filter((t) => t.opponentIncluded).map((t) => t.team_code);
  }, [teamFilters]);

  useEffect(() => {
    if (!seasonId || teamFilters.length === 0) return;

    setLoading(true);
    setFetchError(null);
    if (viewMode === "players") {
      getPlayerTable({
        season_id: seasonId,
        teams: teamRanges,
        opponent_team_codes: opponentTeamCodes,
        filters: playerFilters,
        sort: playerSort,
        per90,
        starts_only: startsOnly,
        positions,
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
    per90,
    startsOnly,
    positions,
    refreshNonce,
  ]);

  const currentSeason = useMemo(() => seasons.find((s) => s.id === seasonId), [seasons, seasonId]);

  const handleRefresh = () => {
    if (!seasonId || refreshing) return;
    setRefreshing(true);
    setRefreshMessage(null);
    refreshSeason(seasonId)
      .then((summary) => {
        setRefreshMessage(`Updated: ${summary.gw_rows_inserted} gameweek rows, ${summary.players} players.`);
        setRefreshNonce((n) => n + 1);
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
          <select value={viewMode} onChange={(e) => setViewMode(e.target.value as "players" | "teams")}>
            <option value="players">Players</option>
            <option value="teams">Teams</option>
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
            positions={positions}
            onPositionsChange={setPositions}
            per90={per90}
            squad={squad}
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
