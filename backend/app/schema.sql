-- FPL dashboard schema.
-- Teams and players are keyed by their stable FPL 'code' (survives across seasons);
-- season-specific numeric ids from the source data are only used as join keys during ingestion.

CREATE TABLE IF NOT EXISTS seasons (
    id            TEXT PRIMARY KEY,   -- e.g. '2025-26'
    label         TEXT NOT NULL,      -- e.g. '2025/26'
    start_date    TEXT,
    end_date      TEXT,
    backfilled    INTEGER NOT NULL DEFAULT 0,
    is_placeholder INTEGER NOT NULL DEFAULT 0  -- season hasn't started; data is a stand-in (see scripts/create_placeholder_season.py)
);

CREATE TABLE IF NOT EXISTS teams (
    season_id      TEXT NOT NULL REFERENCES seasons(id),
    team_code      INTEGER NOT NULL,  -- stable across seasons
    season_team_id INTEGER NOT NULL,  -- this season's numeric id (1..20, alphabetical)
    name           TEXT NOT NULL,
    short_name     TEXT NOT NULL,
    PRIMARY KEY (season_id, team_code)
);
CREATE INDEX IF NOT EXISTS idx_teams_season_local_id ON teams(season_id, season_team_id);

CREATE TABLE IF NOT EXISTS players (
    player_code INTEGER PRIMARY KEY,  -- stable across seasons
    first_name  TEXT,
    second_name TEXT,
    web_name    TEXT
);

CREATE TABLE IF NOT EXISTS player_season (
    season_id         TEXT NOT NULL REFERENCES seasons(id),
    player_code       INTEGER NOT NULL REFERENCES players(player_code),
    season_element_id INTEGER NOT NULL,  -- this season's numeric id, used by gw stat rows
    team_code         INTEGER NOT NULL,
    position          TEXT NOT NULL,     -- GK / DEF / MID / FWD
    start_cost        INTEGER,           -- price * 10 at season start
    selected_by_percent REAL,            -- ownership %: current for the live season, season-end snapshot for archive seasons
    PRIMARY KEY (season_id, player_code)
);
CREATE INDEX IF NOT EXISTS idx_player_season_element ON player_season(season_id, season_element_id);

CREATE TABLE IF NOT EXISTS fixtures (
    season_id     TEXT NOT NULL REFERENCES seasons(id),
    fixture_id    INTEGER NOT NULL,
    round         INTEGER,
    kickoff_time  TEXT,
    team_h_code   INTEGER,
    team_a_code   INTEGER,
    team_h_score  INTEGER,
    team_a_score  INTEGER,
    PRIMARY KEY (season_id, fixture_id)
);
CREATE INDEX IF NOT EXISTS idx_fixtures_round ON fixtures(season_id, round);

CREATE TABLE IF NOT EXISTS player_gw_stats (
    season_id                    TEXT NOT NULL,
    player_code                  INTEGER NOT NULL,
    round                        INTEGER NOT NULL,
    fixture_id                   INTEGER NOT NULL,
    team_code                    INTEGER NOT NULL,
    opponent_team_code           INTEGER,
    was_home                     INTEGER,
    minutes                      INTEGER,
    starts                       INTEGER,       -- NULL for seasons before 2022/23
    goals_scored                 INTEGER,
    assists                      INTEGER,
    clean_sheets                 INTEGER,
    goals_conceded                INTEGER,
    bonus                        INTEGER,
    bps                          INTEGER,
    total_points                 INTEGER,
    expected_goals                REAL,   -- NULL before 2022/23
    expected_assists              REAL,   -- NULL before 2022/23
    expected_goal_involvements    REAL,   -- NULL before 2022/23
    expected_goals_conceded       REAL,   -- NULL before 2022/23
    defensive_contribution        INTEGER, -- NULL before 2025/26
    saves                         INTEGER,
    yellow_cards                  INTEGER,
    red_cards                     INTEGER,
    influence                     REAL,
    creativity                    REAL,
    threat                        REAL,
    ict_index                     REAL,
    price                        INTEGER, -- price * 10 at time of this gameweek
    PRIMARY KEY (season_id, player_code, round, fixture_id)
);
CREATE INDEX IF NOT EXISTS idx_pgs_player_round ON player_gw_stats(season_id, player_code, round);
CREATE INDEX IF NOT EXISTS idx_pgs_team_round ON player_gw_stats(season_id, team_code, round);
CREATE INDEX IF NOT EXISTS idx_pgs_opponent_round ON player_gw_stats(season_id, opponent_team_code, round);

-- The user's own FPL squad, synced from the live FPL API (see app/my_team.py) so it can be
-- highlighted on the player board. One squad per season - this is a single-user local app.
CREATE TABLE IF NOT EXISTS manager_entry (
    season_id    TEXT PRIMARY KEY REFERENCES seasons(id),
    entry_id     INTEGER NOT NULL,   -- the manager's FPL team id (from their team URL)
    entry_name   TEXT,
    manager_name TEXT,
    synced_event INTEGER,            -- NULL until a gameweek has started and picks become public
    synced_at    TEXT,
    bank         INTEGER,            -- money in the bank * 10, as of synced_event
    started_event INTEGER            -- the gameweek this entry first played
);

CREATE TABLE IF NOT EXISTS manager_squad (
    season_id       TEXT NOT NULL REFERENCES seasons(id),
    player_code     INTEGER NOT NULL,  -- stable code, resolved via the live bootstrap
    squad_slot      INTEGER NOT NULL,  -- FPL's own 1-15 ordering; 1-11 start, 12-15 bench
    is_captain      INTEGER NOT NULL DEFAULT 0,
    is_vice_captain INTEGER NOT NULL DEFAULT 0,
    multiplier      INTEGER,
    purchase_price  INTEGER,           -- what this player was bought for, * 10 (see app/my_team.py)
    purchase_estimated INTEGER,        -- 1 when purchase_price is a best guess, not an API fact
    PRIMARY KEY (season_id, player_code)
);

-- Forward-looking expected-points projections, imported from an external model
-- (see app/projections.py). Keyed per gameweek so any horizon can be summed at query time,
-- and per source so two models can be held side by side and compared.
CREATE TABLE IF NOT EXISTS player_projections (
    season_id   TEXT NOT NULL REFERENCES seasons(id),
    player_code INTEGER NOT NULL,   -- stable code, resolved from the live bootstrap on import
    round       INTEGER NOT NULL,   -- gameweek this projection is for
    source      TEXT NOT NULL,      -- e.g. 'fplreview'
    xp          REAL,               -- projected FPL points
    xmins       REAL,               -- projected minutes; the availability signal, often the point
    xg          REAL,               -- projected expected goals for the gameweek
    xa          REAL,               -- projected expected assists
    xcs         REAL,               -- clean sheet probability
    xdc         REAL,               -- probability of hitting the defensive-contribution threshold
    imported_at TEXT,
    PRIMARY KEY (season_id, player_code, round, source)
);
CREATE INDEX IF NOT EXISTS idx_proj_round ON player_projections(season_id, source, round);

-- Daily price snapshots of the live season (see app/prices.py). FPL moves prices once a night
-- and the API only ever serves "now", so the history has to be recorded as it happens - nothing
-- can backfill it later. One row per player per *price day*: the UK date of the overnight change
-- a capture follows. Re-capturing within a day overwrites, so each day ends up holding the last
-- reading before the next change - the transfer counts that change was decided on.
CREATE TABLE IF NOT EXISTS price_snapshot_days (
    season_id     TEXT NOT NULL REFERENCES seasons(id),
    price_day     TEXT NOT NULL,     -- 'YYYY-MM-DD'
    captured_at   TEXT NOT NULL,     -- UTC ISO timestamp of the capture this day currently holds
    event         INTEGER,           -- gameweek in progress (or next up) at capture time
    total_players INTEGER,           -- FPL managers overall; turns ownership % into a head count
    PRIMARY KEY (season_id, price_day)
);

CREATE TABLE IF NOT EXISTS player_price_snapshots (
    season_id           TEXT NOT NULL,
    player_code         INTEGER NOT NULL,
    price_day           TEXT NOT NULL,
    now_cost            INTEGER NOT NULL,  -- price * 10
    cost_change_event   INTEGER,           -- net change this gameweek, * 10
    cost_change_start   INTEGER,           -- net change since the season started, * 10
    transfers_in        INTEGER,           -- season-cumulative; day-over-day deltas come from these
    transfers_out       INTEGER,
    transfers_in_event  INTEGER,           -- this gameweek only; resets at each deadline
    transfers_out_event INTEGER,
    selected_by_percent REAL,
    status              TEXT,              -- a / d / i / s / u / n, as FPL flags availability
    PRIMARY KEY (season_id, player_code, price_day)
);
CREATE INDEX IF NOT EXISTS idx_pps_day ON player_price_snapshots(season_id, price_day);

-- Mini-league tracking (see app/leagues.py): the other managers in one of the user's private
-- leagues, week by week. Entry rows are keyed by entry, not league, so a rival who shares two
-- leagues with the user is fetched and stored once.
CREATE TABLE IF NOT EXISTS leagues (
    season_id  TEXT NOT NULL REFERENCES seasons(id),
    league_id  INTEGER NOT NULL,
    name       TEXT NOT NULL,
    synced_at  TEXT,
    PRIMARY KEY (season_id, league_id)
);

CREATE TABLE IF NOT EXISTS league_entries (
    season_id   TEXT NOT NULL,
    league_id   INTEGER NOT NULL,
    entry_id    INTEGER NOT NULL,
    entry_name  TEXT,
    player_name TEXT,
    PRIMARY KEY (season_id, league_id, entry_id)
);

CREATE TABLE IF NOT EXISTS entry_events (
    season_id            TEXT NOT NULL,
    entry_id             INTEGER NOT NULL,
    event                INTEGER NOT NULL,
    points               INTEGER,   -- gameweek score before transfer hits, as FPL shows it
    total_points         INTEGER,   -- season total after this gameweek, hits included
    event_transfers      INTEGER,
    event_transfers_cost INTEGER,   -- points spent on hits this gameweek
    points_on_bench      INTEGER,
    overall_rank         INTEGER,
    bank                 INTEGER,   -- * 10
    value                INTEGER,   -- squad at market price + bank, * 10
    active_chip          TEXT,      -- wildcard / freehit / bboost / 3xc, or NULL
    final                INTEGER NOT NULL DEFAULT 0,  -- 1 once FPL has finished and checked the gameweek
    PRIMARY KEY (season_id, entry_id, event)
);

CREATE TABLE IF NOT EXISTS entry_picks (
    season_id       TEXT NOT NULL,
    entry_id        INTEGER NOT NULL,
    event           INTEGER NOT NULL,
    player_code     INTEGER NOT NULL,
    web_name        TEXT,              -- kept so a pick still reads if the player isn't on the board yet
    position        TEXT,
    squad_slot      INTEGER NOT NULL,  -- as picked: 1-11 started, 12-15 bench (auto-subs undone)
    multiplier      INTEGER NOT NULL,  -- as scored: 0 bench, 1, 2 captain, 3 triple captain
    is_captain      INTEGER NOT NULL DEFAULT 0,
    is_vice_captain INTEGER NOT NULL DEFAULT 0,
    auto_sub        TEXT,              -- 'in' / 'out' when FPL's automatic substitution touched this pick
    points          INTEGER,           -- the player's own gameweek points, before the multiplier
    minutes         INTEGER,
    xp              REAL,              -- projection held when the pick was first stored; never rewritten
    PRIMARY KEY (season_id, entry_id, event, player_code)
);
