CREATE TABLE IF NOT EXISTS players (
    id SERIAL PRIMARY KEY,
    nba_id VARCHAR(20) UNIQUE,
    name VARCHAR(100) NOT NULL,
    team VARCHAR(50),
    position VARCHAR(20),
    points_per_game NUMERIC(5,1) DEFAULT 0,
    rebounds_per_game NUMERIC(5,1) DEFAULT 0,
    assists_per_game NUMERIC(5,1) DEFAULT 0,
    steals_per_game NUMERIC(4,1) DEFAULT 0,
    blocks_per_game NUMERIC(4,1) DEFAULT 0,
    field_goal_percentage NUMERIC(5,1) DEFAULT 0,
    three_point_percentage NUMERIC(5,1) DEFAULT 0,
    free_throw_percentage NUMERIC(5,1) DEFAULT 0,
    three_pointers_made NUMERIC(4,1) DEFAULT 0,
    turnovers_per_game NUMERIC(4,1) DEFAULT 0,
    minutes_per_game NUMERIC(5,1) DEFAULT 0,
    games_played INTEGER DEFAULT 0,
    injury_status VARCHAR(50),
    injury_detail VARCHAR(255),
    headshot_url VARCHAR(500),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS teams (
    id SERIAL PRIMARY KEY,
    nba_id VARCHAR(20) UNIQUE,
    name VARCHAR(100) NOT NULL,
    abbreviation VARCHAR(10),
    conference VARCHAR(10),
    division VARCHAR(20),
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,
    points_per_game NUMERIC(5,1) DEFAULT 0,
    rebounds_per_game NUMERIC(5,1) DEFAULT 0,
    assists_per_game NUMERIC(5,1) DEFAULT 0,
    steals_per_game NUMERIC(4,1) DEFAULT 0,
    blocks_per_game NUMERIC(4,1) DEFAULT 0,
    field_goal_percentage NUMERIC(5,1) DEFAULT 0,
    three_point_percentage NUMERIC(5,1) DEFAULT 0,
    free_throw_percentage NUMERIC(5,1) DEFAULT 0,
    turnovers_per_game NUMERIC(4,1) DEFAULT 0,
    defensive_rating NUMERIC(5,1) DEFAULT 0,
    offensive_rating NUMERIC(5,1) DEFAULT 0,
    net_rating NUMERIC(5,1) DEFAULT 0,
    logo_url VARCHAR(500),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS games (
    id SERIAL PRIMARY KEY,
    nba_game_id VARCHAR(20) UNIQUE,
    home_team VARCHAR(100) NOT NULL,
    away_team VARCHAR(100) NOT NULL,
    game_date DATE NOT NULL,
    home_score INTEGER,
    away_score INTEGER,
    status VARCHAR(50) DEFAULT 'Scheduled',
    arena VARCHAR(100),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(255),
    password_hash TEXT,
    google_id VARCHAR(64) UNIQUE,
    name VARCHAR(100),
    phone VARCHAR(30),
    -- server-enforced admin role; granted manually, never via signup.
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower_idx
  ON users (LOWER(email))
  WHERE email IS NOT NULL;

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_password_reset_user ON password_reset_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_password_reset_expires ON password_reset_tokens(expires_at);

CREATE TABLE IF NOT EXISTS my_roster (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    added_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (user_id, player_id)
);

CREATE TABLE IF NOT EXISTS analysis_cache (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    roster_hash VARCHAR(64) NOT NULL,
    analysis JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS waiver_cache (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    roster_hash VARCHAR(64) NOT NULL,
    suggestions JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- bets ledger. straight bets (spread/total/moneyline) settle automatically
-- from final scores; prop/parlay/custom entries are free text and settle
-- manually. game fields nullable for multi-game and off-book bets. no money
-- columns: the ledger tracks outcomes, not stakes.
CREATE TABLE IF NOT EXISTS bets (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    market VARCHAR(10) NOT NULL CHECK (market IN ('spread', 'total', 'moneyline', 'prop', 'parlay', 'custom')),
    nba_game_id VARCHAR(20),
    home_team VARCHAR(100),
    away_team VARCHAR(100),
    game_date DATE,
    selection VARCHAR(5) CHECK (selection IN ('home', 'away', 'over', 'under')),
    line NUMERIC(5,1),
    american_odds INTEGER,
    description VARCHAR(300),
    stake NUMERIC(10,2) CHECK (stake > 0),
    wager_type VARCHAR(12) NOT NULL DEFAULT 'cash' CHECK (wager_type IN ('cash', 'bonus_bet', 'odds_boost')),
    status VARCHAR(7) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'won', 'lost', 'push')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    settled_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_bets_user ON bets(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS betting_cache (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    odds_hash VARCHAR(64) NOT NULL,
    picks JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chat_history (
    id SERIAL PRIMARY KEY,
    context_type VARCHAR(50) NOT NULL,
    context_id INTEGER NOT NULL,
    role VARCHAR(20) NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- one row per SPA navigation, posted by the frontend tracker. user_id is null
-- for logged-out visitors; ON DELETE SET NULL keeps history if a user is removed.
-- only the pathname is stored (never query strings — reset tokens live there).
CREATE TABLE IF NOT EXISTS page_views (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    path VARCHAR(300) NOT NULL,
    referrer VARCHAR(300),
    user_agent VARCHAR(300),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_page_views_created ON page_views(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_page_views_user ON page_views(user_id, created_at DESC);

-- fixed-window request counters backing the app-level rate limiter. one row
-- per (bucket, window_start); buckets are scope-prefixed, e.g. 'login:1.2.3.4'.
CREATE TABLE IF NOT EXISTS rate_limits (
    bucket TEXT NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (bucket, window_start)
);

CREATE INDEX IF NOT EXISTS idx_rate_limits_window ON rate_limits(window_start);

-- season-by-season history, one row per player per season. deliberately not
-- foreign-keyed to players: that table is churned on every scrape and holds
-- only current players, while this one holds retired players too. join on
-- nba_player_id = players.nba_id when linking is needed. every stat column is
-- nullable — NULL records "the API did not report this", not a real 0.
CREATE TABLE IF NOT EXISTS player_season_stats (
    id SERIAL PRIMARY KEY,
    nba_player_id VARCHAR(20) NOT NULL,
    player_name VARCHAR(100) NOT NULL,
    season VARCHAR(7) NOT NULL,
    team VARCHAR(10),
    games_played INTEGER,
    minutes_per_game NUMERIC(5,1),
    points_per_game NUMERIC(5,1),
    rebounds_per_game NUMERIC(5,1),
    assists_per_game NUMERIC(5,1),
    steals_per_game NUMERIC(4,1),
    blocks_per_game NUMERIC(4,1),
    turnovers_per_game NUMERIC(4,1),
    field_goal_percentage NUMERIC(5,1),
    three_point_percentage NUMERIC(5,1),
    free_throw_percentage NUMERIC(5,1),
    three_pointers_made NUMERIC(4,1),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (nba_player_id, season)
);

-- one row per team per season. team_abbreviation is whatever the API reported
-- for that season, so defunct franchises (SEA, VAN, NJN, KCK) keep their own
-- identity instead of being mapped onto a current team. ratings are nullable
-- because the advanced measure type only goes back to 1996-97.
CREATE TABLE IF NOT EXISTS team_season_stats (
    id SERIAL PRIMARY KEY,
    nba_team_id VARCHAR(20) NOT NULL,
    team_name VARCHAR(100) NOT NULL,
    team_abbreviation VARCHAR(10),
    season VARCHAR(7) NOT NULL,
    games_played INTEGER,
    wins INTEGER,
    losses INTEGER,
    minutes_per_game NUMERIC(5,1),
    points_per_game NUMERIC(5,1),
    rebounds_per_game NUMERIC(5,1),
    assists_per_game NUMERIC(5,1),
    steals_per_game NUMERIC(4,1),
    blocks_per_game NUMERIC(4,1),
    turnovers_per_game NUMERIC(4,1),
    field_goal_percentage NUMERIC(5,1),
    three_point_percentage NUMERIC(5,1),
    free_throw_percentage NUMERIC(5,1),
    defensive_rating NUMERIC(5,1),
    offensive_rating NUMERIC(5,1),
    net_rating NUMERIC(5,1),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (nba_team_id, season)
);

-- NBA 2K ratings, via nba2kapi.com. key-value: 2K reshuffles its attribute
-- and badge sets every year. not foreign-keyed to players, no shared id.
CREATE TABLE IF NOT EXISTS nba_2k_players (
    id SERIAL PRIMARY KEY,
    slug VARCHAR(120) UNIQUE NOT NULL,
    name VARCHAR(100) NOT NULL,
    -- accent-, suffix-, and punctuation-stripped name, written by the scraper's
    -- _normalize_name. stored so linking to players is an indexed equality test.
    normalized_name VARCHAR(100) NOT NULL,
    team VARCHAR(60),
    -- curr = current rosters, class = classic teams, allt = all-time teams.
    team_type VARCHAR(10) NOT NULL,
    overall SMALLINT,
    -- comma-joined, matching the convention players.position uses.
    positions VARCHAR(20),
    game_version VARCHAR(10),
    archetype VARCHAR(60),
    build VARCHAR(60),
    height VARCHAR(10),
    weight VARCHAR(15),
    wingspan VARCHAR(10),
    player_image TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 35 rows per rated card in 2K27. value is nullable, and a card can legitimately
-- have no attribute rows at all — unrated rookies carry an overall and nothing else.
CREATE TABLE IF NOT EXISTS nba_2k_attributes (
    player_slug VARCHAR(120) NOT NULL
        REFERENCES nba_2k_players (slug) ON DELETE CASCADE,
    attribute_name VARCHAR(40) NOT NULL,
    value SMALLINT,
    PRIMARY KEY (player_slug, attribute_name)
);

-- tiers observed: Legendary, Hall of Fame, Gold, Silver, Bronze.
CREATE TABLE IF NOT EXISTS nba_2k_badges (
    player_slug VARCHAR(120) NOT NULL
        REFERENCES nba_2k_players (slug) ON DELETE CASCADE,
    badge_name VARCHAR(60) NOT NULL,
    tier VARCHAR(20),
    category VARCHAR(40),
    description TEXT,
    image_url TEXT,
    PRIMARY KEY (player_slug, badge_name)
);

-- a card's overall in every 2K game it appeared in, 2K10 onward. delta is
-- nullable: the oldest entry has nothing to diff against.
CREATE TABLE IF NOT EXISTS nba_2k_rating_history (
    player_slug VARCHAR(120) NOT NULL
        REFERENCES nba_2k_players (slug) ON DELETE CASCADE,
    game_version VARCHAR(10) NOT NULL,
    overall SMALLINT,
    delta SMALLINT,
    PRIMARY KEY (player_slug, game_version)
);

-- data truth layer (migration 013): schedule, per-game logs, and one status
-- row per scheduled player-game, the training universe for availability
-- prediction. ids are TEXT (NBA game ids carry leading zeros).

-- read by scraper/check_migrations.py
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- one row per truth-layer scraper phase invocation, for tracing which rows came
-- from which run. watermark_* are TEXT because the incremental sync records game
-- dates and the backfill records season labels.
CREATE TABLE IF NOT EXISTS ingestion_runs (
    id SERIAL PRIMARY KEY,
    kind TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running',
    watermark_from TEXT,
    watermark_to TEXT,
    rows_written INTEGER,
    notes TEXT
);

-- every scheduled game, played or not, keyed on NBA's game id. Separate from
-- `games`, which keys on ESPN's event id — the two id spaces do not join.
CREATE TABLE IF NOT EXISTS nba_schedule (
    id SERIAL PRIMARY KEY,
    nba_game_id TEXT NOT NULL UNIQUE,
    season TEXT NOT NULL,
    season_type TEXT NOT NULL,
    game_date DATE NOT NULL,
    scheduled_at TIMESTAMPTZ,
    home_team_id TEXT,
    away_team_id TEXT,
    home_team_abbr TEXT,
    away_team_abbr TEXT,
    game_status TEXT,
    postponed_status TEXT,
    source TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- one row per player per game they recorded a line for. minutes are decimal
-- (34.20 for "34:12"); dnp_reason is the verbatim box-score COMMENT.
CREATE TABLE IF NOT EXISTS player_game_logs (
    id SERIAL PRIMARY KEY,
    nba_player_id TEXT NOT NULL,
    nba_game_id TEXT NOT NULL,
    season TEXT NOT NULL,
    season_type TEXT NOT NULL,
    game_date DATE NOT NULL,
    team_id TEXT,
    team_abbr TEXT,
    opponent_team_id TEXT,
    is_home BOOLEAN,
    started BOOLEAN,
    minutes NUMERIC(5,2),
    pts SMALLINT,
    reb SMALLINT,
    ast SMALLINT,
    stl SMALLINT,
    blk SMALLINT,
    tov SMALLINT,
    fgm SMALLINT,
    fga SMALLINT,
    fg3m SMALLINT,
    fg3a SMALLINT,
    ftm SMALLINT,
    fta SMALLINT,
    plus_minus SMALLINT,
    dnp_reason TEXT,
    source TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ingestion_run_id INTEGER REFERENCES ingestion_runs (id) ON DELETE SET NULL,
    -- migration 018: from boxscoretraditionalv3, which also fills started and
    -- dnp_reason above. position is the box-score string, starters only.
    oreb SMALLINT,
    dreb SMALLINT,
    pf SMALLINT,
    position TEXT,
    details_source TEXT,
    -- NULL marks a row the box-score pass has not reached yet
    details_fetched_at TIMESTAMPTZ,
    UNIQUE (nba_player_id, nba_game_id)
);

-- two rows per game. doubles as the completed-game schedule, since one league
-- game log request returns both sides of every played game. no wins/losses:
-- those are standings, not a property of this game.
CREATE TABLE IF NOT EXISTS team_game_logs (
    id SERIAL PRIMARY KEY,
    team_id TEXT NOT NULL,
    nba_game_id TEXT NOT NULL,
    season TEXT NOT NULL,
    season_type TEXT NOT NULL,
    game_date DATE NOT NULL,
    team_abbr TEXT,
    opponent_team_id TEXT,
    is_home BOOLEAN,
    minutes NUMERIC(6,2),
    pts SMALLINT,
    reb SMALLINT,
    ast SMALLINT,
    stl SMALLINT,
    blk SMALLINT,
    tov SMALLINT,
    fgm SMALLINT,
    fga SMALLINT,
    fg3m SMALLINT,
    fg3a SMALLINT,
    ftm SMALLINT,
    fta SMALLINT,
    plus_minus SMALLINT,
    source TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ingestion_run_id INTEGER REFERENCES ingestion_runs (id) ON DELETE SET NULL,
    -- migration 018: team totals from boxscoretraditionalv3
    oreb SMALLINT,
    dreb SMALLINT,
    pf SMALLINT,
    UNIQUE (team_id, nba_game_id)
);

-- one row per scheduled player-game, appeared or not: the ML training universe.
-- listed_inactive is NULL when the inactive list has not been fetched yet —
-- "we do not know" must not collapse into "he was active".
CREATE TABLE IF NOT EXISTS player_game_status (
    id SERIAL PRIMARY KEY,
    nba_player_id TEXT NOT NULL,
    nba_game_id TEXT NOT NULL,
    team_id TEXT,
    rostered BOOLEAN NOT NULL,
    listed_inactive BOOLEAN,
    started BOOLEAN,
    played BOOLEAN NOT NULL,
    dnp_reason TEXT,
    minutes NUMERIC(5,2),
    source TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ingestion_run_id INTEGER REFERENCES ingestion_runs (id) ON DELETE SET NULL,
    UNIQUE (nba_player_id, nba_game_id)
);

-- which team a player belonged to over which span. valid_to NULL = still open.
-- needed so a feature computed against his current team cannot leak a trade
-- backwards into pre-trade rows.
CREATE TABLE IF NOT EXISTS player_team_stints (
    id SERIAL PRIMARY KEY,
    nba_player_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    valid_from DATE NOT NULL,
    valid_to DATE,
    source TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (nba_player_id, team_id, valid_from)
);

-- append-only log of scraped injury designations. append-only because the model
-- asks "what was known at the time", which an overwrite-in-place table cannot
-- answer. players.injury_status keeps its overwrite behaviour for the UI.
CREATE TABLE IF NOT EXISTS player_injury_reports (
    id SERIAL PRIMARY KEY,
    nba_player_id TEXT NOT NULL,
    nba_game_id TEXT,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    report_as_of TIMESTAMPTZ,
    status_raw TEXT,
    status_normalized TEXT,
    reason TEXT,
    source TEXT NOT NULL,
    team_id TEXT,
    report_url TEXT,
    team_submitted BOOLEAN,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- odds history (migration 016), append-only so a backtest can replay what the
-- market said at any instant. away spread lines are the home line sign-flipped;
-- price is NULL when the provider published none, never a default.
CREATE TABLE IF NOT EXISTS odds_snapshots (
    id BIGSERIAL PRIMARY KEY,
    espn_event_id TEXT NOT NULL,
    nba_game_id TEXT,
    game_date DATE NOT NULL,
    provider TEXT NOT NULL,
    market TEXT NOT NULL CHECK (market IN ('spread', 'total', 'moneyline')),
    selection TEXT NOT NULL CHECK (selection IN ('home', 'away', 'over', 'under')),
    line NUMERIC,
    price INTEGER,
    price_observed BOOLEAN NOT NULL,
    provider_updated_at TIMESTAMPTZ,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source TEXT NOT NULL,
    ingestion_run_id INTEGER REFERENCES ingestion_runs (id) ON DELETE SET NULL,
    CHECK (price_observed = (price IS NOT NULL))
);

-- espn event id to nba game id; the two id spaces do not join on their own.
CREATE TABLE IF NOT EXISTS espn_event_map (
    espn_event_id TEXT PRIMARY KEY,
    nba_game_id TEXT NOT NULL,
    game_date DATE NOT NULL,
    home_team_abbr TEXT,
    away_team_abbr TEXT,
    mapped_by TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- prediction store (migrations 014, 015), append-only: a run writes new rows,
-- never edits old ones, so a backtest measures foresight not hindsight.
-- forecast_cutoff_at is the latest instant any input was allowed to see:
-- the later of information_as_of and the start of the first scored day.
CREATE TABLE IF NOT EXISTS prediction_runs (
    id SERIAL PRIMARY KEY,
    model_version TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    code_sha TEXT,
    trained_through DATE,
    predicted_at TIMESTAMPTZ NOT NULL,
    forecast_cutoff_at TIMESTAMPTZ NOT NULL,
    artifact_checksum TEXT,
    -- the serving path reads only 'complete'; a killed run leaves a partial slate.
    status TEXT NOT NULL DEFAULT 'complete',
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    -- the app serves only 'production'; 'shadow' runs are recorded for comparison.
    channel TEXT NOT NULL DEFAULT 'production'
        CONSTRAINT prediction_runs_channel_check CHECK (channel IN ('production', 'shadow')),
    -- the latest injury-report instant the run was allowed to see.
    information_as_of TIMESTAMPTZ,
    -- the last game date whose outcomes were in the feature frame.
    history_through DATE
);

-- one row per (run, player, game, stat, quantile). Long format because the stat
-- vocabulary grows. conditional TRUE = "given he plays"; FALSE = over the
-- schedule, already multiplied by P(play) — roughly half the size, and serving
-- one where the other was meant is not a rounding error. quantile NULL = an
-- expected value; 0.10/0.50/0.90 are empirical prediction quantiles.
-- Stat names: 'prob_active' (unconditional, [0,1]), the bare stat name for the
-- conditional estimate, '<stat>_uncond' for the schedule-level expectation.
CREATE TABLE IF NOT EXISTS player_game_predictions (
    id BIGSERIAL PRIMARY KEY,
    prediction_run_id INT NOT NULL REFERENCES prediction_runs (id),
    nba_player_id TEXT NOT NULL,
    nba_game_id TEXT NOT NULL,
    game_date DATE NOT NULL,
    stat TEXT NOT NULL,
    quantile NUMERIC(3,2),
    value NUMERIC NOT NULL,
    conditional BOOLEAN NOT NULL,
    UNIQUE (prediction_run_id, nba_player_id, nba_game_id, stat, quantile)
);

CREATE INDEX IF NOT EXISTS idx_players_team ON players(team);
CREATE INDEX IF NOT EXISTS idx_players_position ON players(position);
CREATE INDEX IF NOT EXISTS idx_players_name ON players(name);
CREATE INDEX IF NOT EXISTS idx_games_date ON games(game_date);
CREATE INDEX IF NOT EXISTS idx_chat_history_context ON chat_history(context_type, context_id);
CREATE INDEX IF NOT EXISTS idx_player_season_stats_season ON player_season_stats(season, points_per_game DESC);
CREATE INDEX IF NOT EXISTS idx_player_season_stats_name ON player_season_stats(player_name);
CREATE INDEX IF NOT EXISTS idx_team_season_stats_season ON team_season_stats(season);
CREATE INDEX IF NOT EXISTS idx_nba_2k_players_team_type_overall ON nba_2k_players(team_type, overall DESC);
CREATE INDEX IF NOT EXISTS idx_nba_2k_players_overall ON nba_2k_players(overall DESC);
CREATE INDEX IF NOT EXISTS idx_nba_2k_players_name ON nba_2k_players(name);
CREATE INDEX IF NOT EXISTS idx_nba_2k_players_normalized_name ON nba_2k_players(normalized_name);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_kind ON ingestion_runs(kind, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_nba_schedule_date ON nba_schedule(game_date);
CREATE INDEX IF NOT EXISTS idx_nba_schedule_season ON nba_schedule(season, season_type);
CREATE INDEX IF NOT EXISTS idx_player_game_logs_player_date ON player_game_logs(nba_player_id, game_date);
CREATE INDEX IF NOT EXISTS idx_player_game_logs_season ON player_game_logs(season);
CREATE INDEX IF NOT EXISTS idx_player_game_logs_game ON player_game_logs(nba_game_id);
CREATE INDEX IF NOT EXISTS idx_team_game_logs_season ON team_game_logs(season);
CREATE INDEX IF NOT EXISTS idx_team_game_logs_game ON team_game_logs(nba_game_id);
CREATE INDEX IF NOT EXISTS idx_team_game_logs_team_date ON team_game_logs(team_id, game_date);
CREATE INDEX IF NOT EXISTS idx_player_game_status_player ON player_game_status(nba_player_id);
CREATE INDEX IF NOT EXISTS idx_player_game_status_game ON player_game_status(nba_game_id);
CREATE INDEX IF NOT EXISTS idx_player_team_stints_player ON player_team_stints(nba_player_id, valid_from);
-- a player belongs to exactly one team at a time, so at most one stint may be open.
CREATE UNIQUE INDEX IF NOT EXISTS idx_player_team_stints_one_open
  ON player_team_stints(nba_player_id) WHERE valid_to IS NULL;
CREATE INDEX IF NOT EXISTS idx_player_injury_reports_player_captured
  ON player_injury_reports(nba_player_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_player_injury_reports_game_as_of
  ON player_injury_reports(nba_game_id, report_as_of DESC) WHERE nba_game_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_odds_snapshots_game_captured ON odds_snapshots(nba_game_id, captured_at);
CREATE INDEX IF NOT EXISTS idx_odds_snapshots_event_captured ON odds_snapshots(espn_event_id, captured_at);
CREATE INDEX IF NOT EXISTS idx_prediction_runs_predicted_at ON prediction_runs(status, predicted_at DESC);
CREATE INDEX IF NOT EXISTS idx_prediction_runs_channel_served ON prediction_runs(channel, status, predicted_at DESC);
-- the UNIQUE constraint above does not bite for expected values: Postgres treats
-- NULL quantiles as distinct, so this is the index that stops a re-run doubling
-- a slate. -1 is safe as the sentinel — real quantiles are in (0,1).
CREATE UNIQUE INDEX IF NOT EXISTS idx_player_game_predictions_key
  ON player_game_predictions(prediction_run_id, nba_player_id, nba_game_id, stat, COALESCE(quantile, -1));
CREATE INDEX IF NOT EXISTS idx_player_game_predictions_player_date
  ON player_game_predictions(nba_player_id, game_date DESC);
CREATE INDEX IF NOT EXISTS idx_player_game_predictions_run
  ON player_game_predictions(prediction_run_id);
