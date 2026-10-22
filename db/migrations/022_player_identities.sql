-- the name every player who dressed or sat inactive was listed under on the
-- nba.com box score, so a rookie has a name without stats.nba.com.
CREATE TABLE IF NOT EXISTS player_identities (
    nba_player_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    position TEXT,
    headshot_url TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
