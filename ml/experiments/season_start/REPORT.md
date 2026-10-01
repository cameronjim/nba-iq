# Season-start minutes: offseason inputs vs the minutes model

**Status: not yet run.** This file records the reading rule before any result exists. ML Evaluate with `season_start: true` overwrites it with the measurements; the rule section is regenerated from the same constants, so a diff against this commit shows whether it moved.

## Pre-registered reading rule

Written before the first run and fixed in `analyze.py` as constants.

1. **The offseason-input hypothesis is SUPPORTED** if, in the counterfactual (b),
   setting `days_since_last_app` alone to 3 raises the frozen artifact's mean
   predicted minutes for star-tier (`roll10_MIN >= 30`) team-game-1 rows by more than
   **2.5 minutes**. A shift of 2.5 or less, or a downward
   shift, is NOT SUPPORTED. If the frozen artifact cannot be loaded, the pooled refit
   models are read instead and the report says so.
2. **The preseason numbers are "accidentally right"** if realized Pre Season minutes
   for star-tier appearances (from `player_game_logs`, `season_type = 'Pre Season'`,
   minutes > 0, tier from the player's regular-season `roll10_MIN` as of the game)
   sit within **3.0 minutes** of the projected
   27.5 (the 2026 preseason slate). If no
   preseason rows exist, this is NOT MEASURABLE.
3. The retrospective (a) and every other variant are descriptive. They say how the
   two verdicts should be read; they do not move either bar.
