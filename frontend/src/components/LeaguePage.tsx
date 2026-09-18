import { useEffect, useState } from "react";
import { getLeagueWeek, getLeagues } from "../api";
import type { LeagueEntry, LeaguePick, LeagueSummary, LeagueWeek } from "../types";
import "./LeaguePage.css";

const CHIP_LABEL: Record<string, string> = {
  wildcard: "Wildcard",
  freehit: "Free Hit",
  bboost: "Bench Boost",
  "3xc": "Triple Captain",
};

const ROWS = ["GK", "DEF", "MID", "FWD"];

const errorMessage = (err: any): string => err?.response?.data?.detail ?? err?.message ?? "Unknown error";
const fmtXp = (v: number | null) => (v == null ? "-" : v.toFixed(1));

function Signed({ value, digits = 0 }: { value: number | null; digits?: number }) {
  if (value == null) return <span className="delta-none">-</span>;
  const shown = Math.abs(value).toFixed(digits);
  if (Number(shown) === 0) return <span className="delta-flat">{shown}</span>;
  return <span className={value > 0 ? "delta-up" : "delta-down"}>{value > 0 ? "+" : "−"}{shown}</span>;
}

function RankMove({ entry }: { entry: LeagueEntry }) {
  if (entry.prev_rank == null || entry.prev_rank === entry.rank) return <span className="rank-move delta-flat">–</span>;
  const up = entry.prev_rank > entry.rank;
  return (
    <span className={`rank-move ${up ? "delta-up" : "delta-down"}`} title={`Was ${entry.prev_rank} the gameweek before`}>
      {up ? "▲" : "▼"}
      {Math.abs(entry.prev_rank - entry.rank)}
    </span>
  );
}

function Chip({ name }: { name: string }) {
  return <span className={`chip-badge chip-${name === "3xc" ? "tc" : name}`}>{CHIP_LABEL[name] ?? name}</span>;
}

function PickRow({ pick }: { pick: LeaguePick }) {
  const armband = pick.is_captain ? "C" : pick.is_vice_captain ? "V" : null;
  const blank = pick.minutes === 0 && pick.points != null;
  return (
    <li className={`pick${pick.counts ? "" : " pick-benched"}`}>
      <span className="pick-name">
        {pick.web_name}
        {armband && <span className={`armband armband-${armband.toLowerCase()}`}>{armband}</span>}
        {pick.auto_sub && (
          <span className="pick-sub" title={pick.auto_sub === "in" ? "Came on as an automatic substitute" : "Didn't play - automatically substituted off"}>
            {pick.auto_sub === "in" ? "↑" : "↓"}
          </span>
        )}
      </span>
      <span className="pick-team">{pick.team_name ?? ""}</span>
      <span className="pick-xp" title="Projected points (with captaincy)">{fmtXp(pick.xp)}</span>
      <span className={`pick-points${blank ? " pick-blank" : ""}`} title={blank ? "0 minutes so far" : undefined}>
        {pick.points ?? "-"}
      </span>
    </li>
  );
}

function TeamCard({ entry }: { entry: LeagueEntry }) {
  const starters = entry.picks.filter((p) => p.squad_slot <= 11);
  const bench = entry.picks.filter((p) => p.squad_slot > 11);
  const formation = ROWS.slice(1).map((pos) => starters.filter((p) => p.position === pos).length).join("-");
  return (
    <article className={`team-card${entry.is_me ? " team-card-me" : ""}`}>
      <header className="team-card-header">
        <span className="team-card-rank">{entry.rank}</span>
        <div className="team-card-title">
          <h3>{entry.entry_name}</h3>
          <span>
            {entry.player_name}
            {starters.length === 11 && <> · {formation}</>}
          </span>
        </div>
        <div className="team-card-score">
          <span className="team-card-points">{entry.points}</span>
          <span className="team-card-xp" title="Projected points for the lineup as picked">xP {fmtXp(entry.projected)}</span>
        </div>
      </header>
      {(entry.chip || entry.hits > 0) && (
        <div className="team-card-flags">
          {entry.chip && <Chip name={entry.chip} />}
          {entry.hits > 0 && <span className="chip-badge chip-hit">−{entry.hits} hit</span>}
        </div>
      )}
      {entry.picks.length === 0 ? (
        <p className="team-card-empty">Lineup not available for this gameweek.</p>
      ) : (
        <>
          <div className="pick-head">
            <span>Starting XI</span>
            <span>xP</span>
            <span>Pts</span>
          </div>
          {ROWS.map((pos) => (
            <ul key={pos} className={`pick-group pick-group-${pos}`}>
              {starters.filter((p) => p.position === pos).map((p) => (
                <PickRow key={p.player_code} pick={p} />
              ))}
            </ul>
          ))}
          <div className="pick-head pick-head-bench">
            <span>Bench{entry.chip === "bboost" ? " (boosted)" : ""}</span>
            <span />
            <span>{entry.points_on_bench}</span>
          </div>
          <ul className="pick-group pick-group-bench">
            {bench.map((p) => (
              <PickRow key={p.player_code} pick={p} />
            ))}
          </ul>
        </>
      )}
    </article>
  );
}

interface Props {
  seasonId: string;
  /** Whether a team has been synced - the league list is read from that entry. */
  hasTeam: boolean;
  refreshNonce: number;
}

export function LeaguePage({ seasonId, hasTeam, refreshNonce }: Props) {
  const [leagues, setLeagues] = useState<LeagueSummary[] | null>(null);
  const [leagueId, setLeagueId] = useState<number | null>(null);
  const [event, setEvent] = useState<number | null>(null); // null = latest
  const [week, setWeek] = useState<LeagueWeek | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!seasonId) return;
    setLeagues(null);
    setWeek(null);
    setEvent(null);
    getLeagues(seasonId)
      .then((list) => {
        setLeagues(list);
        setLeagueId((current) => (list.some((l) => l.league_id === current) ? current : (list[0]?.league_id ?? null)));
      })
      .catch((err) => setError(errorMessage(err)));
  }, [seasonId, hasTeam]);

  useEffect(() => {
    if (!seasonId || leagueId == null) return;
    setLoading(true);
    setError(null);
    getLeagueWeek(seasonId, leagueId, event)
      .then(setWeek)
      .catch((err) => setError(errorMessage(err)))
      .finally(() => setLoading(false));
  }, [seasonId, leagueId, event, refreshNonce]);

  const entries = week?.entries ?? [];
  const live = week != null && week.event != null && week.final === false;
  const anyProjected = entries.some((e) => e.projected != null);

  return (
    <section className="league-page">
      <div className="league-header">
        <h2>{week?.name ?? "League"}</h2>
        {leagues && leagues.length > 1 && (
          <select value={leagueId ?? ""} onChange={(e) => { setEvent(null); setLeagueId(Number(e.target.value)); }}>
            {leagues.map((l) => (
              <option key={l.league_id} value={l.league_id}>
                {l.name}
              </option>
            ))}
          </select>
        )}
        <span className="league-meta">
          {week?.event != null && (
            <>
              Gameweek {week.event} · {live ? "in play - live points, before automatic subs and bonus" : "final"} · {entries.length} teams
            </>
          )}
        </span>
        {loading && <span className="loading">{week ? "Updating…" : "Fetching the league from FPL…"}</span>}
      </div>

      {week && week.events.length > 0 && (
        <div className="gw-tabs" role="tablist" aria-label="Gameweek">
          {week.events.map((gw) => (
            <button
              key={gw}
              type="button"
              role="tab"
              aria-selected={gw === week.event}
              className={`gw-tab${gw === week.event ? " gw-tab-on" : ""}`}
              onClick={() => setEvent(gw)}
            >
              GW{gw}
            </button>
          ))}
        </div>
      )}

      {error && <div className="fetch-error">Couldn't load the league: {error}</div>}
      {week?.warning && <div className="prices-note prices-note-warning">{week.warning}</div>}
      {leagues && leagues.length === 0 && !error && (
        <div className="prices-note">
          {hasTeam
            ? "Your team isn't in any private leagues this season."
            : 'Enter your team id and press "Sync My Team" - your leagues are read from your team.'}
        </div>
      )}
      {week && week.event != null && !anyProjected && (
        <div className="prices-note">
          No projections were held for GW{week.event}, so projected points are blank. They're recorded from
          whichever projections file is loaded when a gameweek's lineups are first fetched.
        </div>
      )}

      {entries.length > 0 && (
        <>
          <div className="data-table-scroll league-table-scroll">
            <table className="data-table league-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Team</th>
                  <th title="Gameweek points, before transfer hits">GW</th>
                  <th title="Projected points for the lineup as picked, captaincy included">xP</th>
                  <th title="Actual minus projected. Blank until the gameweek is final - a half-played week is always 'behind'.">vs xP</th>
                  <th>Chip</th>
                  <th title="Transfers made (points spent on hits)">Transfers</th>
                  <th title="Points left on the bench">Bench</th>
                  <th title="Chips played so far this season">Chips used</th>
                  <th>Total</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((e) => (
                  <tr key={e.entry_id} className={e.is_me ? "my-team" : undefined}>
                    <td className="league-rank">
                      {e.rank} <RankMove entry={e} />
                    </td>
                    <td className="league-team">
                      <b>{e.entry_name}</b>
                      <span>{e.player_name}</span>
                    </td>
                    <td className="league-num league-strong">{e.points}</td>
                    <td className="league-num">{fmtXp(e.projected)}</td>
                    <td className="league-num">
                      <Signed value={e.projected == null || !e.final ? null : e.points - e.projected} digits={1} />
                    </td>
                    <td>{e.chip ? <Chip name={e.chip} /> : <span className="delta-none">-</span>}</td>
                    <td className="league-num">
                      {e.transfers}
                      {e.hits > 0 && <span className="delta-down"> (−{e.hits})</span>}
                    </td>
                    <td className="league-num">{e.points_on_bench}</td>
                    <td className="league-chips-used">
                      {e.chips_used.length === 0 ? (
                        <span className="delta-none">none</span>
                      ) : (
                        e.chips_used.map((c) => (
                          <span key={`${c.event}-${c.chip}`} title={`${CHIP_LABEL[c.chip] ?? c.chip}, gameweek ${c.event}`}>
                            {c.chip === "3xc" ? "TC" : c.chip === "bboost" ? "BB" : c.chip === "freehit" ? "FH" : "WC"}
                            <sub>{c.event}</sub>
                          </span>
                        ))
                      )}
                    </td>
                    <td className="league-num league-strong">{e.total_points.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="team-cards">
            {entries.map((e) => (
              <TeamCard key={e.entry_id} entry={e} />
            ))}
          </div>
        </>
      )}
    </section>
  );
}
