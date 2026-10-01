# PBP lineup validation

Should play-by-play derived lineups be backfilled? This experiment checks reconstructed stints against box-score minutes on a sample of games.

## Pass criteria (stated before results)

Fixed in `validate.py` before the first run. The backfill is justified only if all three hold on the sample:

1. **Players**: at least 95% of player-games have summed on-floor seconds within 30 seconds of box-score minutes.
2. **Lineups**: 100% of stints (positive duration) have exactly five players per team.
3. **Team totals**: every team-game sums to 240 + 25 per OT player-minutes within 0.5 minutes, with no negative stint.

A game pbpstats cannot reconstruct counts as failing. A game that cannot be downloaded at all is listed and excluded.

## Method

- Lineups: `pbpstats` 1.3.11, `Possessions` resource, `live` data provider (cdn live play-by-play JSON). The `stats_nba` provider is dead: it calls `playbyplayv2`, which no longer returns `resultSets`.
- Reference minutes: stats.nba.com `boxscoretraditionalv3` (`minutes` as `mm:ss`), a separate endpoint from the lineup source.
- Expected team minutes: `leaguegamelog` team `MIN` (240, 265, 290, ...).
- Stints: consecutive events with the same five on the floor for one team. The live feed records each substitution as an out event and an in event at the same clock, so the momentary four-man lineup between them has zero duration and is dropped. Event order is preserved, so a clock running backwards would show as a negative stint.
- Team minutes are 5 x summed stint clock; lineup size is judged only by the five-man check.
- Players: summed seconds over stints vs box seconds, outer-joined, so a phantom lineup player and an unseen box-score player both count as misses.
- Sampling: seeded random draw of completed Regular Season games.

## Results

Season 2025-26, seed 20260930. Command: `validate.py --games 20 --season 2025-26`

| game | date | matchup | periods | stints | non-5 stints | neg stints | max team diff (min) | players | within 30s | mean abs diff (s) | max abs diff (s) | ok |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0022500046 | 2025-11-14 | DAL vs. LAC | 6 | 65 | 0 | 0 | 0.00 | 20 | 20 | 0.1 | 0.4 | yes |
| 0022500055 | 2025-11-21 | OKC @ UTA | 4 | 39 | 0 | 0 | 0.00 | 24 | 24 | 0.0 | 0.4 | yes |
| 0022500082 | 2025-10-22 | ATL vs. TOR | 4 | 53 | 0 | 0 | 0.00 | 21 | 21 | 0.0 | 0.1 | yes |
| 0022500103 | 2025-10-25 | IND @ MEM | 4 | 51 | 0 | 0 | 0.00 | 25 | 25 | 0.1 | 0.4 | yes |
| 0022500174 | 2025-11-05 | BOS vs. WAS | 4 | 37 | 0 | 0 | 0.00 | 25 | 25 | 0.0 | 0.0 | yes |
| 0022500364 | 2025-12-08 | IND vs. SAC | 4 | 42 | 0 | 0 | 0.00 | 20 | 20 | 0.1 | 0.4 | yes |
| 0022500456 | 2025-12-29 | SAS vs. CLE | 4 | 51 | 0 | 0 | 0.00 | 20 | 20 | 0.0 | 0.4 | yes |
| 0022500567 | 2026-01-13 | NOP vs. DEN | 4 | 51 | 0 | 0 | 0.00 | 19 | 19 | 0.1 | 0.4 | yes |
| 0022500631 | 2026-01-22 | MIN vs. CHI | 4 | 47 | 0 | 0 | 0.00 | 20 | 20 | 0.1 | 0.5 | yes |
| 0022500716 | 2026-02-03 | DEN @ DET | 4 | 44 | 0 | 0 | 0.00 | 20 | 20 | 0.1 | 0.4 | yes |
| 0022500831 | 2026-02-24 | IND vs. PHI | 4 | 40 | 0 | 0 | 0.00 | 22 | 22 | 0.1 | 0.4 | yes |
| 0022500858 | 2026-02-27 | CLE @ DET | 5 | 54 | 0 | 0 | 0.00 | 20 | 20 | 0.2 | 0.5 | yes |
| 0022500881 | 2026-03-02 | DEN @ UTA | 4 | 38 | 0 | 0 | 0.00 | 19 | 19 | 0.1 | 0.5 | yes |
| 0022500948 | 2026-03-11 | NOP vs. TOR | 4 | 42 | 0 | 0 | 0.00 | 24 | 24 | 0.0 | 0.1 | yes |
| 0022500957 | 2026-03-12 | MIL @ MIA | 4 | 45 | 0 | 0 | 0.00 | 20 | 20 | 0.1 | 0.3 | yes |
| 0022501019 | 2026-03-20 | BOS @ MEM | 4 | 40 | 0 | 0 | 0.00 | 19 | 19 | 0.1 | 0.3 | yes |
| 0022501028 | 2026-03-21 | IND @ SAS | 4 | 41 | 0 | 0 | 0.00 | 23 | 23 | 0.0 | 0.0 | yes |
| 0022501092 | 2026-03-30 | ATL vs. BOS | 4 | 39 | 0 | 0 | 0.00 | 21 | 21 | 0.1 | 0.3 | yes |
| 0022501161 | 2026-04-08 | POR @ SAS | 4 | 45 | 0 | 0 | 0.00 | 20 | 20 | 0.1 | 0.5 | yes |
| 0022501197 | 2026-04-12 | SAS vs. DEN | 4 | 36 | 0 | 0 | 0.00 | 21 | 21 | 0.0 | 0.0 | yes |

Largest player discrepancies:

| game | team | player | pbp s | box s | diff s |
|---|---|---|---|---|---|
| 0022501161 | 1610612757 | 1629680 | 1446.5 | 1447.0 | -0.5 |
| 0022501161 | 1610612757 | 1631104 | 260.5 | 261.0 | -0.5 |
| 0022500858 | 1610612739 | 1630596 | 2222.5 | 2223.0 | -0.5 |
| 0022500881 | 1610612743 | 203999 | 2183.5 | 2184.0 | -0.5 |
| 0022500881 | 1610612743 | 1630192 | 338.5 | 339.0 | -0.5 |
| 0022500858 | 1610612739 | 1642878 | 1133.5 | 1134.0 | -0.5 |
| 0022500858 | 1610612765 | 1629130 | 2041.5 | 2042.0 | -0.5 |
| 0022500631 | 1610612750 | 1630538 | 705.5 | 706.0 | -0.5 |
| 0022500631 | 1610612750 | 203944 | 1831.5 | 1832.0 | -0.5 |
| 0022500858 | 1610612739 | 1628386 | 1887.6 | 1888.0 | -0.4 |

## Verdict

| criterion | threshold | observed | pass |
|---|---|---|---|
| players within 30s | >= 95% | 100.0% (423 player-games, mean abs diff 0.1s) | yes |
| five-man stints | 100% | 100.00% of 900 | yes |
| team totals within 0.5 min | all | 40/40 team-games | yes |
| games failing any check | (reported) | 0/20 | |

**Overall: PASS.**

## Interpretation (hand-written, not regenerated)

`validate.py` regenerates everything above this heading; re-append this section
after a rerun.

**What the pass means.** On 2025-26 the reconstruction matches stats.nba.com box
minutes to within a second for every player, so the lineup pipeline is faithful.
That is not independent ground truth: NBA box minutes are themselves computed from
the same substitution log. A substitution the scorer logged against the wrong
player is wrong in both places and this check cannot see it. What it does catch is
pbpstats losing track of who is on the floor, which is the failure that would
corrupt a backfill.

**Coverage probes on older seasons (10-game samples, same seed, reports under
`data/REPORT_<season>.md`).**

| season | reconstructed | pbpstats errors | feed missing (403) | players within 30s | verdict |
|---|---|---|---|---|---|
| 2022-23 | 10/10 | 0 | 0 | 99.0% (one game had two CHI players swap 79s) | pass |
| 2019-20 | 5/10 | 3 (`InvalidNumberOfStartersException`, period 1) | 2 | 100% of the 5 | fail |

- The live feed (`nba-prod-us-east-1-mediaops-stats` S3) returns 403 for every
  game probed before 2019-20 (2016-17, 2017-18, 2018-19 and earlier), and for some
  2019-20 games. The `stats_nba` provider, which would cover older seasons, is
  dead because `playbyplayv2` no longer returns `resultSets`.
- So pbpstats is usable for 2020-21 onward. 2019-20 would need starter overrides
  (pbpstats supports a missing-starters file) or a hand-written reconstruction
  from `playbyplayv3` plus `boxscoretraditionalv3` starters, and nothing before
  2019-20 can be backfilled through pbpstats at all.

**Recommendation.** Backfill 2020-21 through 2025-26 with pbpstats on the live
provider. Keep the five-man and team-total checks running as a gate on every game
it ingests, and quarantine games that fail rather than dropping them silently.
Before committing to 2020-21 and 2021-22, run a 20-game sample for each
(`--season 2020-21`, `--season 2021-22`); only 2022-23 and 2025-26 were sampled
here.
