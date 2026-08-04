import { useState } from "react";
import type { TeamFilterState } from "../types";
import "./FilterSidebar.css";

interface Props {
  teams: TeamFilterState[];
  onChange: (teams: TeamFilterState[]) => void;
  maxGw: number;
  showPlayerToggles: boolean;
  per90: boolean;
  onPer90Change: (v: boolean) => void;
  startsOnly: boolean;
  onStartsOnlyChange: (v: boolean) => void;
}

export function FilterSidebar({
  teams,
  onChange,
  maxGw,
  showPlayerToggles,
  per90,
  onPer90Change,
  startsOnly,
  onStartsOnlyChange,
}: Props) {
  const [globalStart, setGlobalStart] = useState(1);
  const [globalEnd, setGlobalEnd] = useState(maxGw);

  const updateTeam = (team_code: number, patch: Partial<TeamFilterState>) => {
    onChange(teams.map((t) => (t.team_code === team_code ? { ...t, ...patch } : t)));
  };

  const setAllIncluded = (included: boolean) => {
    onChange(teams.map((t) => ({ ...t, included })));
  };

  const applyGlobalRange = () => {
    onChange(teams.map((t) => ({ ...t, start_gw: globalStart, end_gw: globalEnd })));
  };

  return (
    <aside className="filter-sidebar">
      {showPlayerToggles && (
        <div className="sidebar-section">
          <label className="toggle-row">
            <input type="checkbox" checked={per90} onChange={(e) => onPer90Change(e.target.checked)} />
            Per 90
          </label>
          <label className="toggle-row">
            <input type="checkbox" checked={startsOnly} onChange={(e) => onStartsOnlyChange(e.target.checked)} />
            Starts only
          </label>
        </div>
      )}

      <div className="sidebar-section">
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
      </div>

      <div className="sidebar-section team-list">
        <div className="team-row team-row-header">
          <span>Incl.</span>
          <span>Team</span>
          <span>GW</span>
          <span></span>
          <span>Opp.</span>
        </div>
        {teams.map((t) => (
          <div className="team-row" key={t.team_code}>
            <input
              type="checkbox"
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
    </aside>
  );
}
