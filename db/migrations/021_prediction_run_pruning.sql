-- retention: a pruned run keeps its prediction_runs row and loses its
-- player_game_predictions rows. NULL means the rows are all still there.
ALTER TABLE prediction_runs
  ADD COLUMN IF NOT EXISTS pruned_at TIMESTAMPTZ;
