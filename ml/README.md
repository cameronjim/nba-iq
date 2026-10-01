# `ml/` — availability-first NBA production forecasting

Production port of the phase-0 feasibility spike. Its findings are recorded
in `MODEL.md` (Phase 0) and bind this package; where the code looks
conservative, that is deliberate.

## The one-sentence design

```
E[stat | plays]           = E[minutes | plays] × EWMA(stat per minute)
E[stat over the schedule] = P(play) × E[stat | plays]
```

`P(play)` is a trained LightGBM classifier — the most learnable target in the
spike (**−34% Brier** vs a shifted appearance rate on the four-season dataset,
stable across all five origins). `E[minutes | plays]` is a trained LightGBM
regressor, promoted 2026-08-17 (−2.1% MAE vs EWMA, all five origins). The
**production rate** is `EWMA(halflife 5)` of stat-per-minute and is *not* a model:
no trained conditional estimator beat a smoothed rate by more than ~1%, and
several lost outright on stars and fringe players. Ridge and LightGBM remain
implemented as challengers and are never promoted automatically.

**Minutes propagate.** Until 2026-08-17 the composition was
`P(play) × EWMA(stat)`, in which the minutes model's output could not reach a
production projection at all — `EWMA(stat)` averages past whole-game *totals* and
already contains the minutes the player used to get, so a backup whose minutes
model said 30 kept the points EWMA of his 14-minute nights. The per-minute form
fixes that, for the conditional card number as well as the unconditional one.
Measured: **3.955** MAE against **4.007** for the form it replaced (five origins,
unconditional points). Parity was the bar; the improvement is incidental. See
`fnba_ml/config.py::CHAMPIONS['composition']`.

Why bother with the decomposition at all: applying a conditional-on-playing
estimate to every scheduled row scores 5.67 MAE on unconditional points on the
four-season dataset; counting misses as zeros scores 4.57; the composition reaches
3.955. It also yields `P(play)` as a first-class output, which start/sit decisions
need and a direct model cannot recover — direct LightGBM on all scheduled rows
measures 3.986 and is still not the champion for that reason.

### The serving layer the model cannot supply

`P(play)` knows nothing about the injury report — the report history only starts
accumulating 2026-08-16, so training on it earlier would be
leakage-by-imputation. `fnba_ml/overrides.py` therefore corrects `P(play)` at
serving time from the latest official designation known at the run's information
boundary (OUT → 0.02, DOUBTFUL → 0.10, QUESTIONABLE → blended, PROBABLE → floored,
everything else untouched). The constants are hand-set; the model's own
probability is stored beside the overridden one so the layer stays measurable and
those constants can be replaced by learned ones. A designation older than
`REPORT_MAX_AGE_HOURS` (72) before the boundary is ignored, and the scraper appends
an explicit `cleared` row when a player drops off the CBS page, so a recovered
player's last OUT no longer stands forever. A failed or empty scrape writes
nothing rather than clearing everyone. Full policy table in `MODEL.md` section 7.1.

---

## Runbook

Everything runs offline against parquet. Nothing here makes a network call.

```powershell
# once
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 1. dataset: source -> universe -> as-of-safe features
python build_dataset.py --source parquet --data-dir ..\ml-spike\data
python build_dataset.py --source postgres            # needs DATABASE_URL

# 1b. widen an EXISTING dataset with box columns the build predates, without
#     rebuilding it. read-only: one SELECT, one parquet out. every pre-existing
#     column is passed through untouched, and --verify asserts it.
python backfill_dataset.py --source postgres --stats FGM --verify

# 2. train: availability + minutes models + rate snapshot -> models/<version>/ + registry
python train.py --version 2026-08-16

# 2b. the v1 shadow comparator (MODEL.md 13.4 rung (c)) -> models/<version>-v1/.
#     36 BASE_FEATURE_COLS, no base model, cutoff inherited from models/<version>/.
#     for the pinned artifact: train against prod, then commit the directory and
#     its registry entry (ML Evaluate's train_shadow input does the fit in CI).
python train.py --feature-set v1 --version 20260818

# 3. evaluate: rolling-origin report -> reports/<version>.md
#    exits 1 if the promoted composition regresses >1% against the one it
#    replaced, for ANY of the eleven served stats
python evaluate.py --version 2026-08-16
python evaluate.py --version 2026-08-16 --rate-halflife-selection

# 4. predict: next games -> parquet, and optionally to postgres
python predict.py --version 2026-08-16 --out data\predictions.parquet
python predict.py --version 2026-08-16 --statuses data\statuses.parquet --horizon lock
python predict.py --version 2026-08-16 --write-db    # needs DATABASE_URL + migrations 014, 015
python predict.py --version 2026-08-16 --write-db --channel shadow   # recorded, never served

# 5. score: stored runs vs completed games -> reports/scoring/scoring_<today>.md + _results.csv
python score_runs.py                                  # runs predicted in the last 30 days
python score_runs.py --since 2026-10-20 --channel production
python score_runs.py --run-id 412 --run-id 413 --md reports\scoring\look_dec1.md

# 6. the daily publisher, as predictions.yml runs it (run A, the v1 shadow, run B)
python daily_run.py --shadow-feature-set v1 --dry-run

# 7. challengers and serving options, all off by default (see the section below).
#    one look per candidate: the reports are the record, not a draft.
python report_coherence.py --version 20260818            # reports/<version>_coherence.csv
python run_p3_bracket.py --version p3                     # needs data/dataset_v4.parquet
python report_counts.py --version 20260818               # reports/counts_<version>_*.csv
python train.py --version <new> --tiered-quantiles      # never into models/20260818/
python predict.py --version 20260818 --coherence all --scenarios --out data\challenger.parquet

# tests
python -m pytest tests -v
```

`--write-db` inserts one `prediction_runs` row and one
`player_game_predictions` row per (player, game, stat, quantile), in a single
transaction, against the tables from `db/migrations/014_predictions.sql` and
`015_prediction_run_provenance.sql`. Both are applied by hand in the Neon SQL
editor: run them against prod and the dev branch before the first `--write-db`
with this code (015 adds columns the insert names, so the write fails without
it), then record each in `schema_migrations` the way
`scraper/check_migrations.py` expects.

The store is **append-only**. A re-run writes a new run and never edits an old
one, because a prediction that can be revised after the fact cannot be
backtested. Serving reads the newest run with `status = 'complete'` and
`channel = 'production'`. `--channel shadow` (default `production`) records a
challenger run beside the served one without the app ever reading it; the daily
run's served runs always pass `--channel production`, and their notes say
`channel=production` to match the column.

**The daily shadow.** `daily_run.py --shadow-feature-set v1` (what the workflow
passes) publishes, right after the prospective run A, one more run of
`models/20260818-v1/` with `--channel shadow`: same prospective frame, same
`--statuses-as-of`, `--run-at`, horizon and history boundary, notes
`<label>; feature_set=v1; channel=shadow` when A qualifies. It is disqualified
(`NOT PROSPECTIVE`) if its metadata names another feature set, its cutoff differs
from the pinned artifact's, or its registry checksums do not verify. A missing
artifact is a warning and a skip; a failed shadow is logged, run B still
publishes, and the job exits 1. The extended run B never gets a shadow.

`--write-db` **refuses the approximation universe** unless
`--allow-biased-universe` is passed, and stamps the reason into the run's notes
when it is. A stored prediction outlives the caveat that shipped with it.

Per scheduled player-game the run writes **62 rows**, plus 2 more if an injury
override applied: `prob_active` (unconditional, `[0,1]`, post-override),
`prob_active_model` (pre-override), and for each of the twelve served stats —
minutes plus the full nine-category vocabulary, `pts reb ast stl blk tov fg3m
fgm fga ftm fta` — a conditional expected value, a `<stat>_uncond` schedule-level
one and P10/P50/P90. For overridden rows only there are `status_override` (reason
as a numeric code) and `status_captured_at` (epoch seconds). None of these are new
**columns**: migration 014 is long-format precisely so the vocabulary can grow
without a schema change, and every name above was already reserved in that
migration's own comment — going from 3 served stats to 12 changed no schema and
required no migration.

**Percentages are never written.** FG% and FT% are derived by consumers from the
makes/attempts expectations, because `E[FGM/FGA] != E[FGM]/E[FGA]` and, more
practically, a fantasy league scores a manager's *weekly aggregate*
`sum(FGM)/sum(FGA)` over every player-game he rostered. Shipping the primitives
lets a consumer aggregate that correctly; shipping a per-game percentage would
force it to aggregate wrongly. **Turnovers are not sign-flipped** either — TOV is
a category a manager wants to lose, which is a scoring fact the backend's
`zScoreRank` already owns, and inverting it here would mean every consumer had to
know which of the nine columns had been pre-negated.

Every emitted row is **coherent**: `FGM <= FGA`, `FG3M <= FGM` and `FTM <= FTA`
hold on the conditional estimate, the unconditional estimate and each quantile
level. That is a real correction, not a defensive no-op — see "per-stat halflife"
below.

Quantiles come from the empirical residual quantiles of the training holdout
window (`fnba_ml/intervals.py`), measured on appearances only against the
estimator that actually ships, and are non-crossing by construction — offsets
sorted when built, values sorted again when written.

### Scoring stored runs (runbook step 5)

`score_runs.py` reads runs from the store (read-only) and scores every completed
player-game against the truth layer, per run and pooled per (channel,
prospective label), with the 13.3 endpoints split by `cold_start` and season
type: E1 Brier on `prob_active` and `prob_active_model`, E2 calibration slope and
intercept, E3 minutes MAE on appearances, E4 unconditional MAE for all twelve
stats, the override increment, P10-P90 coverage and mean bias. A pool keeps each
player-game's latest pre-tip forecast once. A completed game with no status row
for a predicted player is counted as a coverage miss, not dropped. E1 skill needs
the shifted appearance rate and E5 needs the frozen `ewma_total` rows; neither is
in the store, so both are reported as not computed. Nothing to score exits 0.
The logic is pure (`fnba_ml/scoring.py`, tested in `tests/test_scoring.py`).

Every run records its **forecast horizon** (`--horizon early|gameday|lock`, i.e.
T-24h / T-6h / T-60m) in `prediction_runs.notes` and in the registry entry. The
same model scoring the same game at T-24h and T-60m makes two different claims.
Each run also stores three boundaries, in the row and the registry entry:
`information_as_of` (`--statuses-as-of`, the newest injury report it could see),
`history_through` (the last game date with outcomes in the feature frame, derived
from played rows or passed as `--history-through`), and `forecast_cutoff_at`, the
later of `information_as_of` and the start of the `--run-at` day. `--run-at` only
selects which games are scored; `predicted_at` is when the run was generated.

### Challengers and serving options (all off by default)

`prospective_2026_27_v2` serves none of these, and `daily_run.py` passes none of
them except the v1 shadow, whose run is `channel = 'shadow'` and never read by the
app. Each was pre-registered before any result existed and gets one look on real
data; none has one yet. Promoting any of them changes an emitted number and is a
13.2 re-freeze. The full record, bars and look order are `MODEL.md` section 18.

| Item | Switch | Measured by | Gate |
|---|---|---|---|
| serving coherence | `predict.py --coherence {none,team_minutes,points_identity,all}` | `report_coherence.py` | report-only |
| scenario serving | `predict.py --scenarios` | `<out>_scenarios.parquet` audit | not backtestable |
| `v5-stakes` | `config.FEATURE_SETS["v5-stakes"]` | `run_p3_bracket.py` | availability Brier or minutes MAE |
| residual rate | `fnba_ml/rate_model.py` | `run_p3_bracket.py` | conditional or unconditional PTS MAE |
| count models | `fnba_ml/count_model.py` | `report_counts.py` | report-only |
| tiered intervals | `train.py --tiered-quantiles` | `report_counts.py` | report-only |
| v1 shadow | `train.py --feature-set v1`, `daily_run.py --shadow-feature-set v1` | `score_runs.py` | ladder rung (c), MODEL.md 13.4 |

**Serving coherence.** Applied last, after the injury overrides and any scenario
mix. `team_minutes` scales each team-game's conditional minutes so
`sum(P_PLAY * E_MIN_COND)` is 240 (factor clipped to [0.8, 1.25], overtime
unmodelled), carries the same factor into every production stat and shifts
quantiles by the conditional delta; the factor lands on each row as
`TEAM_MIN_FACTOR`. `points_identity` sets `E_PTS_COND` to `2*FGM + FG3M + FTM` and
records the change as `PTS_IDENTITY_DELTA`. `all` runs both, minutes first. A
non-default choice is written to the run notes (`coherence=<value>`) and the
registry entry; `none` returns the frame unchanged and adds no note token, so the
frozen note text is untouched. `report_coherence.py` scores every variant against
`none` over the five `ORIGINS` with the promoted path (unconditional PTS/MIN and
conditional MIN MAE, by cohort, plus the raw minute-sum diagnostic).

**Scenario serving.** A questionable star's blended `p_j` fed once through the
nonlinear minutes model is not the average of the night he plays and the night he
sits. `fnba_ml/scenarios.py` finds each team-game's pivotal players (questionable or
doubtful as of the boundary, and `tm_MIN >= SCENARIO_MIN_MAGNITUDE` (20) or top-3 by
usage, at most `SCENARIO_MAX_PIVOTAL` (2) per team-game), scores the team-game once
per play/sit world (teammate `p_j` forced to 1.0 or the OUT 0.02), and
weight-averages the outputs, treating two pivotal players as independent. The
star's own row keeps his blended `P(play)` and takes his conditional numbers from
the worlds he plays in. Every other team-game is scored once, as without the flag.
The audit (world weights, the backup with the largest minutes swing and that swing)
goes to `<out>_scenarios.parquet`, and `notes` gains
`scenarios=on; scenario_team_games=N`.

**`v5-stakes` and the residual rate (`run_p3_bracket.py`).** `v5-stakes` is the 51
served columns plus the seven stakes columns MODEL.md 15.11 says a v5 should keep
(no `late_season` or `stakes_lockedness` column, no blowout or pace family). The
residual rate is a small LightGBM per stat (`RATE_MODEL_TARGETS` = PTS, AST, REB,
FGA; `RATE_MODEL_PARAMS`) fitted on appearances to `stat / max(MIN, 4) - served
rate`, so the challenger rate is the champion's plus a context correction, floored at
0 and composed through the same `minutes_propagated_estimate`, on the incumbent's
availability and minutes models. One invocation takes both looks: paired 7-day
moving-block bootstrap over `DEV_ORIGINS`, 95% CI excluding zero, at least 1%
relative improvement on a gated endpoint, and no gated-endpoint cohort regressing by
more than 1% (`config`'s P3 block). It reads `data/dataset_v4.parquet`
(`build_v4_dataset.py`) and writes `reports/<version>_p3.md` plus
`_decision.csv`, `_per_origin.csv`, `_cohorts.csv`, `_cohorts_all_endpoints.csv`,
`_clip_rates.csv` and `_coherence.csv`. It refuses to overwrite an existing decision
csv. A pass is a recommendation to re-freeze, not a promotion.

**Count models and tiered intervals (`report_counts.py`).** LightGBM Poisson
boosters for STL/BLK/FG3M/TOV with a `log E[MIN]` offset plus the league log rate,
a small grid chosen on inner folds, against the rate composition; and per-minutes-
tier interval offsets (pooled fallback below `MIN_TIER_ROWS` = 200 residuals)
against the pooled ones, 80% coverage and width. Five `ORIGINS`, tidy csvs, no bar.
`train.py --tiered-quantiles` additionally stores the tiered offsets under
`production.quantiles_by_tier`; nothing reads that key.

### Artifacts

| Path | Committed? | Notes |
|---|---|---|
| `models/<version>/` | yes | `availability_model.joblib`, `minutes_model.joblib`, `ewma_state.parquet`, `metadata.json`, `feature_gain.csv` |
| `models/<version>-v1/` | yes | the v1 shadow: the same files minus `base_availability_model.joblib`; `metadata.json` says `feature_set: v1` |
| `models/registry.json` | yes | one entry per version: training window, hyperparams, metrics, per-artifact sha256, git commit |
| `reports/<version>.md` | yes | the evaluation record for that version |
| `reports/*_results.csv` | no | tidy long results, for numeric diffing between runs |
| `data/*.parquet` | no | build artifacts, regenerate in ~1 minute |
| `tests/fixtures/*.parquet` | no | regenerated deterministically on every test session |

There is no artifact store in this project, so `models/` is committed to git.
The registry is what makes that traceable: re-hash with
`fnba_ml.registry.verify_artifacts(version)`.

---

## What each module owns

```
fnba_ml/
  config.py              every constant: seasons, windows, halflife, tiers,
                         feature list, champions, cutoff policy, origins
  data/schema.py         the four canonical frame shapes + id/date normalisation
  data/parquet_source.py spike-shaped parquet; schedule reconstructed from team logs
  data/postgres_source.py  SQL against the migration-013 tables (NOT executed in tests)
  universe.py            scheduled-player-game rows: status-based, or BIASED fallback
  features.py            leakage-safe as-of feature construction
  prospective.py         future scheduled player-games; features built one date
                         at a time, schedule columns read from the whole known
                         schedule
  models.py              the ladder, availability + minutes champions, per-minute
                         rates, the minutes-propagating composition, OOF guards
  overrides.py           serving-time injury-report policy on P(play) (pure)
  evaluate.py            rolling-origin harness, segments, skill scores, champion
                         picks, composition parity check (per stat), the 9-cat
                         rate ladder and the inner-fold halflife selection
  intervals.py           empirical residual quantiles -> non-crossing P10/P50/P90,
                         plus the per-minutes-tier challenger (pooled fallback
                         below MIN_TIER_ROWS = 200 residuals)
  store.py               the migration-014 row builder (pure) and its transaction
  registry.py            models/registry.json
  scoring.py             look-report endpoints for stored runs (pure)
  promotion.py           the pre-registered paired-bootstrap promotion rule

  challengers and serving options, all off by default (MODEL.md 18):
  coherence.py           --coherence: team-minutes and points-identity corrections (pure)
  eval_coherence.py      their retrospective endpoints and the minute-sum diagnostic
  scenarios.py           --scenarios: per-world scoring and output mixing for
                         questionable/doubtful stars
  rate_model.py          P3 candidate: the contextual residual rate
  count_model.py         LightGBM poisson for STL/BLK/FG3M/TOV with a log E[MIN]
                         offset, small grid chosen on inner folds; the docstring
                         states how it differs from tournament M6
  eval_counts.py         tidy [variant, endpoint, stat, cohort, n, value] endpoints
                         for counts and tiered intervals, importable without a dataset
```

Scripts at the top level are thin CLIs: `build_dataset.py`, `train.py`,
`evaluate.py`, `predict.py`, `score_runs.py` and `daily_run.py` on the served path;
`report_coherence.py`, `run_p3_bracket.py` and `report_counts.py` for the
challengers. All logic lives in the package.

---

## The three things that are easy to get wrong

### 1. As-of joins must be scoped like their windows

Exactly two mechanisms enforce "features for game G use only information from
strictly before G":

1. `merge_asof(..., allow_exact_matches=False)` for anything derived from the
   player's **appearance** history.
2. An explicit `.shift(1)` before every `.rolling()` / `.expanding()` for
   anything computed on the universe or schedule frames.

And there are **three** as-of joins, not one:

- **career-scoped** (`roll{3,5,10}_*`, `ewma_*`, `n_appearances`) joined by
  `PLAYER_ID` — windows may span the offseason, so a returning player carries
  prior-season form into his first game of a new season, which is precisely
  when in-season history does not exist.
- **season-scoped** (`std_*`, `avail_rate_std`, `uncond_std_*`) joined by
  `PLAYER_ID + SEASON` — season-to-date means must reset at the boundary.
- **career-scoped over a narrower row set** (`ewma_*_per_min`) joined by
  `PLAYER_ID` — computed only over appearances with minutes > 0, so it cannot ride
  along on the career frame. Per-minute efficiency is far more stable across an
  offseason than minutes are, which is exactly why the composition splits them.

The spike originally used a single join keyed on `PLAYER_ID + SEASON` while
rolling windows were grouped by `PLAYER_ID` alone. Not leakage, but incoherent,
and invisible until tested. `tests/test_features.py` asserts both scopes
independently, with negative controls confirming the leaky variant produces a
*different* number.

### 2. `groupby().first()` skips nulls

It returns the first **non-null** value per column, so a test written with it
**cannot detect** the leak it was written to catch. Use
`drop_duplicates(keep="first")`. Pinned by
`test_groupby_first_cannot_detect_the_leak`.

### 3. Out-of-fold `P(play)` **and** `E[minutes]` must never leak in-fold

The composition multiplies *two* model outputs into a downstream estimate. If
either came from a model whose training window included the row being predicted,
nothing downstream can detect it.

So both travel with the cutoff of the model that produced them (`P_PLAY_CUTOFF`,
`MIN_PRED_CUTOFF`), and `models.validate_out_of_fold` refuses any row whose cutoff
is not at or before its own game date. `AvailabilityModel.fit` and
`MinutesModel.fit` both refuse a training frame containing rows on or after their
cutoff.

There is a third guard, because per-quantity honesty is not sufficient once two
models are multiplied: `models.assert_same_cutoff` requires both halves to share
one training cutoff. An availability model that stopped in January composed with a
minutes model that stopped in March mixes two views of what was knowable, and
neither per-quantity guard fires — both cutoffs precede the game.
`minutes_propagated_estimate` runs all three itself rather than trusting callers.

The same discipline extends one layer out to the injury-report override: a report
whose `captured_at` is at or after the run's information boundary is dropped, or a
backtest of the T-24h horizon quietly reads the T-60m report and looks prescient.

---

## The universe, and why it is the highest-priority data dependency

One row per **(player who could have played, team-game)** — not per game log. A
model trained only on recorded appearances answers "how much *given* he plays",
which is selection-biased for fantasy.

Two constructions, and they are not equivalent:

**Status-based (preferred).** One row per rostered player per team-game from
`player_game_status`. A player listed inactive for two months is present in
every one of those team-games with `PLAYED = 0` — exactly what the availability
model needs to see. Used automatically whenever the source returns status rows.

**Approximation (fallback, BIASED).** Eligibility inferred from game-log
presence within ±15 days. Only for parquet-fixture mode, where no roster table
exists. Measured distortions (REPORT.md §5):

- availability base rate inflated by **at least +0.0192** (0.6638 vs 0.6446)
- longest representable absence streak is **16 team-games**; real season-ending
  injuries (40–60 games) are structurally invisible
- the resulting model **over-predicts availability in every probability bin**,
  worst (−0.136) in the uncertain 0.6–0.7 middle where start/sit calls are hard
- `games_since_last_app` carries 4× the gain of any other feature and is partly
  an artifact of the window — expect the availability numbers to move, most
  likely getting *harder*, once the universe is built properly

Every code path that reaches the fallback logs a `BIASED UNIVERSE` warning and
stamps `UNIVERSE_SOURCE = 'approximation'` on every row; the evaluation report
carries a callout banner. Do not ship predictions from it.

---

## Portability notes

- **Python 3.14.5 on Windows**, pandas 3.0.5, scikit-learn 1.9.0, LightGBM
  4.7.0. `requirements.txt` pins the exact verified set; all had cp314 wheels.
- **`psycopg2` and `python-dotenv` are imported lazily**, inside the functions
  that need them. `data/postgres_source.py` — including its SQL — can be
  imported and inspected with neither package installed, and a parquet-only run
  needs no database driver.
- **No test opens a database connection.** There is no test database in this
  repo (AGENTS.md §6) and `postgres_source.py` is never executed by the suite.
  Its SQL is written against the migration-013 contract and is the first thing
  to re-read if that schema moves.
- **Identifiers are normalised to `str`** everywhere. Postgres stores NBA ids as
  `TEXT`, the spike parquet as `int64`; merging a str key against an int key
  silently produces zero matches.
- **Internal column names are SCREAMING_SNAKE NBA-style** (`PLAYER_ID`, `MIN`,
  `PTS`), not the database's snake_case. The leakage-critical feature code was
  ported verbatim; renaming its columns would have meant rewriting the exact
  logic the tests were built to pin down. Both sources translate at the
  boundary — see `data/schema.py`.
- **LightGBM is not bit-reproducible against the spike.** Rows are sorted by
  string ids rather than integer ids, which reorders ties within a game date and
  therefore changes bagging subsamples. Baselines match the spike to five
  decimals; LightGBM figures move by well under 1%. See the parity table below.

---

## Port fidelity vs the spike

`build_dataset.py --source parquet --data-dir ../ml-spike/data` followed by
`evaluate.py` reproduces the spike end to end. Universe: **79,406 rows, 4,920
team-games, played rate 0.6638, 100% appearance coverage** — identical.

| Metric (mean over 3 origins) | Spike | This package | Δ |
|---|---:|---:|---:|
| Availability Brier — LightGBM | 0.1365 | 0.1373 | +0.6% |
| Availability Brier — logistic | 0.1564 | 0.1564 | — |
| Availability Brier — shifted rate | 0.1769 | 0.1769 | — |
| Availability Brier — global rate | 0.2262 | 0.2262 | — |
| MIN\|played MAE — EWMA | 4.8213 | 4.8213 | — |
| MIN\|played MAE — LightGBM | 4.7527 | 4.7616 | +0.2% |
| PTS\|played MAE — EWMA | 4.5664 | 4.5665 | — |
| PTS\|played MAE — ridge | 4.5202 | 4.5203 | — |
| Unconditional PTS MAE — decomposed (P × LightGBM) | 4.1698 | 4.1681 | −0.04% |
| Unconditional PTS MAE — decomposed (P × EWMA, champion *at the time*) | not run | 4.1719 | — |
| Unconditional PTS MAE — direct LightGBM | 4.1950 | 4.1955 | — |
| Unconditional PTS MAE — naive unconditional | 4.6340 | 4.6340 | — |
| Unconditional PTS MAE — naive conditional | 5.2928 | 5.2928 | — |

Every baseline matches exactly. The LightGBM deltas are the row-ordering effect
described above, not a logic change.

Note the then-champion decomposition (`P × EWMA`, 4.1719) is within 0.1% of the
spike's `P × LightGBM` variant (4.1681) — the trained conditional model buys
essentially nothing at the unconditional level either, which is the whole
argument for shipping a smoothed rate rather than a model.

**This table is a frozen port-fidelity record against the two-season spike, not a
current scoreboard.** It predates the four-season backfill and the 2026-08-17
composition change, and it is deliberately not re-run: its purpose is to prove the
port reproduced the spike, and a table that keeps moving cannot do that. Current
numbers live in `reports/<version>.md`.

---

## Evaluation protocol

Rolling-origin, forward-chaining, **no random splits**. Training is everything
strictly before the validation window, so the first season is always fully in
training.

| Origin | Train | Validate |
|---|---|---|
| O1 | ≤ 2024-11-30 | 2024-12 |
| O2 | ≤ 2024-12-31 | 2025-01 |
| O3 | ≤ 2025-01-31 | 2025-02 |

Reported per task: the metric itself, a **skill score** against that task's
naive baseline, a **segment breakdown by minutes tier** (tiers assigned from
`roll10_MIN`, a prior rolling mean, so the label is itself as-of safe), and
empirical **80% interval coverage** for the champion production estimate.

`evaluate.select_champions` reports the measured winner per target next to the
configured champion and flags mismatches. A mismatch is a finding to look at,
**not** an instruction to promote — a 1% MAE edge does not justify a trained
model in the serving path. Four families are reported: availability, minutes,
production, and **composition** (how the promoted estimators are combined, which
is a separate decision from which they are).

`evaluate.composition_parity` additionally checks the promoted composition against
the one it replaced and the CLI **exits 1** if it lost more than 1% MAE. The
composition change was made for correctness, so parity is the bar; a regression
past it is a finding that should stop a pipeline, not a line someone has to notice
in 18 kB of markdown. `evaluate.rate_composition_parity` asks the same question
**once per served stat** — the correctness argument for minutes propagation is
stat-agnostic, so the accuracy bar has to be cleared stat-by-stat rather than
cleared once for points and assumed for blocks.

### Per-stat halflife, and what it costs

Every production rate is a smoothed stat-per-minute, and until the 9-cat
extension every one of them used the same halflife of 5 — which was fine while
"every one of them" meant points and assists, both of which had been selected at
5. A block, a steal and a field-goal attempt are not observed with remotely the
same signal-to-noise, so each of the nine new stats got its own halflife,
selected on **inner folds carved out of each origin's own training window** over
the grid `{3, 5, 8, 12, 20}` — never on the rows the report then scores. PTS and
AST are **frozen** at the production tournament's verdict and cannot move.

The rule is pre-registered in `config.RATE_HALFLIVES`: the pooled best halflife
ships only if it beats 5 by more than 0.5% relative MAE **and** is the per-origin
winner in at least 3 of the 5 origins. Otherwise the stat keeps 5 and is marked
ambiguous — because picking the smallest of six numbers that are all within noise
of each other is not a finding.

That 0.5% bar is set by **coherence**, not by statistics. Two EWMAs at the same
halflife are the same weighted average of the same rows, so `FGM <= FGA` survives
the averaging; two at *different* halflives are not, and it does not. Every
distinct halflife introduced into a constrained pair is therefore a source of
violations that the serving clip has to correct, and a halflife buying under half
a percent of MAE has not paid for the clipping it causes. The report measures how
often the clip actually binds, and the number is readable as a check on that
reasoning: a constraint whose two stats share a halflife should read ~0.

Caveats inherited from the spike: two seasons, three origins, one month of
validation each. Differences under ~2% are not distinguishable from noise at
this sample size, and no confidence intervals are computed.

### The prospective test is pre-registered and frozen

Everything above is **retrospective**: the out-of-fold discipline is real, and the
questions were still chosen after the era was visible. The 2026-27 season is the
first genuinely untouched evaluation this system will get, and it counts only if
the protocol was fixed before opening night.

**MODEL.md section 13, re-frozen as `prospective_2026_27_v2` in section 17, is that
pre-registration**; v2 exists because the Phase 0 fixes (stale injury reports, future
back-to-backs) change emitted numbers, and since no Regular Season slate was ever
scored under v1 the re-freeze is a clean start. It holds the pinned artifact and
its checksums, five primary endpoints, nine frozen cohorts, a
three-rung comparison ladder (shifted appearance rate / per-stat frozen baselines /
a `v1` no-teammate shadow run), a ten-row falsification table with thresholds
derived from measured between-origin variance, exactly three look dates
(**2026-12-01, 2027-02-15, 2027-04-20**), the cold-start rules, and the ops
commitments that make the store auditable.

`config.PROSPECTIVE_2026_27` carries the machine-checkable half and
`tests/test_prospective_freeze.py` enforces it. **A failure there is not a bug in
the test** — it means the served configuration moved after the freeze, and the
response is a protocol version bump with a re-freeze (before opening
night) or a revert (after it). Editing a frozen literal to match a drifted constant
defeats the entire point.

The single most important number in section 13: the teammate-context availability
claim is **−1.9%** and the minimum detectable effect for it at **season end** is
**2.0%**. One full season is barely enough to test this document's headline. The
protocol says so up front rather than discovering it in April.

---

## Tests

```powershell
python -m pytest tests -q      # 790 tests
```

| File | Covers |
|---|---|
| `tests/test_rate_targets.py` | the 9-category extension, in five blocks. **The vocabulary**: every served stat has a store name and every store name is inside migration 014's reserved list — which is parsed out of the `.sql` file rather than restated, because the schema has no `CHECK` on `stat`, so that comment *is* the contract and a typo'd name would insert cleanly and be invisible to every consumer. **The per-stat halflife**: each rate column reconstructed from the raw ratio at that stat's own configured halflife, plus a negative control showing two halflives on the same history disagree — without it the entire selection mechanism could be inert and every assertion would still pass. PTS/AST pinned frozen at the tournament's verdict. **Coherence**: the clip moves the bounded stat down and never raises the bound, the FG3M→FGM→FGA chain settles in one pass, a missing bound is skipped rather than clipping makes to nothing, and — the test that justifies the clip existing at all — equal halflives *cannot* produce an incoherent expectation while different ones demonstrably can, on a history whose truth is coherent in every row. **The emitted rows**: all eleven stats reach the store conditional, unconditional and at every quantile level. **The selection rule**: material-but-inconsistent and consistent-but-immaterial winners both fall back to the default, a frozen target cannot be moved by *any* evidence, and the synthetic fixture carries its own negative control proving its pooled and per-origin axes are independent |
| `tests/test_features.py` | all 12 leakage tests ported from `ml-spike/leakage_tests.py`, plus the `groupby().first()` trap regression, missingness-flag checks, the per-minute rate definition (hand-recomputed) and the rate backfill reproducing the built-in columns exactly, and a pin that `build_features(schedule=None)` is the pre-existing schedule computation |
| `tests/test_prospective.py` | the future-game universe and its one-date-at-a-time feature build. Outcome-derived columns (`avail_rate_*`, rolls, EWMAs) of a future row are identical whether or not other future dates exist, with the naive whole-week build kept as a negative control that does leak. Schedule-derived columns read the whole known schedule: a future back-to-back has `TEAM_REST_DAYS == 1` and `IS_B2B == 1`, the first future game's rest is measured from the last played game, `OPP_REST_DAYS` follows the opponent's own future games, a new season's second game reads rest from its opener, and a future row's `OPP_DEF_FORM` is the mean of the opponent's last played games, not diluted by unplayed ones |
| `tests/test_rate_model.py` | the residual rate: its target by hand, the cutoff refusal, a permutation control (shuffled context buys nothing) next to a planted +0.1 home effect that is recovered, box-score flip invariance on a rebuilt feature frame with a counter-assertion on later rows, the shared-cutoff guard, and the v5-stakes column list |
| `tests/test_p3_bracket.py` | the P3 bar as written, identical rows across both comparisons, appearance-only conditional rows, the decision logic on a synthetic win, loss, sub-floor win and cohort regression, and `main` end to end on the fixture including the one-look refusal |
| `tests/test_universe.py` | status-based preferred; fallback labeled and warns; approximation over-states availability and truncates long absences; schedule symmetry |
| `tests/test_models.py` | composition math; out-of-fold discipline for **both** multiplied quantities including deliberately constructed in-fold failures and a mismatched-cutoff pair; the minutes-propagation regression test (double the predicted minutes → double both estimates, checked through the fitted serving path); per-minute rate behaviour and the cameo floor; metric helpers |
| `tests/test_overrides.py` | every status rule and its exact arithmetic; the questionable blend; probable as a floor that can never lower a projection (now by arithmetic rather than by a `max`); unlisted and passthrough statuses; unconditional recomputation with conditional estimates and quantiles left alone; the newest-admissible-report rule; a report captured at or after the boundary refused (the leakage case); missing/empty statuses as an identity; the TEXT-vs-int64 id trap |
| `tests/test_evaluate.py` | the composition-parity verdict logic, including the sign of a regression and the could-not-run case reporting nothing rather than a pass; the composition champion family appearing in the selection table; cohort masks defined on dataset columns rather than model output; the permutation controls |
| `tests/test_predictions.py` | the migration-014 row builder as a pure function: conditional vs unconditional flagging, quantile non-crossing (including a deliberately crossed input), `prob_active` clamped to [0,1], non-finite values dropped rather than zeroed, both probabilities stored, override reason as a code and `captured_at` as epoch seconds, horizon labels, channel validation and the three information boundaries in the run record, plus `predict.history_through` and `predict.forecast_cutoff`. No database — `write_predictions` is read, never run |
| `tests/test_teammates.py` | the **oracle** (v2) teammate family, kept because it is the evaluation bracket's upper bound and an oracle whose arithmetic is wrong is a useless bound: self-exclusion under a rebuilt feature frame, the split as-of contract with negative controls on both halves, the usage arithmetic against a hand computation, rank independence from the row's own availability |
| `tests/test_teammates_v3.py` | the **served** (v3) family: teammate-outcome invariance pinned to PASS on the expected columns and to **FAIL** on the oracle ones; closed-form sensitivity to `p_j` for every column; the shrinkage weight at four points; career-scoped magnitudes crossing the season boundary; backward-only reliability features; the base model refusing teammate context; the cross-fit's out-of-fold stamp on every row plus a tampered-cutoff rejection; the team-game block permutation collapsing both gain share and out-of-sample MAE |
| `tests/test_prospective_freeze.py` | **the 2026-27 pre-registration, enforced.** Every pinned artifact checksum recomputed from the bytes on disk, per file so a failure names *which* artifact moved, plus an assertion that the pinned set covers the whole directory (six passing per-file checks would not notice a seventh file appearing). Then the frozen serving configuration against the live module: champions, per-stat halflives and estimators (parametrised per stat, with STL's `expanding` called out separately because it is the most surprising entry), the 51-column feature contract by digest, the override constants against `overrides.DEFAULT_POLICY` — including the PROBABLE floor condition `s >= 1 - w`, which the defaults satisfy with *equality* and which therefore needs a float tolerance to be checkable at all — the horizon windows, the coherence constraints and the frozen cohort definitions. Then the protocol's own shape: exactly three look dates asserted literally, ordered, inside the season, with non-decreasing row minimums; and the falsification table checked for the properties that make it binding — every endpoint names all three looks (a `None` is a pre-registered "no power here", a *missing* key is an oversight), no threshold is a placeholder, no endpoint is report-only everywhere, thresholds only tighten across looks, no bar demands more than the retrospective effect it tests, and the Dec-1 teammate-context bars sit on the *worse* side of zero because at ~1.5 month-blocks the MDE exceeds the effect. The one row we expect to fail (REB/TOV/FG3M at halflife 20, which section 12.4's validation rows already contradict) is pinned as expected-to-fail |
| `tests/test_coherence.py` | team minutes: factors are 240 over the `P_PLAY`-weighted sum within bounds, a team at 190 is clipped and logged, every production stat takes the same factor, quantiles shift without crossing, `P_PLAY` untouched; the points identity by formula, idempotent, skipped when a make column is missing; `none` a byte-identical identity, `all` minutes first; the endpoints and minute-sum diagnostic by hand; the `predict.py` flag defaulting to `none` and the default leaving the frozen note untouched |
| `tests/test_scenarios.py` | no pivotal player leaves the output byte-identical; a backup's minutes are the average of the two forced worlds; the star keeps his blended `P(play)` and his plays-world minutes; two pivotal players make four worlds whose weights sum to one; quantiles do not cross after mixing; a doubtful bench player is not pivotal; a report captured after the boundary cannot make anyone pivotal; forced probabilities reach the teammate sums and default to off |
| `tests/test_train.py` | `--feature-set`: the served set by default, v1 accepted and suffixed `-v1`, other sets refused; the shadow inherits the pinned cutoff unless one is passed; the v1 artifact's metadata, its 36 columns with no teammate column, no base model, a verifying registry entry, and `predict.py` skipping `rebuild_context` for it |
| `tests/test_count_model.py` | the count challenger: fits and predicts non-negative counts on the fixture, refuses training rows past its cutoff and a minutes forecast that is in-fold or from another cutoff, doubling the minutes forecast doubles the conditional count (and recovers a hand-built constant rate), inner-fold selection returns a grid member deterministically, and the count endpoints computed by hand |
| `tests/test_intervals_tiered.py` | tiered intervals: a wide tier gets wider offsets than a narrow one, the pooled fallback below `MIN_TIER_ROWS`, non-crossing per tier even from unsorted hand offsets, the metadata round trip, `train.py --tiered-quantiles` off by default, coverage and width endpoints by hand, and `report_counts.run` end to end on the fixture |
| `tests/test_serving_context.py` | the serving wiring: an injury report must raise a teammate's `exp_vacated_minutes` by exactly the probability shift times the absent player's magnitude, the as-of filter applies at the context stage too, an absent report is an identity, the rebuilt features carry the base model's cutoff, and the corrected measured-offset horizon definition with its stored per-run facts |

Every leakage test runs **twice**, once per universe construction, so a dropped
shift fails on the production path and on the backtest path alike.

Fixtures are seeded synthetic parquet generated by
`tests/fixtures/generate.py` — the suite never depends on `ml-spike/data`
existing. The fixture deliberately contains a 13-game injury spell that the
±15-day approximation provably cannot represent, which is what makes the
universe-bias tests bite.

One real-data claim is **not** asserted in the unit tests: the −22.8% Brier
headline. The fixtures draw availability i.i.d. per roster slot, so the shifted
appearance rate is close to the generating process and a boosted tree on ~2k
rows cannot beat it. That comparison lives in the parquet-mode backtest above.
