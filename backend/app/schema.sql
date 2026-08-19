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
    synced_at    TEXT
);

CREATE TABLE IF NOT EXISTS manager_squad (
    season_id       TEXT NOT NULL REFERENCES seasons(id),
    player_code     INTEGER NOT NULL,  -- stable code, resolved via the live bootstrap
    squad_slot      INTEGER NOT NULL,  -- FPL's own 1-15 ordering; 1-11 start, 12-15 bench
    is_captain      INTEGER NOT NULL DEFAULT 0,
    is_vice_captain INTEGER NOT NULL DEFAULT 0,
    multiplier      INTEGER,
    PRIMARY KEY (season_id, player_code)
);
