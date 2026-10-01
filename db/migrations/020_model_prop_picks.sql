-- paper-trading ledger for model player-prop picks. a pick is recorded once
-- per run with the market as it stood when surfaced; settlement fills the
-- closing price and the result later, nothing else is edited.

CREATE TABLE IF NOT EXISTS model_prop_picks (
    id BIGSERIAL PRIMARY KEY,
    prediction_run_id INT NOT NULL REFERENCES prediction_runs (id),
    nba_player_id TEXT NOT NULL,
    -- display only, as the book printed it
    player_name TEXT,
    nba_game_id TEXT NOT NULL,
    game_date DATE NOT NULL,
    market TEXT NOT NULL
      CHECK (market IN ('pts', 'reb', 'ast', 'fg3m', 'pra', 'stl', 'blk', 'tov')),
    line NUMERIC NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('over', 'under')),
    bookmaker TEXT NOT NULL,
    -- american odds at surfacing
    price INTEGER NOT NULL,
    implied_prob NUMERIC NOT NULL,
    -- NULL when the book posted only one side
    implied_prob_novig NUMERIC,
    -- P(win) under void_rule
    model_prob NUMERIC NOT NULL,
    -- P(win | he plays)
    model_prob_plays NUMERIC NOT NULL,
    prob_active NUMERIC NOT NULL,
    void_rule TEXT NOT NULL CHECK (void_rule IN ('dnp_void', 'dnp_loss')),
    -- per $1 staked at price
    ev NUMERIC NOT NULL,
    kelly_fraction NUMERIC NOT NULL,
    surfaced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- the last snapshot before tipoff for this player, game, market and book
    closing_price INTEGER,
    closing_line NUMERIC,
    result TEXT CHECK (result IN ('win', 'loss', 'push', 'void')),
    actual NUMERIC,
    settled_at TIMESTAMPTZ,
    UNIQUE (prediction_run_id, nba_player_id, nba_game_id, market, line, side, bookmaker)
);

CREATE INDEX IF NOT EXISTS idx_model_prop_picks_game_date
  ON model_prop_picks (game_date, prediction_run_id);
CREATE INDEX IF NOT EXISTS idx_model_prop_picks_unsettled
  ON model_prop_picks (nba_game_id)
  WHERE result IS NULL;
