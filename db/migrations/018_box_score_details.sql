-- per-game box score details from boxscoretraditionalv3: the rebound split,
-- personal fouls and the starter's listed position. Idempotent.
-- player_game_logs.started and dnp_reason already exist (migration 013) and
-- are now populated by this same path, along with player_game_status.started.

ALTER TABLE player_game_logs
  ADD COLUMN IF NOT EXISTS oreb SMALLINT;

ALTER TABLE player_game_logs
  ADD COLUMN IF NOT EXISTS dreb SMALLINT;

ALTER TABLE player_game_logs
  ADD COLUMN IF NOT EXISTS pf SMALLINT;

-- the box-score position string ('G', 'F', 'C'), set for the five starters only
ALTER TABLE player_game_logs
  ADD COLUMN IF NOT EXISTS position TEXT;

ALTER TABLE player_game_logs
  ADD COLUMN IF NOT EXISTS details_source TEXT;

-- NULL marks a row the box-score pass has not reached, which makes the
-- backfill resumable.
ALTER TABLE player_game_logs
  ADD COLUMN IF NOT EXISTS details_fetched_at TIMESTAMPTZ;

ALTER TABLE team_game_logs
  ADD COLUMN IF NOT EXISTS oreb SMALLINT;

ALTER TABLE team_game_logs
  ADD COLUMN IF NOT EXISTS dreb SMALLINT;

ALTER TABLE team_game_logs
  ADD COLUMN IF NOT EXISTS pf SMALLINT;
