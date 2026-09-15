import { useEffect, useState } from "react";
import type { TeamFilterState } from "../types";
import "./FilterSidebar.css";

interface Props {
  teams: TeamFilterState[];
  onChange: (teams: TeamFilterState[]) => void;
  /** Last gameweek with data for this season - the upper bound of every range input. */
  maxGw: number;
  showPlayerToggles: boolean;
  perStart: boolean;
  onPerStartChange: (v: boolean) => void;
}

/** The stats filters. Laid out as a bar above the table (not a side column) so the table
 *  gets the full width; the team list wraps into as many columns as fit. */
export function FilterSidebar({
  teams,
  onChange,
  maxGw,
  showPlayerToggles,
  perStart,
  onPerStartChange,
}: Props) {
  const [globalStart, setGlobalStart] = useState(1);
  const [globalEnd, setGlobalEnd] = useState(maxGw);
  const [teamsOpen, setTeamsOpen] = useState(false); // hidden by default: it's tall and rarely changed

  // Switching season (or fetching new results) changes how far the data runs; follow it so the
  // range reads as what the table actually contains.
  useEffect(() => {
    setGlobalStart(1);
    setGlobalEnd(maxGw);
  }, [maxGw]);

  const updateTeam = (team_code: number, patch: Partial<TeamFilterState>) => {
    onChange(teams.map((t) => (t.team_code === team_code ? { ...t, ...patch } : t)));
  };

  const setAllIncluded = (included: boolean) => {
    onChange(teams.map((t) => ({ ...t, included })));
  };

  const applyGlobalRange = () => {
    onChange(teams.map((t) => ({ ...t, start_gw: globalStart, end_gw: globalEnd })));
  };

  const excluded = teams.filter((t) => !t.included).length;

  return (
    <div className="filter-bar">
      <div className="filter-bar-row">
        {showPlayerToggles && (
          <div className="sidebar-section filter-inline">
            <div className="section-title">Display</div>
            <label className="toggle-row">
              <input type="checkbox" checked={perStart} onChange={(e) => onPerStartChange(e.target.checked)} />
              Per Start
            </label>
          </div>
        )}

        <div className="sidebar-section filter-inline">
          <div className="section-title">Teams</div>
          <div className="bulk-buttons">
            <button onClick={() => setAllIncluded(true)}>Include All</button>
            <button onClick={() => setAllIncluded(false)}>Exclude All</button>
          </div>
          <div className="global-range">
            <span>Gameweeks</span>
            <input
              type="number"
              min={1}
              max={maxGw}
              value={globalStart}
              onChange={(e) => setGlobalStart(Number(e.target.value))}
            />
            <span>to</span>
            <input
              type="number"
              min={1}
              max={maxGw}
              value={globalEnd}
              onChange={(e) => setGlobalEnd(Number(e.target.value))}
            />
            <button onClick={applyGlobalRange}>Apply to All</button>
          </div>
          <button className="teams-toggle" onClick={() => setTeamsOpen((o) => !o)}>
            {teamsOpen ? "Hide teams" : "Show teams"}
            {excluded > 0 && <span className="teams-excluded">{excluded} excluded</span>}
          </button>
        </div>
      </div>

      {teamsOpen && (
        <div className="sidebar-section team-list">
          <div className="team-legend">Include · team · gameweek range · include as opponent</div>
          <div className="team-grid">
            {teams.map((t) => (
              <div className="team-row" key={t.team_code}>
                <input
                  type="checkbox"
                  title="Include team"
                  checked={t.included}
                  onChange={(e) => updateTeam(t.team_code, { included: e.target.checked })}
                />
                <span className="team-name" title={t.name}>
                  {t.name}
                </span>
                <input
                  type="number"
                  className="gw-input"
                  min={1}
                  max={maxGw}
                  value={t.start_gw}
                  disabled={!t.included}
                  onChange={(e) => updateTeam(t.team_code, { start_gw: Number(e.target.value) })}
                />
                <input
                  type="number"
                  className="gw-input"
                  min={1}
                  max={maxGw}
                  value={t.end_gw}
                  disabled={!t.included}
                  onChange={(e) => updateTeam(t.team_code, { end_gw: Number(e.target.value) })}
                />
                <input
                  type="checkbox"
                  title="Include as opponent"
                  checked={t.opponentIncluded}
                  onChange={(e) => updateTeam(t.team_code, { opponentIncluded: e.target.checked })}
                />
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
