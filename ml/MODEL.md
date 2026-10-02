# NBA Player Prediction System — Technical Specification (v20260818)

Standalone description of a fantasy-basketball prediction system, written for
external review. Everything below is implemented and measured unless marked
**[GAP]** or **[PLANNED]**. Sections 9 and 11 answer the external reviews point by
point.

**Latest change (2026-08-19, P2): a 21-column candidate feature set — opponent matchup /
pace, a pregame blowout probability, and season-stakes load-management context — was
built as `feature_version v4` (72 columns), evaluated against the served contract over
identical rows and six rolling origins under a bar registered before the numbers
existed, and DID NOT CLEAR IT.** `prospective_2026_27_v1` remains frozen; `FEATURE_COLS`
is unchanged at 51 columns with the same `914cdc17…` digest; artifact `20260818` is
untouched. Pooled: availability Brier **+0.66%** (95% CI [+0.41%, +1.13%]), minutes MAE
**+0.45%** (CI [+0.28%, +0.73%]) against a **1%** floor — real, significant, and about
half the bar. Two findings survive the null: the **late-season origin** shows −1.55%
availability Brier (triple the pooled effect) and the **stakes-flagged cohort** −1.91%,
so the load-management hypothesis held exactly where it was aimed; and the blowout model
is worthless (AUC 0.536, no Brier skill), which surfaced the incidental finding that the
package's default `LGBM_PARAMS` scores **22% worse than a constant** on a frame fifteen
times smaller than the one it was tuned for. **Section 15** is the full record.

Previous change (2026-08-18, artifact `20260818`, feature_version still `v3`): **the
system serves all nine fantasy categories instead of two.** `RATE_TARGETS` went from
`(PTS, AST)` to `PTS REB AST STL BLK TOV FG3M FGM FGA FTM FTA`, each with a halflife
selected on inner training folds, each through the same guarded composition, and each
clipped so the emitted expectations satisfy `FG3M ≤ FGM ≤ FGA` and `FTM ≤ FTA`.
`feature_version` does not move because not one value in `FEATURE_COLS` changed — the
new stats are outcomes and rate targets, not predictors. **Section 12** is the full
record; the three findings worth reading first:

- **Rare events want long memories, and one wants an infinite one.** REB, TOV and FG3M
  chose halflife 20 in 5 of 5 origins; **steals ship the memoryless career expanding
  mean**, which beat every EWMA on the grid. BLK is the honest counterexample — the
  rarest stat in the vocabulary, predicted to want a long memory, and its inner folds
  do not show it, so it keeps the default and the prediction is recorded as not
  confirmed.
- **All 11 stats beat the pre-composition estimator** unconditionally (+0.20% to
  +3.00%), so the per-stat composition-parity check passes for every one of them.
- **The coherence clip binds on 0.0000% of 30,917 validation rows** — by proof for the
  two pairs that share a halflife, empirically for the one that does not. It ships
  anyway, because that zero is a property of the current halflife assignment rather
  than of the arithmetic.

Previous change (2026-08-17, feature_version **v3**, phase P1b): **the teammate-context
family was rebuilt as expectations over as-of play probabilities, because the round-2
external review found a real flaw in the v2 construction and the flaw invalidated the
v2 headline as a forecasting claim.**

The finding, stated the way the reviewer stated it. A v2 feature on player *i* summed
magnitudes over the set of teammates who **did not play in the target game**. That set
is built from other players' target-game labels, so player *i*'s features depend on
player *j*'s outcome — cross-player leakage. Self-exclusion, which v2 implemented
carefully and tested hard, closes the path from *i*'s own label to *i*'s own features
and does nothing whatsoever about this one, because the label being read is not the
row's own. The consequence is not a small correction:

- **Every v2 number in the old section 5.1 is a value-of-perfect-lineup-information
  (oracle) result.** It measures what the final inactive list is worth, not what a
  forecast is worth.
- **It cannot be earned at any horizon, including `lock`.** Even when tonight's list is
  known an hour before tipoff, the *training* rows' features were built from *their*
  final lists, so the estimator was fitted on information no live run has.
- **There is no as-of rule that fixes it.** "Who did not play tonight" is never
  knowable in advance. The realized indicator has to be replaced by its expectation.

v3 does that: `1(j absent)` becomes `(1 − p_j)` for an out-of-fold, teammate-context-free
`p_j`. Section 4.1 states the new contract, section 4.2 the shrunk career-scoped
magnitudes that also fix the review's cold-start item, section 4.3 the two-stage
pipeline that produces `p_j` without circularity, and **section 5.1 reports the
honest-vs-oracle bracket** — v1, v3-honest, v2-oracle over identical rows — which is
the only form in which the teammate-context claim is now made. The v2 columns are still
computed and still on the dataset as the oracle comparator; they are not in
`FEATURE_COLS` and are never served.

**The headline, so nobody has to find it:**

| Target (5 origins, identical rows) | v1 | **v3-honest (served)** | v2-oracle (upper bound) | **survived** |
|---|---:|---:|---:|---:|
| Availability Brier | 0.0735 | **0.0720** (−1.98%) | 0.0712 (−3.05%) | **65%** |
| Minutes MAE \| played | 4.718 | **4.679** (−0.81%) | 4.532 (−3.94%) | **21%** |
| PTS unconditional MAE | 3.954 | **3.942** (−0.32%) | 3.911 (−1.10%) | **29%** |

The v2 document reported the middle-to-right column as its result. Roughly a fifth of
the minutes gain and two-thirds of the availability gain survive honest construction; on
the fringe minutes tier, where v2's marquee −10.3% lived, **9% survives**. Section 5.3
locates the served construction at about the value of a pre-tipoff report with **20%
absence recall** — which makes the empty `player_injury_reports` table, not any further
modelling, the binding constraint on this feature family.

## 1. Problem statement

For every NBA player and every scheduled game, predict: (a) whether the player
appears at all, (b) minutes if he plays, (c) box-score production (points,
assists; other categories planned), with uncertainty bands. The bands are
currently a **prototype** — global residual offsets, not conditional calibration;
see section 5. Consumers are fantasy-basketball decisions (start/sit, waivers,
weekly category projections) and a "breakout watchlist" product.

## 2. Core design principle

The training universe is **scheduled player-games, not recorded appearances**.
A player-game exists for every (rostered player × team game), including games
the player missed. Rationale, validated empirically: a model trained only on
appearance rows estimates E[stat | played], but fantasy needs E[stat].
Measured on our data, applying a conditional season mean to all scheduled rows
scores 5.67 MAE on points; the honest unconditional mean scores 4.57 — a ~20%
error inflation purely from selection bias.

The forecast is decomposed, and as of 2026-08-17 the implementation matches the
decomposition rather than approximating it. **Written in estimator notation**,
because the previous version of this section wrote it with bare `E[·]` on both
sides and thereby asserted an identity the code does not have:

```
Ê[S | A=1, x] = Ê[M | A=1, x] · r̂(x)
Ê[S | x]      = P̂(A=1 | x) · Ê[S | A=1, x]
```

`A` is availability, `M` minutes, `S` the stat, `x` the as-of features. Hats
everywhere: each factor is a separate fitted object (a LightGBM classifier, a
LightGBM regressor, an EWMA of per-game ratios), and the product of three
estimators is not the estimator of the product.

**What the first line drops.** The true conditional mean is

```
E[S | A=1, x] = E[M · R | A=1, x] = E[M|·]·E[R|·] + Cov(M, R | A=1, x)
```

and we estimate only the first term. The covariance is not plausibly zero: a
blowout truncates minutes and simultaneously shifts who is taking the shots, and a
starter playing 40 minutes in a tight game is scoring at a different rate than the
same starter in his 40th minute of a back-to-back. **The sign is not even
constant** — garbage-time minutes come with an inflated rate for bench players
(positive covariance) and a deflated one for stars who sat the fourth quarter of a
blowout after an efficient three quarters (negative). So the omission is a bias of
unknown sign that varies by cohort, and it is not measured. It is not a rounding
concern either: it is the most likely explanation for why `star_out = 1` shows a
minutes win and no points win (section 5.1) — a rate held constant with respect to
minutes cannot express the interaction.

**The rate floor makes the second line a non-identity too.** `r̂` is an EWMA of
`stat / max(minutes, 4)`, not of `stat / minutes`, so `Ê[M]·r̂` does not reproduce
the observed totals even in-sample. The 2-minute 3-pointer is the whole example: the
true ratio is 1.5 pts/min, the floored one is 0.75, and if that night were the
player's entire history a 30-minute projection would read 22.5 points instead of the
45 the unfloored rate implies. Both numbers are wrong; 22.5 is wrong in the
direction that does not embarrass the product, and 4 is hand-set as a bound on
nonsense rather than tuned (`config.RATE_MINUTES_FLOOR`). The consequence to state
plainly is that the floor introduces a **downward bias on players whose history is
mostly cameos** — exactly the fringe tier — and that bias is a deliberate trade
against the variance of an unfloored ratio, not a neutral choice.

**[PLANNED]** The round-2 review's suggested replacement is to stop multiplying
point estimates and integrate over the minutes distribution:

```
Ê[S | A=1, x] = ∫ Ê[S | M=m, x] · f̂(m | A=1, x) dm
```

which subsumes the covariance term (the rate is allowed to depend on `m`) and
removes the need for a floor (a distribution over minutes has no single denominator
to protect). It needs two things this system does not have yet: a minutes
*distribution* rather than a point estimate plus global quantile offsets (section 5's
first planned upgrade), and a rate model conditional on minutes. Not implemented, and
deliberately sequenced after the conditional-quantile work it depends on.

The previous implementation was `E[stat] = P(plays) × EWMA(stat)`. That is
arithmetically defensible and structurally broken: `EWMA(stat)` averages past
whole-game **totals**, so it already embeds the minutes the player used to get and
is constant with respect to any minutes forecast. A backup whose minutes model
said 30 kept the points EWMA of his 14-minute nights, and no amount of minutes
signal could reach the projection. Predicted minutes now propagate into every
production stat, and the conditional number on a player card is the product of the
two rows above it rather than an unrelated third estimate.

Measured effect on aggregate MAE: the minutes-propagating form scores **3.955**
against **4.007** for the form it replaced (five rolling origins, points,
unconditional). Parity was the expected result and the bar the change had to
clear — most players' predicted minutes are close to their recent minutes, so an
aggregate metric cannot see the defect. The 1.3% improvement is a bonus, not the
argument.

Availability and minutes are first-class modeled targets, not afterthoughts.
In-fold availability probabilities and in-fold minutes predictions are never fed
to downstream estimators (out-of-fold discipline, enforced by runtime guards +
tests). Both multiplied quantities travel with the training cutoff that produced
them, and a third guard asserts the two halves of the composition share one
cutoff — individually-out-of-fold is not sufficient when two models are
multiplied together.

## 3. Data

Source: stats.nba.com via nba_api, plus official per-game inactive lists from
box-score summaries (V3 primary, V2 fallback tagged `v2-suspect` for games
after 2025-04-10 where V2 is documented as unreliable).

Postgres truth layer (four seasons, 2022-23 → 2025-26, regular season only). **Two
row counts appear for the same tables throughout this document and they are not
typos** — see the reconciliation immediately below.

- `player_game_logs` — 105,252 box-score lines (FGA/**FTA**/TOV read since v2 for
  the usage denominator)
- `player_game_status` — **147,565 rows in the table; 147,413 in the built dataset**
  (played, listed_inactive from official inactive lists, dnp_reason, minutes)
- `team_game_logs` — **9,840 team-game rows in the table; 9,830 team-games in the
  built dataset** (opponent-strength features; **minutes, FGA, FTA, TOV** read as of
  v2 as the team-possession denominator)
- `nba_schedule` — 4,920 regular-season games
- `player_team_stints` (trade correctness), `player_injury_reports` (timestamped,
  append-only; **0 rows as of 2026-08-17** — the table exists, the scraper is wired,
  and nothing has accumulated yet)
- `players.position` — reference data, not a per-game fact. Comma-joined strings
  ("PG,SG"); the first listed position decides a G/F/C bucket. **Coverage is
  partial, it is a real limitation, and section 3.2 shows it is not
  missing-at-random**: `players` is the app's currently-tracked roster, so retired and
  released players have no row. 582 of the 895 players in the universe match, covering
  82% of modelled rows (60,200 G / 48,036 F / 12,501 C / 26,676 unknown). Unmatched
  players get a null bucket and null positional features rather than a guessed one.
  This query is also the one exception to the cutoff rule — a position is undated and
  does not change with the outcome of the game being predicted — with the residual risk
  that a position updated after a 2023 game mislabels a bucket in hindsight.

### 3.1 The row-count reconciliation (corrected 2026-08-17, P1b)

Earlier versions of this document quoted 147,413 in section 3 and 147,565 in section
4.1, and 9,840 in section 3 against 9,830 in section 4.1, without saying that these
were different quantities. Both pairs are real and the difference has one cause.

Queried directly:

| Quantity | Value |
|---|---|
| `player_game_status` rows (all seasons, all season types, all `rostered`) | **147,565** |
| ...of which regular-season, four seasons | 147,565 (there is nothing else in the table) |
| distinct (game, team) in `player_game_status` | **9,840** |
| `team_game_logs` rows | **9,840** |
| `nba_schedule` regular-season games | **4,920** (× 2 = 9,840 team-games ✓) |
| rows in the built feature dataset | **147,413** |
| distinct team-games in the built feature dataset | **9,830** |

**The cause: 10 games in `nba_schedule` have a NULL `home_team_id`.** Five in 2024-25
(2024-11-02, two on 2024-12-14, 2025-01-23, 2025-01-25) and five in 2025-26
(2025-11-01, two on 2025-12-13, 2026-01-15, 2026-01-18) — the shape of the dates says
neutral-site games: the in-season-tournament semifinals and final in Las Vegas plus the
international openers. `universe.team_game_frame` builds team-games from the schedule's
home/away pair, so those ten produce a row with a NULL `TEAM_ID`; the real home team's
(game, team) key is then absent from the known set and `universe_from_status` drops its
status rows, logging `152 status rows reference team-games absent from the schedule`.

152 rows, 10 team-games. 147,565 − 152 = 147,413 ✓. 9,840 − 10 = 9,830 ✓.

So the honest statement is: **147,565 is the truth-layer count and 147,413 is the
modelled count**, and this document now says which is which at every occurrence. Two
notes worth keeping:

- A first attempt to find this with SQL returned nothing, because
  `WHERE st.team_id NOT IN (s.home_team_id, s.away_team_id)` is never TRUE when one of
  the compared values is NULL. `IS DISTINCT FROM` finds all 152. The NULL-comparison
  trap is the reason the discrepancy survived two review rounds.
- **[GAP]** This is a data defect, not a modelling choice, and the fix belongs upstream:
  backfill `home_team_id` for neutral-site games. Dropping 0.10% of rows biases nothing
  detectably, but it also means five NBA Cup finals — high-absence, high-rest games — are
  absent from every table in this document.
### 3.2 Position missingness is an era proxy (new finding, 2026-08-17, P1b)

The round-2 review asked whether position coverage encodes era. One SQL query — null
position rate by season, over modelled scheduled rows — and the answer is **yes,
strongly and monotonically**:

| Season | scheduled rows | unmatched rows | **null-position rate** | distinct players | with a position |
|---|---:|---:|---:|---:|---:|
| 2022-23 | 35,284 | 11,347 | **32.2%** | 552 | 331 |
| 2023-24 | 37,411 | 9,734 | **26.0%** | 594 | 389 |
| 2024-25 | 37,160 | 4,839 | **13.0%** | 585 | 469 |
| 2025-26 | 37,710 | 769 | **2.0%** | 601 | 582 |

(counts here are truth-layer, i.e. before the 152-row drop of section 3.1; the trend is
identical either way.)

Zero of the matched players have a blank position string, so every null is an unmatched
player — and the mechanism is exactly what makes it an era proxy: `players` holds the
*currently* tracked roster, so a player is matched if and only if he is still around in
2026. Attrition is monotone in time by construction.

**The consequence, stated as a limitation rather than a caveat.** `vacated_minutes_pos`
and `depth_rank_available_pos` (and their v3 twins `exp_vacated_minutes_pos` /
`exp_depth_rank_pos`) are null on 32% of 2022-23 rows and 2% of 2025-26 rows. A
gradient booster learns from missingness — LightGBM routes NaN down a learned default
branch — so **"this positional feature is null" is a usable 16×-stronger signal for
"this is an old season" than for anything positional.** Since the rolling-origin scheme
trains on early seasons and validates on later ones, the direction is the dangerous one:
the model can learn a rule fitted mostly on 2022-24 rows and keyed on a null pattern that
has almost vanished by the validation window.

This is not measured as a bias in the current tables, and it is not fixed. Three things
follow:

1. The positional half of the family should be read as **partly an era indicator**, and
   any future claim about its contribution has to control for season.
2. The clean fix is a **dated position source** (any per-season roster snapshot), which
   is the same missing ingredient as the P5 item in section 4.1 and should be done once.
3. A cheap interim guardrail, **[PLANNED]** and not implemented: an explicit
   `position_known` indicator, so the model reads the missingness as a declared column
   rather than inferring it from NaN routing, and the era confound is at least visible in
   the importance table.

Validation gates on ingest: 2 team rows per game, no duplicate keys,
FGM≤FGA / 3PM≤3PA / 3PM≤FGM / FTM≤FTA, player-vs-team points reconciliation,
completed-schedule coverage. All green. Known data defect: 3 of 4,920 games
have no available inactive list from any source (NBA-side gap); their rows are
tagged and their listed_inactive flags are unknown.

Roster membership for the universe comes from official inactive lists +
appearances. **[GAP]** Players rostered but neither appearing nor listed
inactive in a stretch (e.g., G-League assignments) may be under-represented.

## 4. Features (feature_version v3)

**All 51 served features are as-of-safe: computed from information available strictly
before the target game. As of v3 there is no exception.** v2 had one — the
teammate-absence set came from the target game — and removing it is what this phase
did. The realized family still exists on the dataset as the oracle comparator
(section 4.1) and is not in the served feature list.

Two as-of join *scopes* (career-scoped rolling vs season-scoped season-to-date) after a
bug where mixing scopes gave a returning player NaN form on game 1 but prior-season form
on game 2; **five as-of joins in total as of v3** — the fifth carries the shrunk
teammate magnitudes and replaces a v2 dependency rather than adding one (section 4.2).

Per scheduled player-game (**51 model features**, up from 45; the count moved by
−8 realized + 8 expected + 6 reliability):
- Rolling 3/5/10-game means of MIN, PTS, AST, FGA over appearance games
  (shifted; null while window unfilled + missingness indicator)
- EWMA (halflife 5) of the same
- **Expected** teammate context — eight features, section 4.1
- **Reliability / cold-start** context — six features, section 4.2
- EWMA (halflife 5) of PTS-per-minute and AST-per-minute over appearance games
  with minutes > 0. The ratio is `stat / max(minutes, 4)`: the floor is on the
  denominator, so a 2-minute cameo with one three contributes at the rate a
  4-minute stint would have implied (0.75 pts/min) rather than an unusable
  1.5 pts/min, while every genuine rotation night is untouched. 4 is hand-set as
  a bound on nonsense, not tuned. These ride a third as-of join (narrower row
  set) and are deliberately **not** model features — they are composition inputs,
  which is why adding them did not bump `feature_version` or invalidate any
  existing artifact.
- Season-to-date expanding means (shifted)
- Availability: appearance rate over team's last 10/20 games,
  games-since-last-appearance, days-since-last-appearance, and their stddevs
- Schedule: team rest days, back-to-back flag, home/away — team rest is
  distinct from player days-since-appearance (a player gap ≠ team off-day)
- Opponent: rolling 10-game defensive form (points allowed), pre-target only
- Unconditional per-scheduled-game stddevs of MIN/PTS (volatility signals)

### 4.1 Teammate context — expected vacated resources (v3), and the oracle it replaced

**Two families exist. One is served; one is a measuring instrument.**

#### The served family: expectations over as-of probabilities

Eight columns, plus the player's own usage rate. Every one is a **linear functional of
teammates' as-of play probabilities `p_j` and as-of magnitudes `m_j`, and of nothing
else.** No target-game outcome of any player — the row's own or anyone else's — appears
anywhere in the arithmetic.

| Feature | Definition |
|---|---|
| `usg_ewma` | the player's own usage rate. EWMA(halflife 5) over appearance games of the standard box-score approximation `USG% = 100 × (FGA + 0.44×FTA + TOV) × (TeamMIN/5) / (MIN × (TeamFGA + 0.44×TeamFTA + TeamTOV))`. `TeamMIN/5` rather than 48 because overtime exists. MIN floored at 4 in the denominator, exactly as the per-minute production rates are floored and for the same reason |
| `exp_vacated_minutes` | `Σ_{j≠i} (1 − p_j) · m_j^MIN` |
| `exp_vacated_fga` | the same sum over the shots magnitude |
| `exp_vacated_usg` | the same sum over the usage magnitude |
| `exp_vacated_minutes_pos` | `exp_vacated_minutes` restricted to the player's own G/F/C bucket. A wing's minutes do not open up because the backup centre is out |
| `exp_depth_rank` | `1 + Σ_{j≠i} p_j · 1(m_j^MIN > m_i^MIN)` — the *expected* number of available teammates ahead of him, plus one |
| `exp_depth_rank_pos` | the same within his position bucket |
| `p_star_out` | `Σ_{j≠i} 1(j is the team's usage leader) · (1 − p_j)`, i.e. `1 − p_star` for everyone but the leader himself, who reads 0 |
| `exp_top3_usage_out` | `Σ_{j≠i} 1(j in the top 3 by usage) · (1 − p_j)`, in [0, 3] |

**`exp_depth_rank` is an expectation, not the rank of an expectation, and that
distinction is doing real work.** The summand is `p_j` — the probability that teammate
*j* is both available and ahead of me — and by **linearity of expectation over indicator
variables** the expectation of a sum of indicators is the sum of their probabilities
*whatever their joint distribution*. So **no independence assumption between teammates'
availabilities is needed**, which matters because their availabilities are obviously not
independent (a team resting three starters for the same back-to-back is one decision, not
three). A construction that instead ranked players by `p_j · m_j`, or that sampled
lineups, would need that assumption or a joint model; this one needs neither.

The rank is consequently a real number, not an integer. `1.4` means "usually the leading
available option, occasionally second", which is strictly more information than either
`1` or `2`.

Eight columns is the whole family, deliberately: both reviews warned explicitly against
per-teammate one-hot indicators and lineup combinatorics, and a 500-column "is teammate X
out" matrix has more parameters than a season has games. `exp_vacated_usg` and
`p_star_out` exist because of a specific point both reviews made: when a team's primary
creator sits, the beneficiary is often an **already-starting** player whose minutes barely
move while his usage jumps, and a minutes-based feature cannot see that at all.

**The v3 contract, in one block:**

```
every input is STRICTLY PRIOR to the target game.
teammate availability enters as a PROBABILITY p_j, never as a realized indicator.
p_j is out-of-fold and comes from a model with no teammate context (section 4.3).
```

**Self-exclusion still applies, and is still exact.** A player's own availability is the
prediction target, so his own `(1 − p_i)·m_i` term is subtracted arithmetically from every
sum: `group_total − own_contribution`. The usage leader therefore reads `p_star_out = 0` —
the honest answer, since "a top-usage *teammate* may be out" is false when the top-usage
player is you.

#### The oracle family: what v2 was, why it is kept, and why it is not served

The v2 columns — `vacated_minutes`, `vacated_fga`, `vacated_usg`,
`vacated_minutes_pos`, `depth_rank_available`, `depth_rank_available_pos`, `star_out`,
`top3_usage_out_count` — summed the same magnitudes over the set of teammates who **did
not play in the target game**. They are still computed, still on the dataset, and
**absent from `FEATURE_COLS`**.

They are kept for two jobs:

1. **They are the upper bound in the evaluation bracket** (section 5.1). The distance
   from v1 to v2-oracle is what perfect pre-tipoff lineup information is worth; the
   distance from v1 to v3-honest is what we actually get.
2. **`config.EVENT_COHORTS` partitions on `vacated_minutes`**, so the v1, v3-honest and
   v2-oracle passes split the validation rows identically and their per-cohort numbers
   describe the same games. Defining the cohorts with hindsight is legitimate for a
   *report* — "on the nights when a lot really was vacated, how did each feature set
   do?" is a question about the games — in a way it is not for a feature.

**Why they are an oracle and not merely a game-day feature.** The old wording said these
features were "honest for `lock` (T-60m), close to honest for `gameday`, optimistic for
`early`", and that framing was wrong in kind. The problem is not the *timing* of the
information; it is that the *training* rows were featurised from *their* final inactive
lists. An estimator fitted that way has learned a mapping from "resources that were in
fact vacated" to minutes. At serving time, at any horizon, that input does not exist —
only an estimate of it does. **So the v2 numbers cannot be recovered at `lock` either,
and the "optimistic for early" framing understated the problem by making it sound like a
horizon gap.** It was a construction gap.

The v2 as-of contract, for completeness, since the oracle columns still obey it and the
tests still pin it:

```
the absent-teammate SET comes from the TARGET game.
every MAGNITUDE summed over that set is STRICTLY PRIOR.
```

Getting the second half backwards would layer a second leak on the first, letting a
teammate's tonight *performance* into the row on top of his tonight *availability*.
`tests/test_teammates.py` pins both halves with negative controls.

#### The complement claim, restated precisely (corrected 2026-08-17, P1b)

Verified: across all 147,565 truth-layer status rows, `listed_inactive` and `played`
partition the rows exactly — 105,252 played / 42,313 listed inactive, zero nulls, zero
"inactive but played", zero "active but did not play".

**The previous wording drew a stronger conclusion than that supports.** The corrected
statement:

> `listed_inactive` and `played` are complements **within the reconstructed universe**.

They are complements *by construction*, not by observation. Roster membership in
`universe_from_status` is itself reconstructed from the inactive list plus appearances, so
a player who was active, dressed, and did not get off the bench has no row unless some
other source put him there. **The identity therefore certifies internal consistency and
certifies nothing at all about active-DNP coverage.** A garbage-time-only rotation of
active players who never check in is invisible to it, and would be counted as "not on the
roster" rather than "available and unused" — which biases the availability base rate
upward and the vacated-resource sums downward.

**[P5]** The fix is an **independent dated roster source** — a per-game active list, or a
per-season dated roster snapshot — against which the reconstruction can be audited rather
than believed. Logged as the P5 item; the same missing ingredient would fix the era proxy
in section 3.2, so it should be done once. Until then "no active-DNP category exists" is a
property of our reconstruction and is quoted as such.

Two further consequences worth naming. First, `LISTED_INACTIVE` is in
`config.TARGET_COLS` and can never be a feature — it *is* the availability label. Second,
for the 3 games (of 4,920) where no inactive list was retrievable from any source, roster
membership came from appearances alone, so nobody on those 6 team-games is marked absent
and their oracle vacated-resource features read ~0. That understates rather than invents,
and at 6 of 9,830 modelled team-games it is not worth special-casing.

**[GAP]** Still no lineup continuity, no minutes restrictions ("he's on a 24-minute
cap coming back from injury"), and no started-rate. Availability enters as a scalar
probability of *appearing at all* — a player expected to play 8 minutes on a
load-managed night contributes `(1 − p)·m` with his full established magnitude `m`,
which is the same binary-presence limitation v2 had, now expressed in probabilities.

**[GAP]** No era features. No injury-report *features* (history only accumulates from
2026-08-16 and currently holds 0 rows; using them for pre-2026 training would be
leakage-by-imputation). Trade context is **no longer a gap** — see `is_traded` and
`games_with_current_team` in section 4.2.

### 4.2 Magnitudes and reliability — the cold-start fix (new in v3)

The review's cold-start item, and it turned out to be two changes: what number to use
when history is thin, and telling the model that history *is* thin.

#### The magnitudes: shrunk, career-scoped, rolling

v2 attached each teammate's **season-to-date** mean minutes as his vacated magnitude.
Three problems, the first two named by the review:

1. **Cold start.** On 20 October a season-to-date mean is one game long. A player whose
   opener was a 6-minute blowout cameo contributed 6 vacated minutes on 21 October; a
   ten-year starter who sat the opener contributed 0. The season boundary discards the
   only history that could fix either.
2. **Trades and returns.** A season-to-date mean resets at the boundary but *not* at a
   trade, so it silently mixes two roles; and a player returning from a two-month absence
   carries a mean from a role he no longer has.
3. **Noise.** An unshrunk mean over *n* games has variance ~σ²/n, and the sum over ~4
   expected absences compounds it.

v3 computes each magnitude as a **career-scoped rolling mean over the last 20 appearance
games** — crossing season boundaries, exactly as `roll10_MIN` and every EWMA already do,
on the two-scope as-of join machinery `features.py` already had — and then **shrinks it
toward a fixed prior**:

```
m = w · rolling_mean_20  +  (1 − w) · prior ,      w = n / (n + k) ,  k = 10
```

`n` is the number of appearances actually in the window (0…20). This is the standard
empirical-Bayes shrinkage weight. Two boundary behaviours are load-bearing: `n = 0`
returns the prior *exactly* (a player with no history contributes replacement level, not
a NaN that would poison his team-game's whole sum and not a 0 that would read as
"vacates nothing"), and a null rolling mean returns the prior too.

**`k = 10` is hand-set, not tuned**, and the reasoning is the same shape as
`RATE_MINUTES_FLOOR`'s: `k` is the number of games at which the rolling mean and the prior
carry equal weight (`w = ½` at `n = k`). 10 puts the half-way point at roughly a fifth of
a season — late enough that a three-game fluke cannot define a magnitude, early enough
that a rotation player is ~⅔ his own numbers by late November. It is a bound on
cold-start nonsense, not a parameter with an optimum, and it is recorded in every
registry entry and every `metadata.json` so a future tuned value appears as a diff.

**The priors are hand-set league-*shape* constants, not means computed from the
dataset**, and that is a leakage decision rather than a convenience: a prior fitted on the
four seasons being featurised would put a little of every future game into every past
row. They are deliberately **replacement-level rather than league-average**, because the
players whose magnitude is mostly prior are by construction the ones with almost no
history — two-way call-ups and rookies — not median NBA players.

| Magnitude | Prior | Reasoning |
|---|---:|---|
| `tm_MIN` | 10.0 | a fringe rotation night |
| `tm_FGA` | 6.0 | ~0.6 shots per minute at 10 minutes, a low-usage bench line |
| `tm_USG` | 15.0 | five men on the floor share 100%, so 20 is average *for a starter*; 15 is the shape of a bench player's usage |

Consolidating all three magnitudes onto **one** career-scoped join also fixed an
incoherence nobody had noticed: v2 read its minutes magnitude off the *season*-scoped join
and its usage magnitude off the *career* one, so "recently" meant two different things
inside a single sum.

#### The reliability columns

Six features. Shrinkage decides what number to use; these tell the estimator how much
evidence is behind it, so it can discount the whole expected-context block for a team of
call-ups in week 1 instead of trusting a sum of priors as though it were a sum of
measurements.

| Feature | Definition |
|---|---|
| `magnitude_ess` | effective sample size behind the row's **own** magnitude: appearances in the window, 0…20 |
| `teammate_magnitude_ess` | the **absence-weighted mean** effective sample size behind this row's expected vacated aggregate, `Σ(1−p_j)·n_j / Σ(1−p_j)`. "The 30 expected vacated minutes rest on 18 games of evidence" and "…on 2" are different claims |
| `season_appearances` | as-of appearances in the **current** season. `n_appearances` is career-scoped and cannot tell a veteran in October from a veteran in April |
| `games_with_current_team` | prior scheduled team-games with this team, career-scoped. The trade-context feature previously listed as a **[GAP]**; derived from the universe itself rather than from `player_team_stints`, which needs no new source and cannot disagree with the rows being modelled |
| `is_rookie` | no appearance in **any** strictly-earlier season. Computed from a per-(player, season) appearance count shifted one season back, **not** from "the first season in which he ever appeared" — that version reads the future for a player rostered in season S who does not debut until S+1 |
| `is_traded` | this row's team differs from the first team he was rostered with this season |

Measured on the four-season dataset: `is_rookie` fires on 36.9% of rows (it is
career-first-season, so it covers the whole of a debut year), `is_traded` on 5.8%,
`magnitude_ess` has mean 17.2 of a maximum 20, and `teammate_magnitude_ess` mean 13.9.

### 4.3 The two-stage pipeline — where `p_j` comes from (new in v3)

The expected-context features are functions of `p_j`. **If `p_j` came from a model whose
training window contained row *j*, the leak is back** — one indirection further away and
correspondingly harder to see, because the number reaching row *i*'s feature would be a
fitted view of row *j*'s own label. And if `p_j` came from a model that itself used
teammate context, the construction is circular.

So, four stages, one iteration:

| Stage | What happens |
|---|---|
| **1** | Fit a **base availability model** on `config.BASE_FEATURE_COLS` — the pre-v2 list, 36 columns, **no teammate context of any kind, expected or realized.** `models.cross_fit_base_probabilities` raises `LeakageError` if handed one, and a test pins that in both the expected and oracle directions |
| **2** | Produce **strictly out-of-fold `p_j` for every scheduled row** by a **time-safe cross-fit**: cut the history into consecutive calendar-month blocks and, for each block, fit the base model on rows strictly *before* the block start and score the block. Every `p` carries its block start as `P_CONTEXT_CUTOFF`, and `validate_out_of_fold` — the same guard `P(play)` and `E[minutes|plays]` already pass through — refuses any row whose cutoff is after its own game date |
| **3** | Build the expected-context columns from those `p` |
| **4** | Fit the final availability and minutes models on them. **One iteration only:** the final availability model's probabilities are never fed back into the context features |

**Why blocks rather than a per-origin refit, which is the obvious alternative.** A base
model trained strictly before an origin's validation window is out of fold for the
*validation* rows and hopelessly in-fold for the *training* rows — and the training rows'
context features are what the final model is fitted on, so the leak would simply move
from the metric to the fit, where no metric can see it. The block cross-fit is the only
scheme that is out of fold on **both sides at once**. It is forward-chaining rather than
random K-fold for the usual reason: a random fold reads the future.

**Why it does not need redoing per origin.** The blocks are strictly time-ordered, so one
cross-fit is simultaneously valid for every origin: whichever origin a row belongs to,
its `p` was produced by a model that could not see its own block. Recomputing per origin
would produce identical numbers at five times the cost. `evaluate.py` therefore consumes
the dataset's stored `P_CONTEXT`, and the OOF guard is what makes that safe rather than
convenient.

**The cold blocks, and the honest accounting.** The earliest blocks have too little
history to fit anything useful (`CROSS_FIT_MIN_TRAIN_ROWS = 5,000`), so their rows fall
back to a **stage-0 baseline** probability: `avail_rate_10`, the shifted appearance rate,
itself as-of safe, with a hand-set constant `CONTEXT_P_PRIOR = 0.70` where even that is
null. That is a *weaker* probability, not a leaky one. On the four-season build:

- **93.62% of rows carry a base-model `p`**; 6.38% carry the stage-0 baseline. The build
  prints the split, so a future run where the fallback share grew would be visible.
- mean `p` = 0.7291 against an observed played rate of 0.7132 — a small upward bias,
  consistent with the fallback rows being disproportionately early-season.

**Iterating is deliberately not done.** A second pass — rebuild context from the final
model's probabilities, refit — is a fixed-point search whose out-of-fold story gets
harder to state with each pass, for a second-order gain nobody has measured. One pass, and
this paragraph, is the honest version.

**Serving.** `train.py` persists the base model as a third artifact at the same cutoff
(`base_availability_model.joblib`), because an unplayed slate has no stored `P_CONTEXT` —
only a model can supply one. `predict.py` then runs stages 1–3 **before** any final
scoring, and the injury report is applied **twice**: once to `p_j` before the teammate sums
are taken, and once to the player's own final `P(play)`. Section 7.1 explains why the
first application is not optional.

## 5. Models and champion policy

Per-target champion/challenger, promoted only on measured evidence. All numbers
below are on the **v3** dataset (`reports/20260817d.md`, 5 development origins). No
champion changed in the v3 phase — the features changed under them, and they changed
downwards.

| Target | Champion | Challengers | Evidence |
|---|---|---|---|
| Availability P(plays) | LightGBM classifier (400 trees, lr 0.05, 31 leaves, min_child 50, subsample/colsample 0.8, `subsample_freq=1`) | logistic regression | Brier **0.0720** vs 0.1112 shifted-appearance-rate baseline (−35%) and 0.0955 logistic, stable across 5 origins. v1 features score 0.0735 on the same rows, so the served teammate context is worth **−2.0%**; the oracle construction was worth −3.1% (section 5.1) |
| Minutes \| played | LightGBM regressor | EWMA, ridge | MAE **4.679** vs EWMA 4.824 (−3.0%) and vs ridge 4.735. v1 features score 4.718, so the served teammate context is worth **−0.8%** — against −3.9% for the oracle construction. Promoted 2026-08-17; the artifact is trained, checksummed and loaded by the serving path |
| Production rate (all 9 cats) \| played — **see section 12, this row is scoped to PTS/AST** | **EWMA of stat per minute, halflife per stat** — deliberately not a trained model. PTS/AST at halflife 5; the nine new stats carry inner-fold selections and STL ships an expanding mean (section 12.3) | ridge, LightGBM on whole-game totals; expanding-mean rate; whole-game EWMA | **Not promoted, and it is no longer a close call.** Ridge's edge over EWMA on PTS totals is **0.81%** and LightGBM's 0.49%; on the v2 (oracle) dataset those read 1.83% and 1.63%. Ridge clears the >2% bar in **0 of 5** origins (0.75 / 0.80 / 1.53 / 0.55 / 0.43%), down from 2 of 5. AST is 0.62%. See section 5.1: the apparent move toward trained production models was itself an artifact of the oracle features |
| Composition (unconditional stat) | **P(plays) × E[minutes \| plays] × per-minute rate** | P × EWMA(total), P × LightGBM(total), direct LightGBM on all scheduled rows | **3.942** MAE vs 3.999 for P × EWMA, 3.974 for P × LightGBM, 3.987 direct, 4.572 naive unconditional mean. Still the measured best as well as the configured champion, on every origin. v1 features score 3.954 |

A fourth "composition" family exists in `config.CHAMPIONS` because how the
promoted estimators are combined is a separate decision from which estimators
they are, and the old report had no place to record it. `evaluate.py` emits a
parity check on it and exits non-zero if the promoted composition loses more than
1% MAE to the one it replaced. On v3 it **gains** 1.44%, comfortably inside tolerance
in the favourable direction.

Direct LightGBM on all scheduled rows measures 3.987 and is still not the
champion — it does not even lead, so the argument for the decomposition does not have
to lean on "the decomposition also yields P(plays)". It wins outright.

### 5.1 The honest-vs-oracle bracket (v1 / v3-honest / v2-oracle, identical rows)

**This section replaces the old "what the teammate features bought" table, and it
replaces it because that table was measuring the wrong thing.** Same dataset, same
validation rows, same five origins, same estimators, same hyperparameters; three
feature lists:

- **v1** — 36 features, no teammate context of any kind. The floor.
- **v3-honest** — 51 features: v1 + `usg_ewma` + 8 expected-context + 6 reliability.
  **The served set.**
- **v2-oracle** — 45 features: exactly the historical v2 list. v1 + `usg_ewma` + the
  8 realized-absence columns. **An upper bound, not a forecast**: it reads other
  players' target-game labels and cannot be earned at any horizon.

`survived` = honest_delta / oracle_delta — the share of the
value-of-perfect-lineup-information result that survives honest construction. Negative
percentages are better.

| Target | Cohort | n | v1 | **v3-honest** | v2-oracle | honest Δ% | oracle Δ% | **survived** |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| **Availability Brier** | ALL | 30,917 | 0.0735 | **0.0720** | 0.0712 | −1.98% | −3.05% | **65%** |
| | star (≥30) | 6,958 | 0.0666 | 0.0662 | 0.0665 | −0.55% | −0.22% | 246% |
| | starter (20-30) | 9,940 | 0.0681 | 0.0672 | 0.0672 | −1.38% | −1.38% | 100% |
| | bench (10-20) | 8,177 | 0.0681 | 0.0675 | 0.0659 | −0.89% | −3.29% | 27% |
| | fringe (<10) | 5,058 | 0.1076 | 0.1022 | 0.1001 | −4.99% | −6.94% | 72% |
| | unknown (no history) | 784 | 0.0373 | 0.0359 | 0.0334 | −3.71% | −10.33% | 36% |
| | event: vacated ≥ 30 | 23,231 | 0.0770 | 0.0759 | 0.0750 | −1.42% | −2.68% | 53% |
| | event: star_out = 1 | 7,219 | 0.0784 | 0.0771 | 0.0757 | −1.65% | −3.47% | 48% |
| | **control**: vacated < 5 | 955 | 0.0601 | 0.0534 | 0.0518 | −11.18% | −13.87% | 81% |
| **Minutes MAE** | ALL | 21,853 | 4.718 | **4.679** | 4.532 | −0.81% | −3.94% | **21%** |
| | star (≥30) | 5,598 | 3.830 | 3.771 | 3.822 | −1.54% | −0.21% | 728% |
| | starter (20-30) | 7,823 | 4.642 | 4.626 | 4.548 | −0.34% | −2.02% | 17% |
| | bench (10-20) | 5,995 | 5.524 | 5.485 | 5.199 | −0.71% | −5.90% | 12% |
| | fringe (<10) | 2,411 | 5.040 | 4.991 | 4.511 | −0.96% | −10.49% | **9%** |
| | unknown (no history) | 26 | 5.640 | 4.668 | 4.293 | −17.23% | −23.88% | 72% |
| | event: vacated ≥ 30 | 16,625 | 4.751 | 4.719 | 4.576 | −0.68% | −3.67% | 18% |
| | event: star_out = 1 | 5,196 | 5.127 | 5.098 | 4.914 | −0.56% | −4.17% | 14% |
| | **control**: vacated < 5 | 550 | 4.306 | 4.296 | 4.251 | −0.23% | −1.28% | 18% |
| **PTS uncond. MAE** | ALL | 30,917 | 3.954 | **3.942** | 3.911 | −0.32% | −1.10% | **29%** |
| | star (≥30) | 6,958 | 6.299 | 6.280 | 6.285 | −0.30% | −0.21% | 143% |
| | starter (20-30) | 9,940 | 4.571 | 4.560 | 4.533 | −0.23% | −0.82% | 28% |
| | bench (10-20) | 8,177 | 3.105 | 3.096 | 3.044 | −0.30% | −1.96% | 16% |
| | fringe (<10) | 5,058 | 1.444 | 1.434 | 1.373 | −0.72% | −4.96% | 14% |
| | unknown (no history) | 784 | 0.334 | 0.299 | 0.303 | −10.57% | −9.47% | 112% |
| | event: vacated ≥ 30 | 23,231 | 4.051 | 4.040 | 4.023 | −0.26% | −0.69% | 38% |
| | event: star_out = 1 | 7,219 | 4.149 | 4.140 | 4.154 | −0.21% | **+0.13%** | −161% |
| | **control**: vacated < 5 | 955 | 3.096 | 3.052 | 2.973 | −1.42% | −3.96% | 36% |

#### What the bracket says, without softening it

**1. Most of the v2 minutes headline was hindsight.** The v2 phase reported −3.9%
aggregate minutes MAE and −10.3% on the fringe tier as the marquee result of the
teammate family. Honest construction recovers **21% of the aggregate and 9% of the
fringe number**. The −10.3% fringe figure, which the v2 write-up called "the reason to
believe the result is real", was almost entirely a measure of what the final inactive
list is worth — and the fringe tier is where that is most true, because a fringe
player's minutes are the *most* determined by exactly who is out and the *least*
predictable from anything else.

**2. Availability holds up much better than minutes: 65% versus 21%.** That asymmetry
is the informative part of the whole table and it has a clean reading. Availability
decisions are correlated *across a roster and across time* — a team resting three
starters on a back-to-back is one decision, and the base model can see the
back-to-back, the rest days and the appearance histories that produced it. So a
probabilistic reconstruction of "who is likely out" recovers most of the availability
signal. Minutes, by contrast, need to know **which specific 34 minutes were actually
vacated tonight**, and an expectation over a roster's play probabilities is a much
blunter instrument than the list. The gap between 65% and 21% is a direct measurement
of how much of the teammate signal is *identity-specific* rather than *aggregate*.

**3. The reliability columns pay for themselves, and they pay in the places the
expected-context sums do not.** Three cells have `survived` above 100%, meaning
v3-honest beat the oracle set:

- **star minutes: −1.54% honest vs −0.21% oracle.** The oracle set has nothing to say
  about stars — they play ~34 minutes whoever is out — but `season_appearances`,
  `magnitude_ess` and `games_with_current_team` distinguish a veteran in April from a
  veteran in October, and that turns out to be worth more on stars than any vacancy
  information.
- **`unknown (no history)` unconditional PTS: −10.6% honest vs −9.5% oracle.** This is
  the cold-start cohort by definition, and the cold-start features win it.
- **starter availability: exactly 100%** — the two constructions tie.

This also means the bracket is **not** a clean one-variable contrast, and it is not
presented as one: v3-honest carries the reliability block and v2-oracle does not,
because the served set is the thing worth bracketing and the cold-start fix ships with
the probabilistic construction. The three >100% cells are where that choice shows up.

**4. No cohort regressed under v3-honest.** Every one of the 27 cells is negative. The
control cohort (`vacated_minutes < 5`, 955 rows) improves by 11.2% on Brier and 0.2% on
minutes, so the family is not buying wins on busy nights by adding noise to quiet ones.
The control's *large* Brier gain is not a bug and was explained in the v2 write-up for a
reason that still holds: "the team is fully healthy" is itself strong evidence this
player suits up, so the low end of the vacancy scale is informative and is the easier
end to exploit.

**5. `star_out = 1` unconditional points: the oracle set makes it WORSE (+0.13%) and
the honest set makes it slightly better (−0.21%).** The v2 write-up read its +0.01% as
"minutes are redistributed predictably when a creator sits, but *who* absorbs his shots
is not, and a per-minute rate that cannot respond has nothing to move". That reading
survives and gets sharper: with a full point of extra minutes accuracy from the oracle
and a rate that cannot use it, the composition **converts a real minutes gain into a
points loss**. It is the strongest available argument for making the production rate
context-aware, which remains the top item in section 9.

**6. The "trained production models are catching up" finding from v2 was itself an
oracle artifact.** Ridge's edge over EWMA on conditional points read 1.83% on the v2
dataset and reads **0.81%** on v3 — and clears the 2% promotion bar in 0 of 5 origins
rather than 2 of 5. The round-1 reviewers predicted trained models would start beating
EWMA once context features existed; the honest answer is that they start beating it once
*oracle* context features exist, which is not the same claim. The promotion bar was set
before either result and has not moved.

Model-free cohort check (no models involved; `evaluate.cohort_outcome_lift`), retained
because it is what makes the cohort definitions themselves auditable:

| Cohort | rows | mean MIN | vs population | mean PLAYED | vs population |
|---|---:|---:|---:|---:|---:|
| event: vacated ≥ 30 | 74,715 | 22.79 | +0.22 | 0.7210 | +0.0077 |
| event: star_out = 1 | 22,476 | 23.11 | **+0.54** | 0.7224 | +0.0092 |
| control: vacated < 5 | 8,916 | 22.02 | −0.55 | 0.6875 | −0.0257 |
| vacated ≥ 30 **[permuted control]** | 74,054 | 22.61 | **+0.04** | 0.7146 | +0.0013 |

The permuted column's identically-sized cohort moves the mean by 0.04 minutes against
the real column's 0.22 and `star_out`'s 0.54. The cohorts contain what they claim to.

### 5.2 Feature importance, the negative control, and the single-feature ablation

Split gain, mean over 5 origins, ranked within each model (availability gain and
minutes gain are different units and a pooled ranking would be arithmetic on
incomparable scales). **51 features total** in the served set.

| Model | Feature | Rank | Share of total gain |
|---|---|---|---|
| Minutes | `exp_depth_rank` | **4 / 51** | **4.2%** |
| Minutes | `exp_vacated_usg` | 7 | 0.50% |
| Minutes | `teammate_magnitude_ess` | 8 | 0.45% |
| Minutes | `exp_depth_rank_pos` | 9 | 0.45% |
| Minutes | `season_appearances` | 10 | 0.45% |
| Minutes | `exp_top3_usage_out` | 11 | 0.44% |
| Availability | `exp_vacated_usg` | **7 / 51** | 1.01% |
| Availability | `games_with_current_team` | 9 | 0.98% |
| Availability | `exp_depth_rank` | 11 | 0.89% |
| Availability | `season_appearances` | 13 | 0.60% |
| Availability | `teammate_magnitude_ess` | 15 | 0.59% |

**The v2 write-up's headline importance finding shrank by a factor of four, and it
shrank in exactly the way the bracket predicted.** `depth_rank_available` was rank
**2 / 45** carrying **18.4%** of the minutes model's split gain, and the v2 document
called that "the single biggest finding". Its honest twin `exp_depth_rank` is rank
**4 / 51** carrying **4.2%**. The ordinal is still the most valuable thing in the
family — it still beats every sum, and "where does this player sit in tonight's likely
available depth chart" is still almost the definition of a minutes forecast — but
**three-quarters of the gain it appeared to carry came from knowing the answer.**

The reliability columns are a genuine and unexpected presence in both models:
`games_with_current_team` is rank 9 of 51 for availability, ahead of every expected
vacancy sum except `exp_vacated_usg`, and `teammate_magnitude_ess` and
`season_appearances` sit in the minutes top ten. That is consistent with the >100%
`survived` cells in section 5.1: the cold-start block is doing work the vacancy block
is not.

**`magnitude_ess`, `is_rookie` and `is_traded` are near-useless** by gain (`magnitude_ess`
rank 49 in both models at 0.01–0.02% of gain; `is_rookie` 47/47; `is_traded` 48/45), and
they are kept for the same reason `star_out` was: they cost nothing and they are
interpretable handles. The honest reading is that `season_appearances` and
`teammate_magnitude_ess` already carry the continuous version of what the binaries
encode — `magnitude_ess` in particular is nearly a deterministic function of
`season_appearances` and `n_appearances` once a player is past his twentieth game.

#### The negative control, and it does not come out well

A randomly *permuted* copy of `exp_vacated_minutes` — same marginal distribution, same
skew, same zero mass, no relationship to the row — is fitted alongside the real column
in a separate pass, so the headline numbers above are untouched by it.

| Model | real `exp_vacated_minutes` | permuted twin | ratio | v2's ratio, for contrast |
|---|---|---|---|---|
| Availability | 2,770 (rank 25) | 3,349 (rank 20) | **0.8×** | 3.8× |
| Minutes | 164,050 (rank 17) | 118,342 (rank 26) | **1.4×** | 3.1× |

**`exp_vacated_minutes` is not distinguishable from a permutation of itself in the
availability model — the permuted twin carries slightly MORE gain — and is only 1.4×
its permutation in the minutes model.** The v2 realized column was 3.8× and 3.1×. This
is reported rather than buried because it is the sharpest single statement of what the
phase found:

- The realized column carried genuine outcome information, which is why it beat its
  permutation comfortably and why the bracket's oracle end is where the numbers are.
- The expected column is a smooth function of the same as-of features the base model
  already used, so much of its content is **redundant with features the model has
  anyway** — a booster given `avail_rate_10`, `roll3_MIN` and `games_since_last_app`
  can approximate a team's expected vacancy total without being handed it.
- What survives is concentrated in the columns that are *not* smooth reweightings of
  own-player history: `exp_depth_rank` (an ordinal about the roster) and
  `exp_vacated_usg` (a usage hierarchy, which own-player features cannot see).

The permuted column's gain is **not zero**, and that is why the control is run rather
than a raw gain number read: LightGBM assigns gain to any column with variance and
enough distinct values to split on.

#### Single-feature ablation: `exp_depth_rank` removed and refit

Split gain says how a booster allocated credit among correlated columns. An ablation
says what a column is worth. **Reported, not asserted — there is no bar it has to
clear.** Positive means removing it cost accuracy.

| Target | Cohort | with | without | cost of removal |
|---|---|---:|---:|---:|
| Availability Brier | ALL | 0.07201 | 0.07223 | **+0.32%** |
| | fringe (<10) | 0.10223 | 0.10358 | +1.32% |
| | bench (10-20) | 0.06749 | 0.06719 | −0.45% |
| | control: vacated < 5 | 0.05340 | 0.05519 | +3.34% |
| Minutes MAE | ALL | 4.6794 | 4.6876 | **+0.18%** |
| | bench (10-20) | 5.4849 | 5.5159 | +0.56% |
| | fringe (<10) | 4.9914 | 5.0080 | +0.33% |
| | star (≥30) | 3.7707 | 3.7608 | −0.26% |
| | event: star_out = 1 | 5.0984 | 5.1148 | +0.33% |
| PTS uncond. MAE | ALL | 3.9416 | 3.9469 | **+0.13%** |
| | bench (10-20) | 3.0956 | 3.1076 | +0.39% |

**A column carrying 4.2% of the minutes model's split gain is worth 0.18% of its MAE.**
That is the ablation's whole point, and the gap is not a contradiction: gain measures how
often a booster *chose* to split on a column, and with `ewma_MIN`, `roll3_MIN`,
`roll5_MIN` and `std_MIN` all available, most of what `exp_depth_rank` says about a
player's role is recoverable from his own recent minutes. The signs are also
interpretable rather than random: the cost is positive on **bench** (+0.56%), **fringe**
(+0.33%) and **star_out = 1** (+0.33%) — the cohorts where role is genuinely contested —
and mildly *negative* on **stars** (−0.26%), where a depth rank of 1 is a constant and
the column is noise the model is better off without.

Two consequences worth stating: any future claim of the form "feature X carries N% of
the gain" in this document should be read as an allocation statement and not a value
statement; and the natural next experiment is a **per-cohort feature gate** — supply
`exp_depth_rank` below the star tier and withhold it above — which is not implemented.

### 5.3 Level-C: the degraded-oracle grid, and what it says our base model is worth

The bracket has two ends — our own base model and perfect information. The round-2
review asked for the space between them, and it is the most useful table in this
document because it converts an abstract "21% survived" into an operational quantity.

The construction (`evaluate.degrade_absence_knowledge`): a synthetic pre-tipoff report
identifies a fraction `recall` of tonight's *real* absences and falsely flags a fraction
`false_positive_rate` of the players who did in fact play. A flagged player gets `p = 0`;
everyone else keeps his base-model probability — because a report that *misses* an
absence is silent about that player, not confident about him. The expected-context
features are then rebuilt from those probabilities and availability + minutes are refit
on all five origins. **This is a diagnostic: it reads target-game labels by construction,
which is what makes it a measure of information value and what disqualifies it from ever
being a feature.**

| recall ↓ / FP → | 0% Brier | 0% MAE | 5% Brier | 5% MAE | 10% Brier | 10% MAE |
|---|---:|---:|---:|---:|---:|---:|
| **40%** | 0.07146 | 4.640 | 0.07178 | 4.667 | 0.07184 | 4.677 |
| **60%** | 0.07113 | 4.611 | 0.07175 | 4.647 | 0.07176 | 4.663 |
| **80%** | 0.07075 | 4.587 | 0.07139 | 4.619 | 0.07158 | 4.644 |
| **100%** | **0.07039** | **4.547** | 0.07130 | 4.589 | 0.07136 | 4.616 |

Reference points from section 5.1, on the same rows: **v1** = 0.07346 / 4.718,
**v3-honest** = 0.07201 / 4.679, **v2-oracle** = 0.07122 / 4.532. Runtime: 4.7 minutes.

**1. The top-left cell is the oracle, and it checks out.** `recall = 100%, FP = 0%`
scores 4.547 minutes MAE against v2-oracle's 4.532 — 0.3% apart. The residual difference
is not noise but arithmetic: this cell expresses the same absence set through the v3
machinery (shrunk career-scoped magnitudes, expected form) while v2-oracle uses
season-to-date magnitudes and a realized indicator. That the two agree to 0.3% is the
check that the grid's reference is the oracle it claims to be, and a test pins the
absence set itself as exact at this cell.

**2. Our base model is worth about 20% recall on minutes.** Expressing v3-honest's
position on the recall axis by linear interpolation (v1 → oracle span = 0.171 minutes
MAE; v3-honest recovers 0.038 of it, i.e. 22%; the 40%-recall row already recovers 46%):

| | share of the v1 → oracle minutes span recovered |
|---|---:|
| v3-honest (our base model's probabilities) | **22%** |
| a report with 40% absence recall, 0% FP | 46% |
| a report with 60% recall | 62% |
| a report with 80% recall | 76% |
| perfect information | 100% |

**So the whole two-stage probabilistic construction is worth less than a hypothetical
report that catches two absences in five.** That is the sharpest available statement of
how much of the teammate signal is identity-specific, and it is the number that should
govern where effort goes next: a real injury-report feed at even mediocre recall
dominates any amount of further work on `p_j` from box scores. `player_injury_reports`
holding 0 rows is therefore not a minor gap — it is the binding constraint on this entire
feature family.

**3. Availability is the mirror image, as the bracket said.** On Brier the v1 → oracle
span is 0.00307, of which v3-honest recovers **47%** and a 40%-recall report recovers 65%.
Availability probabilities transfer across a roster (the back-to-back that rests three
starters is one visible decision); minutes need to know which specific 34 minutes opened
up.

**4. False positives are expensive, and asymmetrically so.** Moving from 0% to 10% FP at
full recall costs 0.069 minutes MAE — **more than the entire gain from raising recall
from 40% to 100%** (0.093 for the whole range, so 10% FP eats three-quarters of it). At
40% recall, 10% FP wipes out the gain almost entirely (4.677 vs v3-honest's 4.679). The
operational implication for any future report ingestion: **a feed that occasionally
flags a healthy player as out is worse than a feed that misses absences**, and precision
should be preferred to recall when the two trade off. Nothing in the current system would
have told us that.

### 5.4 The shipped artifact's own holdout

The rolling origins above are for model *selection*. The registry records a separate
28-day holdout immediately before the training cutoff, which measures the recipe
that ships. **All three artifacts share an identical holdout window** (2026-03-17,
6,527 scheduled rows, played rate 0.6974) and identical hyperparameters, so the
differences are the feature set and nothing else:

| Registry metric | v1 `20260817b` | v2-oracle `20260817c` | **v3 `20260817d`** | v3 vs v1 | v3 vs v2-oracle |
|---|---:|---:|---:|---:|---:|
| Brier | 0.08883 | 0.08244 | **0.08710** | −1.9% | +5.7% |
| log loss | 0.30662 | 0.28354 | **0.29901** | −2.5% | +5.5% |
| Brier skill vs shifted rate | 0.2720 | 0.3244 | **0.2862** | +1.4 pp | −3.8 pp |
| minutes MAE \| played | 5.3315 | 4.7950 | **5.2256** | −2.0% | +9.0% |
| PTS conditional MAE | 4.7771 | 4.6530 | **4.7544** | −0.5% | +2.2% |
| AST conditional MAE | 1.4151 | 1.3868 | **1.4101** | −0.4% | +1.7% |

**The `20260817c` column is not a target and never was — it is what the oracle scored,
and the "+" signs in the last column are the honest cost of removing the leak.** Read
left to right: v3 improves on v1 on every metric, and gives back most of what v2
appeared to gain.

The same caveats as before apply to the *magnitude* of all of these: one 28-day window
carries far more variance than a mean over five, and this particular window is late
March, when absences and rest days are at their densest — the conditions the family was
built for, which flatters the oracle most of all. **The five-origin numbers in section
5.1 are the conservative ones and the ones to quote.** The v2 write-up's −10.1% minutes
figure from this window is the single most inflated number the previous version of this
document published; its honest counterpart is −2.0%.

The shipped `20260817d` directory carries **three** artifacts, not two:
`availability_model.joblib` (51 features), `minutes_model.joblib` (51 features), and
`base_availability_model.joblib` (36 teammate-free features, stage 1 of section 4.3),
all at cutoff 2026-04-14. `metadata.json` records a `context` block with the cross-fit
frequency, the minimum-training-rows gate, `CONTEXT_P_PRIOR`, the magnitude window, the
shrinkage constant, the priors and `iterations: 1`, because those are hyperparameters of
the *features* and belong in the audit record for the same reason `subsample_freq` does.

A model-free version of the same control is in the report: the real
`vacated_minutes ≥ 30` cohort's mean minutes differ from the population by
+0.22 (and `star_out = 1` by +0.54), while the permuted column's identically-sized
cohort differs by −0.006. Both the model and the model-free check are pinned as
tests, so a future change that broke the cohort machinery would fail CI rather than
produce a flattering table.

### Uncertainty — a prototype, not calibration

P10/P50/P90 are served for minutes and points as **global additive offsets**: the
empirical quantiles of the champion's residuals over one holdout window, added to
every point estimate, sorted so they cannot cross. That is the whole mechanism.

Stated plainly, because the previous wording implied more than the code does:

- These bands are **not conditionally calibrated.** The offsets do not depend on
  the player, his minutes tier, the opponent, or anything else. A fringe player
  and a star receive the same ±8 points.
- They are **not conformal.** There is no exchangeability argument, no calibration
  set held separate from the residual set beyond the single holdout window, and no
  finite-sample coverage guarantee of any kind.
- The only property they have is average coverage over the window they were fitted
  on. Reported 80% coverage in `reports/` is that in-window average, and it should
  not be read as a per-player claim.
- Consequence, expected and unmeasured per-segment: intervals are too wide for
  low-minute players and too narrow for stars.

The offsets are refit against whatever estimator actually ships — when the
conditional points estimate changed shape to `E[minutes] × rate`, the offsets moved
with it. An offset set measured against one point estimate and applied to another
is a band drawn around a different number than the one it decorates, and its
coverage claim is void.

**[PLANNED]** The upgrade is two changes, in this order: (1) conditional quantile
regression (LightGBM quantile objective at 0.1/0.5/0.9, or an explicit
heteroscedastic parameterisation) so width varies with context; (2) **group
conformal** calibration by minutes tier on top, so each tier gets a finite-sample
coverage guarantee rather than a league-average one. Neither is implemented.

**[GAP]** Only MIN/PTS/AST are served, so only MIN/PTS carry bands. REB/STL/BLK/3PM/TOV and the
makes/attempts decomposition for FG%/FT% (predict FGA/3PA/FTA + make rates,
derive points, keep arithmetic coherence) are designed and still not built —
requires widening ROLL_STATS, which bumps the feature version.

## 6. Training & evaluation protocol

### Terminology (corrected 2026-08-17)

The previous version of this document claimed the final months of 2025-26 were
"held out untouched as an eventual test period" while also stating the shipped
artifact is refit through 2026-04-14. Both cannot be true, and the second one is.
Four distinct windows, named so the claim cannot be muddled again:

| Name | What it is | What it can support |
|---|---|---|
| **Development origins** | Dec-2024, Jan-2025, Feb-2025, Dec-2025, Jan-2026. Train = everything strictly before each validation month | Model selection, champion promotion, hyperparameters |
| **Selection holdout** | Feb-2026 → Apr-2026, the final months of 2025-26. **Never used for model selection**, and **present in the deployment fit** | Nothing yet. It is untouched *by selection*, not untouched |
| **Deployment window** | Everything through 2026-04-12 (cutoff 2026-04-14), i.e. all four seasons including the selection holdout | The shipped artifact. Refitting on all available history is the right call for deployment and it consumes the holdout |
| **Prospective test** | The 2026-27 season, not yet played | The only genuinely untouched evaluation this system will ever have |

So: the selection holdout is **selection-untouched, used in the deployment fit**.
It can still detect a gross regression if a future artifact is trained with an
earlier cutoff and scored on it, but it is no longer a clean test set for the
shipped model and must not be reported as one. Every honest forward-looking number
about the shipped artifact will come from the prospective test, from the
append-only prediction store, or from nowhere. **The protocol governing that
prospective test is frozen in section 13** (`prospective_2026_27_v1`) — endpoints,
cohorts, comparison ladder, falsification thresholds and look dates, fixed before
opening night and enforced by `tests/test_prospective_freeze.py`.

- Rolling-origin (forward-chaining) evaluation over the development origins,
  never random splits: train = everything strictly before each validation month.
- Shipped artifact: honest metrics from a 28-day holdout immediately before the
  training cutoff, then refit on the full deployment window. Cutoff for
  v20260817b: 2026-04-14 (day after the 2025-26 regular season). The 28-day
  holdout is inside the deployment window too, for the same reason — it measures
  the recipe, not the shipped bytes.
- **359 tests in CI** (315 before this phase), of which ~40 are leakage tests: first-ever row has null history;
  season-scoped vs career-scoped join coherence; rolling windows exclude the target
  game (negative control: leaky variant provably differs); opponent form stops
  pre-target; last-appearance strictly precedes target; universe covers 100%
  of real appearances. One meta-lesson encoded as a test: pandas
  `groupby().first()` silently skips nulls and can mask the very leak a test
  targets. The v2 family added self-exclusion under a rebuilt feature frame, the split
  as-of contract (both halves, each with a negative control), the usage arithmetic
  against a hand computation, and rank independence from the row's own availability —
  all retained, now **scoped to the oracle variant** they were written for.

  **The v3 phase adds 33 tests (`tests/test_teammates_v3.py`)**, and four of them are
  the ones that matter:

  1. **Teammate-outcome invariance.** Flip any player's realized `PLAYED` /
     `LISTED_INACTIVE` / `MIN`, hold every as-of input including `p` fixed, and **no
     served feature of any row may move** — not the flipped player's, not his
     teammates', not a stranger's on another team. A structural twin drops the outcome
     columns from the frame entirely and requires bit-identical output, which is the
     version a reader can verify without trusting a flip.
  2. **The same test, pinned to FAIL on the oracle family.** Its `vacated_minutes`
     must move, and by *exactly* the flipped player's as-of MPG. This is a test we want
     to keep failing invariance: if it ever starts passing, either the oracle columns
     stopped being an oracle (so the bracket's upper bound is fiction) or the invariance
     test above became vacuous, and a pair of constant columns would satisfy it.
  3. **Pregame-status sensitivity, closed form.** Change `p_j` by Δ and each served
     column must move by exactly the stated amount: `−Δ·m_j` for the sums,
     `+Δ·1(m_j > m_i)` for the ranks, `−Δ` for `p_star_out` when *j* is the usage
     leader and 0 otherwise. Positional sums move only for bucket-mates. "Moved in the
     right direction" is a far weaker claim and would pass against the wrong magnitude,
     the wrong teammate, or a sign error.
  4. **Team-game block permutation.** The whole context block is permuted across
     team-games of equal roster size, so every row still receives a *real, internally
     coherent* team's context — merely attached to the wrong game. This is a strictly
     harder null than the per-row permutation, which hands a row a physically impossible
     block a booster could discount for the wrong reason. On a frame built so minutes
     genuinely depend on the context, gain share collapses and **out-of-sample MAE rises
     by more than 50%**; the MAE half is the substantive assertion, since gain is an
     allocation of credit and MAE is that credit's value.

  Plus: the base model refuses teammate-context features (`LeakageError`, pinned in both
  the expected and oracle directions); every cross-fit `p` carries a cutoff at or before
  its own game date, with the fitted branch exercised and a tampered-cutoff row required
  to raise; the shrinkage weight checked at `n = 0`, `n = k`, `n = 2k` and `n → ∞`;
  `is_rookie` verified against a per-season appearance count for every player-season; and
  the degraded-oracle probe verified to reproduce the oracle exactly at recall = 1,
  fp = 0.

  **11 further tests (`tests/test_serving_context.py`) pin the serving path**, which is
  where a leakage-safe feature set can still be wired wrongly. The load-bearing one
  requires that a report ruling a star OUT raises his backup's `exp_vacated_minutes` by
  *exactly* `(0.93 − 0.02) × 34` minutes — the probability shift times the star's
  magnitude — because a serving path that corrects the star's own number and nothing else
  is the original override defect displaced one column to the left. The same file pins
  that the as-of report filter applies at the *context* stage too (a T-60m report must not
  reach a T-24h run's teammate features, where it would be harder to notice than in a
  probability), that an absent report is an exact identity, that the rebuilt features
  carry the base model's cutoff through `validate_out_of_fold`, and the corrected horizon
  definition — window boundaries, the stored-metadata list, report staleness, zero-reports
  as a recorded fact, and `first_deadline_passed` on both sides of 5pm.
- Segment reporting by prior-minutes tier (star/starter/bench/fringe/
  no-history) **and by event cohort** (`vacated_minutes ≥ 30`, `star_out = 1`, plus
  `vacated_minutes < 5` as a no-event control). The tiers answer "where in the
  league is the model good"; the event cohorts answer the question a
  teammate-absence feature has to face, which is whether it helps on the nights it
  is about and leaves the quiet nights alone. Availability now has a cohort
  breakdown too — before v2 it had none, so "does knowing a teammate is out improve
  the availability model on high-absence games" was unanswerable from the report.

  **The cohorts are defined on the ORACLE `vacated_minutes` column, deliberately**, and
  that is what makes the v1 / v3-honest / v2-oracle bracket a comparison: all three
  passes partition the validation rows identically, so their per-cohort columns describe
  the same games. Using hindsight to *select which games to report on* is legitimate;
  using it to *build a feature* is not.
- Feature importance is recorded as tidy rows in the versioned results csv rather
  than printed and discarded, and every importance claim ships with a
  permuted-column control in the same table. **Two permutation controls as of v3**,
  because the report leans on two different nulls and they need two different columns:
  the *importance* null permutes the served `exp_vacated_minutes` (only a served column
  can appear in a fitted model's gain), and the *cohort* null permutes the oracle
  `vacated_minutes` (that is the column the cohorts are defined on).
- **Every evaluation artifact is a csv beside the markdown**, so a claim can be
  recomputed a year later without refitting: `<version>_results.csv` (the served pass),
  `<version>_bracket_results.csv` (all three feature-set passes, tagged), and
  `<version>_degraded_oracle.csv` (the Level-C grid). The markdown is a rendering, not
  the record.
- Immutable prediction store: every prediction run is versioned
  (model_version, feature_version, git SHA, artifact checksum, forecast
  cutoff timestamp) and append-only, so backtests compare what was actually
  predicted. A guard refuses to persist predictions built from the biased
  approximation universe.

## 7. Serving

Local training (minutes on CPU; artifacts ~1.4 MB, committed to git with a
registry). Batch predictions written to Postgres; the web app only reads
stored predictions — no model in the serving path. Products: per-player
projection card (P(plays), minutes and points bands, schedule-adjusted
expected points), a daily league-wide projections page, and a rule-based
watchlist (role increase, shot-volume surge, teammate-absence, return from
absence, hot streak — deterministic thresholds, score = weighted reasons ×
P(plays)). **[GAP]** The watchlist reason weights are hand-set, not learned,
and its retrospective precision has not yet been measured against realized
breakouts.

### 7.1 Injury-report override layer (implemented 2026-08-17; applied twice as of v3)

The availability model does not read the injury report, because report history
only starts accumulating on 2026-08-16 and training on it before then would be
leakage-by-imputation (section 4). So a player officially ruled OUT still scored
~0.93 to play and the projections page showed him at 28 points. No amount of model
quality fixes that — the information is not in the features.

A post-hoc layer, applied after scoring and before rows are written, corrects
P(plays) from the latest official designation known at the run's information
boundary. It is deliberately not a feature: a feature would need history to train
on, would diffuse the report's signal into coefficients where it cannot be audited
or switched off, and could not be measured separately from the model.

**As of feature_version v3 the same policy runs twice in one prediction run, and the
first application is the new one.** The served teammate-context features are
expectations over play probabilities, so the report has to reach `p_j` and not only the
player's own served number:

1. **On the base probabilities `p_j`, before the teammate sums are taken.**
   `predict.rebuild_context` scores the base availability model, applies the override, and
   *then* rebuilds `exp_vacated_minutes` and the rest from the corrected probabilities.
2. **On the final `P(play)`**, after scoring and before rows are built, with the
   unconditional stats recomputed. This is what the layer has always done.

**Why the first one is not optional.** Without it the serving path knows a star is out —
his own probability is correctly driven to 0.02 downstream — and declines to act on it for
anyone else. His backup's expected vacated minutes would still be computed as though the
star were 93% likely to play, so the backup would be projected for the minutes of a night
the star suits up. **Every number on the page would look individually defensible**, which
is exactly the failure mode the override layer was built to prevent, displaced one column
to the left and considerably harder to notice. `predict.py` prints the count of rows
corrected at the context stage next to the mean base and final probabilities.

Both applications share the policy table, the constants, and the as-of discipline; only
the vector being corrected differs. `overrides.resolve_overrides` is the shared
implementation and `overrides.apply_status_overrides` is the frame-level wrapper the
second stage uses.

**Measured end to end**, because a wiring claim should not be taken on trust. A probe
artifact trained to a 2026-03-01 cutoff, scored over the following slates twice — once
with a synthetic report ruling OUT each team's usage leader, once with no report at all,
everything else identical. Restricted to the 8 affected team-games on 2026-03-02:

| | no report | report | Δ |
|---|---:|---:|---:|
| the ruled-out players' `P(play)` | 0.4545 | **0.0200** | −0.4345 |
| their unconditional `E[MIN]` | 14.99 | **0.59** | −14.40 |
| their **conditional** `E[MIN\|plays]` | 29.35 | 29.35 | **0.00** |
| **their 113 teammates' conditional `E[MIN\|plays]`** | 20.86 | **21.77** | **+0.91** |
| their teammates' conditional `E[PTS\|plays]` | 9.13 | **9.40** | +0.27 |

The last two rows are the point. **Under the pre-P1b wiring they would both have been
exactly 0.00**, because the report reached the ruled-out player's own probability and
nothing else. The conditional row for the ruled-out players is 0.00 by design — being
less likely to play does not change how good the night would be, and moving both would
double-count availability.

Policy as implemented (`fnba_ml/overrides.py`). **Every number is hand-set** from
published league-wide play-through rates and the asymmetry of the two mistakes,
pending learned replacements:

| Designation | Rule | Rationale |
|---|---|---|
| OUT / SUSPENDED / G_LEAGUE | `P = 0.02` | Not 0.0: an official "out" is occasionally reversed, and a hard zero makes every calibration statistic on the bucket degenerate |
| DOUBTFUL | `P = 0.10` | Published play-through rate sits in the 5–15% range; a team's statement about a specific game dominates appearance history |
| QUESTIONABLE | `P = 0.6 × model + 0.4 × 0.60` | The only bucket where the model keeps a say — "questionable" is where teams put everyone undecided, stars and fringe alike. `0.60` is `LEAGUE_QUESTIONABLE_PLAY_RATE`, a **placeholder** from public analyses, not measured on our data, and the number here most likely to be wrong |
| PROBABLE | `P = 0.85 × model + 0.15` | A floor everywhere on [0, 1] — see the correction below |
| AVAILABLE / DAY_TO_DAY / UNKNOWN / unlisted | model unchanged | "day_to_day" is a roster note, not a statement about tonight. **"Available" is *not* informationally null** — see the note below |

**Correction, 2026-08-17 (P1b): the PROBABLE rule's `max` was provably dead code, and
its stated rationale was false.** The rule read `max(model, 0.85·model + 0.15)` and was
justified with "above ~0.99 the shifted form dips below the model's own number, and a
probable tag must never lower a projection". One line of algebra:

```
0.85p + 0.15 ≥ p   ⟺   0.15 ≥ 0.15p   ⟺   p ≤ 1
```

which holds for every probability. The two forms coincide at exactly `p = 1` and the
shifted form is strictly larger everywhere below it, so **the `max` never once selected
its first argument.** The code is now the shifted form alone. This was worse than ordinary
dead code: a reader who trusted the comment would have believed the layer carried a guard
it does not need, and would have looked for that guard's effect in the wrong place.

The general statement, for whoever tunes these next: `w·p + s` is a floor on [0, 1] **iff
`s ≥ 1 − w`**. Here `0.15 ≥ 0.15` with equality — the tight case. Any smaller shift at
this weight *would* need the `max`, so a change to either constant has to re-check the
inequality rather than assume it. What the numbers do: a bench player the model has at
0.40 is lifted to 0.49; a star at 0.95 moves to 0.9575, a deliberate near-no-op.

**[PLANNED] "AVAILABLE" is not informationally null.** The justification for passing it
through — "the model is already answering that question with more information than the
label carries" — is true only of a player who was *never on the report*. A player who
appears on the report **as** `available` is a different animal: he was listed questionable
or doubtful at some earlier capture and has since been **cleared**. That transition
carries information the model provably does not have (it does not read the report at all),
and it points the *opposite* way from the appearance history that a recent absence has
just depressed. The honest fix needs the report **history**, not the latest row —
`available` after a `questionable` is a clearance, `available` with no prior designation
this cycle is noise, and only a query over the player's report sequence can tell them
apart. `player_injury_reports` is append-only and currently holds 0 rows, so the sequence
exists going forward and does not exist for any backtest. **Logged as future work rather
than guessed at:** an `available` rule invented from nothing would be a sixth hand-set
constant with no measurement behind it.

Every overridden row carries the model's own probability, the overridden one, the
reason, and the report's `captured_at`, so the layer is measurable against the
model it corrects and these constants can be replaced by learned ones with a
visible diff. Unconditional stats are recomputed from the overridden probability;
conditional stats and quantiles are **not** touched — being less likely to play
does not change how good the night would be, and moving both would double-count
availability.

As-of discipline applies here too: a report captured at or after the run's
boundary is dropped, or a backtest of the T-24h horizon quietly reads the T-60m
report and looks prescient.

Storage: no schema change. `player_game_predictions` is long-format precisely so
the stat vocabulary can grow — `prob_active` (post-override), `prob_active_model`,
`status_override` (reason as an append-only numeric code) and `status_captured_at`
(epoch seconds) are new stat names, not new columns. The two status rows are
written only for overridden player-games; absence is the honest record of "the
model's probability stands".

That design was paid off in full on 2026-08-18: going from three served stats to
twelve took the run from **14 rows to 62 rows per scheduled player-game and required
no migration at all**, because every name involved was already reserved in migration
014's own comment (section 12.6).

### 7.2 Named forecast horizons — redefined 2026-08-17 (P1b)

A projection is a number *and* how long before tipoff it was made. The same model
scoring the same game at T-24h and T-60m makes two different claims.

**The previous definitions were wrong about what separates them, and the `early` row
was factually false.** It said "no injury report yet; pure model". The NBA's
participation-report policy requires an **initial report by 5pm local on the day before
the game**, so a run made 24 hours before a 7pm tipoff has usually had a report available
for two hours. **What distinguishes the horizons is not report / no report; it is which
report and how stale.**

So a horizon is now defined by a **measured offset window**, and the rule for what a run
may read is stated once:

> **Every report captured strictly before the run's actual cutoff is admissible. The
> horizon is the bucket the measured hours-to-tip falls into.**

| Label | Eligibility window (hours before tipoff) | Nominal | What actually differs |
|---|---|---|---|
| `lock` | (0, 2] | T-60m | The final report, after late status changes |
| `gameday` | (2, 12] | T-6h | The initial report, updated at least once |
| `early` | (12, 48] | T-24h | The initial report — which usually **exists** — or nothing, if the run precedes the 5pm deadline |

The buckets partition (0, ∞) up to 48h, so a run lands in exactly one or in none;
`config.horizon_for_offset` returns `''` rather than clamping, because "this run is not
any of our named horizons" is a fact worth keeping.

**A label alone cannot support the comparison the horizons exist for.** Two runs both
tagged `early` can differ by twenty hours of report freshness, and the one that scores
better may simply have been made later. So every run stores the measurement alongside the
label (`config.HORIZON_RUN_METADATA`, written by `predict.horizon_metadata` into the
registry's per-run entry):

| Stored | Why it is needed |
|---|---|
| `hours_to_tip_min` / `_median` / `_max` | A **range**, not a point: one run scores a whole slate, and a 4pm game and a 10:30pm game are different horizons within it |
| `latest_report_at` | `captured_at` of the newest report the run used |
| `report_age_hours` | boundary − `latest_report_at`. The staleness number; a 3-day-old "out" and a 20-minute-old "out" are different claims |
| `report_count` | How many admissible reports existed at all. **Zero is the honest record of "the model stood alone", and it is not the same fact as "nobody was hurt"** |
| `first_deadline_passed` | Whether the boundary is after 5pm local on the day before the earliest game — i.e. whether the league's initial-report deadline had passed. **This is the flag that makes `early` interpretable**: an `early` run before the deadline genuinely has no report and one after it does, and the two must not be pooled |
| `horizon_measured` | The bucket the median offset assigns, recorded next to the label the operator requested, so a mislabelled run is visible in a diff. `predict.py` logs a warning when they disagree |
| `tip_source` | **Whether the offsets are measured or approximated.** `nba_schedule.scheduled_at` is NULL for a meaningful share of rows today, so `predict.py` falls back to `GAME_DATE + 00:00 UTC` and says so. Reporting a guessed tip time as measured would be worse than reporting nothing; **[GAP]** ingesting real tipoff timestamps is the fix |

The label is recorded in `prediction_runs.notes` as `horizon=<name> (<offset>)` and the
full metadata block in the registry's per-run entry. Notes rather than a new column:
migration 014 has none, the run row is append-only so a free-text label is as immutable as
a column would be, and a consumer that has never heard of horizons still reads the row.
These are labels, not schedulers — nothing enforces when a run executes.

## 8. Honest current standing

Strong: leakage discipline, selection-bias handling, availability modeling,
evaluation protocol, immutability/reproducibility, a composition whose parts
actually compose, a serving path that cannot project a player who is officially
out — and, as of feature_version v3, **teammate context that is a function of
information a live run actually has**, measured against both a no-context floor and a
perfect-information ceiling, with the gap between them reported rather than elided.

**The single most important sentence in this document:** the teammate-context win is
**−2.0% availability Brier and −0.8% minutes MAE**, not the −3.3% / −3.9% the previous
version reported. The difference is not a re-measurement; it is the removal of
cross-player leakage. **Roughly 21% of the v2 minutes gain and 65% of the v2
availability gain survive honest construction** (section 5.1).

Weak/known-untrue-yet, in rough order of how much it costs:

- **The teammate features are now honest and correspondingly modest.** The largest
  remaining question is not their semantics — that is fixed — but whether an
  *expectation over a roster's play probabilities* can ever recover the
  identity-specific signal the realized list carries. Section 5.1's 21% says: not with
  this construction. The 79% gap is an upper bound on what better availability
  modelling, or a lineup-level model, could buy.
- **`exp_vacated_minutes` does not beat a permutation of itself in the availability
  model (0.8×).** Section 5.2. The expected sums are largely smooth reweightings of
  own-player history the model already has; what survives is concentrated in
  `exp_depth_rank` and `exp_vacated_usg`, which are not.
- **Production rates are still a smoothed average and still context-blind**, and the
  bracket sharpened rather than softened this: with the oracle's extra minutes accuracy
  the `star_out = 1` cohort's unconditional points get *worse* (+0.13%). A rate that
  cannot respond converts a minutes gain into a points loss.
- **The composition drops a covariance term of unknown, cohort-varying sign** and its
  rate floor makes it a non-identity even in-sample (section 2). Neither is measured.
- Uncertainty bands are global offsets and not conditionally calibrated.
- 3 stats served of 9 promised.
- No lineup continuity, no minutes restrictions, no started-rate. Availability enters
  as a scalar probability of appearing at all, so a load-managed 8-minute night is
  "present" with a full magnitude.
- **Position coverage is 82% of rows AND its missingness is an era proxy** (32% null in
  2022-23, 2% in 2025-26). The positional features partly encode season, and the
  rolling-origin scheme trains early and validates late — the dangerous direction.
- `magnitude_ess` / `is_rookie` / `is_traded` measure as near-noise by gain (section
  5.2) and are retained for interpretability.
- **The complement identity is internal to our reconstruction** and certifies nothing
  about active-DNP coverage; an independent dated roster source is the P5 fix.
- **`player_injury_reports` holds 0 rows.** The override layer is implemented, tested
  and unexercised on real data; its five constants are hand-set and none is measured.
  The `AVAILABLE`-as-clearance case is not handled at all.
- **Tipoff timestamps are largely NULL**, so horizon offsets are approximated from
  `GAME_DATE` and every derived number says so.
- 10 neutral-site games (5 NBA Cup finals among them) are dropped by a NULL
  `home_team_id`; 152 rows, 0.10%.
- Offseason roster changes unmodeled; watchlist unvalidated; no genuinely untouched
  test window until the 2026-27 season is played — **whose scoring protocol is now
  frozen in section 13**, so the claims above have pre-registered ways to be wrong.

Questions we'd most value critique on: (1) how to make the production rate
context-aware without reintroducing whole-game totals — a per-minute rate
conditional on expected vacated usage, or usage as a separately predicted quantity;
(2) whether the 79% of the oracle minutes gain that does not survive is recoverable at
all from box-score-only inputs, or whether it is irreducibly identity-specific;
(3) whether minutes should be modeled as a distribution (zero-inflated / mixture)
rather than point + quantiles, which would also subsume the composition's covariance
term; (4) per-possession vs per-minute production rates with pace adjustment;
(5) conditional interval calibration with limited per-player data; (6) whether the
two-stage pipeline should iterate, and how to state the out-of-fold argument if it does.

## 9. External review responses

External review, answered point by point. Two findings were real serving defects
and were fixed on 2026-08-17 (P0). The top-priority feature gap was closed on the
same day (P1, feature_version v2). The rest are acknowledged as the next phases, in
priority order.

**Verified, no change needed**

- *"Is LightGBM bagging actually active? `subsample` alone is a no-op in LightGBM
  without `subsample_freq`."* Correct concern, and it is configured: `LGBM_PARAMS`
  carries `subsample: 0.8` **and** `subsample_freq: 1`, so bagging runs every
  iteration. Verified in `fnba_ml/config.py`; the value is recorded in every
  registry entry and every `metadata.json`, so any future run that drops it is
  visible in a diff.

**Fixed**

- *Minutes did not propagate into production.* The reviewed defect: the promoted
  composition was `P(plays) × EWMA(stat)`, in which a minutes forecast could not
  move a points or assists projection at all. Now
  `P(plays) × E[minutes | plays] × EWMA(stat per minute)`, for both the
  unconditional number and the conditional one shown on the player card. 3.955 MAE
  against 4.007 for the old form. Encoded as a regression test that doubles
  predicted minutes and requires both estimates to double. Two further correctness
  consequences shipped with it: the champion minutes model is now actually
  persisted and loaded (the config had named it champion while serving quietly used
  the demoted EWMA), and quantile offsets are refit against the estimator that
  ships rather than the one it replaced.
- *A player who is officially OUT was still projected.* Serving-time
  injury-report override layer, section 7.1. The model's own probability is
  preserved beside the overridden one so the layer is measurable and its hand-set
  constants are replaceable. Named forecast horizons (section 7.2) ship with it,
  because an override layer whose input is timestamped is meaningless if the run
  does not record when it ran.
- *Documentation contradicted itself on the holdout, and overstated uncertainty.*
  Section 6 now names four windows explicitly and states that the final months of
  2025-26 are selection-untouched but present in the deployment fit; the only
  untouched test is the prospective 2026-27 season. Section 5 states plainly that
  the bands are global residual offsets and a prototype — not conditional, not
  conformal, no coverage guarantee.
- *No teammate context — "the largest expected win", and the reviewers' #1 item.*
  Shipped as feature_version **v2** and **then rebuilt as v3 after round 2 found the v2
  construction leaky**. The v2 answer is retained below in strikethrough form because
  the correction is the more useful record.

  **What v2 claimed:** availability Brier −3.3%, minutes MAE −3.9%, unconditional points
  MAE −1.1% aggregate; −7.6% / −10.3% / −5.3% on the fringe tier;
  `depth_rank_available` the #2 minutes feature carrying 18.4% of gain.

  **What those numbers are:** an **oracle bracket result** — the value of perfect
  pre-tipoff lineup information, not the value of a forecast. Round 2 found that a v2
  feature on player *i* is a function of player *j*'s target-game label, which
  self-exclusion does not close. See the header, section 4.1, and section 11.

  **What v3 measures, and what this document now claims:** availability Brier **−2.0%**,
  minutes MAE **−0.8%**, unconditional points MAE **−0.3%** aggregate; **65% / 21% / 29%**
  of the corresponding oracle deltas survive. `exp_depth_rank` is the #4 minutes feature
  carrying **4.2%** of gain, and an ablation puts its actual value at **0.18%** of
  minutes MAE. Section 5.1 is the full bracket; section 5.2 is the importance and the
  ablation.

  Four things the reviews asked for that were done as asked and remain done in v3:
  absence conditioned on *knowable* rather than hindsight information (now a
  probability rather than a list); `exp_vacated_usg` and `p_star_out` included
  specifically because the beneficiary of a creator's absence is often an
  already-starting player whose *usage* jumps and whose minutes do not; positional
  overlap included; and the family held to eight columns, with no per-teammate
  one-hots and no lineup combinatorics.

  One thing the reviews did not ask for and that is worth reporting: the "no-event"
  control cohort is reported next to the event cohorts in every pass, because a family
  that helps on absence nights by hurting quiet ones has not helped. Under v3 no cohort
  regressed.

- *"Trained production models should start beating EWMA once context features
  exist."* **This is now answered differently than in the v2 write-up, and the
  difference is instructive.** v2 reported ridge's edge on conditional points moving
  0.80% → 1.83% and called it "a real move in the predicted direction". On the honest v3
  dataset it reads **0.81%** — back where v1 was — and clears the >2% bar in **0 of 5**
  origins rather than 2 of 5. So the prediction is right about *oracle* context features
  and unsupported for honest ones. **No champion was promoted at either version.** The
  bar was set before both results and was not moved to accommodate either.

**Acknowledged, next phases in priority order** (re-ordered after round 2)

1. **Make the production rate context-aware.** Still the top item, and the bracket
   sharpened the argument rather than weakening it: with the *oracle's* extra minutes
   accuracy the `star_out = 1` cohort's unconditional points get **worse** (+0.13%),
   while the honest set's slightly improve (−0.21%). A rate that cannot respond to a
   creator's absence turns a minutes gain into a points loss. Candidates: a per-minute
   rate conditional on expected vacated usage, or usage as its own predicted quantity
   feeding a rate model. **Not** "accept a trained conditional-production model", which
   the v2 write-up floated on the strength of a 1.8% edge that has since evaporated.
2. **Close the 79% gap, or establish that it cannot be closed.** New, and it is the
   direct consequence of section 5.1: 21% of the oracle minutes gain survives. Either
   better availability modelling recovers more of it, or the residual is
   identity-specific and needs a lineup-level model rather than a roster aggregate.
   That is a question worth answering before building anything else in this area,
   because the answer determines whether the team-allocation simulator (currently out of
   scope) is the next step or a dead end.
3. **~~Quantify the game-day/early-morning semantics gap~~ — superseded.** This item
   asked for a season of timestamped report history to measure how optimistic the v2
   features were at T-24h. That framing was wrong: the problem was construction, not
   timing, and v3 removes it outright. What remains from the item is narrower and still
   worth doing — measure whether the *override layer* is worth more at `lock` than at
   `early` — and it still needs report history (`player_injury_reports` currently holds
   0 rows).
4. **Vegas lines, start rate, and opponent profile.** Team total and spread as
   pace/blowout proxies; started-rate as the cleanest remaining role signal, and now a
   more attractive one than it looked under v2: `exp_depth_rank` measures role
   indirectly and is worth only 0.18% of minutes MAE, so a direct role signal has more
   room than the v2 importance table implied. Opponent profile beyond a single
   points-allowed rolling mean (pace, positional defense).
5. **A dated position / roster source.** Promoted from a footnote because it now fixes
   two separate problems at once: the era proxy in section 3.2 and the active-DNP
   coverage question in section 4.1 (the P5 item). One ingestion job.
6. **Lineup continuity and minutes restrictions.** Availability is a scalar probability
   of appearing at all; a load-managed 8-minute night is "present" with a full
   magnitude.
7. **Conditional quantiles, then group conformal.** Section 5's planned upgrade,
   deliberately still after the feature work — and now with a second motivation: a
   minutes *distribution* is the prerequisite for the composition's planned integral
   form (section 2), which would subsume the dropped covariance term.
8. **Evaluation hardening.** Per-segment interval coverage rather than a pooled
   number; measuring the override layer against the model it corrects once report
   history is deep enough; retrospective watchlist precision against realized
   breakouts; a prospective-test protocol fixed in advance of the 2026-27 season so
   it cannot be chosen after seeing the results.

## 10. Round-2 review request (as submitted — kept for the record)

**This section is the request that went out with the v2 document. It is retained
unedited so the round-2 answers in section 11 can be read against what was asked.
Every number in 10.1 is an oracle-bracket number** — see section 11 and the header.

This section frames what we want critiqued now. Round 1's structural findings
were adopted (section 9); please do not re-litigate acknowledged [PLANNED]
items unless the plan itself is wrong. Argue with the evidence below.

### 10.1 What changed since round 1, measured — ⚠ SUPERSEDED

⚠ **These are v2 (oracle) numbers. Their honest counterparts are in section 5.1.**

Same dataset, same 5 rolling origins, identical rows; v1 → v2 features:

| Metric | cohort | v1 | v2 | delta | **honest (v3) delta** |
|---|---|---:|---:|---:|---:|
| Availability Brier | all | 0.0734 | 0.0710 | −3.3% | **−2.0%** |
| | fringe (<10 mpg) | 0.1075 | 0.0993 | −7.6% | **−5.0%** |
| Minutes MAE | all | 4.72 | 4.54 | −3.9% | **−0.8%** |
| | fringe | 5.05 | 4.53 | −10.3% | **−1.0%** |
| | bench (10-20) | 5.53 | 5.21 | −5.8% | **−0.7%** |
| | stars (>=30) | 3.83 | 3.81 | −0.4% | **−1.5%** |
| PTS uncond. MAE | all | 3.95 | 3.91 | −1.1% | **−0.3%** |
| | fringe | 1.45 | 1.37 | −5.3% | **−0.7%** |
| Control: vacated<5 games | no metric regressed | | | | still none |

Negative control: a permuted copy of vacated_minutes carries 3–4× less gain
and zero cohort lift (pinned as tests). `depth_rank_available` is the #2
minutes feature (18.4% of gain). `star_out` measured as noise once
`vacated_usg` existed and is documented as redundant. No champion promotion:
ridge's conditional-PTS edge grew 0.8% → 1.8% but cleared the 2% bar in only
2 of 5 origins; the bar was not moved.

⚠ **All four of those claims changed under v3.** The permuted twin of the served
`exp_vacated_minutes` carries *more* gain than the real column in the availability model
(0.8×); `exp_depth_rank` is #4 at 4.2% of gain and worth 0.18% of MAE by ablation;
ridge's edge is back to 0.81% and clears the bar in 0 of 5. Section 5.2.

### 10.2 Questions for this round (in priority order)

1. **Promotion discipline vs evidence accumulation.** Ridge sits at a 1.8%
   mean edge, >2% in 2/5 origins, direction consistent in 5/5. Our rule says
   don't promote. Is a fixed per-decision threshold the right rule when
   evidence accumulates across rounds, or should we adopt something like a
   sequential test / block-bootstrapped interval on the paired delta before
   the next promotion decision? Concretely: what promotion protocol would you
   pre-register?
2. **Single-feature dominance.** `depth_rank_available` carries 18.4% of the
   minutes model's gain, and its input (the availability set) comes from the
   same inactive lists that define the target's own played label. We believe
   self-exclusion + as-of magnitudes close the leakage paths (tests pin
   both); what failure modes remain? Distribution shift when rosters churn in
   October? Degradation when position coverage is null (18% of rows)?
3. **Oracle-optimism quantification.** Our teammate features use final
   inactive lists (game-day semantics). Before timestamped report history
   exists, is there any defensible way to bound the optimism vs a T-24h
   forecast — e.g., restricting the absence set to players also absent in the
   previous game (a "knowable yesterday" proxy) and re-running the ladder?
   Critique that proxy or propose a better one.
4. **Opponent profile under box-score-only constraints.** We have team game
   logs (pace-computable), no tracking data, no shot-zone data at ingestion
   reliability we trust. Rank the opponent features worth building from box
   scores alone: possessions-per-48 pace, per-possession defensive rating,
   defensive rebound rate, FT rate allowed, 3PA rate allowed. What's the
   expected ceiling without shot-location data?
5. **Historical Vegas.** Forward-only odds snapshotting starts now (no
   historical archive in our sources). Is buying/scraping a historical odds
   dataset worth licensing/reliability risk for ~2% expected minutes-tail
   gain, or is forward accumulation + a blowout-proxy from final margins
   (trained, deliberately leaky-labeled, used only to learn the minutes-
   truncation *shape*) an acceptable bridge? Critique the bridge idea hard.
6. **Conditional quantile design (before we build it).** Planned: LightGBM
   quantile objectives for minutes/PTS P10/50/90, split-conformal on a
   rolling window, group-conformal by minutes tier + status cohort,
   non-crossing by rearrangement. Known tension: tier is itself a prediction
   input. Pre-register the coverage report format and name the failure modes
   you'd test first.
7. **October cold-start, imminently.** 2026-27 opens with (a) rookies with
   zero rows, (b) traded players whose depth_rank/vacated features reference
   new teammates with fresh aggregates, (c) 82%→lower position coverage until
   rosters stabilize. Which of our features actively mislead in week 1, and
   what guardrail (wider intervals? feature gating below n appearances?)
   would you ship before opening night?
8. **Pre-registered prospective protocol.** Draft the 2026-27 scoring
   protocol we should freeze NOW: metrics (incl. rank-based decision metrics),
   cohorts, horizons, minimum sample before any conclusion, and what result
   would falsify the system's core claims. We will commit to it in the repo.

### 10.3 Out of scope this round

Team-allocation simulator timing (we hold: compact features first, simulator
when they plateau), neural models, tracking-data features, defender-matchup
micro-data, referee/narrative features.

## 11. Round-2 review responses (phase P1b, 2026-08-17)

The round-2 review found one substantive flaw and several documentation defects. The
flaw was real, it invalidated the previous version's headline as a forecasting claim,
and closing it is what feature_version v3 is.

**The finding — accepted in full, no hedging**

- *"The v2 teammate-context features use realized teammate outcomes, so a player's
  features depend on other players' target-game labels. Self-exclusion does not close
  that. The v2 gains are honest only as a value-of-perfect-lineup-information result."*

  **Correct on every point.** We had documented the absence set as "game-day / oracle
  semantics" and framed the consequence as a *horizon* problem — "optimistic for
  `early`, close to honest for `gameday`, honest for `lock`". That framing was wrong in
  kind. The training rows were featurised from *their* final inactive lists, so the
  estimator learned a mapping from resources that were in fact vacated; no serving
  horizon has that input, `lock` included. It was a construction defect, not a timing
  caveat, and describing it as the latter made it sound smaller than it was.

  **What shipped:** every realized indicator `1(j absent)` replaced by `(1 − p_j)` for a
  strictly out-of-fold, teammate-context-free `p_j` (sections 4.1, 4.3); a two-stage
  pipeline with a base availability model, a forward-chaining calendar-block cross-fit,
  and the existing out-of-fold guard extended to the probability that *builds features*
  rather than only to the ones that get multiplied; the v2 columns retained as the
  bracket's oracle end and removed from `FEATURE_COLS`; the serving path rewired so the
  injury report corrects `p_j` *before* the teammate sums are taken.

  **What it cost, measured on identical rows (section 5.1):** availability Brier
  −3.05% → **−1.98%** (65% survived); minutes MAE −3.94% → **−0.81%** (21% survived);
  unconditional points −1.10% → **−0.32%** (29% survived). On the fringe minutes tier,
  where v2's marquee −10.3% lived, **9% survived**. Section 5.3 puts the served
  construction at roughly the value of a report with **20% absence recall**.

- *"Keep the oracle as a named comparator."* Done — `config.FEATURE_SETS` names three
  sets and `evaluate.py --bracket` runs the ladder over all three on identical rows,
  reporting a `survived` fraction per cohort. `tests/test_teammates_v3.py` pins the
  invariance test to PASS on the served family and to **FAIL** on the oracle family, so
  the upper bound cannot quietly stop being an upper bound.

- *"Run the Level-C degraded-oracle grid if cheap."* Done in 4.7 minutes; section 5.3.
  It produced the most actionable finding of the phase: **false positives are more
  expensive than missed absences** (10% FP at full recall costs more than the entire
  40%→100% recall range), which nothing in the previous evaluation would have surfaced
  and which should govern how any future report feed is filtered.

**Documentation and code defects — all four confirmed against the code before changing
anything**

- *"The PROBABLE rule's `max` is redundant: `0.85p + 0.15 ≥ p` on [0,1]."* **Confirmed
  and it never fired once.** The docstring's justification ("above ~0.99 the shifted form
  dips below the model's own number") was false. Code simplified to the shifted form,
  policy table corrected, and the general condition stated (`w·p + s` is a floor iff
  `s ≥ 1 − w`; the defaults satisfy it with equality, so any change must re-check).
  Section 7.1.

- *"`AVAILABLE` is not informationally null."* **Confirmed**, and logged as future work
  rather than guessed at: a player listed `available` was *cleared* from an earlier
  designation, which is information the model cannot have. The honest rule needs the
  report *sequence*, not the latest row, and `player_injury_reports` holds 0 rows.
  Section 7.1.

- *"The composition section asserts an identity it does not have."* **Confirmed.**
  Section 2 is rewritten in estimator notation, states the dropped covariance term and
  argues its sign is not even constant across cohorts, states the rate floor's
  non-identity with the 2-minute-3-pointer worked through, and cites the suggested
  joint-integral form as **[PLANNED]** with its dependency on a minutes *distribution*.

- *"147,413 vs 147,565 and 9,840 vs 9,830 appear in different sections."* **Both real,
  one cause, now reconciled with a table:** 10 games have a NULL `home_team_id`
  (neutral-site: NBA Cup finals and international openers), so the universe builder drops
  152 rows across 10 team-games. 147,565 is the truth-layer count, 147,413 the modelled
  one. Section 3.1 — including the note that the first SQL attempt to find this returned
  nothing because `NOT IN` is never TRUE against a NULL.

- *"Redefine the horizons; T-24h usually HAS a report."* **Confirmed — the `early` row
  said "no injury report yet" and that was simply false** given the league's 5pm-day-before
  initial-report deadline. Horizons are now measured-offset windows, eligibility is
  "every report captured strictly before the actual cutoff", and eight per-run facts are
  stored (offset range, latest report timestamp and age, report count,
  `first_deadline_passed`, measured-vs-requested bucket, and whether the offsets are
  measured or approximated). Section 7.2.

- *"The complement claim cannot certify active-DNP coverage."* **Confirmed.** Reworded to
  "complements **within the reconstructed universe**", with the mechanism spelled out
  (roster membership is itself reconstructed from the list plus appearances, so the
  identity is definitional) and an independent dated roster source logged as the **P5**
  fix. Section 4.1.

- *"Check whether position missingness encodes era."* **It does, strongly and
  monotonically:** 32.2% / 26.0% / 13.0% / 2.0% null across the four seasons, because
  `players` holds the *currently* tracked roster and attrition is monotone in time. Since
  the rolling-origin scheme trains early and validates late, the direction is the
  dangerous one. Flagged in section 3.2 with a `position_known` indicator as an interim
  **[PLANNED]** guardrail and the dated roster source as the real fix.

**Also addressed, from the round-2 question list**

- **10.2 Q2 (single-feature dominance).** The 18.4% figure is gone: `exp_depth_rank` is
  4.2% of gain and, by ablation, worth 0.18% of minutes MAE. Section 5.2 draws the general
  conclusion — **split gain is an allocation statement, not a value statement** — and
  every importance claim in this document should now be read that way.
- **10.2 Q3 (oracle-optimism quantification).** Answered better than the "absent in the
  previous game" proxy the question proposed: the bracket measures the optimism directly
  and the Level-C grid maps the interior. The proxy is no longer needed.
- **10.2 Q7 (October cold-start).** Partly shipped rather than only answered: the
  magnitudes are shrunk career-scoped rolling windows (so a season boundary no longer
  discards the only usable history), and six reliability features tell the model when the
  evidence is thin. `games_with_current_team` measures as the #9 availability feature.
  The guardrail question — feature gating below *n* appearances — remains open, and the
  ablation's per-cohort signs (section 5.2) suggest gating by *tier* rather than by *n*.

**Deliberately not changed**

- **The promotion bar stayed at >2% consistently across origins.** Ridge's conditional-PTS
  edge fell from 1.83% to 0.81% when the oracle features were removed, so the bar was not
  tested this round — but it is worth recording that it would have been the right call
  either way, and that the v2 write-up's "climbing toward the bar" reading was an artifact.
- **One iteration of the two-stage pipeline**, not a fixed point. Stated as a choice with
  its reason (section 4.3) rather than left implicit.
- **The bracket is not a one-variable contrast**: v3-honest carries the reliability block
  and v2-oracle does not. Stated where the tables are, because three cohorts show
  v3-honest *beating* the oracle set and that is why.

---

## 12. The 9-category extension (2026-08-18, artifact `20260818`)

**What changed:** the system now serves all nine fantasy categories instead of two.
`RATE_TARGETS` went from `(PTS, AST)` to the full vocabulary — `PTS REB AST STL BLK
TOV FG3M FGM FGA FTM FTA` — and every one of them travels through the same
composition, the same out-of-fold guards and the same store schema the two
incumbents already used.

**`feature_version` stays `v3`, and that is a claim rather than an omission.** Not one
value in `FEATURE_COLS` changed. The new stats are *outcomes* and *rate targets*; none
of them is in `ROLL_STATS`, `UNCOND_STATS`, any teammate magnitude, or the feature
list. The availability and minutes models are fitted against a byte-identical feature
frame, so every artifact fitted under v3 remains valid and a bump would have
invalidated them for nothing.

### 12.1 The one missing column, and why the dataset was widened rather than rebuilt

The four-season dataset carried ten of the eleven box columns already. The exception
was **FGM**, and it is the one that matters most: FG% is derived by consumers as
FGM/FGA, so without it the entire shooting-efficiency half of a 9-cat league is
unservable. It also cannot be reconstructed from what was present — `PTS = 2·FGM +
FG3M + FTM` is an identity only when no free throw was an and-one technical and no
line was scrubbed, and inverting it would manufacture a makes column out of rounding
error.

The obvious move is `build_dataset.py --source postgres`. It was not available:
`player_game_logs` carries FGM for all four seasons, but `player_game_status` — the
table the *universe* is built from — covers only the last two. A full rebuild returns
**74,718 rows where the shipped dataset has 147,413**, and the 72,695 rows it drops are
two entire seasons of training history. Losing half the history to gain one column is
not a trade worth making, and doing it silently would be worse.

`backfill_dataset.py` is the alternative, and its guarantee is stronger than a
rebuild's would have been:

- every pre-existing column is **passed through untouched** — not recomputed and then
  compared, never recomputed at all, so parity is a property of the code path rather
  than a result. It is checked anyway (`--verify`): **all 99 original columns
  byte-identical, 147,413 rows preserved.**
- FGM is joined on `(PLAYER_ID, GAME_ID, TEAM_ID)` with 0.0 for scheduled rows with no
  log line — byte-for-byte the rule `universe._attach_outcomes` applies to every other
  stat column. **100.0000% of appearance rows matched a log line**; mean 3.92 makes.
- the rate columns are rebuilt by `features.per_minute_rate_features` plus the same
  `allow_exact_matches=False` as-of join `build_features` uses — the same two
  functions in the same order, not a reimplementation. The pre-existing
  `ewma_PTS_per_min` / `ewma_AST_per_min` were dropped and recomputed, and reproduced
  the shipped columns exactly, which is a second parity check on the rate
  construction itself.

Independent correctness check on the joined column: **`PTS = 2·FGM + FG3M + FTM` holds
exactly on 105,142 of 105,142 appearance rows**, max residual 0.00. The truth data also
satisfies `FGM ≤ FGA`, `FG3M ≤ FGM` and `FTM ≤ FTA` on 100% of rows, which is what
makes section 12.5 a statement about *estimates* rather than about data quality.

### 12.2 What is served, and what is deliberately not

**Percentages are never predicted.** FG% and FT% are ratios of two random variables and
`E[FGM/FGA] ≠ E[FGM]/E[FGA]`. More practically: a fantasy league scores a manager's
*weekly aggregate* percentage, `sum(FGM)/sum(FGA)` over every player-game he rostered.
That is a function of the makes and attempts *expectations*, so shipping the primitives
lets a consumer aggregate correctly and shipping a per-game percentage would force it
to aggregate wrongly. The package's job ends at the primitives.

**TOV is not sign-flipped.** Turnovers are a category a manager wants to lose, which is
a *scoring* fact the backend's `zScoreRank` already owns. An honest expectation of 2.4
turnovers is the same kind of object as an honest expectation of 2.4 rebounds;
inverting it here would mean every consumer had to know which of the nine columns had
been pre-negated.

**No trained model is in the rate bracket at all.** The production tournament
(`ml/experiments/production_tournament/TOURNAMENT.md`) had just spent a full
pre-registered pass establishing that trained rate models do not pay for PTS and AST,
and there is no reason to expect a *scarcer* stat to behave better. The bracket is
therefore two members — an expanding-mean baseline and an EWMA — plus the
pre-composition whole-game estimator as a third reference.

### 12.3 Per-stat halflife: the decision PTS and AST never had to make

Until now every rate used one halflife of 5. That was fine while "every rate" meant
points and assists, both of which had been *selected* at 5. A block, a steal and a
field-goal attempt are not observed with remotely the same signal-to-noise, and forcing
one memory length on all of them is a choice nobody made on purpose.

**The prediction, recorded before the numbers.** A halflife is a bet about how fast the
smoothed quantity moves relative to how noisily it is observed. A rare event seen a
handful of times a game (STL, BLK, FG3M) has enormous per-game sampling noise around a
fairly stable underlying rate and should want a **long** memory. A volume stat tied to
role moves when the role moves and should want a shorter one.

**The protocol.** Two 28-day folds carved off the *end* of each origin's own
**training** window, forward-chained: fold *k*'s training rows are everything strictly
before fold *k*'s start. The minutes model that supplies the common factor is fitted on
appearances strictly before the fold and carries the fold start as its cutoff; the
rates come from an as-of join that refuses exact matches. **No origin's validation rows
are read by anything in the selection.** Selecting on the rows 12.4 reports would make
every MAE there an in-sample number wearing an out-of-sample label.

Because the minutes prediction is a *common factor* across the whole grid, two
halflives' conditional estimates differ by exactly the ratio of their rates — so any
MAE difference is attributable to the memory length and to nothing else.

**The rule, pre-registered in `config.RATE_HALFLIVES` before the sweep ran.** The
pooled best halflife ships only if it beats halflife 5 by more than **0.5%** relative
MAE *and* is the per-origin winner in at least **3 of 5** origins. Otherwise the stat
keeps 5 and is marked `ambiguous`. The expanding-mean baseline replaces the selected
EWMA only if it clears the same two bars against it — it is a different estimator
family, not a sixth halflife, so it gets its own comparison rather than being thrown
into the same argmin. PTS and AST are **frozen** by the tournament's verdict and cannot
move whatever the folds say; `rate_halflife_winners` enforces that in code, and
`tests/test_rate_targets.py` pins it with evidence strong enough to move an unfrozen
stat.

The 0.5% bar is set by *coherence*, not by statistics — see 12.5.

| Stat | Ships | Best on grid | Inner-fold gain vs h5 | Origins won | Verdict |
|---|---|---|---:|---:|---|
| PTS | **h5**, ewma | h12 | +0.69% | 2/5 | FROZEN by the tournament |
| AST | **h5**, ewma | h12 | +0.55% | 1/5 | FROZEN by the tournament |
| REB | **h20**, ewma | h20 | +1.02% | **5/5** | selected |
| STL | **expanding** | h20 (+1.78%), then beaten by expanding (+0.58%) | — | **5/5** | selected |
| BLK | **h5**, ewma | h12 | +0.30% | 1/5 | **ambiguous → default** |
| TOV | **h20**, ewma | h20 | +1.84% | **5/5** | selected |
| FG3M | **h20**, ewma | h20 | +1.13% | **5/5** | selected |
| FGM | **h5**, ewma | h20 | +0.92% | 2/5 | **ambiguous → default** |
| FGA | **h5**, ewma | h8 | +0.26% | 3/5 | **ambiguous → default** |
| FTM | **h12**, ewma | h12 | +0.59% | 4/5 | selected |
| FTA | **h12**, ewma | h12 | +0.61% | 4/5 | selected |

Per-origin inner-fold winner — the consistency half of the rule, and the only guard
against a pooled mean being moved by one unusual month:

| Stat | O1 | O2 | O3 | O4 | O5 |
|---|---|---|---|---|---|
| REB | h20 | h20 | h20 | h20 | h20 |
| TOV | h20 | h20 | h20 | h20 | h20 |
| FG3M | h20 | h20 | h20 | h20 | h20 |
| STL | exp | exp | exp | exp | exp |
| FTM | h12 | h12 | h12 | h8 | h12 |
| FTA | h20 | h12 | h12 | h12 | h12 |
| FGM | h20 | h12 | h12 | h20 | h12 |
| FGA | h8 | h8 | h12 | h12 | h8 |
| BLK | exp | exp | h12 | h8 | h8 |
| PTS | h20 | h12 | h20 | h12 | h8 |
| AST | h20 | h12 | h20 | h8 | h8 |

**The prediction held for three stats and was taken to its limit by a fourth.** REB,
TOV and FG3M chose halflife 20 unanimously. Steals went further: 20 was the best *grid*
value in 5 of 5 origins, and then the memoryless career expanding mean beat even that.
That is not an embarrassment for the EWMA family — it is the same story's limiting
case. A steal rate is close to constant over a career and almost all of a single game's
steal count is noise, so the estimator that throws away the least history wins. **STL
is the one stat in the vocabulary that ships the baseline rather than the incumbent
family**, and it is recorded that way in `config.RATE_ESTIMATORS` rather than being
quietly rounded to "halflife 20, near enough".

**The prediction did not hold for BLK, which is the honest surprise.** Blocks are the
rarest event in the vocabulary and the argument above predicts a long memory for them.
The inner folds do not show it: the nominal best was h12, it won 1 origin of 5, and it
bought 0.30%. BLK falls through to the default. The prediction is recorded as made and
as **not confirmed** rather than quietly dropped.

FGM and FGA are ambiguous in the ordinary way — nominal winners longer than 5 that
failed one bar each. Presenting the smallest of six numbers that are all within noise
of each other as a finding is exactly what the rule exists to prevent.

**Corroboration worth noting.** These folds independently reproduce the production
tournament's own finding for the two frozen stats: h12 is the nominal best for both
PTS and AST, by 0.69% and 0.55%, which is well short of the tournament's pre-registered
2% floor. The freeze and the evidence agree; the freeze would have held either way.

### 12.4 The ladder: did each stat's estimator earn its place

Five origins, identical rows, **one shared availability model and one shared minutes
model** — the only thing that differs between members is the per-minute production
estimate, so an MAE difference is attributable to the estimator and to nothing else.
Three members:

- `expanding` — `E[MIN|play] ×` career expanding mean of stat/minute. The baseline,
  with no memory parameter to tune.
- `ewma_total` — `EWMA(halflife 5)` of the whole-game total, **with no minutes term at
  all**. What this package served for PTS before the composition change, so it measures
  whether minutes propagation earns its place *for each stat* rather than inheriting
  the PTS result.
- `champion` — `E[MIN|play] ×` the selected estimator at the selected halflife.

Conditional MAE (appearances), mean over 5 origins. Positive `vs` = the champion is
better:

| Stat | champion | expanding | ewma_total | vs expanding | vs ewma_total |
|---|---:|---:|---:|---:|---:|
| PTS | **4.5110** | 4.5565 | 4.5484 | +1.00% | +0.82% |
| AST | **1.3168** | 1.3331 | 1.3269 | +1.22% | +0.76% |
| REB | **1.8960** | 1.8827 | 1.9074 | −0.70% | +0.60% |
| STL | **0.7077** | 0.7077 | 0.7275 | 0.00% | +2.73% |
| BLK | **0.5076** | 0.5103 | 0.5105 | +0.53% | +0.57% |
| TOV | **0.8960** | 0.8861 | 0.8940 | −1.12% | −0.23% |
| FG3M | **0.8823** | 0.8744 | 0.8867 | −0.90% | +0.50% |
| FGM | **1.7296** | 1.7422 | 1.7430 | +0.72% | +0.77% |
| FGA | **2.6959** | 2.7721 | 2.7314 | +2.75% | +1.30% |
| FTM | **1.2942** | 1.3048 | 1.3026 | +0.81% | +0.64% |
| FTA | **1.5702** | 1.5856 | 1.5784 | +0.97% | +0.52% |

Unconditional MAE (every scheduled row, `P(play) ×` the conditional estimate):

| Stat | champion | expanding | ewma_total | vs expanding | vs ewma_total |
|---|---:|---:|---:|---:|---:|
| PTS | **3.9416** | 3.9817 | 3.9995 | +1.01% | +1.45% |
| AST | **1.0960** | 1.1081 | 1.1094 | +1.09% | +1.20% |
| REB | **1.6255** | 1.6201 | 1.6464 | −0.33% | +1.27% |
| STL | **0.5415** | 0.5415 | 0.5583 | 0.00% | +3.00% |
| BLK | **0.3819** | 0.3846 | 0.3853 | +0.70% | +0.88% |
| TOV | **0.7173** | 0.7093 | 0.7188 | −1.14% | +0.20% |
| FG3M | **0.6951** | 0.6888 | 0.7030 | −0.92% | +1.13% |
| FGM | **1.4933** | 1.5058 | 1.5144 | +0.83% | +1.39% |
| FGA | **2.5299** | 2.5851 | 2.5756 | +2.14% | +1.78% |
| FTM | **1.0183** | 1.0250 | 1.0283 | +0.66% | +0.98% |
| FTA | **1.2454** | 1.2558 | 1.2576 | +0.83% | +0.97% |

**Every stat beats the pre-composition estimator unconditionally**, from +0.20% (TOV)
to +3.00% (STL). `evaluate.rate_composition_parity` runs the section-5 parity check
**once per served stat** — the correctness argument for minutes propagation is
stat-agnostic, so the accuracy bar is cleared stat-by-stat rather than cleared once for
points and assumed for blocks — and **all 11 pass, all in the favourable direction**. A
failure on any one of them makes `evaluate.py` exit non-zero.

Internal consistency worth stating: the PTS row of the unconditional table reads
**3.9416**, identical to `decomposed_p_x_minutes_x_ppm` in the section-5 table. The rate
ladder reuses the same scored frame rather than refitting a lookalike composition, so
the two numbers are the same number and not two numbers that happen to agree.

**The honest disagreement.** For REB, TOV and FG3M the inner folds preferred halflife 20
over the expanding mean by 0.4–0.5%, and the *validation* rows reverse that by
0.3–1.1%. Both differences are inside the package's ~2% noise line, in both directions,
and the selection stands: switching the champion to whatever wins the column being
reported would turn this table into a selection surface rather than a report of one. It
is recorded because a selection protocol that only ever agrees with its own
out-of-sample check is a protocol nobody is reading carefully. TOV's conditional
`vs_ewma_total` of −0.23% is the same phenomenon at the other end: minutes propagation
is very slightly negative for turnovers conditionally and positive unconditionally,
which is what a difference of that size looks like when it is noise.

### 12.5 Coherence, and what per-stat halflives cost

A made shot is an attempted shot and a made three is a made shot, so `FG3M ≤ FGM ≤ FGA`
and `FTM ≤ FTA` hold in every game ever played — and hold on 100% of this dataset's
rows. They do **not** hold automatically for the *expectations*.

The argument, because it is the whole reason the clip exists. Two EWMAs **at the same
halflife** are the same weighted average of the same rows, and a weighted mean is
monotone, so `FGM ≤ FGA` survives the averaging. Two EWMAs at **different** halflives
are different weighted averages, and it does not: a player who never scored on one
attempt a night and has lately been taking and making ten has a short-memory *makes*
estimate above his long-memory *attempts* estimate, though he has never in his life
made more shots than he took. The league-rate fallbacks are a second, smaller source —
a player with attempts history and no makes history takes his two numbers from two
different places.

So **per-stat halflife selection has a coherence price**, and that is what sets the
0.5% selection bar: a halflife buying under half a percent of MAE has not paid for the
clipping it causes.

`predict.py` clips the bounded stat **down** to its bound on every emitted row, in the
order `config.COHERENCE_CONSTRAINTS` lists — `FGM ≤ FGA` first, then `FG3M ≤` the
*already-corrected* `FGM`, so the chain settles in one pass — and does it once per
emitted template: the conditional estimate, the unconditional estimate, and **each
quantile level independently**. Clipping levels pairwise is a per-level coherence
statement and explicitly not a claim about the joint distribution: it says the P90 of
FGM does not exceed the P90 of FGA, which is the property a rendered player card needs,
and says nothing about `P(FGM > FGA)` on any single night. Clipping *down* rather than
raising the bound is deliberate — attempts are the higher-volume, lower-variance member
of each pair and therefore the better estimated, so when the two disagree the makes
estimate is the one that is wrong. A missing stat on either side is skipped rather than
treated as zero: "we did not project attempts" is not a licence to clip makes to
nothing.

**Measured clip frequency, over 30,917 validation rows across 5 origins:**

| Constraint | conditional | unconditional |
|---|---:|---:|
| `FGM ≤ FGA` | **0.0000%** | **0.0000%** |
| `FG3M ≤ FGM` | **0.0000%** | **0.0000%** |
| `FTM ≤ FTA` | **0.0000%** | **0.0000%** |

Zero, and the zeros mean two different things. `FGM ≤ FGA` (both h5) and `FTM ≤ FTA`
(both h12) are zero **by the argument above** — same halflife, monotone, cannot cross —
so these two are a check that the reasoning and the code agree, and they do. `FG3M ≤
FGM` is the one pair with genuinely different halflives (20 vs 5), and its zero is
**empirical**: the gap between ~1.2 expected made threes and ~3.9 expected made field
goals is much larger than any memory-length difference moves either estimate.

**The clip is therefore a guarantee at these selections, not a correction.** It stays,
for two reasons. The empirical zero is a property of the current halflife assignment
rather than of the arithmetic, so a future selection that puts FGM and FGA on different
memories would start binding silently. And the count is reported by the evaluation, so
that day shows up in a diff rather than in a user's browser.

### 12.6 What ships

**Artifact `20260818`**, feature_version `v3`. `metadata.json` gained a per-stat
decision record — `production.rate_halflives`, `production.rate_estimators`,
`production.rate_targets` and `production.coherence_constraints` — so a stored
prediction can be traced to the estimator that produced it *even after `config` moves
on*. `predict.py` reads the halflife and estimator out of the **artifact** rather than
out of `config` for exactly that reason: a version trained when a stat used one
halflife must keep scoring with that halflife, or the stored prediction and the model
that made it stop being the same object.

`ewma_state.parquet` snapshots **both** rate families for all eleven stats, not only
the promoted one, so re-reading the losing estimator later does not require replaying
four seasons of appearance history.

**Quantiles widened** from `(MIN, PTS)` to minutes plus all eleven stats, by the same
holdout-residual mechanism and measured against each stat's own shipped estimator. A
rare-event stat is where an interval matters most: "1.1 blocks" and "1.1 blocks,
anywhere from 0 to 3" are very different inputs to a start/sit decision.

**Store rows went from 14 to 62 per scheduled player-game** (plus 2 for an overridden
row) — `prob_active`, `prob_active_model`, and for each of twelve stats a conditional
value, a `<stat>_uncond` value and P10/P50/P90. **This required no migration.** Every
stat name emitted was already reserved in migration 014's own comment, which is
long-format precisely so the vocabulary can grow without a schema change. Because that
comment is the *only* thing constraining `stat` — the column has no `CHECK`, and a
typo'd name would insert cleanly and be invisible to every consumer —
`tests/test_rate_targets.py` parses the reserved list out of the `.sql` file and asserts
every emitted name is inside it, rather than restating the list in Python where the two
could drift.

### 12.7 Gaps this phase did not close

- **[GAP] The rare-event stats are still modelled as scaled minutes.** `E[BLK] =
  P(play) × E[MIN] × EWMA(BLK/min)` treats a block rate as a per-minute constant. For a
  stat averaging 0.46 events per game, the per-minute framing is doing more work than
  the evidence clearly supports, and a count model (Poisson or negative-binomial on the
  appearance rows) is the obvious challenger nobody has run.
- **[GAP] Opponent context does not reach the production stats at all.**
  `OPP_DEF_FORM` is a feature of the minutes and availability models; the rate is a
  pure player property. Blocks against a team that shoots 40 threes a game and blocks
  against a post-heavy team are not the same expectation, and nothing in the
  composition can express that.
- **[GAP] No per-stat interval width by tier.** The quantile offsets are league-wide
  per stat, so a star and a fringe player get the same block interval though their true
  spreads differ. Section 7 already records this for PTS; widening the vocabulary
  widened the gap rather than closing it.
- **[GAP] The selection is one-look and shallow.** Two inner folds per origin, one
  minutes model, no bootstrap, and **no multiplicity correction across eleven stats** —
  the production tournament did all three for two stats. With eleven stats and a 0.5%
  bar, the probability that at least one "selected" halflife is noise is not small, and
  the per-origin unanimity columns are the only guard against it. The three unanimous
  5/5 stats are the ones to trust; FTM/FTA at 4/5 are the ones to re-check first.

---

## 13. `prospective_2026_27_v1` (FROZEN)

*Re-frozen as `prospective_2026_27_v2` on 2026-10-01; see section 17 for what moved.*
*Re-frozen as `prospective_2026_27_v3` on 2026-10-02; see section 21 for what moved.*

**Frozen 2026-08-17, 64 days before opening night. Append-only. Nothing in this
section may be edited after 2026-10-20; before that date a change requires bumping to
`prospective_2026_27_v2` and re-freezing the whole section.**

Everything in sections 1–12 is **retrospective**. The out-of-fold discipline is real,
the forward-chaining is real, and none of it changes the fact that **we chose the
questions after seeing the era**. Every halflife, every cohort boundary, every
promotion bar was picked by people who had already looked at 2022–2026. Section 6's
own terminology table says as much: the selection holdout is selection-untouched and
*used in the deployment fit*, and "the only genuinely untouched evaluation this system
will ever have" is the 2026-27 season.

That evaluation is worth something only if the protocol is fixed **before** the first
tipoff. This section is that pre-registration. The round-2 review asked for it
(10.2 Q8) and this is the answer, in binding form rather than as a plan.

**The freeze is enforced by tests, not by intention.** `config.PROSPECTIVE_2026_27`
carries the machine-checkable half — pinned checksums, champions, halflives,
estimators, override constants, horizon windows, the feature-contract digest, look
dates, the run label and every falsification threshold — and
`tests/test_prospective_freeze.py` compares each one against the live module and
against the bytes on disk. **A change to the served configuration without a re-freeze
turns the suite red**, which is the only mechanism that survives the eight months
between now and the season-end look.

`config.py` is a snapshot here, not a second source of truth: `predict.py` still reads
halflives and estimators out of the **artifact** (section 12.6), and nothing in the
serving path imports `PROSPECTIVE_2026_27`. Wiring serving through the
pre-registration would make the pre-registration a configuration source, and those two
things must not be the same object.

### 13.1 The frozen serving configuration

**Artifact `20260818`, feature_version `v3`**, `models/20260818/`, trained to cutoff
`2026-04-13` over `2022-10-18 → 2026-04-12` (147,413 rows, 895 players, played rate
0.713248), `universe_source = status`, git commit
`7c9960c4d636a0cd070ebb105bbbe7e43eefa412`.

| File | bytes | sha256 |
|---|---:|---|
| `availability_model.joblib` | 1,402,486 | `aa62f880f6774537ab58ba52d0aa4e641c96964ca3d26691e9ab7dd52180f06c` |
| `base_availability_model.joblib` | 1,400,822 | `3d5fcdeb180f4e8c6650b9c3542d4ef1714401f4048a3d14605de7747262adba` |
| `minutes_model.joblib` | 1,164,395 | `2fd13615d64ba1d62e33bc903cfa4851ff63b602f2a2dec4e96af9343f28115a` |
| `ewma_state.parquet` | 229,388 | `bcc83dec9e55375645f80165df6f490c6056d9659215bde497d31ced99943328` |
| `feature_gain.csv` | 1,595 | `f2765c542b452e1ff8726be27e556c0a1a4a7b323b503ca2cdd01dc191728b94` |
| `metadata.json` | 9,952 | `24978b3261261ad52d28cae7e7fceee6378655d20e515c26882391545adae3fe` |

*Correction, 2026-08-24.* The two rows above originally carried checksums copied from a
registry snapshot taken before `feature_gain.csv` and `metadata.json` reached the bytes
the freeze commit actually pinned, so the recorded values never described any committed
file and the daily-run preflight refused every slate. The four model/state files were
recorded correctly, `metadata.json`'s own `artifact_checksum` fields name exactly the
pinned `.joblib` bytes, and every content assertion in `test_prospective_freeze.py`
passes, so the served configuration never moved: this is a record fix under the 13.2
carve-out ("provably does not change any emitted number"), not a re-freeze.

The checksum set is asserted to cover the **whole** directory, so a file *added* to the
served artifact fails too — six per-file assertions would all pass while a seventh file
sat there unwatched.

**Champions** — `availability: lightgbm`, `minutes: lightgbm`, `production: ewma`,
`composition: decomposed_p_x_minutes_x_ppm`.

**Feature contract** — 51 columns, `sha256("\n".join(FEATURE_COLS)) =
914cdc17c25ee9cb32b072f254691a625472b43ecb37b3da0c23483f165e1b6e`. A digest rather than
a copied list, because what is frozen is "the contract did not move", and a 51-element
literal in `config.py` would be a second place for the contract to live.

**Per-stat production rates**, as selected in section 12.3 and shipped in
`metadata.json`:

| Stat | halflife | estimator | | Stat | halflife | estimator |
|---|---:|---|---|---|---:|---|
| PTS | 5 | ewma | | TOV | 20 | ewma |
| AST | 5 | ewma | | FG3M | 20 | ewma |
| REB | 20 | ewma | | FGM | 5 | ewma |
| STL | 20 | **expanding** | | FGA | 5 | ewma |
| BLK | 5 | ewma | | FTM | 12 | ewma |
| | | | | FTA | 12 | ewma |

STL's `20` is recorded for the audit trail and **not used** — the career expanding mean
beat every EWMA on the grid, and steals is the one stat in the vocabulary that ships
without an EWMA. Coherence clips `FGM ≤ FGA`, `FG3M ≤ FGM`, `FTM ≤ FTA`, in that order,
on the conditional estimate, the unconditional estimate and each quantile level
independently. `RATE_MINUTES_FLOOR = 4.0`.

**Injury-override policy** (`overrides.StatusPolicy` defaults, section 7.1) —
`out_probability 0.02`, `doubtful_probability 0.10`, `questionable_model_weight 0.6`
with `questionable_prior 0.60`, `probable_model_weight 0.85` with `probable_shift
0.15`. The floor condition `s ≥ 1 − w` holds with equality at these values and is
asserted, so a future change to either PROBABLE constant cannot silently reintroduce
the case the removed `max` used to pretend to handle. The layer runs **twice** per
run — on the base probabilities `p_j` before the teammate sums, and on the final
`P(play)` — and both applications share this table.

**Horizons** — `lock (0, 2]`, `gameday (2, 12]`, `early (12, 48]` hours before tipoff,
assigned from the **measured** offset, with the eight `HORIZON_RUN_METADATA` facts
stored per run. The prospective test is served at **`gameday`** (13.8).

**Cold-start context constants**, frozen because the October rules depend on them:
`MAGNITUDE_WINDOW 20`, `MAGNITUDE_SHRINK_K 10`, `MAGNITUDE_PRIORS {MIN 10, FGA 6,
USG 15}`, `CONTEXT_P_PRIOR 0.70`, `CROSS_FIT_FREQ "MS"`,
`CROSS_FIT_MIN_TRAIN_ROWS 5000`, `STAR_USAGE_MIN_APPEARANCES 15`, `TOP_USAGE_N 3`.

### 13.2 What counts as a change

Any of the following, made at any time before opening night, requires bumping
`PROSPECTIVE_PROTOCOL_VERSION` to `prospective_2026_27_v2` and re-freezing this
section against the new configuration. Made **after** opening night, any of them ends
the prospective test for `v1` — the run is no longer serving the thing that was frozen,
and no later look can be repaired by noting the change in a footnote.

1. **Any `CHAMPIONS` entry** — availability, minutes, production or composition.
2. **Any `RATE_HALFLIVES` assignment**, including an "ambiguous → default" stat moving
   off 5, and including PTS/AST, which the production tournament separately froze.
3. **Any `RATE_ESTIMATORS` assignment** — in particular STL moving off `expanding`.
4. **Any `RATE_TARGETS` addition or removal**, and any change to
   `COHERENCE_CONSTRAINTS` or `RATE_MINUTES_FLOOR`.
5. **Any override constant** in `overrides.StatusPolicy`, any change to
   `UNAVAILABLE_STATUSES` / `PASSTHROUGH_STATUSES`, and **shipping an `AVAILABLE`-as-
   clearance rule** (section 7.1's `[PLANNED]` item) — which would be a sixth
   hand-set constant and is exactly the kind of change this list exists to catch.
6. **`FEATURE_COLS`** — an addition, a removal, or a reordering. The pinned artifact
   was fitted against this exact 51-column contract; any of the three invalidates it.
7. **Any training-window rule** — the cutoff, `ORIGINS`, `CUTOFF_POLICY`, the
   universe source, or a refit of the artifact for any reason.
8. **Any of the frozen cold-start constants** in 13.1, or the cohort definitions in
   13.3.
9. **`HORIZON_WINDOWS`**, or the horizon the slate runs are served at.

Not on this list, and deliberately: bug fixes in code paths that provably do not change
any emitted number, report formatting, and anything under `ml/experiments/`. A fix that
*does* change an emitted number is a change to the artifact and is covered by (7).

### 13.3 Primary prospective endpoints

**Five, chosen so that a reader can hold them all at once.** The temptation with an
eleven-stat vocabulary and nine cohorts is a hundred numbers, and a hundred numbers is
a garden of forking paths with a table of contents. Each endpoint below is the *one*
number that would move if the corresponding claim in this document were wrong.

| # | Endpoint | Definition | Why this one |
|---|---|---|---|
| **E1** | **Availability Brier** | Brier score of `P(play)` over every scheduled player-game, plus **Brier skill vs the shifted appearance rate** | Availability is the only strongly learnable target this package has (section 5) and it multiplies every other number the system emits |
| **E2** | **Availability calibration** | Slope `b` and intercept `a` from `logit(y) ~ a + b·logit(p)` fitted over the season's scheduled rows | Brier is a proper score but a *summary*; the override layer injects five hand-set constants into `P(play)` and miscalibration is the way that shows up. Reported on **both** `prob_active_model` and `prob_active` |
| **E3** | **Minutes MAE** | Mean absolute error of `E[MIN\|plays]` over **appearance** rows | The quantity the composition propagates. A minutes error is three separate stat errors downstream |
| **E4** | **Unconditional PTS MAE** | MAE of `P(play) × E[MIN\|plays] × rate` against realized PTS over **every scheduled** row | The end-to-end number a user actually sees, and the only endpoint that scores availability, minutes and rate as one object |
| **E5** | **9-cat aggregate** | Mean over the 11 shipped stats of the relative unconditional-MAE improvement against each stat's **frozen `ewma_total` baseline** | A single number for the whole vocabulary. `ewma_total` (EWMA-5 of the whole-game total, no minutes term) is the pre-composition estimator, so E5 asks whether minutes propagation earns its place prospectively, stat by stat, exactly as section 12.4 asked it retrospectively |

**The pre-override / post-override split is part of the definition, not a footnote.**
The retrospective bracket was measured with **no injury reports at all**
(`player_injury_reports` held 0 rows). Prospectively the override layer will be live,
and it should *improve* availability. So:

> **Every teammate-context comparison (E1, E3, E4 against the v1 shadow) is scored on
> the MODEL probability `prob_active_model`**, which is the quantity the retrospective
> numbers describe. **The override layer's own contribution is a separate endpoint**
> (`prob_active` Brier minus `prob_active_model` Brier), because otherwise a good
> report feed and a good model become one indistinguishable number and the layer's
> five hand-set constants never get measured.

**Cohorts, fixed in advance and frozen as of the section 11 definitions.** Nine, and no
others: the four minutes tiers plus `unknown (no history)`, assigned from `roll10_MIN`
(a strictly prior rolling mean, so the label is as-of safe); the two event cohorts
`vacated_minutes ≥ 30` and `star_out = 1`; the control cohort `vacated_minutes < 5`;
and `ALL`. The event and control cohorts are defined on the **oracle**
`vacated_minutes` column, deliberately and for the same reason as retrospectively:
using hindsight to select *which games to report on* is legitimate, using it to *build
a feature* is not — and it is what makes the served and shadow passes partition
identical rows.

**Every endpoint is additionally reported split by `cold_start`** (13.7). Cold-start
rows are **not** excluded from any headline number; excluding them would be a post-hoc
filter on precisely the hardest games.

### 13.4 The comparison ladder

Prospective skill is not "the model did well". It is "the model beat the things that
were available for free", and the frozen model must clear all three rungs.

**(a) Shifted appearance rate** — the availability floor. `avail_rate_10`, itself
shifted and as-of safe. E1's skill score is measured against it, and it is the same
baseline `evaluate.py` has always used, so the prospective number and the
retrospective one are the same quantity.

**(b) Per-stat frozen baselines** — for minutes, `EWMA(halflife 5)` of prior minutes;
for each of the eleven stats, the two members of the section 12.4 ladder,
`expanding` (career expanding mean of the per-minute ratio) and `ewma_total`
(EWMA-5 of the whole-game total, no minutes term). **These require no shadow model
run**: `ewma_state.parquet` snapshots *both* rate families for all eleven stats
(section 12.6), so the losing estimator can be scored from the frozen artifact without
replaying four seasons of appearance history.

**(c) The v1 no-teammate feature set, as a shadow comparator.** The 36-column
`BASE_FEATURE_COLS` model — no teammate context of any kind, expected or realized. This
is the rung that tests the claim section 8 calls the single most important sentence in
the document.

**How shadow runs are executed and stored, frozen:**

- **Same cutoff, same slate, same information boundary** as the served run. A shadow
  scored at a different boundary is a horizon comparison wearing a feature-set label.
- **Same prediction store**, append-only, **labeled**. The run note carries
  `prospective_2026_27_v1; feature_set=v1; shadow=true`; the served run carries
  `prospective_2026_27_v1; feature_set=v3-honest; shadow=false`.
- **The shadow gets the same override layer**, applied the same way at both stages, so
  the pre-override comparison of 13.3 is like-for-like on both sides.
- **A shadow never reaches the app.** Only the served run's rows are read by the
  projections page; the label is what separates them and there is no second table.
- **The v2-oracle set is not run prospectively at all.** It is a function of other
  players' target-game labels, so it has no live boundary to be scored at. Its role
  ended with the retrospective bracket, and running it "for reference" would produce a
  number nobody could interpret.

### 13.5 Falsification table

**How the thresholds were derived, because a threshold without a derivation is a
preference.** The unit of variance is a **month-block**, not a row: player-games within
a slate share a schedule, a rest pattern and an injury report, so a per-row
independence assumption would understate the standard error badly. Each of the five
development origins *is* one calendar month, so the five per-origin values of each
endpoint give a directly measured **block standard deviation** of exactly the quantity
being frozen. That estimate is **conservative** — it includes each origin's refit
variance and any genuine month-to-month heterogeneity in the effect, neither of which a
frozen model scoring a single season will have.

Season arithmetic: 1,271 scheduled games; **30.02 modelled player-rows per game**
(147,413 / 4,910); appearance rate 0.7132; one month-block ≈ 215 games.

| Checkpoint | Look date | Games | Blocks *k* | Scheduled rows | Appearance rows |
|---|---|---:|---:|---:|---:|
| Dec 1 | 2026-12-01 | ~315 | 1.46 | ~9,460 | ~6,740 |
| All-Star | 2027-02-15 | ~840 | 3.91 | ~25,220 | ~17,990 |
| Season end | 2027-04-20 | 1,271 | 5.91 | ~38,160 | ~27,220 |

Minimum detectable effect at 80% power, two-sided α = 0.05, is
`2.802 × block_sd / √k`:

| Endpoint | block sd | MDE @ Dec 1 | MDE @ All-Star | MDE @ season end | retrospective effect |
|---|---:|---:|---:|---:|---:|
| Availability Brier, v3 vs v1 | 1.724 | **3.99%** | **2.44%** | **1.99%** | −1.89% |
| Minutes MAE, v3 vs v1 | 0.580 | 1.34% | 0.82% | 0.67% | −0.81% |
| PTS uncond. MAE, v3 vs v1 | 0.282 | 0.65% | 0.40% | 0.33% | −0.32% |
| 9-cat aggregate vs `ewma_total` | 0.202 | 0.47% | 0.29% | 0.23% | +1.30% |
| Minutes MAE vs EWMA baseline | 0.518 | 1.20% | 0.73% | 0.60% | +2.99% |
| Availability Brier skill | 0.0151 | 0.035 | 0.021 | 0.017 | 0.352 |
| STL `expanding` vs h20 EWMA | 0.855 | 1.71% | 1.21% | 0.99% | +2.08% |

**Read the first row before anything else.** The teammate-context availability claim is
−1.89% and the season-end MDE for it is **1.99%**. *One full season is barely enough to
detect the headline result of this document.* That is not a defect in the protocol, it
is the honest size of the effect against the honest size of the noise, and it is why
the Dec 1 look cannot test the claim at all. Any protocol that promised a verdict on
teammate context by December would have been lying.

**The table. `Δ%` is relative, negative = better, unless the row says otherwise. A look
is BINDING only if its row minimum in 13.6 is met.**

| # | Claim under test | Prospective observation that COUNTS AS FAILURE | Dec 1 | All-Star | Season end | false-falsify rate¹ | power vs a true null² |
|---|---|---|---|---|---|---:|---:|
| **F1** | The system has availability skill at all (§5) | Brier skill of `prob_active_model` vs the shifted appearance rate falls **below** the bar | < 0.25 | < 0.25 | < 0.25 | ~0% | ~100% |
| **F2** | Teammate context buys **−2.0% availability Brier** (§5.1, §8) | `v3-honest` Brier vs the `v1` shadow is **above** the bar | > +1.00% | > −0.40% | > −0.75% | 5.4% | 85.5% |
| **F3** | Teammate context buys **−0.8% minutes MAE**, surviving honest construction (§5.1) | `v3-honest` minutes MAE vs the `v1` shadow is **above** the bar | > +0.50% | > −0.30% | > −0.40% | 4.4% | 95.3% |
| **F4** | The gain reaches the served number (**−0.3% uncond. PTS**, §5.1) | `v3-honest` uncond. PTS MAE vs the `v1` shadow is **above** the bar | > +0.30% | > 0.00% | > −0.10% | 3.0% | 80.6% |
| **F5** | LightGBM minutes deserved promotion over EWMA (§5, +2.1% at promotion, +2.99% on the v3 dataset) | Minutes MAE improvement over the frozen EWMA baseline is **below** the bar | < +2.00% | < +2.00% | < +2.00% | 1.1% | ~100% |
| **F6** | Minutes propagation + per-stat estimator selection pays across the whole 9-cat vocabulary (§12.4) | E5 (mean relative improvement vs `ewma_total` over 11 stats) is **below** the bar | < +0.50% | < +0.50% | < +0.50% | ~0% | ~100% |
| **F7** | STL ships the **expanding** mean because a steal rate is near-constant and the EWMA throws away usable history (§12.3, 5/5 origins, +2.08%) | STL unconditional MAE improvement of `expanding` over the frozen h20 EWMA rate is **below** the bar | *report-only* | < +1.00% | < +1.00% | 0.1% | 99.8% |
| **F8** | The **halflife-20 selection for REB / TOV / FG3M** generalises (§12.3, 5/5 origins on inner folds) | Mean relative unconditional-MAE improvement of the champion over the frozen `expanding` baseline, averaged over the three stats, is **below** the bar | *report-only* | *report-only* | < 0.00% | — see note | 50% |
| **F9** | `P(play)` is calibrated, not merely well-scored | Fitted slope outside `[0.85, 1.20]`, **or** \|intercept\| > 0.25, on `prob_active` at season end | *report-only* | *report-only* | binding | [JUDGMENT] | — |
| **F10** | The injury-override layer's five hand-set constants help (§7.1, **never measured on real data**) | `prob_active` Brier is **worse** than `prob_active_model` Brier — i.e. the increment is < 0 | *report-only* | < 0.00 | < 0.00 | [JUDGMENT] | — |

¹ Probability the row falsifies **given the retrospective effect is the truth**, at the
season-end threshold. ² Probability the row correctly falsifies **given the true effect
is zero**, at the season-end threshold.

**Notes that are part of the pre-registration, not commentary:**

- **F2/F3/F4 at Dec 1 are wrong-sign tripwires, not tests.** Their Dec-1 thresholds sit
  on the *worse* side of zero, and their power against a true null is 24% / 15% / 10%.
  They catch a catastrophe — the honest features actively hurting — and nothing
  smaller. This is stated here so that a December reading of "only −0.1%" cannot be
  presented in February as evidence of anything.
- **F8 is pre-registered as a claim we already have evidence against, and that is the
  point.** Section 12.4's "honest disagreement": the inner folds preferred halflife 20
  for REB, TOV and FG3M, and the *validation* rows reversed it by 0.33–1.14%, mean
  −0.80%. We shipped the inner-fold selection anyway, because switching the champion to
  whatever wins the column being reported would turn that table into a selection
  surface. **So we expect F8 to fail**, and recording the expectation before the season
  is the only thing that makes a failure informative rather than a shrug. A false-
  falsify rate is not quoted because the retrospective estimate is on the failing side
  of the bar already.
- **A failure never triggers an in-season change.** F8 failing sends REB/TOV/FG3M to
  `expanding` in the **2027-28** freeze, not in March. The artifact does not move
  mid-season (13.2).
- **F1 and F5 and F6 are binding from Dec 1** and are the only rows that are. They are
  the ones where a season-fraction of data is already overwhelming, and they are the
  rows that would catch a *broken deployment* — a wrong artifact, a feature-build
  regression, a universe defect — which is a different and more urgent failure than a
  wrong scientific claim.
- **Multiplicity.** Ten rows at a nominal 5% false-falsify rate is a ~30–40% chance of
  at least one spurious falsification over the season if every claim is true and the
  rows were independent (they are not — F2/F3/F4 share a model and F5/F6/F7/F8 share a
  minutes model). **No correction is applied and the reason is stated rather than
  hidden:** the asymmetry runs the other way here. Each row is a *separate* claim we
  have made in public, a correction would raise the bar on all of them to protect a
  family-wise error rate nobody is decision-making against, and this document's failure
  mode has consistently been claiming too much rather than retracting too eagerly. The
  raw per-row rates are published above so a reader can apply their own correction.

### 13.6 The look schedule

**Exactly three scheduled looks. No others.**

| Look | Date (cutoff; scores every game with `GAME_DATE` strictly before it) | Binding if | Purpose |
|---|---|---:|---|
| `dec1` | **2026-12-01** | ≥ 7,500 scheduled rows | Deployment health (F1, F5, F6) and the cold-start report. Wrong-sign tripwires only for F2–F4 |
| `all_star` | **2027-02-15** | ≥ 20,000 scheduled rows | First real reading on F2–F4, F7, F10 |
| `season_end` | **2027-04-20** | ≥ 32,000 scheduled rows | The verdict. Every row binds |

The row minimums are ~80% of the expected count at that point in the season. **A look
that falls short is reported and NON-BINDING**, and its decisions move to the next
look — not taken on thin data and not quietly taken anyway.

The All-Star date is a **fixed calendar date**, not "the break". The break is not a
labelled event in `nba_schedule`, and a look date that depends on a lookup is a look
date that can be argued about in February.

**Everything examined off this schedule is EXPLORATORY and cannot trigger a
promotion, a demotion, or an amendment to this section.** Daily monitoring, a
mid-January curiosity, a dashboard someone refreshes — all legitimate, all incapable of
changing a champion or a threshold. The mechanism that makes this real is 13.2: the
artifact cannot move mid-season without ending the `v1` test outright, so an
off-schedule observation has nothing it is *allowed* to act on.

**One look per feature version, restated for any in-season challenger.** If a `v4`
feature set is built during 2026-27, it gets **one** pre-registered evaluation at
**one** of the three look dates, against the same frozen comparison ladder, at a bar
declared before the look is taken. It does not get a second look at a later date, it
does not get re-scored after a fix, and a fix produces `v5` with its own single look.
This is the same rule the production tournament closed PTS and AST under
(`ml/experiments/production_tournament/TOURNAMENT.md`), applied to the season.

### 13.7 Cold-start window rules

**Implementation is P5's; the acceptance criteria are frozen here.** P5 lands after
this section, and the ordering is deliberate — a gate whose pass mark is set after the
gate is built is not a gate.

**The flag.** Every prediction whose `GAME_DATE` is **on or before 2026-11-30** carries
`cold_start = true`. The window runs past October on purpose: `MAGNITUDE_SHRINK_K = 10`
means a player is only ~2/3 his own numbers at ten appearances, which teams reach in
late November, so the window is tied to a frozen constant rather than to a calendar
month. The flag is derivable from the schedule alone — no target, no outcome — and it
must not collide with a served stat name in the long-format store.

**What the flag does and does not do.** It is a **mandatory reporting split** on every
endpoint in 13.3. It is **not** a filter: cold-start rows stay in every headline
number. It does **not** gate features — the section 11 open question about gating below
*n* appearances is not resolved by this section and no gating ships in `v1`.

**The October replay gate.** P5 replays **2025-10-01 → 2025-10-31** with the frozen
artifact and a per-date cutoff, **no refit**, to set expectations for what October 2026
should look like. Its acceptance criteria, frozen:

1. **Coverage ≥ 99%** of scheduled player-games in the window receive a prediction,
   with the `insufficient_history` share and the `CROSS_FIT_MIN_TRAIN_ROWS` fallback
   share reported alongside. A replay that silently drops the rows it finds hard is
   measuring the wrong month.
2. **October availability Brier ≤ 1.42 ×** the same replay's non-October Brier.
3. **October minutes MAE ≤ 1.15 ×** the same replay's non-October minutes MAE.
4. **The replay uses the pinned checksums.** A replay against a differently-trained
   artifact tells us about that artifact.

**Where 1.42 and 1.15 come from**, because they are the two numbers in this section
most likely to be waved through. They are the **measured fringe-tier ratios** from the
section 5.1 bracket: fringe availability Brier 0.1022 / ALL 0.0720 = **1.42×**, fringe
minutes MAE 4.991 / 4.679 = **1.07×**. The criterion is therefore "October may be as
bad as our worst-served minutes tier, and no worse" — an envelope derived from a
measurement rather than picked. Minutes gets 1.15 rather than 1.07 because October
adds roster churn on top of thin history and 1.07 would leave no headroom at all.

**If the replay fails the gate**, the response before opening night is to widen the
cold-start flag window (October → October + November was already chosen; the next step
is through 31 December) and/or widen the quantile bands for flagged rows — and either
is a **change** under 13.2 requiring `prospective_2026_27_v2`. It is *not* a licence to
retrain, and it is *not* a licence to drop October from the endpoints.

### 13.8 Operational commitments

1. **A prediction run at the `gameday` horizon before every slate**, best effort. The
   `gameday` bucket is `(2, 12]` hours before tipoff: the initial participation report
   exists and has usually been updated at least once, and it is early enough to be
   useful to a manager setting a lineup.
2. **A missed slate is recorded, not backfilled.** The record is the absence of a run
   plus a line in the look report. **A prediction made after tipoff is never inserted
   into the store, under any circumstance, for any reason.** This is the one rule in
   this section with no judgement in it: a post-tipoff row is not a late forecast, it
   is a different kind of object, and one of them in the store makes every aggregate
   over the season unauditable.
3. **The store is append-only.** No update, no delete, no correction-in-place. A wrong
   row is superseded by a new run, and both remain.
4. **Runs that count carry the label.** `prediction_runs.notes` contains
   `prospective_2026_27_v1`, plus `feature_set=` and `shadow=` per 13.4, plus the
   existing `horizon=<name> (<offset>)`. **A run without the label is not part of the
   prospective test**, whatever else it did — which is what makes an ad-hoc backfill or
   a debugging run harmless rather than contaminating.
5. **Every run stores the eight `HORIZON_RUN_METADATA` facts**, including
   `report_count = 0` where the model stood alone and `tip_source` where the offset is
   approximated from `GAME_DATE` because `scheduled_at` is NULL. A look report that
   pools measured and approximated offsets says so.
6. **Look reports are markdown plus a csv beside it**, on the same pattern as
   `reports/<version>.md` + `<version>_results.csv`, so a claim in a look can be
   recomputed later without rerunning the season.

### 13.9 `[JUDGMENT]` calls

Where the retrospective evidence underdetermined a number, the conservative option was
taken and it is named here. **None of these is "TBD" — an unfrozen number is the exact
thing this section exists to prevent.**

- **`[JUDGMENT]` F2/F3/F4 season-end thresholds (−0.75% / −0.40% / −0.10%).** Set so the
  false-falsify rate under the retrospective effect is ≈5%, rather than at the
  retrospective effect itself. Falsifying a real effect because one season was
  unlucky is the more expensive error: it would retire a construction that works, and
  the construction cost the round-2 review to get right.
- **`[JUDGMENT]` F2/F3/F4 Dec-1 thresholds on the worse side of zero.** At *k* ≈ 1.46
  the MDE exceeds the effect for all three. A bar demanding improvement would be a coin
  flip presented as a test; a wrong-sign tripwire is the strongest honest statement
  available at that sample size.
- **`[JUDGMENT]` F6's bar at +0.50%.** The package's own per-stat selection bar from
  section 12.3, reused rather than reinvented, so the prospective bar and the bar the
  halflives were selected under are the same number.
- **`[JUDGMENT]` F9's calibration bounds, slope `[0.85, 1.20]` and \|intercept\| ≤
  0.25.** There is **no retrospective measurement** of calibration slope in this
  document, so these are set from the standard reading of a recalibration fit rather
  than from our data. Deliberately wide: the override layer will be operating on live
  reports for the first time, and a narrow band would falsify on the layer's teething
  rather than on the model's calibration.
- **`[JUDGMENT]` F10's bar at 0.00.** The override layer has **never been measured on
  real data** — `player_injury_reports` held 0 rows when it shipped — so there is no
  effect size to set a bar from. "Must not make availability worse" is the weakest
  claim that is still falsifiable, and it is the right one for five hand-set constants
  whose author flagged `LEAGUE_QUESTIONABLE_PLAY_RATE = 0.60` as the number most likely
  to be wrong.
- **`[JUDGMENT]` The cold-start flag window ending 2026-11-30** rather than 2026-10-31.
  Tied to `MAGNITUDE_SHRINK_K = 10` (a player is ~2/3 his own numbers at ten
  appearances) rather than to the calendar. The wider window is the conservative one:
  it labels more rows as cold-start and therefore claims less about them.
- **`[JUDGMENT]` The October gate's 1.15× minutes ratio.** The measured fringe ratio is
  1.07×; 1.15 adds headroom for October roster churn, which the fringe tier does not
  contain. The Brier ratio 1.42× is *not* a judgement — it is the measured fringe/ALL
  ratio used unmodified.
- **`[JUDGMENT]` Look row minimums at ~80% of expectation.** A schedule can lose games
  to postponement; 80% keeps a look binding through ordinary attrition and demotes it
  to report-only under a genuine disruption.
- **`[JUDGMENT]` The All-Star look on a fixed 2027-02-15** rather than on the break.
  See 13.6.
- **`[JUDGMENT]` No multiplicity correction across the ten falsification rows.**
  Reasoned at length in 13.5. The raw rates are published so a reader can disagree
  arithmetically rather than rhetorically.
- **`[JUDGMENT]` Block-level rather than row-level standard errors.** Every MDE here is
  computed from between-origin variance, which is conservative — it includes refit
  variance a frozen model will not have. The tighter row-level paired bootstrap was
  available and was not used, because understating the uncertainty of a
  pre-registration is a worse failure than overstating it.

## 14. Season rollover, cold start, and the October gate (P5, 2026-08-17)

**Append-only, and it appends to a freeze rather than editing one.** Section 13 was
written first on purpose (13.7: "a gate whose pass mark is set after the gate is
built is not a gate"), so everything below is machinery built *around* a frozen core:
no champion, halflife, estimator, override constant, feature column or training-window
rule moved, and `tests/test_prospective_freeze.py` still passes unchanged. Four pieces:
the season stops being a constant, the offseason stops being invisible, the October
gate gets run, and the cold-start flag gets stamped.

### 14.1 The season is a CLI argument now

`SEASON = "2025-26"` in `scraper/run_scraper.py` was a constant to edit on opening
night, which is a deployment step disguised as a code change. It is now a **default**
threaded through every truth-layer phase, with `--season` overriding it and
`--sync-truth` running just those phases:

```
python run_scraper.py --dev --sync-truth --season 2026-27
```

**Flipping the default is still the one-liner, and it is deliberately still manual.**
Once 2026-27 has tipped off, `SEASON` moves to `"2026-27"` so the un-flagged 6-hour
cron follows it. A date-derived default was rejected: it would switch seasons in the
middle of the playoffs, which is exactly when the old season's rows are still being
corrected by the scorer.

**`scrape_injuries` is in `--sync-truth` and takes no season.** It scrapes a page of
*today's* designations; `player_injury_reports` is a truth-layer table and the
override layer reads it, but there is no season parameter to give it and inventing one
would be a label rather than a filter.

**An empty season no-ops rather than erroring.** Between the schedule landing (the
2026-27 schedule is already ingested on dev and prod) and opening night, the new
season has 1,271 games and zero game logs. Every phase must pass cleanly through that
window: the endpoints return empty result sets, no rows are built, the ingestion run
closes `succeeded` with 0 rows, and `_sync_player_team_stints` finds no team changes —
which is the *correct* answer and also the reason it cannot see an offseason trade
(14.2). Treating "no games yet" as a failure would make the whole preseason look like
an outage.

**The season-boundary guard.** `split_rows_on_season_boundary` partitions built log
rows against a July 1 – June 30 window (`season_start_date` / the new
`season_end_date`, which tile the calendar with no gap and no overlap) and refuses
anything outside it, loudly, before any write. The watermark logic already floors the
fetch at the season start, so in normal operation nothing out of range arrives — but
that is an assumption about a remote API rather than an invariant we control, and the
failure it prevents is **silent and permanent**: `build_player_game_log_row` stamps the
*requested* season onto any row whose own `SEASON_YEAR` is missing, so one stray
April-2026 row returned by a 2026-27 request would be stored as a 2026-27 game. The
truth layer would then hold that game twice under two seasons and every season-scoped
aggregate — the availability universe included — would double-count it. The guard runs
on both the incremental sync and the backfill.

### 14.2 The offseason roster snapshot

`player_team_stints` is derived from game logs, so it can only learn that a player
changed teams once he has **played** for the new one. Every trade and signing between
April's last game and October's first is therefore invisible to it, and an October
projection built off it would put a traded star on his old team — with his old team's
teammate-context sums, which is the error that then propagates to *everyone else on
both rosters*.

`--roster-snapshot` fetches `commonteamroster` for all 30 teams and writes the
assignments as stints with `source='roster_snapshot'` and `valid_from` = the snapshot
date, behind the existing `--dry-run` machinery. Three rules, all of which are about
what a roster page is *not* allowed to assert:

1. **The old stint closes the day before the snapshot**, not on the player's last game
   with the old team. Those differ by an entire offseason and only one of them was
   observed: we know he is on the new roster *today*; we never saw when he left the old
   one. Closing at the last game would assert a transaction date the truth layer never
   saw. (This is the opposite of the game-log planner's choice, where the gap belongs
   to neither team — there, a second observed date exists to bound it with.)
2. **A player missing from every roster is not closed.** Absence has two
   indistinguishable causes — genuinely unsigned, or one team's fetch failed — so
   closing on absence would turn a single HTTP error into a whole roster of wrongly
   ended stints. The cost is a stale open stint for a retired player, which over-states
   nothing any model reads.
3. **`source` is a distinct value.** A game-log stint is an *observation*; a snapshot
   stint is a *declaration*. A query that cannot tell them apart cannot say which it
   has.

**The measured offseason, `--dry-run` against dev on 2026-08-17** (30/30 teams
fetched, nothing written):

| | count |
|---|---:|
| players on a current 2026-27 roster | **578** |
| returning players (also in 2025-26 game logs) | 491 |
| **of those, on a different team than their last 2025-26 game** | **99 (20.2%)** |
| players new to the truth layer (2026 draft, undrafted, international) | 87 |
| 2025-26 players on no current roster | 91 |
| stint changes the write would make | 578 (0 closing an open stint) |

The 578-with-0-closes figure is an artefact of the dev branch, where
`player_team_stints` is empty — on a populated environment most of those 578 would be
no-ops and the closes would be the interesting number. **99 of 491 is the real
finding**: one returning player in five is on a different team than the stint table
believes, and every one of them would have been projected onto the wrong roster.

`--snapshot-out` writes the same assignments to csv. That is a *file*, not a database
write, so it happens under `--dry-run` too — which is what let the projection in 14.4
consume a snapshot that has deliberately not been committed.

### 14.3 The October replay gate — RUN, and the verdict

`ml/replay_gate.py` replays 2025-10-01 → 2025-10-31 through the frozen serving shape
(base probability → expected context → the two champions) with **artifact 20260818 and
no refit**, and scores the three criteria 13.7 pinned. Every threshold is *read* from
`config.PROSPECTIVE_OCTOBER_GATE`; none is defined in the gate.

**The known limitation, stated here rather than in a footnote.** October 2025 is
**inside** artifact 20260818's training window (2022-10-18 → 2026-04-12). Its October
numbers are in-sample and their absolute level is optimistic. The gate survives that
only because **every criterion is a ratio against non-October rows contaminated the
same way** — same season, same model, same training window — and a *uniform* in-sample
optimism divides out of a ratio while it does not divide out of a level. That form was
frozen in 13.7 before this run, not chosen after seeing which one passed. The residual
risk the replay cannot rule out is a **non-uniform** optimism: a model overfitting its
October rows harder than its March rows would make the ratio too kind. So a PASS reads
as *"no October-shaped catastrophe is visible"*, not as *"October 2026 will look like
this"*. Refitting to a pre-October cutoff was rejected because 13.7 criterion 4
requires the pinned checksums and a replay against a differently-trained artifact
measures that artifact.

**Verdict — GATE PASSED, all four criteria.** 2,429 October scheduled rows against
35,212 non-October (2025-11-01 → 2026-04-12), season 2025-26.

| # | criterion | observed | | bar | verdict |
|---|---|---:|:-:|---:|:-:|
| 1 | prediction coverage | **1.0000** | ≥ | 0.99 | **PASS** |
| 2 | October / non-October availability Brier | **1.0633** | ≤ | 1.42 | **PASS** |
| 3 | October / non-October minutes MAE | **1.0097** | ≤ | 1.15 | **PASS** |
| 4 | pinned checksums | all 6 files match | = | frozen set | **PASS** |

Supporting numbers (in-sample, per the limitation above):

| | October | non-October |
|---|---:|---:|
| scheduled rows | 2,429 | 35,212 |
| appearance rows | 1,791 | 24,810 |
| played rate | 0.7373 | 0.7046 |
| availability Brier | 0.0701 | 0.0659 |
| minutes MAE | 4.5718 | 4.5277 |
| `insufficient_history` share | **0.1272** | 0.0383 |
| cross-fit (`CROSS_FIT_MIN_TRAIN_ROWS`) fallback share | 0.0000 | 0.0000 |

**The headline passed comfortably and the cohort split is where the reading actually
is.** Criterion 1 asks for the hard-row shares beside coverage precisely so that a
replay cannot pass by dropping what it finds difficult; nothing was dropped, and the
`insufficient_history` share is **3.3× higher in October** (12.7% vs 3.8%) — which is
the thing October *is*, arriving as a composition shift rather than as a degraded
model.

| cohort | Oct rows | Brier ratio | minutes MAE ratio |
|---|---:|---:|---:|
| ALL | 2,429 | 1.0633 | 1.0097 |
| star (≥30) | 556 | 0.7452 | 1.0794 |
| bench (10-20) | 526 | 0.9143 | 0.9537 |
| fringe (<10) | 261 | 1.0320 | 0.9187 |
| starter (20-30) | 891 | 1.1724 | 1.0344 |
| **unknown (no history)** | **195** | **3.0036** | **1.3231** |

**The one thing worth carrying into the season.** The `unknown (no history)` cohort
degrades 3.0× on Brier and 1.32× on minutes MAE — both far past the ALL-level bars —
and the headline passes anyway because that cohort is 8% of October rows (195 / 2,429)
against 2% of the rest (773 / 35,212). This is **not** a gate failure: 13.7 set the
criteria on the pooled window and the pooled window passed, and moving the bar to a
cohort after seeing the cohort is the exact manoeuvre section 13 exists to prevent. It
is recorded as a **prospective expectation**: if October 2026's `unknown` cohort is
larger than October 2025's — a bigger rookie class, more roster churn — the pooled
ratio will drift toward that cohort's, and 1.0633 is not the number to expect. The
2026-27 dec1 look reports the same split (13.3 requires the cold-start split on every
endpoint), which is where this gets read again.

`--per-date` re-scores the window one game date at a time and asserts the numbers
match the single vectorised pass. **Measured drift: 0.000e+00** — exact. That is what
makes "features as-of each game date" a checked claim rather than a comment: the
served context features aggregate strictly within a `(GAME_ID, TEAM_ID)` group and
every other feature came off an `allow_exact_matches=False` as-of join, so there is no
cross-date path for the single pass to take.

Report: `ml/reports/october_replay_20260818.md` + `.csv`.

### 14.4 Cold start, and a preseason projection that was actually run

**The flag.** `predict.py` stamps `cold_start` on every emitted row — true when
`GAME_DATE ≤ PROSPECTIVE_COLD_START_THROUGH` (2026-11-30) — into the parquet, and
records the flagged-row count in the run's notes (`cold_start=N/M rows (GAME_DATE <=
2026-11-30)`), on the same pattern `store.build_run_record` already uses for the
horizon. It is a mandatory reporting split, never a filter, and nothing reads it back
as a feature.

**Migration 014 is NOT altered, and that is the design rather than a deferral.** The
per-row store is long-format `(run, player, game, stat, quantile, value)` with no flag
column, and 13.7 requires that the flag "must not collide with a served stat name in
the long-format store" — so it is not emitted as a pseudo-stat. It does not need to be:
the flag is a **pure function of `GAME_DATE`**, which every stored row already carries,
so any consumer recomputes it exactly with `config.is_cold_start`. The run-level note
records the one fact the rows cannot reconstruct — what the run itself observed.
`config.is_cold_start` lives *below* the frozen bundle in `config.py` and introduces no
number; it only owns the arithmetic of comparing a date to a constant section 13 owns.

**There is no `--early-season` flag, and that is a finding.** The investigation asked
what a preseason run actually needs that the serving path lacks, and the answer was:
nothing on `predict.py`. A player with no games in the new season keeps his prior-season
form through the **career-scoped** as-of joins (which may span the offseason, by
design — that is why `player_appearance_features` returns two frames with two join
scopes). A player with no NBA history at all is caught by `features.insufficient_history`
and served the artifact's league-rate fallbacks. The leakage guards pass trivially
because every future game date is after the training cutoff. What was missing was not a
mode but a **dataset**: `build_dataset.py` reads `player_game_status`, which is derived
from box scores and has exactly zero rows for a game nobody has played.

`fnba_ml/prospective.py` supplies those rows from the two things that do exist — the
schedule and a roster observation — and `project_preseason.py` is the command line
around it. Two rules carry the weight:

- **The roster is an input, not an inference.** Team membership for an unplayed season
  cannot come from game logs; it comes from 14.2's snapshot. This is what puts a traded
  player on his new team's teammate-context sums.
- **One future date at a time.** Almost every feature is an as-of join over
  *appearance* rows and a future row is never an appearance, so future rows cannot
  reach each other through any of them. `features._availability_history` is the
  exception: it takes shifted rolling means over the player's whole **scheduled**
  series, so a Tuesday future row carrying `PLAYED = 0` would enter a Thursday future
  row's `avail_rate_10` as a **fabricated absence**, and by the end of an opening week
  a healthy starter would look like he had missed three games. Building features for
  one future date at a time against the full played history makes that structurally
  impossible. `tests/test_prospective.py` asserts the per-date result equals scoring
  that date alone, *and* demonstrates that the naive whole-week build really does
  differ — a correctness rule whose violation nobody has seen is a rule nobody can be
  sure is doing anything.

**The dry run: opening week 2026-27, `--out` parquet only, NOT written to the store.**
2026-10-20 → 2026-10-27 (the ingested schedule's first eight regular-season dates),
roster assignments as of the 2026-08-17 snapshot, artifact 20260818, horizon labelled
`early` (the run correctly warns that a T-1608h measured offset is outside every
window — this is a projection, not a slate run).

| | |
|---|---:|
| game dates / games / rows | 8 / 56 / **2,155** |
| players | 578 |
| `cold_start` rows | **2,155 of 2,155 (100%)** |
| mean P(play) | 0.7943 |
| mean E[MIN] · conditional | 13.3003 · 16.2315 |
| mean E[PTS] · conditional | 6.5024 · 7.9037 |
| mean E[REB] / E[AST] | 2.3944 / 1.5087 |
| rows dropped by the pipeline | **0** |

**Rookies appear, flagged, rather than vanishing** — the specific failure this exercise
was built to catch, because a player dropped by a join produces no row, no warning and
no prediction, and looks identical to a season with no rookies in it. 79 of the 578
players have zero NBA history (2026 draftees, undrafted signings, international
arrivals), contributing **299 rows, all present**, all `insufficient_history = 1`,
`has_history = 0`, and all carrying the artifact's single league-rate fallback
(`RATE_PTS = 0.474598` on every one of them — one distinct value, which is what a
fallback looks like). They project at P(play) 0.588 / 9.17 conditional minutes / 2.53
unconditional PTS against 0.828 / 17.37 / 7.14 for the 499 players with history. The
`unknown (no history)` minutes tier is 307 of 2,155 rows (14.2%) — the same cohort the
October replay measured at a 3.0× Brier ratio, which is the connection between 14.3
and 14.4 and the reason both numbers are recorded here.

Per-date means are flat across the week (P(play) 0.777–0.802, conditional minutes
16.09–16.70), which is the visible consequence of the one-date-at-a-time build: a
Friday projection that had absorbed four fabricated absences would trend downward
across the table, and it does not.

### 14.5 What P5 deliberately did not do

- **No frozen constant moved.** No champion, halflife, estimator, override constant,
  `FEATURE_COLS` entry or training-window rule was touched; `PROSPECTIVE_PROTOCOL_VERSION`
  is still `prospective_2026_27_v1` and the 59-test freeze suite is unchanged and green.
- **The gate was not adjusted to its result.** It passed; had it failed, 13.7 names the
  response (widen the cold-start window, widen the quantile bands for flagged rows) and
  both are changes under 13.2 requiring a re-freeze. Neither is licence to retrain and
  neither is licence to drop October from the endpoints.
- **Nothing was written to any database.** The roster snapshot ran `--dry-run`, the
  preseason projection ran without `--write-db`, and the replay gate has no write path
  at all.
- **`SEASON` still defaults to 2025-26.** The rollover machinery exists; pulling the
  trigger is an opening-week decision, not a P5 one.

## 15. P2 — matchup, blowout and season stakes (2026-08-19). The candidate did NOT clear its bar.

**The verdict first, because a null result buried under its own methodology is a null
result nobody reads.** A 21-column candidate feature set (`feature_version v4`,
72 columns against v3's 51) describing the *game* rather than the player — possession
environment, a pregame blowout probability, and season-stakes / load-management
context — was built, evaluated against the served contract over identical rows and six
rolling origins under a bar registered before the numbers existed, and **it did not
clear the bar. `prospective_2026_27_v1` remains frozen. `FEATURE_COLS` is still the
same 51 columns with the same `914cdc17…` digest, `FEATURE_VERSION` is still `v3`,
artifact `20260818` is untouched, and `tests/test_prospective_freeze.py` is green.**

**Nothing in section 13 is superseded and nothing in it was edited.** The candidate
exists as `config.FEATURE_COLS_V4` and `config.FEATURE_SETS["v4"]`, alongside the frozen
contract rather than replacing it. That was the design constraint from the start (13.2
item 6 makes any `FEATURE_COLS` change a re-freeze trigger) and it is what makes this
section an experiment record rather than a re-freeze.

### 15.1 The pre-registered bar, and one look

Written into `config`'s P2 block before the bracket ran, and quoted by
`run_p2_bracket.py`'s own header before it prints a single number:

> A paired **7-day moving-block bootstrap** over game dates (the frozen convention from
> `ml/experiments/production_tournament/bootstrap.py`, loaded by path rather than
> reimplemented, 2,000 replicates), **95% percentile CI excluding zero**, **AND ≥ 1.0%
> pooled relative improvement on minutes MAE or availability Brier**, **AND no reported
> cohort regressing by more than 1.0%**.

**Why 1% and not the package's usual 2%.** The 2% line is the bar for promoting a new
*estimator* — a different model class in the serving path is a large change and should
have to pay for itself largely. This is a **feature-set change to an existing champion**:
same LightGBM, same composition, same artifact shape, more columns. The precedent is
section 11's v3 adoption, which shipped on −1.98% availability Brier and −0.81% minutes
MAE and was accepted because it was measured on identical rows with the same estimator.
1% is that precedent's bar made explicit. **Stating the rationale before the result is
the whole point**: the pooled availability number came in at +0.66% with a CI upper
bound of +1.13%, which is exactly the situation in which a bar chosen afterwards would
have been chosen at 0.5%.

**Unconditional PTS is reported and is not a gate.** It is downstream of both gated
endpoints, so letting it clear the bar would count one win twice.

**One look, per 13.6.** "If a `v4` feature set is built during 2026-27, it gets **one**
pre-registered evaluation… it does not get a second look at a later date, it does not
get re-scored after a fix, and a fix produces `v5` with its own single look." This is
that look. The numbers below are final for v4; anything that changes them is v5.

### 15.2 What was built

**A. Opponent matchup / possession environment (9 columns).** Own and opponent pace
(possessions per 48), the game's pace mean and product, own and opponent rolling net
rating, opponent **defensive rating** (points allowed per 100 possessions — the
refinement of the v3 `OPP_DEF_FORM`, which is raw points allowed per game and therefore
conflates good defence with slow pace), and opponent **style**: 3PA allowed and FTA
allowed, both per 100 possessions. All rolling 15 team-games, season-scoped, every one
`shift(1)`-ed before the rolling window and *before* the own/opponent merge.

**`OPP_DEF_FORM` was kept, not replaced.** Removing it would have made this a
two-variable change and there would be no way to distinguish a refinement from a
removal.

**The possession estimate deviates from the textbook and the deviation is measured.**
`team_game_logs` **has no `oreb` column** — verified against the dev schema: the table
carries `reb` with no offensive/defensive split, and no other table in the truth layer
has one. So the standard OREB-free fallback is used:

```
poss ≈ FGA + 0.44·FTA + TOV      (documented; config.POSSESSION_USES_OREB = False)
```

which is the same formula the v2 usage feature already uses, so the two are consistent
rather than being two notions of a possession. **What it costs:** offensive rebounds
extend a possession, so the fallback overcounts by roughly a team's OREB count — the
measured mean pace is **112.3** against a published league figure near 99–100, a level
shift of about +12%. Every consumer of the number is a *relative* comparison between
teams (pace percentiles, a shared defensive-rating denominator), so a level shift is
close to harmless; it becomes a real error only where teams differ a lot in OREB rate,
which is a second-order spread on a first-order one. **[GAP]** An upstream OREB/DREB
split would allow the textbook form, and the fix belongs in the scraper.

**B. Honest pregame blowout model (2 columns).** Two-stage, all out-of-fold.
`blowout_prob` plus one interaction, `blowout_prob × minutes_share`. See 15.7 — this
family is the phase's clearest negative result and it produced the phase's most useful
incidental finding.

**C. Season stakes (8 columns).** `team_games_remaining` (82 − games played; 82 is a
constant, and it is exactly right — 2,460 team-game rows / 30 teams = 82.0 in all four
seasons), `team_win_pct`, `team_games_over_500` = (W−L)/2 signed, a `late_season`
indicator at ≤ 15 games remaining, and a **clinch proxy** rather than seed math:

```
stakes_lockedness = 1(late season) · min(1, |games over .500| / games remaining)
```

which reads as "how far from .500 this team is, in units of the games it has left to
change it". At 1.0 the remaining schedule cannot return the team to .500 — the
tiebreak-free version of "this team's season is decided". True seed math needs the full
conference standings on every date plus tiebreak rules and would still not produce
"clinched"; the proxy is cheap, continuous (a hard flag would throw away the ordering
inside the band where the effect is strongest), and documented as a proxy. Plus the two
player-level interactions the observation demands: `stakes_lockedness × minutes_share`
and `stakes_lockedness × log1p(career appearances)`, because a rest candidate is not "a
player on a locked team" but a **high-minute veteran** on one, and neither factor alone
identifies him.

**D. Start rate: the data does not exist, and it is not sparse — it is absent (1
column).** Measured on the dev truth layer:

| column | rows | NULL | `true` | `false` |
|---|---:|---:|---:|---:|
| `player_game_logs.started` | 105,253 | **105,253 (100%)** | 0 | 0 |
| `player_game_status.started` | 74,870 | 52,957 (70.7%) | **0** | 21,913 |

**Zero `true` values league-wide.** A rolling start rate would be a rolling mean of
zero. This confirms the suspicion in `build_player_game_log_row` and extends it: the
box-score path writes only the negative case, so the column is structurally absent
rather than thinly populated. The proxy shipped instead is `top5_min_share_10` — the
share of the player's last 10 **scheduled** team-games in which he was among his team's
top 5 by minutes played. Five is the number of players on the floor at tip, so "top 5 in
minutes" is the outcome a start usually produces; it is not the same thing (a sixth man
can out-minute a starter) and the column is named for what it measures. Non-appearances
count as 0, exactly as `avail_rate_10` counts them, because "did not play" is not
"started". **Nothing was scraped.**

Plus `minutes_share` (roll10_MIN ÷ rolling team minutes per lineup slot, ~48 in
regulation), which is its own column and not only an interaction term because it is the
quantity *both* interactions multiply and a booster handed only the products cannot
recover it.

### 15.3 The sixth origin, and why it is March **2025**

`config.ORIGINS` is untouched at five entries — `tests/test_teammates_v3.py` pins
`len(ORIGINS) == 5` and every champion decision in sections 5, 11 and 12 was made on
exactly those five, so a sixth entry there would silently redefine what "the five
origins" means in every published table. The sixth lives in **`config.DEV_ORIGINS`**,
which is `ORIGINS + [LATE_SEASON_ORIGIN]`, and the P2 bracket runs on that.

**It validates on 2025-03-15 → 2025-04-12 and the choice of year is the load-bearing
part.** March–April **2026** is inside the **selection holdout** (section 6: Feb-2026 →
Apr-2026, "never used for model selection"). Using it as a development origin would have
consumed the holdout for selection, which is precisely the claim section 6 makes.
March–April **2025** is in 2024-25, is not the holdout, and carries comparable volume
(6,679 scheduled rows against ~6,500 for the 2026 window). The more recent data was
available and was not usable for this purpose.
`tests/test_matchup_v4.py::test_the_late_season_origin_is_outside_the_selection_holdout`
encodes the constraint so it cannot be quietly reverted.

### 15.4 The decision table

Paired 7-day moving-block bootstrap, 2,000 replicates, blocks drawn within origin,
pooled over all six origins. **Positive = the candidate is better.**

| endpoint | gate | v3-honest | v4 | rel. improvement | 95% CI | CI excl. 0 | clears 1% bar |
|---|:--:|---:|---:|---:|---|:--:|:--:|
| **availability Brier** | yes | 0.073697 | 0.073208 | **+0.66%** | [+0.41%, +1.13%] | yes | **NO** |
| **minutes MAE** | yes | 4.7330 | 4.7117 | **+0.45%** | [+0.28%, +0.73%] | yes | **NO** |
| uncond. PTS MAE | report | 3.9744 | 3.9617 | +0.32% | [+0.25%, +0.48%] | yes | — |

37,596 scheduled rows / 26,498 appearance rows / 170 distinct game dates. Every
bootstrap p-value is at the resolution floor (0.0005).

**The candidate is real and it is too small.** All three effects are in the right
direction, all three CIs exclude zero, and neither of the two gated point estimates
reaches 1%. This is the cleanest kind of null this document has produced: not "we could
not tell", but "we could tell, and the answer is about half the bar". Availability's CI
upper bound of +1.13% straddles the bar, which is worth stating plainly — a season's more
data might well put it over — and is exactly why the bar was written down first.

**One cohort regresses past the tolerance**, and it would have blocked promotion even
had a gate cleared: `control: vacated_minutes < 5` minutes MAE at **+1.63%** on 564
rows. That is the *control* cohort — the quiet nights where nobody is out — and it is
the smallest cohort in the report, so the honest reading is "probably noise on 564 rows,
and the rule does not have a noise exemption".

### 15.5 Per-origin: the late-season effect is real and it is the largest one

| endpoint | O1 12-24 | O2 01-25 | O3 02-25 | O4 12-25 | O5 01-26 | **O6 Mar–Apr 25** |
|---|---:|---:|---:|---:|---:|---:|
| availability Brier Δ% | −0.52 | −0.15 | −0.26 | −0.80 | −0.50 | **−1.55** |
| minutes MAE Δ% | −0.03 | −0.50 | −0.52 | −0.35 | −0.54 | **−0.69** |
| uncond. PTS MAE Δ% | −0.35 | **+0.14** | −0.36 | −0.39 | −0.40 | **−0.57** |

(negative = v4 better.)

**The late-season origin is the best origin on all three endpoints, and on availability
it is roughly triple the pooled effect.** −1.55% on a single month of March–April rows
is within touching distance of the 1% bar on its own. This is the load-management
hypothesis behaving exactly as predicted, and it is the finding that makes the sixth
origin worth having existed: pooled over five winter months the same effect reads
−0.66%, which is a dilution and not a measurement.

**It is not, however, licence to promote on the late-season origin.** Selecting the
window where the answer is most flattering is the thing the pre-registration exists to
prevent, and the pooled number is the gate. What it *is* licence to do is say where a v5
should look.

### 15.6 The two new cohorts: the stakes family earns its keep, the blowout family does not

Both cohorts are appended (`config.V4_DESCRIPTIVE_COHORTS`); `EVENT_COHORTS` is frozen
by `test_prospective_freeze.py` and was not touched. The blowout cohort is a **quantile**
cut rather than a fixed threshold — the league blowout rate drifted 26.8% → 37.2% across
the four seasons, so a fixed probability would not mean the same share of rows in every
origin.

| cohort | n | availability Brier Δ% | minutes MAE Δ% | uncond. PTS Δ% |
|---|---:|---:|---:|---:|
| ALL | 37,596 | −0.66 | −0.45 | −0.32 |
| **v4: stakes-flagged (locked, late)** | 4,755 | **−1.91** | −0.51 | **−0.80** |
| **v4: blowout_prob top decile** | 3,800 | **−0.00** | −0.06 | −0.01 |

**The stakes-flagged cohort is the largest availability gain of any cohort in the
report** — larger than `star_out = 1` (−1.14%), larger than `star (≥30)` (−1.28%), and
close to triple the pooled effect. The features aimed at load management improve
availability on exactly the games load management happens in. **That is the phase's
positive result and it survived a null verdict.**

**The blowout-decile cohort is indistinguishable from zero on all three endpoints**, on
the 10% of rows where a blowout feature should help most. Combined with 15.7 that is a
coherent story rather than two puzzles: a probability with AUC 0.536 cannot separate
anything, so the cohort it defines is barely a cohort.

### 15.7 The blowout model: AUC 0.536, no Brier skill, and a hyperparameter finding worth more than the feature

**Label.** `1(|final margin| ≥ 15)`. Chosen from the data and the alternatives are
recorded so the choice can be argued with — over all 9,840 team-games (mean |margin|
12.45, median 10):

| threshold | 5 | 10 | 12 | **15** | 18 | 20 | 25 |
|---|---:|---:|---:|---:|---:|---:|---:|
| share | 80.1% | 53.0% | 44.0% | **32.6%** | 24.2% | 19.7% | 11.2% |

10 is a coin flip and names nothing; 20 leaves 1,900 positives and stops describing the
games where a starter loses six minutes rather than sixteen. 15 keeps a third positive.
The **per-season trend is 26.8% / 32.4% / 34.2% / 37.2%** — a real league-level drift,
and one more reason the classifier is cross-fitted forward in time rather than fitted
once on the pooled four seasons.

**Construction.** Pregame-knowable inputs only: both teams' rolling net ratings and
their absolute gap, both season-to-date win percentages as a gap and a sum, the pace
environment, rest and back-to-back on both sides, home/away. Cross-fitted over
consecutive calendar-month blocks — **the identical scheme
`models.cross_fit_base_probabilities` uses for `p_j`**, deliberately the same rather
than merely similar — with every probability stamped with its block start and checked by
`validate_out_of_fold`. 93.4% of team-games scored by a fitted block; the opening month
(6.6%) falls back to the hand-set `BLOWOUT_PRIOR = 0.33`.

**Both a GAP and a SUM of every strength measure, because the target is symmetric.**
`|margin| ≥ 15` is the same label for both team-games of a game, so pooled over the
dataset every game appears twice with the roles swapped and `corr(own_net_rating, y)`
is forced to equal `corr(opp_net_rating, y)` exactly — measured at −0.0166 for both,
which looks like a duplicated-column bug and is not one. The gap is the mismatch; the
sum is the quality level, which is a different question.

**Quality, out-of-fold, over 9,840 team-games:**

| | value |
|---|---:|
| base rate | 0.3264 |
| **AUC** | **0.5360** |
| Brier | 0.2209 |
| Brier of the constant base rate | 0.2199 |
| **Brier skill** | **−0.0045** |
| calibration slope / intercept | 0.601 / −0.177 |

**So the honest statement is that this feature set predicts blowouts essentially not at
all in Brier terms, with a small but real ranking signal.** The signal is visible where
you would look for it — the top decile of |as-of margin gap| contains **41.5%** blowouts
against **27.1%** in the bottom decile, and the shipped `blowout_prob` top decile
contains **43.0%** against 34.6% elsewhere — but a 1.24× lift on a base rate of 0.33 is
not enough to move a minutes model, and 15.6 confirms it did not.

**The incidental finding, which is worth more than the feature.** The first
implementation used the package's default `LGBM_PARAMS` (400 estimators, 31 leaves,
`min_child_samples` 50) for the blowout classifier, and shipped a `blowout_prob` with
**AUC 0.515, Brier skill −0.122 and calibration slope 0.057. Nothing failed.** The
dataset build succeeded, every leakage guard passed, and the column looked like a
probability. An inner-fold selection — a time-ordered 70/30 split of every team-game
strictly before 2024-12-01, i.e. inside the training window of every reported origin,
3,854 / 1,652 rows — found this:

| estimator | AUC | Brier | Brier skill |
|---|---:|---:|---:|
| constant base rate | 0.500 | 0.2217 | 0.0000 |
| **logistic (shipped)** | 0.516 | 0.2218 | **−0.0005** |
| LightGBM 150/7/150 | — | 0.2268 | −0.0205 |
| LightGBM 200/15/100 | — | 0.2331 | −0.0490 |
| **LightGBM 400/31/50 (`LGBM_PARAMS`)** | 0.465 | 0.2696 | **−0.2163** |

**`LGBM_PARAMS` is catastrophically wrong for a 9,840-row frame, by a factor nobody
would guess: 22% worse than a constant.** It was tuned for the 147,413-row player-game
frame; a team-game frame is fifteen times smaller and the same settings memorise it.
**Reusing a package default across a fifteen-fold change in sample size is the error**,
and it is recorded because it would otherwise have shipped silently and every
`blowout_prob`-derived number in this section would have been a statement about noise.
`config.BLOWOUT_MODEL_KIND` now names the logistic with the table above written next to
it, and `matchup.select_blowout_estimator` reruns the pass — *rerunning it is how the
choice is CHECKED, not how it is changed*, the same rule `select_rate_halflives`
operates under.

**Scored on Brier and not AUC**, deliberately: the column is consumed as a probability
and multiplied by `minutes_share`, so a well-ranked but badly calibrated probability
would put a systematic scale error into the interaction.

### 15.8 Which features carried signal

Mean split-gain share over all six origins, v4 feature set. The 21 candidate columns
take **5.84%** of the availability model's gain and **4.27%** of the minutes model's —
and the top of each table is unchanged from v3 (`games_since_last_app` 59.8% for
availability, `ewma_MIN` 58.0% for minutes), which is the correct shape: a game-context
family should not displace the player's own history.

| rank | availability | share | minutes | share |
|---|---|---:|---|---:|
| 1 | `team_games_remaining` | 0.587% | `team_games_remaining` | 0.757% |
| 2 | `team_win_pct` | 0.427% | `team_games_over_500` | 0.278% |
| 3 | `own_net_rating` | 0.424% | `opp_fta_allowed_per100` | 0.271% |
| 4 | `own_pace` | 0.391% | `opp_net_rating` | 0.269% |
| 5 | `opp_net_rating` / `opp_fta_allowed_per100` | 0.383% | `team_win_pct` | 0.267% |
| … | `blowout_x_minutes_share` | 0.334% | `blowout_prob` | 0.236% |
| … | `stakes_x_minutes_share` | 0.232% | `stakes_late_x_over500` | 0.228% |
| … | `top5_min_share_10` | 0.155% | `top5_min_share_10` | 0.094% |
| last | **`late_season`** | **0.0002%** | **`late_season`** | **0.0002%** |

**Read the first row and the last row together.** `team_games_remaining` is the single
largest new column in both models, and `late_season` — which is a deterministic step
function *of* `team_games_remaining` — carries essentially zero gain. The model did not
want a hand-drawn threshold at 15 games; it wanted the continuous variable and it cut it
where it liked. **`late_season` should not exist in a v5**, and neither should
`stakes_lockedness` as a *gate* on the interactions: the interactions
(`stakes_x_minutes_share` 0.232% for availability, `stakes_late_x_over500` 0.228% for
minutes) do carry signal, but the lockedness column itself is near the bottom (0.049% /
0.056%), which says the composite was doing less work than its two ingredients would
have separately.

**And section 5.2's general conclusion applies again: split gain is an allocation
statement, not a value statement.** 5.84% of gain co-exists with a +0.66% Brier
improvement. The stakes family's *value* claim rests on 15.6's cohort table, not on this
one.

### 15.9 Leakage tests

**57 new tests in `tests/test_matchup_v4.py`.** Suite total **575** (518 before this
phase); `ml/experiments/production_tournament` is untouched at **70**. The six that
carry the argument:

1. **The freeze, asserted from this side too.** `FEATURE_COLS` is 51 columns, its
   sha256 is still `914cdc17…`, `FEATURE_VERSION` is `v3`, `FEATURE_SETS["v1"]` and
   `["v3-honest"]` are byte-identical, and `TARGET_COLS ∩ FEATURE_COLS_V4 = ∅`. A
   candidate feature set is the single most likely thing to break the freeze by
   accident, because the natural way to write one is to append to `FEATURE_COLS`.
2. **Box-score flip invariance, with a mandatory counter-assertion.** One mid-season
   game's box score is replaced with absurd values on both sides; **every rolling and
   stakes feature on that game's own two rows must be bit-identical**, and *later* rows
   must move. Without the second half, a construction where the flip silently failed to
   take effect would pass. This is the v2 defect (section 11) transplanted to team level
   and the opponent's target-game box score is sitting one merge away.
3. **A hand-computed rolling window plus an unshifted negative control.** The context
   column at row *k* must equal the mean of the raw per-game pace over rows
   [*k*−15, *k*−1], null before `min_periods`; and the leaky twin
   (`rolling().mean()` with no `shift(1)`) is built in the test and required to
   **differ**. "The first row is null" passes against an unshifted window as soon as
   `min_periods` is 1, so it is not a shift test.
4. **The peeked blowout control.** `cross_fit_blowout_probabilities(peek=True)` fits one
   model on every row including its own outcome; its Brier must be **at least 5% better**
   than the out-of-fold one and its AUC strictly higher. A margin rather than mere
   inequality is what makes it detect a vacuous test. Plus a truncation test: deleting
   every game from a month onward must not change any earlier row's probability, which is
   the strongest available statement of forward chaining.
5. **The stakes record cannot see the target game's result.** A game's winner is flipped
   in a synthetic three-game season; that row's `team_wins_to_date` must be unchanged and
   the *next* row's must move. Plus the lockedness formula against an 80-game hand-built
   60-20 record, where the arithmetic is checkable by addition.
6. **The start-rate proxy gets the same pair.** It is the one candidate column computed
   directly from the target game's own minutes (step 1 ranks that game's appearances), so
   its safety rests on a single `shift(1)`: an unshifted twin is built and required to
   differ, and a bench player is given 48 minutes in one game with the requirement that
   his start rate on *that* row is unchanged and on later rows is not. The victim is
   chosen from rows currently OUTSIDE the top-5 on purpose — flipping a player who
   already leads his team in minutes changes no label, and the counter-assertion would
   then fail for a reason unrelated to leakage.

Also pinned: the possession formula including its OREB-free deviation (the deviation is
the thing most likely to be silently "fixed" by someone who remembers the textbook);
that matchup rest days equal `features.schedule_features`' rest days, because the
definition is written twice and two definitions of one quantity is how a package
acquires a number that is right in one table and wrong in another; that
`opp_def_rating` on team A's row is team B's shifted rating and **not** A's own; that
the blowout label is symmetric and the signed margins sum to zero; that a thin block
falls back to the prior rather than to a model; that the pregame model raises if an
outcome column is added to its feature list; that a quantile cohort over a *constant*
column is **skipped** rather than reported as a 100%-of-rows duplicate of ALL; and both
bootstrap nulls — two identical passes must not promote, and a uniform 20% improvement
must.

### 15.10 Deviations, with justification

- **`ORIGINS` was not extended; `DEV_ORIGINS` was added.** The task allowed either;
  `tests/test_teammates_v3.py:918` pins `len(ORIGINS) == 5`, so the extra origin is in a
  separate list and the P2 bracket runs on it. 15.3.
- **The late-season origin is 2025, not 2026.** Reasoned in 15.3: the 2026 window is
  inside the selection holdout.
- **`evaluate.py --bracket` was run with `--no-rate-ladder`.** Four feature-set passes
  (v1 / v3-honest / v2-oracle / v4) over six origins with the full 11-stat rate ladder
  did not fit the phase's wall-clock budget. The rate ladder is a per-stat estimator
  question that this feature-set change does not bear on, and the ladder's headline
  numbers (availability Brier, minutes MAE, unconditional PTS) are unaffected by the
  flag. Composition parity still ran and passed: −1.44% against the previous
  composition, i.e. the promoted composition is *better*, well inside the 1% regression
  tolerance.
- **`run_p2_bracket.py` exists rather than the decision being computed inside
  `evaluate.py`.** A paired bootstrap needs **per-row** losses from both passes with row
  identity intact; `evaluate.py` aggregates to per-cohort means over origins. The new
  script fits only the promoted path, twice per origin, and derives the pooled numbers,
  the per-origin table, the cohort tables *and* the bootstrap from one per-row frame — so
  the cohort table cannot disagree with the bootstrap, because both are aggregations of
  the same loss array.
- **The bootstrap is loaded from `ml/experiments/production_tournament/bootstrap.py` by
  file path.** That module is the frozen convention (the instrument PTS/AST were closed
  under, which 13.6 names as this phase's precedent), `ml/experiments/` is read-only in
  this phase, and a second implementation would be a second set of edge cases in the
  block-sum arithmetic. `importlib` by path rather than a `sys.path` insertion because
  `fnba_ml` is imported from several working directories.
- **`build_v4_dataset.py` backfills rather than `build_dataset.py` rebuilding.** The dev
  database holds `player_game_status` for 2024-25 onward only (74,870 rows against the
  truth layer's 147,565) and `nba_schedule` likewise, so a full rebuild there would have
  silently produced a two-season dataset and every number would have been incomparable
  with every number in this document. `team_game_logs` **is** complete in dev (2,460 rows
  per season, all four), so the backfill reads the existing 147,413-row dataset and
  decorates it. Both paths call the same `matchup.attach_v4_features`, so a backfilled
  column and a freshly built one are the same number by construction.
- **`TEAM_LOGS_SQL` no longer joins `nba_schedule`.** It was reading two tables to get
  one table's own `season` / `season_type` / `game_date`, and where `nba_schedule` is
  incompletely backfilled the INNER join dropped every team-game of the earlier seasons.
  On a complete database the two forms return identical rows. `FG3A` was added to the
  same query and is **optional** downstream, so a parquet directory that predates it
  yields a null `opp_fg3a_allowed_per100` and a warning rather than a failure.
- **`FG3A` was added to the test fixtures, derived and not drawn.** `fg3a = min(max(fg3m,
  fg3m/0.36), fga)` makes **no rng call**, so every previously recorded fixture number in
  the repo is byte-identical — the same technique the FGM addition used in section 12.1.
- **No hyperparameter grid was reduced except the blowout classifier's**, which was
  selected on inner folds over four LightGBM configurations plus a logistic (15.7). Pace
  window (15) and `min_periods` (5) are hand-set on the same reasoning as
  `OPP_FORM_WINDOW`, and a window selected on validation rows would be selecting on the
  thing being reported. No origin and no row count was reduced anywhere.

### 15.11 What this means for opening night, and what a v5 should do

**For opening night: nothing changes.** `prospective_2026_27_v1` is intact, artifact
`20260818` is what serves, `FEATURE_COLS` is the same 51 columns, and the ten
falsification rows in 13.5 stand unmodified. The candidate columns *are* on
`data/dataset_v4.parquet` and `build_dataset.py` computes them by default (additively —
`FEATURE_COLS` names none of them, so `available_features` returns the same 51 names),
which costs one extra LightGBM cross-fit over 9,840 team-games and makes a future
re-evaluation a scoring run rather than a rebuild. `--no-v4-candidate` reproduces the
exact pre-P2 column set.

**The one thing this phase changes about how the prospective test will be read.** F2/F3
concern teammate context and are unaffected. But 15.5's per-origin table says the
game-context effect is **three times larger in March–April than in December–January**,
and 13.6's look schedule is Dec 1 / All-Star / season end. A v5 evaluated at the
season-end look will see its best month; one evaluated at Dec 1 will see its worst. That
is a fact about the effect, not about the protocol, and recording it now means a
December reading of "v5 bought nothing" cannot be presented in April as evidence of
anything — the mirror image of 13.5's note on the F2/F3/F4 Dec-1 tripwires.

**What a v5 should do differently**, in order of expected value:

1. **Drop the blowout family.** AUC 0.536, no Brier skill, and exactly zero effect on the
   cohort it defines (15.6). The inputs available at forecast time — 15-game rolling net
   ratings — are too noisy a strength measure, and the thing that would fix it is not a
   better model but better information: a market line, or a lineup-aware strength
   estimate. Until one exists, three columns and a cross-fit are buying nothing.
2. **Drop `late_season` and `stakes_lockedness` as columns and keep their interactions.**
   15.8: the model wanted `team_games_remaining` continuous and cut it where it liked;
   the hand-drawn 15-game threshold carries 0.0002% of gain.
3. **Take the stakes family seriously and scope it to where it works.** −1.91%
   availability Brier on the stakes-flagged cohort and −1.55% on the whole late-season
   origin are the two largest numbers in this section. A v5 that shipped the stakes
   family *alone* — five or six columns instead of twenty-one — would face a far smaller
   dilution penalty on the pooled endpoint, and dilution is the most likely reason a
   real effect measured at −1.91% on 4,755 rows landed at +0.66% on 37,596.
4. **Fix the OREB gap upstream first if the pace family is to be revisited.** The pace
   columns carry gain (0.39% / 0.22%) and no measurable value; a possession count that is
   12% too high is not the obvious reason, but it is a reason that can be removed rather
   than argued about.
5. **Do not re-score v4.** 13.6: one look per feature version. This was it.

## 16. Ops automation: the daily publishing run (2026-08-19)

**What changed and what did not.** Nothing in the model changed. `FEATURE_COLS` is the
same 51 columns with the same `914cdc17…` digest, `FEATURE_VERSION` is `v3`, artifact
`20260818` is byte-identical, and `tests/test_prospective_freeze.py` is green. What
landed is the thing section 13.8 committed to and did not build: a scheduled process
that actually serves the commitment. **13.8.1 says "a prediction run at the `gameday`
horizon before every slate, best effort" — until now the best effort was a human
remembering to run three scripts in the right order with the right flags.**

### 16.1 The two files

`ml/daily_run.py` is one idempotent command that performs the whole publishing
pipeline in seven named phases: verify the pinned artifact against the freeze, compute
the window, read the slate, rebuild the historical dataset from the truth layer, build
prospective rows for the window, read the latest injury designations, and score and
write. `.github/workflows/predictions.yml` runs it once a day at 16:00 UTC and does
nothing else. It is the same shape as `.github/workflows/scraper.yml`, which has been
reaching `nba_api` from a GitHub runner on a six-hour cron since May.

**The driver computes nothing the three scripts it drives do not already compute.**
`build_dataset.py`, `fnba_ml.prospective` and `predict.py` are called with arguments,
not reimplemented. That is deliberate: an ops wrapper that grows its own feature logic
is a second serving path, and a second serving path is a second thing that can disagree
with the freeze.

### 16.2 The four decisions that are genuinely new

Everything else in the driver is sequencing. These four are judgements, they are pure
functions at the top of `daily_run.py`, and `tests/test_daily_run.py` is about them
because each can be wrong in a way that produces a plausible-looking run rather than an
error.

**1. The window is Eastern, and that is not a detail.** `nba_schedule.game_date` is an
Eastern calendar date; a 9:30pm ET tip on 2026-10-20 carries
`scheduled_at = 2026-10-21 01:30Z`. A window computed in UTC would, for five hours
every evening, ask for tomorrow's slate — and would then publish it, at a measured
horizon of 27 hours, under a `gameday` label. The window is `[today, today + N - 1]` in
`America/New_York` with `N = 2` by default: tonight, plus tomorrow so that one missed
cron does not lose a night. **It always extends forward. 13.8.2 forbids backfilling a
missed slate, so the arithmetic cannot express a backward window.**

**2. Staleness is a warning, never a refusal.** The truth layer's lag is
`max(nba_schedule.game_date before the window) − max(player_game_logs.game_date)`. Over
three days — the scraper runs every six hours, so a one-day gap is an ordinary
overnight and a four-day gap is a broken scrape — the run logs it loudly, **writes the
message into `prediction_runs.notes`, and serves anyway.** Friday's form is a worse
projection for Monday than Sunday's would have been and a much better one than an empty
page; and a stale run has to be identifiable as stale from the store alone, in April,
by someone who was not reading the logs that morning.

**3. The prospective label is a claim about seven things at once.** 13.8.4 makes the
label the *definition* of what counts, so `prospective_conditions()` returns the
reasons a run does not qualify and the note says which: the season is `2026-27` (read
from `PROSPECTIVE_2026_27["season"]`, not typed out again), the season type is Regular
Season, the horizon is `PROSPECTIVE_SERVING_HORIZON`, the model is
`PROSPECTIVE_MODEL_VERSION`, the feature version is `PROSPECTIVE_FEATURE_VERSION`, the
universe is `prospective` rather than the biased approximation, and the pinned
checksums verified. A qualifying run's note is 13.4's verbatim
`prospective_2026_27_v1; feature_set=v3-honest; shadow=false`. **A disqualified run's
note must not contain the label anywhere** — a look report will select the season by
substring, and `NOT prospective_2026_27_v1` would be selected by it — so `run_notes()`
raises rather than emit one. A reworded reason string is the failure mode that
assertion exists for.

**4. The post-tipoff filter is here because it can only be here.** 13.8.2: *"a
prediction made after tipoff is never inserted into the store, under any circumstance,
for any reason."* `predict.py` has no notion of a tip time, so the rule is enforced in
the driver, and it is applied **twice** — once when the slate is chosen and once against
the frame that is about to be scored, because the phases in between take minutes and a
7pm tip does not wait for them. The comparison is `tip > now` strictly: a prediction
made *at* the tip is not before it. A game whose tip cannot be computed at all is
dropped, and the `GAME_DATE + 00:00 UTC` fallback `predict.NOMINAL_TIP_HOUR_UTC`
supplies is *earlier* than almost every real tip, so an unknown tip errs toward
dropping rather than publishing. **Games dropped this way are counted in the log and in
the run summary. They are a recorded missed slate (13.8.2), not a backlog.**

### 16.3 Why 16:00 UTC (moved from 21:00 UTC, 2026-08-24)

Noon EDT / 11am EST. Every tip from 3pm through 10:30pm ET is 3–11.5 hours out at that
instant, so the **whole slate** — the afternoon games included — is served inside the
`gameday` bucket of `(2, 12]` hours before tipoff. The rare noon/1pm ET holiday tips
land in the `lock` bucket `(0, 2]`: still published before tip, recorded off-gameday by
the horizon facts.

The original 21:00 UTC (5pm EDT / 4pm EST) schedule sat 2–6 hours before evening tips
and after the league's 5pm-local initial participation report deadline for the
following day — fresher information — but a 3pm ET game had already tipped at that
hour and the driver dropped it, a real, small, *stated* gap in 13.8.1's "before every
slate". The honest options were a second earlier cron, accepting the gap, or moving
the single run earlier; a second cron would put two gameday-horizon runs on the same
slate and make every per-slate aggregate ambiguous, so the run moved instead. **The
price, stated just as plainly: evening games are now served 7–11.5 hours before tip
rather than 2–6, so injury news that breaks in the afternoon lands after publication,
and the day-ahead half of the two-day window is built before the following day's
initial participation reports exist.** Both halves of the tradeoff serve at `gameday`;
13.8.1's commitment is unchanged and no frozen value moved (the cron instant is an ops
detail, not a protocol value — the horizon bucket is the protocol value).

### 16.4 The offseason no-op, and what red means

**No game in the window means exit 0 having written nothing.** No empty run row, no
placeholder. That is the expected result on most days of the year, and it matters
because a cron that goes red every morning from June to October is a cron whose red is
worth nothing by November. **A red run is the alert**: any phase failure exits non-zero
naming the phase, so the failed-workflow notification points at the database or at the
artifact rather than at this document. There is no retry, and re-running after tipoff
will correctly refuse to publish the games it missed.

Two more guardrails worth naming. `timeout-minutes: 30` — the dataset rebuild fits one
LightGBM base-availability model per calendar block and then builds features once per
game date, which runs in single-digit minutes; the cap exists because CI grew timeouts
on 2026-08-18 after a stalled step sat "in progress" for six hours. And a
`concurrency: predictions` group with `cancel-in-progress: false` — the store is
append-only so two overlapping runs would both *succeed*, leaving one slate carrying
two runs at the same information boundary, which makes every per-slate aggregate
ambiguous. The run already underway holds the earlier and therefore correct boundary,
so a manual dispatch queues behind it instead of killing it.

### 16.5 Python 3.14, not the scraper's 3.12

`predictions.yml` installs `ml/requirements.txt` on Python 3.14 where `scraper.yml`
uses 3.12, and the reason is the freeze. The daily run **unpickles the frozen serving
artifact** — joblib pickles of scikit-learn 1.9.0 and LightGBM 4.7.0 estimators whose
sha256 digests are pinned in `PROSPECTIVE_ARTIFACT_CHECKSUMS` and must not be
regenerated (13.2). Python 3.12 cannot install those versions at all: the newest cp312
wheels are pandas 2.3.2 and scikit-learn 1.7.2. A 3.12 job would therefore install an
*older* scikit-learn and then be handed a *newer* pickle, which is the direction
scikit-learn does not support. **Matching the artifact's own library versions is the
constraint; the interpreter version follows from it**, and every pin has a cp314
`manylinux_2_28_x86_64` wheel so nothing on the runner builds from source.

### 16.6 What this does not do

- **No shadow run.** 13.4 requires the `v1` comparator at the same boundary on the same
  slate. It is one more `predict.py` invocation with a different feature set and a
  `shadow=true` note, and it is not wired up here. Until it is, the comparison ladder's
  rung (c) has no prospective data.
- **No look reports.** 13.6's three looks and 13.5's falsification table still have to
  be computed by hand from the store.
- **No alerting beyond a red workflow run.** Adequate while the author is the only
  consumer; not adequate once the projections page has users.
- **It has never run against production.** Both demonstrated runs were `--dry-run`
  against the dev branch, whose `nba_schedule` holds only two of the four seasons prod
  holds (2024-25 and 2025-26, so 74,718 played-universe rows against prod's ~147,000).
  The pipeline is season-count agnostic — the career-scoped as-of joins simply have
  less history to work with — but **the first production run will be the first time
  this code sees four seasons and a live `secrets.DATABASE_URL`**, and it should be
  triggered by hand with `dry_run: true` before October rather than being met for the
  first time by a cron.

### 16.7 The extended run: seven days, preseason included

The app serves only the **newest complete run**, and a manager needs predictions for
every game in the next seven days, preseason included. The prospective run covers two
days of Regular Season games, so `daily_run.py` publishes a second run, built from the
same schedule, universe, feature frame and `statuses_as_of`, covering today through
today + 6 Eastern (`--extended-days`, default 7) over both `Pre Season` and
`Regular Season`. Values for a given game match between the two runs.

- **Order is A then B.** Run A is the frozen prospective run, unchanged: `--window-days`
  window, Regular Season only, `gameday`, the 13.4 note. It is skipped (not a failure)
  when the window holds no Regular Season game, e.g. all of preseason. Run B is
  published last so it is the run the app shows.
- **B is NOT PROSPECTIVE by construction.** Its notes start `NOT PROSPECTIVE (extended
  N-day serving window; ...)` and go through the same `run_notes` assertion, so they
  can never contain `prospective_2026_27_v1`; look reports select on that label and
  therefore exclude B. It carries no `horizon=` token (`predict.py --horizon none`)
  because games one to seven days out have no honest single bucket.
- **Preseason predictions are out of distribution.** The model is trained on Regular
  Season games, and starters play far fewer preseason minutes than their rolling form
  implies. Treat them as informational. Run B replaces their minutes with a tier
  prior (20.6).
- **Known caveat, unchanged:** the prospective two-day run labels tomorrow's games
  `gameday` although their measured offset is roughly 24-36 hours. The label is part
  of the freeze and is not touched here.

## 17. Phase 0 correctness and the `prospective_2026_27_v2` re-freeze (2026-10-01)

**Section 13 is not edited.** Its text is the `v1` pre-registration and stays on the
record as written. This section is the 13.2 re-freeze: it names what moved, what did
not, and the two places where 13's wording is superseded (17.4, 17.5). Everything in
13 that this section does not amend binds `v2` exactly as it bound `v1`.

### 17.1 The verdict first

**`v1` ended before it began.** No Regular Season slate has ever been scored under
`v1`. Run A (16.7) only exists when the window holds a Regular Season game, and the
first one is on 2026-10-20, so every run published so far is a run B: the extended
seven-day window over preseason games, whose notes start `NOT PROSPECTIVE` and can
never contain the label. The store holds zero rows a look report would select.

So `v2` is a **clean start, not a mid-test change.** 13.2's rule is that a change
made after opening night ends the test; a change made before it re-freezes the test,
and this one lands 19 days early. There is no `v1` data to splice, discard or explain
away. The bump is required because two of the fixes below (1 and 2) change an emitted
number, which 13.2's last paragraph classes as a change to what is served.

### 17.2 The seven defects

**1. A recovered player stayed out forever.** The override layer took each player's
newest report known at the boundary, with no age limit, and the CBS feed signals a
recovery by *dropping the player from the page*, which wrote nothing. A player listed
OUT in March was served at `P(play) = 0.02` in April. Found by an external code review on 2026-09-30 and
verified against the code: the history table is append-only, so the absence of a row was the
only clearance signal, and the model cannot read an absence. Fixed twice over. The scraper now appends a `cleared` row for every player
who was listed last scrape and is not listed now, and a failed fetch or a page that
parses to zero rows writes nothing at all (an empty page must never read as "everyone
is healthy"). `overrides.REPORT_MAX_AGE_HOURS = 72.0` drops any report captured more
than 72 hours before `as_of`, and `cleared` joins `PASSTHROUGH_STATUSES`, so a newer
clearance supersedes an older OUT and the model stands. **Moves:** `prob_active` for
every player whose last designation is stale or superseded, and, because the layer
also runs on the base probabilities before the teammate sums (13.1), the
expected-context features and `prob_active_model` of his teammates. Tested in
`test_overrides.py` (a four-day-old OUT has expired, a one-day-old one applies, a
newer clearance wins, `max_age_hours=None` and a missing boundary disable expiry) and
in `scraper/test_truth_layer.py` (clearance rows, failed and empty scrapes write
nothing, dry run writes nothing).

**2. A future back-to-back did not read as one.** `build_prospective_features` builds
one future date at a time so no future outcome can reach another future row (the
section 14 rule). That rule was also applied to the *schedule*, which is not an
outcome: a Nov 4 row built after a known Nov 3 game read its rest from the last
*played* game, so the second night of a back-to-back carried `IS_B2B = 0` and a rest
count one or more days too long. Found by the same external review, which reproduced it
on a synthetic Nov 3 / Nov 4 pair; run B's seven-day windows made it routine. Now every per-date build is handed the whole
known schedule (`prospective.known_schedule`: the played history plus both sides of
every future team-game, so an opponent with no rostered player still has rest days),
and `TEAM_REST_DAYS`, `IS_B2B`, `OPP_REST_DAYS` and `DEF_FORM` / `OPP_DEF_FORM` are
read from it. Every outcome-derived column still sees exactly one future date.
**Moves:** every row after the first scored day for a team that plays earlier in the
window; on run A that is tomorrow's half of the two-day window, on run B most of it.
`build_features(schedule=None)` is pinned to the old computation, so training and the
pinned artifact are untouched. Tested in `test_prospective.py::TestScheduleIsReadWhole`
and `test_features.py`.

**3. `forecast_cutoff_at` understated what the run saw.** It was stored as midnight
of the window's first date, while the run read injury reports through
`statuses_as_of`, about 16:00 UTC that day. A column documented as the information
boundary claimed sixteen hours less visibility than the run had. Now
`forecast_cutoff_at = max(start of first scored day, information_as_of)`, and the two
inputs are stored separately in new columns `information_as_of` and
`history_through` (the last game date whose outcomes fed the features). **Moves:** no
emitted number; provenance only. Tested in `test_predictions.py` and
`test_daily_run.py::TestPredictArgv`.

**4. Nothing but a note kept a shadow run off the app.** 13.4 promised that "a shadow
never reaches the app" and made the note the separator, but every backend reader took
the newest complete run regardless of its note. The first shadow written to the store
would have been served. `prediction_runs.channel` (`production` | `shadow`, migration
015, default `production`) is now written on every run, and every backend reader
filters `channel = 'production'`. **Moves:** no emitted number. Tested in the backend
`predictions`, `playerPredictions`, `slate`, `watchlist` and `aiContext` suites, and
in `test_predictions.py` for the store record.

**5. The look reports had no tool.** 16.6 recorded that 13.6's looks "still have to
be computed by hand from the store", which for a season of append-only long-format
rows is a way of guaranteeing a different computation at each look. New pure module
`fnba_ml/scoring.py` and read-only loader `score_runs.py` score stored runs against
completed games (17.5). **Moves:** nothing; it only reads. Tested in
`test_scoring.py`.

**6. `availability_risk` documented the wrong bound.** The weekly docstring called
`1 - prod(prob_active)` a lower bound on the risk of missing a game. Absences are
positively correlated, which raises `P(plays every game)` above the product, so it is
an *upper* bound. Docstring only, plus a test at perfect dependence in
`test_weekly.py`. Not protocol-relevant.

**7. The betting board invented prices.** Outside the ML package, recorded because it
shares the store's consumers. ESPN spread and total markets with no published price
were filled with `-110`, a number nobody quoted. They are now null, and the edge on a
pick is computed against the no-vig implied probability of the two priced sides and
labelled a Claude estimate; a pick whose price is missing is dropped. The ESPN event
id is now named `espn_event_id` rather than `nba_game_id`, because it is not one.
Tested in `oddsParsing`, `oddsMath`, `betting` and `BettingPicksPanel`. Not
protocol-relevant.

### 17.3 What did not change

- **Artifact `20260818`**, byte for byte. All six pinned checksums in 13.1 are
  unchanged and `test_prospective_freeze.py` recomputes them from disk.
- **`FEATURE_COLS`**: 51 columns, digest `914cdc17…`. Fix 2 changes how three
  schedule columns are *computed* for future rows, not which columns exist.
- **Champions, halflives, estimators, rate targets, coherence constraints**, exactly
  as in 13.1.
- **The `StatusPolicy` constants**: 0.02, 0.10, 0.6 / 0.60, 0.85 / 0.15.
  `UNAVAILABLE_STATUSES` is unchanged.
- **Horizons, the serving horizon (`gameday`), the nine cohorts, the cold-start window,
  the October gate, the three look dates and row minimums, and every row of the
  falsification table.** The thresholds were derived from retrospective block
  variance (13.5), which none of these fixes touch.

The frozen additions are two: `PROSPECTIVE_REPORT_MAX_AGE_HOURS = 72.0` and
`PROSPECTIVE_PASSTHROUGH_STATUSES = {available, day_to_day, cleared, unknown}`, both
hand-copied literals in `frozen.py` and both asserted against `overrides`. The 72
hours is a hand-set constant in the same class as the five in `StatusPolicy`, with
the same status: unmeasured, and frozen so it cannot drift during the season.

### 17.4 Amendment to 13.4: the channel is the separator

The run note's `shadow=` token is replaced by `channel=<production|shadow>`. A
qualifying served run carries `prospective_2026_27_v2; feature_set=v3-honest;
channel=production`, and a shadow carries `...; feature_set=v1; channel=shadow`.
`daily_run.py` derives the token from the same constant it writes to the column, so
the two cannot disagree.

**The store's `channel` column is the authoritative separator; the note is a
readable copy.** 13.4's "the label is what separates them" is superseded. A shadow
run must be written with `predict.py --channel shadow`; a run written without it is a
production run whatever its note says, and the app will serve it. `scoring.py` falls
back to the note tokens only where the column is absent (015 not yet applied). Everything else in
13.4 stands: same cutoff, same slate, same boundary, same override layer, one table.

### 17.5 Ops: migration 015 and the scoring tool

**Migration 015 must be applied to prod and to the dev branch before the first run of
this code.** The run insert names the three new columns, so `--write-db` fails without
them, and the backend readers filter on `channel`. It is applied by hand in the Neon
SQL editor and recorded in `schema_migrations`, as 014 was. The 16.6 advice holds with
more force: trigger the first production run by hand with `dry_run: true` before
opening night.

**Look reports are produced by `score_runs.py`** (13.8.6's markdown plus csv), scored
per run and pooled per (channel, prospective label), keeping each player-game's latest
pre-tip forecast once. It computes E1 Brier on both `prob_active` and
`prob_active_model`, the override increment (F10), E2 slope and intercept (F9), E3,
E4 for every stat, P10 to P90 coverage, and coverage misses, each split by
`cold_start` and season type. **It now also computes** (Phase 3 package D) E1 skill
against the shifted appearance rate (F1), F5, E5 (F6), the `ewma_state`-seeded rate
families behind F7 and F8, F2 to F4 from paired served and v1 shadow runs, and the
tier and vacated-minutes cohorts of 13.3, all rebuilt as of each game's date from
the truth layer, with `--look` adding the falsification table; **it cannot compute**
the `star_out = 1` cohort, which needs `usg_ewma` from team box totals, and F2 to F4
until a shadow run exists, and a look report names every row it could not compute
rather than leaving it blank.

### 17.6 Open items this phase did not do

- **ESPN to NBA game mapping.** Odds are keyed by ESPN event id and nothing joins them
  to `nba_schedule`, so odds cannot be joined to the model's predictions.
- **Odds history.** Odds are cached, not stored, so no line movement or closing-line
  comparison exists.
- **`matchup.py` computes rest days a second time** from its own team-game pairing.
  It is the v4 candidate family and not served, but if promoted it must read the same
  known schedule as fix 2 or it reintroduces the defect.
- **Learning the override constants.** All six (five `StatusPolicy` values and the
  72-hour expiry) are still hand-set. `player_injury_reports` now holds clearance rows,
  which is the history a learned policy would need; F10 is the measurement.
- **E5 in the scoring tool**, with the shifted-rate skill and the frozen-baseline rows
  above. These are needed by the `dec1` look, where F1 and F6 bind.

### 17.7 Rung (c) is wired: the daily v1 shadow

16.6's "no shadow run" is closed. Section 13 is unchanged; this records how its
rung (c) is now executed.

- **The artifact.** `train.py --feature-set v1 --version 20260818` writes
  `models/20260818-v1/`: availability and minutes models on the 36
  `BASE_FEATURE_COLS`, the same `ewma_state` snapshot logic, `feature_set: v1` in
  `metadata.json` and the registry entry. With no `--cutoff` it inherits the
  cutoff of `models/20260818/metadata.json` (2026-04-13), and it is fitted from
  the same dataset build as everything else in that run. Stages 1-3 of the
  two-stage pipeline are skipped and recorded as skipped: v1 has no teammate
  context, so there is no base model and `predict.py` skips `rebuild_context`.
  `models/20260818/` is not touched.
- **The run.** `daily_run.py --shadow-feature-set v1` publishes the shadow right
  after run A from the same prospective frame, `statuses_as_of`, window and
  horizon, with `predict.py --channel shadow`. Its note is
  `prospective_2026_27_v2; feature_set=v1; channel=shadow` when run A qualifies:
  a shadow at the served boundary is a qualifying prospective run, it is just not
  served. It is `NOT PROSPECTIVE (...)` if run A is, or if its own artifact names
  another feature set, has another cutoff, or fails its registry checksums. The
  same row-level override layer applies; the context-stage override has nothing
  to act on in v1.
- **The served run is untouched.** Its argv, notes and code path are
  byte-identical with or without the shadow. A missing shadow artifact is a
  warning and a skip. A failed shadow is logged, run B still publishes, and the
  job exits 1 afterwards. Run B gets no shadow.
- **Not yet live.** The workflow passes `--shadow-feature-set v1`, but the
  artifact must be trained (ML Evaluate, `train_shadow`, against prod), committed
  with its registry entry and checked with `fnba_ml.registry.verify_artifacts`.
  Until then rung (c), and F2 to F4 in `score_runs.py`, still have no data.

### 17.8 Active-DNP rows enter `player_game_status` (the P5 gap, partly closed)

The box-details pass (`boxscoretraditionalv3`, one request per game, both the
`--backfill-box-details` backfill and the live hook in the game-status phase) now
INSERTs a status row for every v3 player row with no minutes that has no
`player_game_status` row for that game: `rostered` true, `played` false, `started`
false, `dnp_reason` the verbatim box-score comment, `source = 'boxscoretraditionalv3'`.
`listed_inactive` is false, except NULL when the comment starts `DND` or `NWT`, since
those are injury and not-with-team designations the inactive list owns. The insert is
`ON CONFLICT DO NOTHING`, so an inactive-list or game-log row always wins. Each run's
notes record `active_dnp_rows=N`.

**This changes the training universe.** These are exactly the "available and unused"
rows 4.1 says the reconstruction cannot see, so once the backfill has run, the
complement identity in 4.1 stops holding by construction (there is now a played-false,
listed-inactive-false category), the availability base rate falls, and the
vacated-resource sums change. Any retrain or dataset rebuild on a backfilled database
is therefore a 13.2 item (7) change (the universe source) and requires a re-freeze to
`prospective_2026_27_v3` before it may be served. The `v2` artifact is not retrained by
this change, and nothing it serves moves until someone rebuilds against the new rows.

**The magnitude is measured before deciding.** `build_dataset.py` now prints the
universe composition (`universe.universe_composition`): row counts by
(`PLAYED`, `LISTED_INACTIVE`) and the count of rows whose `STATUS_SOURCE` is
`boxscoretraditionalv3`. That printout, run against the backfilled database, is the
number that decides whether the re-freeze is worth taking. It does not close [P5]: the
rows come from the same box score the appearances do, so they extend the
reconstruction rather than audit it against an independent roster source. Games whose
player rows were already stamped `details_fetched_at` before this change are not
revisited by the backfill and gain no active-DNP rows until those stamps are cleared.

## 18. Phase 2 challengers (2026-10-01): built, pre-registered, none served

**Verdict first: everything Phase 2 built is a challenger or a serving option that is
off by default, and none of it has been measured on real data.**
`prospective_2026_27_v2` serves exactly as section 17 froze it: artifact `20260818`,
the 51 `FEATURE_COLS` (digest `914cdc17…`), the 13.1 champions, and a `daily_run.py`
that passes no Phase 2 flag except `--shadow-feature-set v1`, whose run is written
`channel = 'shadow'` and never read by the app (17.4).
`tests/test_prospective_freeze.py` is green. Sections 13 and 17 are not edited.

### 18.1 What Phase 2 is and is not

| Item | Kind | Switch (default) | Measured by |
|---|---|---|---|
| serving coherence | serving option | `predict.py --coherence` (`none`) | `report_coherence.py` |
| scenario serving | serving option | `predict.py --scenarios` (off) | its audit parquet only (18.3d) |
| `v5-stakes` | feature-set challenger | `FEATURE_SETS["v5-stakes"]` (not served) | `run_p3_bracket.py` |
| residual rate | estimator challenger | `rate_model.py` (not served) | `run_p3_bracket.py` |
| count models | estimator challenger | `count_model.py` (not served) | `report_counts.py` |
| tiered intervals | interval challenger | `train.py --tiered-quantiles` (off) | `report_counts.py` |
| v1 shadow | ladder rung (c) | `daily_run.py --shadow-feature-set v1` | `score_runs.py` (17.7) |
| active-DNP rows | data | scraper box-details pass | `build_dataset.py` printout (17.8) |

**Nothing here has a number yet.** This machine has no database, so every figure in
this section comes from a synthetic test fixture and says how the code behaves, not
what the challenger is worth. The measurement path is the ML Evaluate workflow's
dataset build against prod, then `run_p3_bracket.py`, `report_coherence.py` and
`report_counts.py` on that build. None of the three is a workflow step yet, and the
workflow does not upload parquet, so a look is either a new step or a local run with
`DATABASE_URL`. **One look per candidate, per 13.6**, as v4 got in 15.1: a result
that disappoints is the result, and a fix is a new version with its own look.

### 18.2 The pre-registered bars, quoted before any result exists

From `config`'s P3 block, committed in `3574289` before `run_p3_bracket.py` had run:

```
P3_PROMOTION_FLOOR: float = 0.01
P3_COHORT_REGRESSION_TOLERANCE: float = 0.01
P3_V5_GATED_ENDPOINTS: tuple[str, ...] = ("availability_brier", "minutes_mae")
P3_RATE_GATED_ENDPOINTS: tuple[str, ...] = ("cond_pts_mae", "uncond_pts_mae")
```

The rule is P2's (15.1), applied by the same `promotion.decide`: a paired 7-day
moving-block bootstrap over game dates (2,000 replicates, the tournament's
`bootstrap.py` loaded by path) on `DEV_ORIGINS` (the five `ORIGINS` plus the March
2025 late-season origin, 15.3); a **95% CI excluding zero AND at least 1.0% pooled
relative improvement on at least one gated endpoint, AND no cohort of a gated endpoint
regressing by more than 1.0%**. 1% and not 2% for the reason 15.1 gives: `v5-stakes`
is a feature-set change to an existing champion, and the rate is a correction to the
champion rate rather than a new model class in its place.

| Candidate | Gates | Reported only |
|---|---|---|
| `v5-stakes` | availability Brier, minutes MAE | unconditional PTS MAE |
| residual rate | conditional PTS MAE, unconditional PTS MAE | AST, REB, FGA (both forms), clip rates, coherence endpoints |
| serving coherence | none: report-only | unconditional PTS and MIN MAE, conditional MIN MAE, by cohort; the raw minute-sum diagnostic |
| count models | none: report-only | conditional and unconditional MAE, conditional Poisson deviance, by tier |
| tiered intervals | none: report-only | 80% coverage and mean width, pooled vs tiered, by tier |

A pass on the bracket is a recommendation to re-freeze (18.4), not a promotion: the
script changes no served constant. It refuses to overwrite an existing
`<version>_p3_decision.csv`, so a second look needs a new `--version`, in the open.

### 18.3 The challengers

**(a) `v5-stakes` (`FEATURE_COLS` + 7).** 15.11's recommendation taken literally: the
stakes family alone, so the dilution 15.11 blamed for −1.91% on the stakes cohort
becoming +0.66% pooled is as small as it can be. `team_games_remaining`,
`team_win_pct`, `team_games_over_500`, the three interactions
`stakes_late_x_over500`, `stakes_x_minutes_share` and `stakes_x_veteran`, and
`minutes_share`, kept as a column because the share interactions multiply it (15.2).
Dropped: `late_season` and `stakes_lockedness` as columns (15.8: the model wanted the
continuous count and the 15-game threshold carried 0.0002% of gain), the blowout
family (15.7) and the pace family. `FEATURE_VERSION` stays `v3`; the candidate is
`v5` only in `CANDIDATE_FEATURE_VERSION_V5`.

**(b) Contextual residual rate (`rate_model.py`).** For PTS, AST, REB and FGA, a
LightGBM regressor on appearances fitted to `stat / max(MIN, 4) − served rate`, so
the challenger rate is `max(0, served rate + f(context))`: it can move the champion's
per-minute rate, never replace it, and it composes through the same
`minutes_propagated_estimate`. Context is the 15 `RATE_CONTEXT_COLS` (usage, four
expected-context and star columns, home, rest, back-to-back, `OPP_DEF_FORM`, both
paces, opponent defensive rating and 3PA/FTA allowed, `minutes_share`), plus the row's
served rate, a minutes column and one-hot `POS_GROUP`. `RATE_MODEL_PARAMS` are 200
trees, 15 leaves, `min_child_samples = 200`: a residual target is mostly noise, four
stats are four chances to fit it, and `LGBM_PARAMS` overfit the P2 blowout frame
(15.7). On training rows the minutes input is the strictly prior `ewma_MIN`, because
an in-fold `MIN_PRED` would be fitted on the same games; scoring requires the
out-of-fold `MIN_PRED`, and the rate and minutes cutoffs must match. It is fitted on
the incumbent's availability and minutes models, so the comparison differs in the
rate and nothing else. **The permutation control matters for reading the result:**
on a fixture whose residual is pure noise, the booster *costs* 1-2% of residual MAE
across seeds and never buys anything (`test_rate_model.py`). A real gain has to
clear that noise-fitting tax as well as the 1% floor.

**(c) Serving coherence (`coherence.py`).** Two model-free corrections.
`team_minutes` scales each team-game's conditional minutes so
`sum(P_PLAY × E_MIN_COND) = 240` (overtime unmodelled), with the factor clipped to
`[0.8, 1.25]` so a thin or stale roster cannot be inflated without bound; a clipped
team-game is logged and still does not sum to 240. Every production stat takes the
same factor (they are rate × minutes), quantiles shift by the conditional delta, and
`P_PLAY` is never touched. `points_identity` sets `E_PTS_COND = 2·FGM + FG3M + FTM` and
carries the delta to `E_PTS` and the PTS quantiles. `all` runs minutes first, so the
identity sees rescaled makes. In `predict.py` it is applied **last**, after the
injury overrides and after the scenario mix. `none` returns the input frame itself,
and the `coherence=<value>` note token is written only for a non-default choice, so
the frozen run's note text stays byte-identical to what 13 and 17 pinned (`5a036bc`).

**(d) Scenario serving (`scenarios.py`).** `f(E[p])` is not `E[f(p)]`: a
questionable star's blended `p_j` fed once through nonlinear models gives his backup
a projection that is neither night nor their average. A player is **pivotal** if his
latest status at the boundary is questionable or doubtful **and** his shrunk
`tm_MIN ≥ 20` (`SCENARIO_MIN_MAGNITUDE`) or he is top-3 by usage among established
players (the `p_star_out` rule). At most `SCENARIO_MAX_PIVOTAL = 2` per team-game,
largest magnitude kept; the rest are served at their blended `p` and logged. Each
pivotal team-game is enumerated over its `2^k` play/sit worlds (`p_j` forced to 1.0
or the OUT 0.02), and each world is context-rebuilt, rescored and overridden. The
**outputs** are then averaged, with world weights the product of `p` or `1 − p`:
every teammate column is the weighted average; the star keeps his blended
`P(play)`, takes his conditional numbers from the worlds he plays in, and his
unconditional ones are `P(play) × conditional`. The coherence clip and a quantile
sort run after mixing; a team-game with no pivotal player is the single run
unchanged. **Assumption, stated in the code:** two pivotal teammates play or sit
independently, which a team resting both on one back-to-back violates. The audit
(`<out>_scenarios.parquet`) records per pivotal player his status, `p`, magnitude,
the world count and weights, and the backup with the largest `E_MIN_COND` swing with
both values; notes gain `scenarios=on; scenario_team_games=N`. It cannot be
backtested: the report history starts 2026-08-16 (7.1), so past rows carry no
designations to build worlds from.

**(e) Count models (`count_model.py`).** The 12.7 gap: a per-minute constant for a
stat averaging under one event a game. A LightGBM Poisson booster for STL, BLK, FG3M
and TOV. It is not a rerun of tournament M6/M7, which were fitted only for PTS and
AST, ran untuned on `LGBM_PARAMS`, and lost by under 0.5%. Here: (1) the rare stats;
(2) a grid of 8 small configurations (`n_estimators {100, 200}`, `num_leaves {7, 15}`,
`min_child_samples {100, 300}`) chosen by Poisson deviance on inner folds inside each
origin's training window, each fold's offset from a minutes model fitted before it;
(3) `init_score = log(max(E[MIN|plays], 1)) + log(league rate)`, the offset being
`MIN_PRED` on validation rows and `ewma_MIN` on training rows. The league log-rate
term (`sum(stat) / sum(offset minutes)` over training rows) is there because LightGBM
skips `boost_from_average` when an `init_score` is supplied, so without it a 100-tree
booster spends itself climbing to the intercept. Champion and count model share one
availability and one minutes model per origin.

**(f) Tiered intervals (`intervals.py`).** The pooled offsets give a star and a
fringe player the same band. The challenger fits one offset set per minutes tier
(`roll10_MIN`, the 13.3 tiers), same construction; a tier with fewer than
`MIN_TIER_ROWS = 200` finite residuals takes the pooled set itself, and the fallback
tiers are listed. `train.py --tiered-quantiles` stores them under
`production.quantiles_by_tier` in `metadata.json`, and only with the flag. Nothing
reads that key. It is a training option, so it writes a new artifact; `20260818` is
never retrained for it.

**(g) The v1 shadow.** See 17.7.

**(h) Active-DNP rows.** See 17.8. They change the training universe, so a rebuild on
a backfilled database is a 13.2 item (7) change on its own, independent of every
challenger above, and every look in 18.4 must record which universe its dataset was
built from.

### 18.4 What promotion would mean, and the order of looks

Every promotion below changes an emitted number, so under 13.2 it bumps the protocol
to `prospective_2026_27_v3` and re-freezes **before opening night (2026-10-20)**.
After it, a promotion ends the `v2` test, so a pass found then waits for the 2027-28
freeze, as 13.5 says of F8.

| Candidate | 13.2 item(s) |
|---|---|
| serving coherence | (4): a new coherence constraint on emitted rows |
| `v5-stakes` | (6) `FEATURE_COLS`, and (7) the refit |
| residual rate | (1) the production champion and (3) `RATE_ESTIMATORS` for four stats, and (7) |
| count models | (1) and (3), naming STL explicitly (item 3 calls out its `expanding`), and (7) |
| tiered intervals | (7): a refit whose metadata the served quantiles come from |
| scenario serving | 13.2's last paragraph (a serving change that moves emitted numbers), plus two new hand-set constants (20 minutes, 2 players) of the kind item (5) exists to catch |

**Order.** (1) **Coherence first**: model-free, no fitting beyond the promoted path,
the cheapest look, and its answer (whether team minute sums sit far enough from 240
to matter) conditions how every other number is read. (2) **`run_p3_bracket.py`
once**, which takes the `v5-stakes` and residual-rate looks in one invocation. Read
`v5-stakes` first. The rate was fitted on the `v3-honest` incumbent's pieces, so if
`v5-stakes` is promoted the rate's verdict describes a composition that no longer
ships; it is recorded as such, not re-run. (3) **`report_counts.py`** for counts and
tiered intervals, as reports. Neither has a bar, so neither can be promoted from
this look; promotion would first need a bar pre-registered here.

### 18.5 Open items

- **No real-data run yet**, of any challenger. The first database-backed look is the
  next step and the only one that can produce a number.
- **The v1 artifact is not trained or committed** (17.7), so rung (c) and F2 to F4
  still have no data.
- **Scenario independence.** Correlated rest of two pivotal teammates is not
  modelled; the audit will show how often a team-game has two.
- **E5 is still not in `score_runs.py`** (17.5, 17.6), nor the shifted-rate skill or
  the frozen-baseline rows. The `dec1` look needs them for F1 and F6.

## 19. Phase 3 decision layers (2026-10-01): numbers own the ranking, Claude explains

**Verdict first: every ordering the app shows a manager is now computed, and Claude
explains and sanity-checks it rather than producing it.** Candidates are ranked by a
deterministic score, the roster week is a seeded joint simulation, Projections sorts
by a computed edge, and `score_runs.py --look` produces the 13.5 table. **No served
prediction number moved**: all four packages only read the production run, and
`prospective_2026_27_v2` is untouched. Sections 13, 17 and 18 are not edited.

### 19.1 The principle, and why

Three places let a language model or a coin decide what a number should have decided.
(1) `buildWaiverContext` **shuffled** the rank band (`shuffleInPlace`) before slicing
it, so the best waiver candidate could fail to reach the prompt by chance. (2) The one
model number Claude saw per roster player was `P(active next game)`. (3) The betting
edge was Claude's own guess against the market (17.2 item 7 now prices it no-vig).

**Now numeric:** the candidate order and drivers (19.2), weekly category ranges and
win probabilities (19.3), projection deltas and the edge sort (19.4). **Claude is
asked to** weigh injury news, role changes, schedule and preferences against a list it
did not order, and say why when it passes over a higher-ranked player. Its roster
context gains a seven-day line per player (games, `P(play)`, totals, minutes vs usual).

### 19.2 Candidate ranking (`candidateRanking.ts`, `windowProjections.ts`)

**The score is the expected number of 9-category matchup wins the candidate adds to
the manager's roster over the next seven days.** Each player's window line is turned
into category z-scores against the whole population (roster, trade pool, waiver band),
percentages as volume-weighted impact through the slate's `poolRates`. Per category,
the roster's margin is its z-sum minus a **typical opponent**: the mean z of the
rostered tier (the top `teams x 13` by fantasy rank, excluding the manager's own
players) times the roster size. The margin's noise is taken as `sqrt(2 x roster size)`,
a sum of unit z-scores on each side. A candidate's gain in a category is
`Phi((margin + z) / spread) - Phi(margin / spread)`, the normal-curve change in its
win probability; the score sums nine, so a narrowly lost category is worth more than
one already won, which a summed z-score cannot say. The top three gains are drivers.

**Inputs.** The production run's **unconditional** seven-day totals
(`fetchWindowProjections`), so sitting risk is priced in. A player the run does not
project falls back to season per-game averages times his team's scheduled games
(`basis = season_average`), or per game when no schedule is loaded. **The fallback has
no attempts**, so FG% and FT% are neutral for it: it can never gain or lose a
percentage category, which understates a volume shooter; the prompt says so.

**Deterministic:** no randomness, ties break on player id. Up to 25 waiver candidates
and 20 trade targets are listed with score, basis and drivers, under this instruction
(verbatim apart from the window length):

> RANKING METHOD: both candidate lists below are pre-ranked numerically, best first.
> score = expected 9-category matchup wins the player adds to MY ROSTER over the next
> 7 days, computed from z-scores of his projected category totals against a typical
> opponent built from the rostered tier, so it already rewards filling my weak
> categories and discounts categories I already win. basis projection = the production
> model's unconditional projections (sitting risk priced in); basis season_average =
> season per-game averages times scheduled games, with FG% and FT% treated as neutral.
> drivers = the three categories contributing most to the score. Your job is to explain
> and sanity-check this ranking (injury news, role changes, schedule, fit with my
> preferences), not to re-rank from scratch: favor the top of each list, and if you
> pass over a higher-ranked player, say why.

Tested in `candidateRanking.test.ts`, `aiContext.test.ts` and `api/ai.test.ts`.

### 19.3 Weekly outlook by joint simulation (`weeklySimulation.ts`, `weeklyOutlook.ts`)

`GET /api/fantasy/weekly-outlook?start=&days=` (default 7, at most 14), bound to the
authenticated roster, shown as `WeeklyOutlookCard` on My Team. A seeded Monte Carlo
(2,000 draws, mulberry32, seed `20260930`) over the roster's games in the window.

- **The draw.** Each stat in each game is drawn by inverse transform through the
  stored **conditional** p10 / p50 / p90 (piecewise linear, linear tails, floored at
  0). A stat with no stored quantiles takes a hand-set relative spread
  (`FALLBACK_RELATIVE_SPREAD`), and the response names every player and stat that did.
- **Makes are coherent with attempts.** FGA and FTA are drawn, makes are attempts
  times a jittered rate, and FG3M is FGM times its own, so `fgm <= fga`, `ftm <= fta`
  and `fg3m <= fgm` hold in every draw. FG% and FT% are ratios of simulated sums.
- **Availability is dependent within a player-week.** With probability
  `DEPENDENCE_RHO = 0.5` one shared uniform decides every game (comonotone: a
  lingering injury), otherwise each game is an independent draw against its
  `prob_active`. The miss-at-least-one risk is then
  `rho x max(1 - p) + (1 - rho) x (1 - prod p)`, between the comonotone lower bound
  and the independence upper bound that `weekly.availability_risk` documents (17.2
  item 6), which closes that docstring's caveat without measuring it: **0.5 is
  hand-set**, not fitted; `player_injury_reports` is the history a fit would need.
- **The typical opponent**: the window's top `12 x roster size` players by summed
  slate impact, mean unconditional weekly totals scaled to the roster size.
- **Reported:** per category mean, p10, p50, p90, the opponent's value and the win
  probability (ties half); per player expected games and analytic and simulated miss
  risk.

**Two limitations, stated rather than fixed.** (1) Outside the make/attempt chains,
stats within a game are drawn **independently**: PTS is not tied to FGM and FTM, and
a big night does not raise rebounds, so category ranges differ from a joint model's by
an unmeasured amount. (2) The simulated mean is the piecewise-linear distribution's,
**not** the stored expectation (asymmetric anchors, the floor at 0), so it can differ
from the totals Projections and the ranking show. Tested in `weeklySimulation.test.ts`,
`api/weeklyOutlook.test.ts` and `WeeklyOutlookCard.test.tsx`.

### 19.4 Projections explanations (`projectionReasons.ts`, `slate.ts`)

- **One definition.** The five reason rules, their evidence and the deviation
  weights moved from `watchlist.ts` into `projectionReasons.ts`, which the slate and
  the Watchlist both call; the Watchlist's output is unchanged.
- **`vs_usual` on every slate row:** minutes, points and the two largest moves among
  REB, AST, STL, BLK and 3PM (ranked by delta over the slate's spread), each with his
  usual (last 15 games played, at least 5). Every delta is **conditional on playing**,
  so a game-time decision never reads as lost production.
- **The edge sort.** `GET /api/predictions/slate?sort=impact|edge` (unknown is a 400).
  Edge is the absolute weighted upside (minutes 2, points 1.5, the rest 1, each delta
  over the slate's spread), so a minutes cut ranks like a bump; games sort by top edge.
- **The player card** shows `vs usual: MIN +x, PTS +y` for the next game (optional
  `prediction.vs_usual` on `/analytics`). **ReasonBadge**: "Teammate out" said "a
  teammate who usually starts"; the rule is minutes, so it now says "usually plays 28+
  minutes", which is what `TEAMMATE_ABSENCE_MIN_MINUTES` tests.

Tested in `projectionReasons.test.ts`, `slate.test.ts`, `api/predictions.test.ts`,
`api/analytics.test.ts`, `SlatePage.test.tsx`, `vsUsual.test.ts` and `e2e/slate.spec.ts`.

### 19.5 The scoring tool completed (`fnba_ml/scoring.py`, `score_runs.py`)

17.6's and 18.5's open item is closed. `score_runs.py --look dec1|all_star|season_end`
scores games before the look date from runs predicted since the freeze, every baseline
rebuilt as of the game date from the truth layer, and appends the 13.5 table:

- **E1 skill** (F1) on `prob_active_model` against `avail_rate_10`; **F5**, conditional
  minutes against `ewma_MIN` on appearances; the **tiers** (`roll10_MIN`) and the
  **event and control cohorts** (`vacated_minutes >= 30`, `< 5`, oracle by design).
- **E5** (F6), the mean over the eleven rate stats of relative MAE improvement, both
  sides composed on `prob_active_model`: `p x E[stat | plays]` against
  `p x ewma_total`. Not the served unconditional column, which carries the override
  layer: 13.3 scores composition comparisons on the model probability, and F10
  measures the layer on its own.
- **F7 and F8**: the artifact's expanding and h20 rates replayed forward from its
  `ewma_state` snapshot (seed weight = appearances since training start, so the
  replay is exact), both composed through the same `p x E[MIN | plays]`.
- **F2 to F4** from served and v1 shadow runs paired on the same slate and an
  information boundary within 10 minutes, scored on identical rows with the paired
  7-day moving-block bootstrap; a shadow at another boundary stays unpaired (13.4).
- **The table**, bars from `config.PROSPECTIVE_FALSIFICATION`, each row `pass`, `fail`,
  `non-binding` (short of the 13.6 row minimum, reading still shown), `report-only`
  or `not computable` with the reason.

**Judgement calls.** (1) E5 on `prob_active_model x conditional`, above. (2) **F10's
sign**: the stored `override_increment` is served minus model Brier (positive = the
layer hurt); the table reports its negation, model minus served, so positive means
the layer helped and the 13.5 bar `< 0.00` fails as written. (3) **`vacated_minutes`
is not `teammates.py`'s**: absent teammates' 20-appearance rolling minutes, not
season-to-date `std_MIN`. It only selects cohorts, so it can move a cohort's rows,
never a forecast.
(4) **400 days of history** (`--history-days`), so October baselines see last season.

**Still not computable:** the `star_out = 1` cohort, which needs `usg_ewma` from team
box totals that no baseline rebuilds; and F2 to F4 until the v1 artifact is trained
and committed (17.7), since no shadow run exists to pair. Tested in `test_scoring.py`
and `test_scoring_baselines.py`.

### 19.6 What Phase 3 did not do

- **No team-market betting model.** For sides and totals the market line is the
  forecast. Player props are where a player-level model could add something, and no
  props odds source exists to price them against.
- **The betting picks remain Claude estimates**, labelled as such (17.2 item 7).
- **Nothing here changes a served prediction number.** The ranking, outlook and
  explanations read the production run; the scoring tool only reads the store.

## 20. v3 candidates (2026-10-02)

**Nothing in this section changes a served number.** `prospective_2026_27_v2` is
untouched and sections 13, 17, 18 and 19 are not edited. Each candidate below lands
behind a config switch whose default is the current behaviour, so
`tests/test_prospective_freeze.py` and every emitted column stay as they were. A later
package flips the switches it adopts and re-freezes as `prospective_2026_27_v3`.

### 20.1 Postseason appearances in the rate history

**The gap.** Only Regular Season games were scraped, and only Regular Season rows
reached the model, so a player's play-in and playoff games were invisible. Those are
the most recent and highest-leverage games before a new season, and every career-scoped
EWMA served in October is anchored on them being absent. Scottie Barnes is served a
0.51 points-per-minute rate from his last regular-season games while his playoff rate
was far higher.

**What changed, all of it inert until the switch flips:**

- **Scraper.** `config.SEASON_TYPES_INGESTED = ("Regular Season", "PlayIn",
  "Playoffs")`. The incremental game-log phase loops the per-season-type fetchers
  (`playergamelogs`, the `leaguegamelog` team log and player supplement) over it with
  the existing request delays and **one watermark per season type**;
  `rows.season_types_to_fetch` skips the two postseason types before April 1 of the
  season's second year, so an October run pays no extra requests. `--backfill-game-logs`
  covers all three types and keeps play-in and playoff rows from `scheduleleaguev2`
  (`rows.ingested_schedule_rows`); the schedule's team-log fallback reads all three.
  The status and box-score path needed no change: it selects games from
  `team_game_logs` by season, so a playoff game gets status rows once its logs land.
  `season_type` is written from the game id prefix (`004` Playoffs, `005` PlayIn), as
  it always was. The validation gate "completed schedule games have logs" is scoped to
  the Regular Season, because a scheduled "if necessary" playoff game that was never
  played is not a missing log; the other gates hold for every type.
- **Sources.** `config.HISTORY_SEASON_TYPES` is what both sources load; the postgres
  source's default moved from `SEASON_TYPES` to it. Every frame carries
  `COMPETITION` (`regular` / `playin` / `playoffs`). The parquet source reads a file's
  own `COMPETITION` column and defaults to `regular`, which is what the nba_api exports
  are.
- **Training frames.** `config.TRAINING_SEASON_TYPES = ("Regular Season",)`, and
  `config.SEASON_TYPES` keeps its value as that same training set. The universe builders
  (status and approximation), `coverage_report`, the matchup team context and
  `backfill_dataset.py` keep training rows only (`data.schema.training_rows`), and
  `build_features` **refuses** a universe holding a non-regular row: postseason rows
  enter only through `build_features(postseason=...)`, never as modelled rows.
  `universe.postseason_appearances` builds them in the universe shape (a log row with a
  dnp reason is a dressed DNP, as the status universe treats it), and
  `build_dataset.py` writes them beside the dataset as `<stem>_postseason.parquet` so
  `daily_run.py` and `project_preseason.py` serve from the same history the dataset was
  built from. The dataset itself stays a training frame.
- **Features.** `config.RATE_HISTORY_INCLUDES_POSTSEASON = False`. On, the postseason
  appearances join the **career-scoped** as-of joins: `roll{3,5,10}_*`, `ewma_*`,
  `n_appearances`, `LAST_APP_DATE` (so `days_since_last_app`), both per-minute rate
  families, `usg_ewma` and the teammate magnitudes. The **season-scoped** columns
  (`std_*`, `season_appearances`) and every scheduled-row window (`avail_rate_*`,
  `avail_rate_std`, `uncond_std_*`, `games_since_last_app`) stay regular season only,
  either way. A playoff game carries the same season label as the regular season before
  it, so the season-to-date frame is computed from the universe alone rather than
  filtered by label; and a playoff game is not a scheduled row a player could have
  missed, so availability never reads it.
- **Scoring tool.** `scoring.build_baselines` and `seeded_rate_baselines` scope the
  history the same way when it carries `season_type` (which `score_runs.py` now
  selects): `avail_rate_10` reads Regular Season scheduled rows, the appearance
  baselines (`roll10_MIN`, `ewma_MIN`, `ewma_total_*`, the F7/F8 rate families) follow
  the switch. The look report's header names the history it used, so a report scored
  with the switch on is never compared with one scored with it off.
- **Backend.** `BASELINE_INCLUDES_POSTSEASON` (env, default off) widens the "usual"
  form baseline from Regular Season to Regular Season, PlayIn and Playoffs, and the
  baseline descriptor carries `season_types`, `includes_postseason` and a definition
  that names which. It is flipped together with the model switch, so "usual" and the
  forecast read the same games.

**Why training stays regular season.** A playoff row as a *modelled* row would put a
second regime into every availability and minutes fit: rotations shorten, starters
play 40 minutes, nobody rests on a back-to-back, and DNP-CD means something different
in a seven-game series. The season types also sit in different calendar blocks, so
`ORIGINS` validation months would be trained on rows unlike anything they score. What
the gap costs is *history*, not training rows: the October forecast reads a stale rate.
So the postseason enters only where it is information about the player (career form),
and the served models are fitted on exactly the rows they were before.

**The re-freeze trigger.** Flipping `RATE_HISTORY_INCLUDES_POSTSEASON` changes emitted
feature values for every player with a postseason game, which 13.2's last paragraph
classes as a change to the artifact (item 7: the training window rule and a refit). It
is therefore the trigger for `prospective_2026_27_v3`, not a footnote, and it must land
before opening night or wait for the next season.

**The measurement plan, written before any number exists.**

1. Backfill the postseason from the home PC: `run_scraper.py --backfill-game-logs
   --from 2022-23` (all three types), then `--validate-game-logs` and check the
   per-season-type game counts it now prints.
2. Build both datasets from one database state:
   `build_dataset.py --source postgres --out data/dataset.parquet --no-v4-candidate`
   and the same with `--out data/dataset_postseason.parquet --postseason-history
   --universe-from data/dataset.parquet`.
3. `run_postseason_bracket.py`: the served `v3-honest` incumbent with unchanged
   champions, fitted on each dataset over the five `ORIGINS`, identical validation rows,
   cohorts taken from the switch-off dataset for both passes (a tier is assigned from
   `roll10_MIN`, which the switch moves). The bar is the P3 bar: paired 7-day
   moving-block bootstrap, 95% CI excluding zero and at least 1% relative improvement on
   a gated endpoint, `minutes_mae`, `uncond_pts_mae` or `cond_pts_mae`, and no gated
   cohort regressing by more than 1%. Everything else is reported only.
4. The October origins are where the effect should live and `ORIGINS` has none (O1 is
   December). The per-origin table is read for direction, and the 14.3 October replay
   gate is rerun with the switch on before the re-freeze is signed.

**First look (2026-10-02): null at mid-season origins.** ML Evaluate run 36956925799
(prod) measured the switch over the five `ORIGINS`. Every endpoint moved by under 0.2%
and every gated CI spans zero: `minutes_mae` -0.00% [-0.24%, +0.09%], `uncond_pts_mae`
+0.04%, `cond_pts_mae` +0.01%. The switch does not clear the bar on this origin set.
That is the expected null: the earliest origin is December, by which point a 5-game
halflife rate has forgotten the previous April, so the switch can only matter at
season start, and no origin in `ORIGINS` covers October or November.

**The season-start look, pre-registered before it runs.** `config.SEASON_START_ORIGINS`
holds two windows, S1 (2024-10-22 to 2024-11-30) and S2 (2025-10-21 to 2025-11-30).
They exist for season-start questions only and are not part of `ORIGINS` or
`DEV_ORIGINS`, so no champion decision is redefined. Both end before the Feb-Apr 2026
selection holdout and each has at least two full seasons of training before it. Each
start is clamped at runtime to the first regular-season game date the dataset records
for that season, so a mistyped date cannot pull the previous season into a window, and
training is everything strictly before the clamped start, exactly as for `ORIGINS`.
`run_postseason_bracket.py --origins season-start` runs it; the bar is unchanged (at
least 1% relative improvement on a gated endpoint with a 95% CI excluding zero, no gated
cohort regressing by more than 1%), and the report adds a reported-only split of the
first 14 days of each window against the rest, where the effect should decay. **This
second look does not violate the one-look rule, because that rule is per origin set per
candidate:** the first look was on the dev set, this one is on a different origin set,
and each writes under its own report stem (`<version>_postseason_<set>`), which is
refused if it already exists. The candidate dataset is now built with
`--universe-from` the baseline parquet, so the two builds hold identical modelled rows
by construction; the first attempt was refused once because a status backfill landed
between two independent builds.

**Known limit.** `parsing.season_end_date` closes a season on June 30, so the July
playoff games of 2020-21 (and the 2019-20 bubble) would be dropped by the stray-row
guard. Every season from the 2022-23 backfill default on finishes in June, so this
costs nothing now and is recorded rather than fixed.

### 20.2 Injury-report resolution: scope, source and which statuses expire

Section 7.1 resolves each player to his newest admissible report and drops any
report older than `REPORT_MAX_AGE_HOURS = 72.0`. Since migration 017 the table holds
two feeds with different shapes: the league's official report (`source =
'nba_official'`), filed per team per game and carrying `nba_game_id`, and the CBS page
(`source = 'cbssports'`), which is general (`nba_game_id` NULL) and now writes
`cleared` rows when a player leaves it. Three semantics were wrong for that table.

**(a) Expiry dropped OUT designations.** If the scraper stalled for three days, a
long-term injured player fell back to the history-only model, about 0.90 to play.
With clearance rows carrying the recovery signal, age is only a good proxy for
staleness on the game-specific designations that decay within days. With
`config.EXPIRE_UNAVAILABLE_STATUSES = False` only `questionable`, `doubtful` and
`probable` (`overrides.FAST_DECAY_STATUSES`) expire; `out`, `suspended` and
`g_league` stand until a newer row (a clearance or a new designation) replaces them.
Expiry is applied to the newest report, not before choosing it, so an expired
questionable cannot let an older out resurface. The SQL's 7-day lower bound is then
the only age limit an out has.

**(b) Resolution ignored the game.** An official OUT for tonight also zeroed the
player's game two days later in a multi-day window. With
`config.GAME_SCOPED_STATUS_RESOLUTION = True`, `overrides.resolve_statuses` resolves
per (player, game) against the scored row's `GAME_ID`:

1. Reports at or after `as_of` are dropped in every scope, as before.
2. Per player, scope (that game, or general) and feed, keep the newest report, then
   apply expiry.
3. Within a scope, the newer of the official and the non-official report wins, except
   that a disagreeing non-official report must be newer by more than
   `overrides.OFFICIAL_PRECEDENCE_HOURS = 6.0` to beat the official one.
4. Across scopes, the report for this game wins over the general one, except that a
   disagreeing non-official general report newer than an official game report by more
   than 6 hours wins. Without this clause the precedence rule would never fire in
   production, because CBS rows are always general and official rows always carry a
   game.
5. A report for a different game is ignored for this row.

**(c) Provenance reaches the resolver.** `LATEST_INJURY_STATUS_SQL` now selects
`nba_game_id` and uses `DISTINCT ON (nba_player_id, nba_game_id, source)`. The newest
row per player is still in that set, so the default resolver picks the same report it
did before. `daily_run.with_status_scope_columns` guarantees both columns in the
statuses parquet, `overrides.OPTIONAL_STATUS_COLUMNS` names them (an absent column
reads as a general report from source `unknown`), and predict.py passes `GAME_ID` to
both override applications (the base probabilities before the teammate sums, and the
served `P(play)`).

**Audit.** Every overridden row carries `STATUS_SCOPE` (`game` or `general`) and
`STATUS_SOURCE`, which are descriptive under the defaults too. predict.py prints the
override counts by scope and by source, and the registry's per-run entry stores them
as `status_override_provenance`.

**Unchanged.** `StatusPolicy`, `UNAVAILABLE_STATUSES`, `PASSTHROUGH_STATUSES`,
`REPORT_MAX_AGE_HOURS`, and the per-player `latest_statuses` that the horizon facts
and the scenario layer's pivotal-player selection read. Tested in `test_overrides.py`
(a verbatim copy of the old algorithm pins the default resolver, the switch cases,
precedence at 6 and 7 hours, the audit columns, absent columns, and the as-of
boundary in both scopes), `test_serving_context.py` and `test_daily_run.py`.

### 20.3 Injury-triggered rescoring

**The defect.** Predictions publish once a day at 16:00 UTC. A player ruled out at
18:00 UTC shows the new injury chip ("changed after run") beside a projection that
still assumes he plays, and his teammates' projections, which the override layer
moves through the base probabilities (7.1), never move at all.

**The trigger.** `predictions.yml` gains a second cron, `45 15,19,20,21,22,23,0,1,2
* * *`: the scraper's injuries-only lane shifted by 15 minutes so the fresh rows exist.
It runs `daily_run.py --if-status-changed`. Before any heavy phase the new `rescore`
phase reads the newest complete production run's `information_as_of` (the slate's own
selection, `COALESCE(information_as_of, forecast_cutoff_at)`), resolves every
player's designation as of that boundary and as of now through the same
`overrides.latest_statuses` a run applies (strict `captured_at < as_of`, 72-hour
expiry), and maps each to one of three classes read off the `StatusPolicy`: **out**
where the policy replaces the model with a constant (out, suspended, g_league,
doubtful), **questionable** where it blends (questionable, probable), **available**
where it passes through or the player is not listed. A player counts when his class
moved and he is rostered on a team with a game in the extended window that has not
tipped. No player counts: exit 0, `no status change since <boundary>; nothing to
publish`, nothing written. Otherwise the normal pipeline runs unchanged (run A if
its window holds a Regular Season game, the shadow, run B) with
`rescore=status_change; changed_players=N` appended to every note through
`run_notes`, so the assertion that a non-qualifying note cannot carry the label still
applies, and with `trigger: status_change` on each registry entry (`trigger:
schedule` on the 16:00 lane's). No previous production run means no boundary and no
rescore: the scheduled lane publishes the first one.

**Why it is protocol-neutral.** A run is a run. The rescore computes exactly what the
16:00 run computes: same artifact, same features, same policy, same label logic, its
own `statuses_as_of` as its boundary, its own measured horizon facts (13.8.5). 13.8 is
unaffected: (2) both `drop_tipped_off` passes still run, so a game that has tipped is
never published and the gate never counts its players; (3) the store stays
append-only, a rescore supersedes by being newest and the earlier run stays on the
record; (4) the label is still decided by `prospective_conditions` alone, and the
`rescore=` token makes every rescored run identifiable from the notes. Nothing frozen
moved: no constant, no cron of the 16:00 lane, no label, no horizon bucket.

**What a look report has to know.** A slate can now carry more than one labelled run.
`scoring.py`'s pools already keep the newest run per player-game, so the rescored
forecast is the one scored, and that is the right forecast to score: it is what was
served. A run A rescored late in the evening is still labelled `gameday` (the label is
part of the freeze) while its measured offset may sit in `lock`; the horizon facts on
the registry entry record the measured bucket, and 13.8.5 already obliges a look report
that pools across them to say so. The 15:45 slot will usually rescore against the
previous day's 16:00 boundary 15 minutes before the scheduled run; the `predictions`
concurrency group (`cancel-in-progress: false`) queues the 16:00 run behind it rather
than overlapping.

### 20.4 The player-prop layer: priced, paper-traded, not staked (`propProbability.ts`, `propPicks.ts`)

**Verdict first: the model now prices player props against the book and records every
pick it would make, with the market at the time, so closing-line value and calibration
can be measured. Nothing is staked.** This closes the 19.6 gap ("no props odds source
exists to price them against") on the numeric side; the odds come from
`prop_odds_snapshots` (a separate package). No served prediction number moved.

- **One distribution.** The piecewise-linear quantile function through the stored
  **conditional** p10 / p50 / p90 (linear tails, floored at 0) moved into
  `quantileDistribution.ts`; the weekly simulation draws from it and the prop layer
  inverts it (`cdfAt`), so both read the same three anchors the same way.
- **Discrete lines.** The stat is a count, so `P(X = k)` is the continuous mass on
  `[k - 0.5, k + 0.5]`. A .5 line is `P(over) = 1 - F(L)`; an integer line has
  `P(over) = 1 - F(L + 0.5)`, `P(under) = F(L - 0.5)` and the remainder as push.
- **PRA is approximated.** The sum's anchors are centred on the summed expectations
  with each half-width the quadrature sum of the components', i.e. **independent**
  PTS, REB and AST. They are positively correlated in a real game, so the PRA spread
  is understated and its tail probabilities are too confident by an unmeasured amount.
- **DNP.** Most books void a prop when the player does not play, so under `dnp_void`
  the stored `model_prob` is `P(win | plays)` and `prob_active` is reported beside it.
  A `dnp_loss` variant multiplies by `prob_active` for books that grade a DNP as a
  loss. Every pick records its `void_rule`; the default is `dnp_void`.
- **Price.** EV is per $1 at the offered American price (a push returns the stake),
  conditional on action under `dnp_void`. The edge shown is `model_prob` minus the
  no-vig implied probability of the two posted sides. Kelly is a quarter of full
  Kelly with pushes removed, capped at 2% of bankroll.
- **Selection.** For the latest production run, the newest snapshot per player, game,
  market, book and line inside the run's covered dates, before tipoff, with lines the
  book stopped re-posting dropped. A pick needs EV above 0.02, a stored `prob_active`
  above 0.6 and stored quantiles for every stat it needs; there is no fallback spread.
- **The ledger.** `model_prop_picks` records each pick once per run (on conflict do
  nothing). Settlement grades a pick once its game has box-score rows (no row or zero
  minutes is a DNP) and fills the last snapshot before tipoff as the close. CLV is the
  closing implied minus the surfaced implied in percentage points, counted only when
  the closing line equals the surfaced line. The summary reports hit rate over picks
  with action, mean `model_prob` beside it, realized ROI, mean EV and mean CLV, by
  market. Refresh and settle are admin endpoints for now; a workflow step can call
  them later.

**The paper-trading rule.** No real staking until at least **300 settled picks** show
**positive mean CLV** and calibration within **3 points** (hit rate minus mean
`model_prob`, overall and in each market with enough picks). EV above the threshold
on the model's own numbers is not evidence; CLV is the market's verdict and arrives
long before win rates are informative.

**What a proper distribution model would change.** Three quantiles pin the middle of
the distribution and extrapolate the tails linearly, which is where alt lines and
low-count markets (STL, BLK, FG3M at 0.5 or 1.5) live. A count model per stat
(negative binomial or a minutes-times-rate mixture with a zero-minute mass) would give
exact integer probabilities and push mass, a real tail instead of a linear one, and a
joint draw (shared minutes and pace) that prices PRA and same-game combinations
without the independence assumption. Until then the low-count and PRA markets should
be read with the most suspicion in the summary.

### 20.5 Preseason games are truth only

**The gap.** The daily run has published preseason forecasts since September 30
(`daily_run.SLATE_SEASON_TYPES` serves the Pre Season slate), but no preseason box
score was ever ingested. `score_runs.py` could not grade those runs, and the
season-start experiment's part (c) had no realized preseason minutes to compare the
projections with, so its "accidentally right" verdict could only read NOT MEASURABLE.

**The rule.** Preseason games are collected as truth and nothing else. They land in
`player_game_logs`, `team_game_logs` and `player_game_status` with `season_type = 'Pre
Season'` (game id prefix `001`), they are graded, and they never become a training row
or a rate input. Preseason minutes are not representative: starters play a half or
less, rotations are auditions, and the opponent can be an overseas club. A rate history
that read them would drag every star's October EWMA toward an exhibition workload, the
opposite of what 20.1 adds the playoffs for.

**What changed:**

- **Scraper.** `config.SEASON_TYPES_INGESTED` now leads with `"Pre Season"`, so the
  incremental game-log phase, `--backfill-game-logs` and the schedule's team-log
  fallback fetch it, with its own watermark. `rows.season_types_to_fetch` asks for it
  only from September 15 through October 31 of the season's first year
  (`PRESEASON_WINDOW_START` / `PRESEASON_WINDOW_END`), so the rest of the year pays no
  request. The status and box-score path is unchanged: it selects games from
  `team_game_logs`, so a preseason game gets status rows once its logs land. The
  validation gate "completed schedule games have logs" stays regular season only, and
  "team rows per game == 2" now skips preseason games, whose opponent may have no team
  log of its own.
- **Sources and frames.** `COMPETITION` gains `preseason`. `HISTORY_SEASON_TYPES` and
  `TRAINING_SEASON_TYPES` do not include it, so the dataset build never loads a
  preseason row. For frames that do carry one: the status universe drops the status
  rows of every non-training game (not only postseason ones), `build_features` refuses
  a preseason row as a modelled row, and `career_history` admits only play-in and
  playoff rows even with `RATE_HISTORY_INCLUDES_POSTSEASON` on. The scoring baselines
  (`build_baselines`, `seeded_rate_baselines`) read regular-season or history types
  only, so a preseason appearance moves none of them; the backend's usual-form baseline
  whitelists the regular season (plus the postseason behind
  `BASELINE_INCLUDES_POSTSEASON`) and never reads Pre Season either way.
- **Scoring.** `config.TRUTH_SEASON_TYPES` is the history types plus Pre Season, and
  `score_runs.py`'s truth query reads exactly those. A preseason game is matched and
  graded like any other and shows as the `season_type=Pre Season` cohort. The look
  report (`--look`) rescores every pre-registered endpoint on regular-season games
  only (`scoring.ENDPOINT_SEASON_TYPES`, as 13.3 defines them): the falsification
  table, the row minimum and the paired v3 vs v1 comparison never see a preseason row.
  The report states how many player-games it excluded and prints the preseason cohort
  in its own table, reported and never binding. Outside `--look` the ALL cohort still
  pools every graded game, with the season-type cohorts beside it.

**What it enables.** The preseason runs since September 30 are graded, the first
realized check on the season-start projections before any frozen look. Part (c) of the
season-start experiment reads `player_game_logs` rows with `season_type = 'Pre
Season'`; its query already uses that string, so it finds rows without a code change
once the October scrapes land (and a backfill fills earlier seasons).

### 20.6 Preseason minutes prior (serving only, not prospective)

**The measurement.** ML Evaluate run 36956136123 (2026-10-02) read four seasons of
preseason box scores, 8,174 player-games, and grouped realized minutes by each
player's regular-season tier (`roll10_MIN`, `config.TIER_EDGES`):

| Tier | Realized preseason minutes | Usual regular-season minutes |
|---|---|---|
| star (>=30) | 21.65 | 33.55 |
| starter (20-30) | 18.36 | 25.11 |
| bench (10-20) | 15.66 | 15.03 |
| fringe (<10) | 13.99 | 5.96 |
| unknown (no history) | 16.06 | n/a |

Players who start a preseason game play about 22 minutes whatever their tier. The
model learned regular-season rotations, so the preseason slate projected stars at
27.5 minutes: about 6 too many for stars and about 8 too few for the fringe.

**The rule.** `predict.py --preseason-prior on` calls
`preseason.apply_preseason_minutes_prior` right after `build_predictions` and before
the status overrides. For rows whose `SEASON_TYPE` is `Pre Season` only, it sets
`E_MIN_COND = w * PRESEASON_MINUTES_PRIOR[tier] + (1 - w) * model`, with
`PRESEASON_PRIOR_WEIGHT = 1.0` (a full replacement). Every production stat's
conditional and unconditional expectation takes the minutes ratio, because the
composition is rate x minutes (the same carry-through as
`coherence.renormalise_team_minutes`), quantiles move by the conditional delta,
`E_MIN` is recomputed and `P_PLAY` is untouched. The overrides then recompute the
unconditional columns from the adjusted conditional. Each row records
`PRESEASON_PRIOR_APPLIED` and `PRESEASON_PRIOR_TIER`; the notes gain
`preseason_prior=on` and the registry entry records the choice and the adjusted row
count. A player with no history takes the unknown prior. Every other row is left byte
for byte as the model emitted it. `daily_run.py` attaches `SEASON_TYPE` from the
schedule to run B's frame and passes `--preseason-prior on` to run B only; run A never
holds a preseason game and gets neither. `--scenarios` with the prior on is refused,
because the scenario rescore calls `build_predictions` again.

**Why it needs no re-freeze.** 13.2 binds the frozen prospective run, which is run A:
Regular Season only, scored without the flag, so every number it emits is unchanged
and `tests/test_prospective_freeze.py` pins nothing new. Run B is NOT PROSPECTIVE by
construction (16.7), its notes can never carry the label, and the look reports
exclude it by that label. The prior is a serving correction to an informational
slate, not a change to the artifact.

**How it will be graded.** Once the 2026 preseason games are final, `score_runs.py`
grades run B's preseason rows as the `season_type=Pre Season` cohort (20.5). The test
is whether minutes MAE on those rows, by tier, beats the model-only preseason runs
published before this change, and whether the star and fringe gaps above close.

## 21. The `prospective_2026_27_v3` re-freeze (2026-10-02)

**Sections 13, 17, 18, 19 and 20 are not edited.** Section 13 is the `v1`
pre-registration, 17 is the `v2` re-freeze, and 20 is the candidate record the two
looks below were run against. This section is the 13.2 re-freeze that 20's preamble
promised: it names what moved, what did not, and why. Everything in 13 and 17 that
this section does not amend binds `v3` exactly as it bound `v2`.

### 21.1 The verdict first

**Two switches flip, one stays off.**

- `config.EXPIRE_UNAVAILABLE_STATUSES` is now **False**. Out-class designations
  (`out`, `suspended`, `g_league`) no longer expire at 72 hours; a clearance row or a
  newer designation is what ends them.
- `config.GAME_SCOPED_STATUS_RESOLUTION` is now **True**. Each scored row resolves per
  (player, game), and an official report beats a disagreeing CBS one unless the CBS
  report is more than `OFFICIAL_PRECEDENCE_HOURS = 6.0` newer.
- `config.RATE_HISTORY_INCLUDES_POSTSEASON` **stays False**. Two pre-registered looks
  (21.2) did not clear the bar, and the season-start look leaned the wrong way on the
  rate-driven stats.

**Why a re-freeze, and why now.** The two injury switches change the override rule,
which is 13.2 item 5, and they change `prob_active` and every downstream expectation
for the players they touch, which 13.2's last paragraph classes as a change to what
is served. Either alone requires the bump. It lands on 2026-10-02, 18 days before
opening night. As with `v2` (17.1), **no Regular Season slate has been scored under
`v2` either**: run A only exists when its window holds a Regular Season game, the
first is 2026-10-20, and every run published so far is a run B whose notes start
`NOT PROSPECTIVE`. So `v3` is again a clean start, with no `v2` rows to splice or
discard. The decision was made on 2026-10-02 by the project owner's delegate after
both postseason looks had been read.

### 21.2 The postseason history switch: two looks, retained and off

**First look, the five mid-season `ORIGINS`** (ML Evaluate run 36956925799, prod;
20.1). Every endpoint moved by under 0.2% and every gated CI spans zero:

| Endpoint | Relative change (switch on vs off) | 95% CI |
|---|---|---|
| `minutes_mae` | -0.00% | [-0.24%, +0.09%] |
| `uncond_pts_mae` | +0.04% | [-0.06%, +0.13%] |
| `cond_pts_mae` | +0.01% | [-0.06%, +0.06%] |

That is the null 20.1 predicted for December-onward origins: a 5-game halflife has
forgotten the previous April by then.

**Season-start look, `config.SEASON_START_ORIGINS`** (ML Evaluate run 36958920471;
S1 Oct-Nov 2024 and S2 Oct-Nov 2025, pre-registered in 20.1). A positive number is
an improvement with the switch on:

| Endpoint | Relative change | 95% CI |
|---|---|---|
| `minutes_mae` | +0.60% | [-0.14%, +1.36%] |
| `uncond_pts_mae` | -0.06% | spans zero |
| `cond_pts_mae` | -0.08% | spans zero |

Minutes by window: S1 (2024) 1.27% better, and 3.2% better over its first two weeks;
S2 (2025) 0.08% worse. Nine rate-driven endpoints were slightly **worse** with CIs
excluding zero: unconditional REB -0.29%, TOV -0.37%, FGA -0.30%, FTM -0.27%, FTA
-0.29%, STL -0.17% and AST -0.15%; conditional AST -0.20% and FGA -0.41%. No gated
endpoint reached the 1% bar with a CI excluding zero.

**The reading.** Playoff per-minute rates are a different regime (shortened
rotations, heavier starter minutes, one opponent for a whole series), and mixing them
into the career rate history costs more on the rate-driven stats than it buys. The
one positive signal, first-two-weeks minutes in S1, is one season, did not replicate
in S2, and is not enough to serve on.

**The switch is retained in code, off.** The ingestion, the `COMPETITION` column, the
`build_features(postseason=...)` path, the scoring-tool scoping and the backend
`BASELINE_INCLUDES_POSTSEASON` all stay, and
`frozen.PROSPECTIVE_RATE_HISTORY_INCLUDES_POSTSEASON = False` pins the default, so it
cannot be flipped during the season without turning `test_prospective_freeze.py` red.
Postseason history may be re-proposed only as a **new candidate with a different
construction**, for example a separate playoff-context feature (last postseason's
minutes share or rate ratio as its own column, which a refit can weight or ignore)
rather than playoff games mixed into the regular-season EWMA. Re-running this
candidate on another origin set is not a new candidate; the one-look rule (20.1) has
been spent on both sets.

### 21.3 The injury switches, and what they change for a served number

Both rules are as specified in 20.2 (a) and (b); this section records what flipping
them does to an emitted number.

**`EXPIRE_UNAVAILABLE_STATUSES = False`.** Under `v2` a long-term OUT with no new
scrape for three days fell back to the history-only model, about 0.90 to play. Under
`v3` it stays at `OUT_PROBABILITY = 0.02` until a `cleared` row (17.2 fix 1) or a
newer designation replaces it, bounded only by the 7-day window of
`LATEST_INJURY_STATUS_SQL`. `questionable`, `doubtful` and `probable`
(`overrides.FAST_DECAY_STATUSES`) still expire at `REPORT_MAX_AGE_HOURS = 72.0`, and
an expired questionable does not let an older out resurface. **Moves:** for a player
whose newest report is an out-class designation more than 72 hours old, his
`prob_active` and unconditional expectations, and, because the layer also runs on the
base probabilities before the teammate sums (13.1), his teammates' expected-context
features and `prob_active_model`.

**`GAME_SCOPED_STATUS_RESOLUTION = True`.** Under `v2` an official OUT for tonight
also zeroed the same player's game two days later in a multi-day window, and the
newest report won whatever its source. Under `v3` a report for a different game is
ignored for the row, a report for this game beats a general one, and within or across
scopes a disagreeing CBS report must be more than 6 hours newer to beat an official
one. **Moves:** on run A, tomorrow's half of the two-day window for any player with a
game-scoped official report for today; on run B, most of its seven days; and on any
slate where CBS and the official report disagree within 6 hours, the official
designation is served where `v2` served the newer CBS one. The same teammate and
unconditional carry-through applies.

**The per-player view follows the same rule.** `overrides.latest_statuses` reads the
expiry switch through `resolve_statuses`, so the horizon facts (`report_count`), the
scenario layer's pivotal-player selection and the 20.3 rescore classes see the same
non-expiring outs the override layer applies. The rescore gate therefore compares
classes under the rule a run serves, which is what 20.3's "the same
`overrides.latest_statuses` a run applies" requires.

**Not re-measured, and why that is acceptable.** F10
(`override_layer_brier_increment`) is the prospective measurement of the override
layer and is unchanged; it now measures the `v3` rule. The 14.3 October replay gate
reads no injury reports (`replay_gate.py` applies no override), so its verdict carries
over. Tested in `test_overrides.py` (the mixed fixture pinned under the `v3` defaults
and under the explicit `v2` switches, the verbatim pre-switch algorithm pinned against
the `v2` switches, a four-day-old OUT applying by default and expiring under `v2`) and
`test_serving_context.py` (a game-scoped OUT reaches only its game by default and
every game under `v2`).

### 21.4 What remains frozen

Unchanged from 13.1 and 17.3, and asserted by `tests/test_prospective_freeze.py`:

- **Artifact `20260818`**, byte for byte: all six pinned checksums.
- **`FEATURE_COLS`**: 51 columns, digest `914cdc17…`.
- **Champions, halflives, estimators, rate targets, coherence constraints.**
- **The `StatusPolicy` constants** (0.02, 0.10, 0.6 / 0.60, 0.85 / 0.15),
  `UNAVAILABLE_STATUSES`, `REPORT_MAX_AGE_HOURS = 72.0` and `PASSTHROUGH_STATUSES =
  {available, day_to_day, cleared, unknown}`.
- **Horizons and the serving horizon (`gameday`), the nine cohorts, the cold-start
  window, the October gate, the three look dates and row minimums, and every row of
  the falsification table.** The thresholds derive from retrospective block variance
  (13.5), which neither switch touches.

The frozen additions are four hand-copied literals in `frozen.py`, each asserted
against the live module, and the two override switches and the precedence window also
against the parameter defaults of `resolve_statuses`, `resolve_overrides` and
`apply_status_overrides`, which is where serving reads them:

```python
PROSPECTIVE_EXPIRE_UNAVAILABLE_STATUSES = False
PROSPECTIVE_GAME_SCOPED_STATUS_RESOLUTION = True
PROSPECTIVE_OFFICIAL_PRECEDENCE_HOURS = 6.0
PROSPECTIVE_RATE_HISTORY_INCLUDES_POSTSEASON = False
```

The bundle records `"frozen_at": "2026-10-02"` and `"refrozen_from":
"prospective_2026_27_v2"`. The run label is `prospective_2026_27_v3`, written by
`daily_run.py` from `PROSPECTIVE_RUN_NOTE_LABEL`. `scoring.PROSPECTIVE_PREFIX` is the
bare `prospective_2026_27`, so a look report pools `v1`, `v2` and `v3` runs by
prefix; since no Regular Season run carries any of the three labels yet, every run a
look selects will be a `v3` run. `score_runs.py --look` defaults its window's start
to the bundle's `frozen_at`, which is now 2026-10-02.

### 21.5 What is deliberately outside the freeze

- **The preseason minutes prior (20.6).** Serving only, on run B, which is NOT
  PROSPECTIVE by construction and whose notes can never carry the label. Run A never
  holds a preseason game and never gets the flag, so no frozen number moves and no
  frozen literal pins it.
- **The player-prop layer (20.4).** Priced and paper-traded in the backend,
  downstream of the stored predictions; it writes no prediction row.
- **The scoring tool (`fnba_ml/scoring.py`, `score_runs.py`).** It reads the store and
  writes reports. A fix to it changes what a look computes, never what was served, and
  13.8.6's markdown plus csv lets a look be recomputed with the corrected tool.
- **The UI and the backend readers.** How a served number is displayed, explained or
  ranked (section 19) is not the forecast.

## 22. v6-context candidate (2026-10-02): built, pre-registered, unmeasured

**Verdict first: two new challengers exist, `v6-context` and `residual-rate-v6`, and
neither has a number.** Everything in this section was written before
`run_p3_bracket.py` had scored either one. `prospective_2026_27_v3` serves exactly as
section 21 froze it: artifact `20260818`, the 51 `FEATURE_COLS` (digest `914cdc17…`),
`FEATURE_VERSION = "v3"`, the 13.1 champions. `tests/test_prospective_freeze.py` is
green and sections 13, 17, 18 and 21 are not edited.

### 22.1 Why: the P3 look on prod (report stem `p3-2026-10-02`)

The one look 18.4 scheduled, against the 18.2 bars (95% CI excluding zero AND >= 1%
pooled relative improvement on a gated endpoint, no cohort regressing > 1%):

- `v5-stakes` vs incumbent: availability Brier +0.38% [+0.15, +0.56], minutes MAE
  +0.24% [+0.12, +0.40]. Real, under the bar. NOT PROMOTED.
- residual rate vs champion EWMA rate: conditional PTS MAE +0.65% [+0.55, +0.87]
  pooled, stars (>=30 min) +1.12%, starters +0.70%; unconditional PTS MAE +0.05% (CI
  spans zero); unconditional REB MAE -0.17% (worse, CI excludes zero); fringe (<10 min)
  uncond PTS +4.0% worse (n=8781, flagged regresses). NOT PROMOTED.

Both are directionally right and too small. Under 13.6 neither is re-scored: a fix is a
new candidate with its own single look, which is what this section registers. The
lever both lacked is the same one. The database now holds the full box-score details for
every game 2022-23 through 2025-26 (`player_game_logs.started`, `plus_minus`,
`oreb`/`dreb`/`pf`, `position`, `dnp_reason`, `details_source`, plus the inactive and
rostered rows in `player_game_status`), and no feature read any of it. Who starts is the
single largest determinant of a player's minutes that the v3 contract can only infer
from a minutes rank (`top5_min_share_10` was built in 15.2 because `started` was null
league-wide; it no longer is).

### 22.2 `v6-context` (`FEATURE_SETS["v6-context"]`, 58 + 9 = 67 columns)

The 58 `v5-stakes` columns, unchanged and in the same order, plus nine columns from
`fnba_ml/box_context.py` (`config.V6_CONTEXT_FEATURE_COLS`):

| Column | Definition |
|---|---|
| `started_rate_10` | share of the last 10 appearances (`V6_START_WINDOW`) started; fewer than 10 is the mean so far; a null `started` is missing, not a bench game |
| `started_last` | started in the most recent appearance (1/0, null if that row's flag is null) |
| `starts_streak` | consecutive starts ending at the most recent appearance; a null flag breaks it |
| `team_starters_out_exp` | `sum_{j != i, started_rate_10_j >= 0.5} (1 - p_j)` over the team-game: the expected number of usual starters out |
| `exp_vacated_starts` | `sum_{j != i} (1 - p_j) * started_rate_10_j`: the same, weighted by how often each teammate starts |
| `plus_minus_ewma_10` | EWMA of plus-minus, halflife 10 appearances (`V6_BOX_EWMA_HALFLIFE`) |
| `pf_per36_ewma` | EWMA of `PF * 36 / max(MIN, 4)`, the 4-minute floor being `RATE_MINUTES_FLOOR` |
| `oreb_share_ewma` | EWMA of the player's share of his team-game's offensive rebounds (sum over the team's player lines) |
| `dreb_share_ewma` | the same for defensive rebounds |

**Leakage contract.** Every per-appearance window is inclusive of its own game and
reaches a scheduled row only through `merge_asof(..., allow_exact_matches=False)` by
`PLAYER_ID`, the same guard as the five joins in `build_features`, so the row for game
g reads appearances strictly before g. Career-scoped, regular season only (the rate
history's scope under 21.2). The two teammate columns are expectations over the same
as-of, out-of-fold `P_CONTEXT` p_j that `exp_vacated_usg` is built from, so they read
no target-game outcome; at serving, a player listed OUT would enter at the override
table's 0.02 exactly as he does in `exp_vacated_usg`. The raw `STARTED`, `PLUS_MINUS`,
`OREB`, `DREB` and `PF` are never columns of the feature frame.

**Plumbing.** `PostgresSource.load_box_details` (one parameterised SELECT on
`player_game_logs`) and `ParquetSource.load_box_details` (optional
`box_details_<season>.parquet`) return the canonical `schema.BOX_DETAIL_COLS` frame;
`build_dataset.py` attaches the family after the v4 one unless `--no-v6-candidate`,
and a source with no details skips it with a warning. `CANDIDATE_FEATURE_VERSION_V6 =
"v6"`; promoting it is a 13.2 item (6) and (7) re-freeze, as `v5-stakes` would have
been.

### 22.3 `residual-rate-v6` and the fringe guard

The 18.3(b) residual rate with two pre-registered changes and nothing else
(`RATE_MODEL_TARGETS`, `RATE_MODEL_PARAMS`, the training rows, the `ewma_MIN` training
minutes and the incumbent's availability and minutes models are all the parent's):

1. **Context.** `RATE_CONTEXT_COLS_V6` = the 15 `RATE_CONTEXT_COLS` plus
   `started_rate_10`, `started_last`, `team_starters_out_exp` and `exp_vacated_starts`.
2. **The fringe guard, written in config before any result:**

```
RATE_RESIDUAL_MIN_MINUTES: float = 10.0
```

At scoring, the residual is multiplied by `1(MIN_PRED >= 10)`, so a row whose
out-of-fold projected minutes are under 10 (or null) is served the champion rate
exactly, and the challenger's loss on it equals the champion's. Fitting is unchanged. A
hard cut and not a shrink toward zero, because a ramp needs a second constant to set its
width and nothing measured yet says what that width should be. **A stated risk, not a hedge:** the fringe cohort is
defined by `roll10_MIN < 10` (13.3) and the guard by `MIN_PRED`, which is the number the
composition multiplies and the only one serving has. A fringe row projected at 10 or
more still takes the residual, so the guard can narrow the parent's +4.0% without
closing it, and if it does not close it the bar blocks the candidate.

### 22.4 The bars, and the look

Unchanged from 18.2 and applied by the same `promotion.decide`:

```
P3_PROMOTION_FLOOR: float = 0.01
P3_COHORT_REGRESSION_TOLERANCE: float = 0.01
P3_V6_GATED_ENDPOINTS: tuple[str, ...] = P3_V5_GATED_ENDPOINTS
P3_RATE_V6_GATED_ENDPOINTS: tuple[str, ...] = P3_RATE_GATED_ENDPOINTS
```

| Candidate | Against | Gates | Reported only |
|---|---|---|---|
| `v6-context` | `v3-honest` | availability Brier, minutes MAE | unconditional PTS MAE |
| `residual-rate-v6` | champion rate | conditional PTS MAE, unconditional PTS MAE | AST, REB, FGA (both forms), clip rates, coherence endpoints |

`run_p3_bracket.py` now runs four comparisons over identical rows in one invocation:
both v6 candidates and both parents. The parents are listed in
`config.P3_DECIDED_COMPARISONS` against `p3-2026-10-02`; their rows carry
`binding = False`, `promoted` is forced False, and their verdict reads `REFERENCE ONLY
(decided at p3-2026-10-02; this rerun is not a second look)`. They are there so the
child and the parent can be read off the same rows, not to be decided again.

**The look:** ML Evaluate on prod with `challengers: true` and `challenger_version:
p3-v6-<date>`, which rebuilds the dataset with the v6 columns and runs the bracket. The
workflow needs no new flag. One look per candidate: a v6 that disappoints is the
result, and a fix is `v7`. As in 18.4, `v6-context` is read first; the rate challenger is
fitted on the `v3-honest` pieces, so if `v6-context` passes, the rate verdict describes a
composition that would no longer ship and is recorded as such.

### 22.5 Open items

- **Serving cannot build the v6 teammate columns yet.** `predict.rebuild_context`
  rebuilds the v3 expected columns from override-adjusted p_j; it does not call
  `box_context.teammate_start_features`. A promotion would need that wiring, a 13.2
  re-freeze in any case.
- **Fixture-only so far.** The suite exercises the family on synthetic box details in
  which the first five roster slots always start, so `team_starters_out_exp` and
  `exp_vacated_starts` coincide there. On real data they differ wherever a rotation
  moves.
- **`position` and `dnp_reason` are still unread.** The box position is set for starters
  only and `players.position` already feeds `POS_GROUP`; neither seemed worth a column
  ahead of the start flag.
