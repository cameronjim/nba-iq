-- prediction run provenance: which channel a run belongs to, and the two
-- information boundaries forecast_cutoff_at is derived from.

-- only 'production' runs are served; 'shadow' runs are recorded for comparison.
ALTER TABLE prediction_runs
  ADD COLUMN IF NOT EXISTS channel TEXT NOT NULL DEFAULT 'production';

ALTER TABLE prediction_runs
  DROP CONSTRAINT IF EXISTS prediction_runs_channel_check;

ALTER TABLE prediction_runs
  ADD CONSTRAINT prediction_runs_channel_check
  CHECK (channel IN ('production', 'shadow'));

-- the latest injury-report instant the run was allowed to see
ALTER TABLE prediction_runs
  ADD COLUMN IF NOT EXISTS information_as_of TIMESTAMPTZ;

-- the last game date whose outcomes were in the feature frame
ALTER TABLE prediction_runs
  ADD COLUMN IF NOT EXISTS history_through DATE;

CREATE INDEX IF NOT EXISTS idx_prediction_runs_channel_served
  ON prediction_runs (channel, status, predicted_at DESC);
