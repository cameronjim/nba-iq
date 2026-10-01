-- player prop odds history, append-only like odds_snapshots: a snapshot writes
-- new rows and never edits old ones.

CREATE TABLE IF NOT EXISTS prop_odds_snapshots (
    id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    provider_event_id TEXT NOT NULL,
    -- NULL when the event could not be matched to an nba_schedule game
    nba_game_id TEXT,
    -- eastern game date, not utc
    game_date DATE NOT NULL,
    bookmaker TEXT NOT NULL,
    market TEXT NOT NULL CHECK (market IN ('pts', 'reb', 'ast', 'fg3m', 'pra', 'stl', 'blk', 'tov')),
    -- the provider's spelling; nba_player_id is NULL when it matched no one
    player_name TEXT NOT NULL,
    nba_player_id TEXT,
    line NUMERIC NOT NULL,
    -- american odds, NULL when the book published only one side
    over_price INTEGER,
    under_price INTEGER,
    provider_updated_at TIMESTAMPTZ,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source TEXT NOT NULL,
    ingestion_run_id INTEGER REFERENCES ingestion_runs (id) ON DELETE SET NULL,
    CHECK (over_price IS NOT NULL OR under_price IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_prop_odds_snapshots_player_market
  ON prop_odds_snapshots (nba_player_id, game_date, market, captured_at);
CREATE INDEX IF NOT EXISTS idx_prop_odds_snapshots_game_captured
  ON prop_odds_snapshots (nba_game_id, captured_at);
