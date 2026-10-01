-- odds history, append-only: a snapshot writes new rows and never edits old
-- ones, so a backtest can replay what the market said at any instant.

CREATE TABLE IF NOT EXISTS odds_snapshots (
    id BIGSERIAL PRIMARY KEY,
    espn_event_id TEXT NOT NULL,
    -- NULL when the event could not be matched to an nba_schedule game
    nba_game_id TEXT,
    -- eastern game date, not utc
    game_date DATE NOT NULL,
    provider TEXT NOT NULL,
    market TEXT NOT NULL CHECK (market IN ('spread', 'total', 'moneyline')),
    selection TEXT NOT NULL CHECK (selection IN ('home', 'away', 'over', 'under')),
    -- spread: the selection's own line (away is the home line sign-flipped);
    -- total: the total for both over and under; moneyline: NULL
    line NUMERIC,
    -- american odds, NULL when the provider published none, never a default
    price INTEGER,
    price_observed BOOLEAN NOT NULL,
    provider_updated_at TIMESTAMPTZ,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source TEXT NOT NULL,
    ingestion_run_id INTEGER REFERENCES ingestion_runs (id) ON DELETE SET NULL,
    CHECK (price_observed = (price IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_odds_snapshots_game_captured
  ON odds_snapshots (nba_game_id, captured_at);
CREATE INDEX IF NOT EXISTS idx_odds_snapshots_event_captured
  ON odds_snapshots (espn_event_id, captured_at);

-- espn event id to nba game id; the two id spaces do not join on their own.
CREATE TABLE IF NOT EXISTS espn_event_map (
    espn_event_id TEXT PRIMARY KEY,
    nba_game_id TEXT NOT NULL,
    game_date DATE NOT NULL,
    home_team_abbr TEXT,
    away_team_abbr TEXT,
    -- 'date_abbr': matched on et game date plus home and away tricodes
    mapped_by TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
