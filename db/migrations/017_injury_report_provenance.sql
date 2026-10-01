-- provenance for the league's official injury report, which is filed per team
-- per game and published several times a day.

-- the team the report listed the player under, which can differ from players.team
ALTER TABLE player_injury_reports
  ADD COLUMN IF NOT EXISTS team_id TEXT;

ALTER TABLE player_injury_reports
  ADD COLUMN IF NOT EXISTS report_url TEXT;

-- false would mean the team had not filed; such teams get no rows today, so
-- written rows carry true and CBS rows carry NULL.
ALTER TABLE player_injury_reports
  ADD COLUMN IF NOT EXISTS team_submitted BOOLEAN;

CREATE INDEX IF NOT EXISTS idx_player_injury_reports_game_as_of
  ON player_injury_reports (nba_game_id, report_as_of DESC)
  WHERE nba_game_id IS NOT NULL;
