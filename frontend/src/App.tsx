import { useEffect, useState } from "react";
import { getPlayerTable, getSeasonTeams, getSeasons, getTeamTable } from "./api";
import { PlayerTable } from "./components/PlayerTable";
import { TeamTable } from "./components/TeamTable";
import type { PlayerRow, Season, SortSpec, TeamMeta, TeamRow } from "./types";
import "./App.css";

const MAX_GW = 38;

function App() {
  const [seasons, setSeasons] = useState<Season[]>([]);
  const [seasonId, setSeasonId] = useState<string>("");
  const [viewMode, setViewMode] = useState<"players" | "teams">("players");
  const [teams, setTeams] = useState<TeamMeta[]>([]);
  const [playerRows, setPlayerRows] = useState<PlayerRow[]>([]);
  const [teamRows, setTeamRows] = useState<TeamRow[]>([]);
  const [playerSort, setPlayerSort] = useState<SortSpec | null>(null);
  const [teamSort, setTeamSort] = useState<SortSpec | null>(null);
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
    getSeasonTeams(seasonId).then(setTeams);
  }, [seasonId]);

  useEffect(() => {
    if (!seasonId || teams.length === 0) return;
    const teamRanges = teams.map((t) => ({ team_code: t.team_code, start_gw: 1, end_gw: MAX_GW }));

    setLoading(true);
    if (viewMode === "players") {
      getPlayerTable({
        season_id: seasonId,
        teams: teamRanges,
        opponent_team_codes: null,
        filters: [],
        sort: playerSort,
        per90: false,
        starts_only: false,
      })
        .then(setPlayerRows)
        .finally(() => setLoading(false));
    } else {
      getTeamTable({
        season_id: seasonId,
        teams: teamRanges,
        opponent_team_codes: null,
        filters: [],
        sort: teamSort,
      })
        .then(setTeamRows)
        .finally(() => setLoading(false));
    }
  }, [seasonId, teams, viewMode, playerSort, teamSort]);

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

      {viewMode === "players" ? (
        <PlayerTable data={playerRows} sort={playerSort} onSortChange={setPlayerSort} />
      ) : (
        <TeamTable data={teamRows} sort={teamSort} onSortChange={setTeamSort} />
      )}
    </div>
  );
}

export default App;
