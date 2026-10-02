"""single source of truth for seasons, windows, features, tiers and champions.

both data sources normalise to the SCREAMING_SNAKE NBA-style column names
(PLAYER_ID, GAME_ID, MIN, ...) rather than the database's snake_case.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .frozen import (  # noqa: F401
    PROSPECTIVE_2026_27,
    PROSPECTIVE_ARTIFACT_CHECKSUMS,
    PROSPECTIVE_CHAMPIONS,
    PROSPECTIVE_COHERENCE_CONSTRAINTS,
    PROSPECTIVE_COLD_START_FLAG,
    PROSPECTIVE_COLD_START_THROUGH,
    PROSPECTIVE_FALSIFICATION,
    PROSPECTIVE_FEATURE_COLS_SHA256,
    PROSPECTIVE_FEATURE_VERSION,
    PROSPECTIVE_HORIZON_WINDOWS,
    PROSPECTIVE_LOOK_DATES,
    PROSPECTIVE_LOOKS,
    PROSPECTIVE_MODEL_VERSION,
    PROSPECTIVE_N_FEATURES,
    PROSPECTIVE_OCTOBER_GATE,
    PROSPECTIVE_OCTOBER_REPLAY_WINDOW,
    PROSPECTIVE_OVERRIDE_CONSTANTS,
    PROSPECTIVE_PROTOCOL_VERSION,
    PROSPECTIVE_RATE_ESTIMATORS,
    PROSPECTIVE_RATE_HALFLIVES,
    PROSPECTIVE_RATE_TARGETS,
    PROSPECTIVE_RUN_NOTE_LABEL,
    PROSPECTIVE_SERVING_HORIZON,
    PROSPECTIVE_SHADOW_FEATURE_SETS,
    is_cold_start,
)

PACKAGE_ROOT = Path(__file__).resolve().parent
ML_ROOT = PACKAGE_ROOT.parent
MODELS_DIR = ML_ROOT / "models"
REPORTS_DIR = ML_ROOT / "reports"
DATA_DIR = ML_ROOT / "data"

# bumped whenever feature construction changes in a way that invalidates
# previously trained artifacts. recorded in every registry entry.
FEATURE_VERSION = "v3"

SEASONS: list[str] = ["2022-23", "2023-24", "2024-25", "2025-26"]
SEASON_TYPES: list[str] = ["Regular Season"]

# the sources load every competition a player logs minutes in; the universe and
# every training frame keep TRAINING rows only (MODEL.md 20.1).
HISTORY_SEASON_TYPES: tuple[str, ...] = ("Regular Season", "PlayIn", "Playoffs")
TRAINING_SEASON_TYPES: tuple[str, ...] = ("Regular Season",)

# the COMPETITION column carried through the frames, one label per season type.
COMPETITION_COL = "COMPETITION"
COMPETITION_BY_SEASON_TYPE: dict[str, str] = {
    "Pre Season": "preseason",
    "Regular Season": "regular",
    "PlayIn": "playin",
    "Playoffs": "playoffs",
}
TRAINING_COMPETITIONS: tuple[str, ...] = tuple(
    COMPETITION_BY_SEASON_TYPE[t] for t in TRAINING_SEASON_TYPES
)
POSTSEASON_COMPETITIONS: tuple[str, ...] = ("playin", "playoffs")

# preseason is truth only (MODEL.md 20.5): graded by score_runs.py, never a
# training row and never in a rate history, whatever the postseason switch says.
PRESEASON_SEASON_TYPE = "Pre Season"
TRUTH_SEASON_TYPES: tuple[str, ...] = (PRESEASON_SEASON_TYPE, *HISTORY_SEASON_TYPES)

# off until the v3 re-freeze: on, postseason appearances enter the career-scoped
# rate history while season-to-date and availability columns stay regular only.
RATE_HISTORY_INCLUDES_POSTSEASON: bool = False

# ---- feature windows ----
ROLL_WINDOWS: tuple[int, ...] = (3, 5, 10)
ROLL_STATS: tuple[str, ...] = ("MIN", "PTS", "AST", "FGA")
EWMA_HALFLIFE: float = 5.0
AVAIL_WINDOWS: tuple[int, ...] = (10, 20)
OPP_FORM_WINDOW: int = 10
OPP_FORM_MIN_PERIODS: int = 3
UNCOND_STATS: tuple[str, ...] = ("PTS", "MIN", "AST")

# ---------------------------------------------------------------------------
# P2: the v4 candidate family (matchup, blowout, season stakes). nothing below
# is served; it adds names and one FEATURE_SETS entry and touches neither
# FEATURE_COLS nor FEATURE_VERSION.
# ---------------------------------------------------------------------------

# ---- A. matchup / possession environment ----
PACE_WINDOW: int = 15
PACE_MIN_PERIODS: int = 5

# team_game_logs carries no oreb column, so possessions use the OREB-free
# fallback FGA + 0.44 * FTA + TOV, which overcounts by roughly a team's OREB
# count. every consumer is a relative comparison, so the level shift cancels.
POSSESSION_USES_OREB: bool = False

# ---- B. the pregame blowout model ----
BLOWOUT_MARGIN: float = 15.0

# symmetric: both team-games of a game carry the same label, because both
# benches empty.
BLOWOUT_TARGET = "blowout"
BLOWOUT_MARGIN_COL = "team_margin"

# same forward-chaining scheme as models.cross_fit_base_probabilities, but one
# month of team-games is ~430 rows rather than ~12k player-games.
BLOWOUT_CROSS_FIT_MIN_TRAIN_ROWS: int = 400

# a constant near the league-wide blowout rate, not a mean over the evaluation
# window, which would put a little of every future game into every past row.
BLOWOUT_PRIOR: float = 0.33

# pregame-knowable only; no box-score quantity from the target game appears.
BLOWOUT_MODEL_FEATURES: tuple[str, ...] = (
    "bo_own_net_rating",
    "bo_opp_net_rating",
    "bo_net_rating_gap",
    "bo_win_pct_gap",
    "bo_win_pct_sum",
    "bo_pace_mean",
    "bo_own_rest_days",
    "bo_own_is_b2b",
    "bo_opp_rest_days",
    "bo_opp_is_b2b",
    "bo_is_home",
)
BLOWOUT_PROB = "blowout_prob"
BLOWOUT_PROB_CUTOFF = "BLOWOUT_PROB_CUTOFF"

# LGBM_PARAMS was tuned on a 147k-row player-game frame and overfits the ~10k
# team-game frame, so the blowout champion is the regularised logistic.
BLOWOUT_MODEL_KIND = "logistic"
BLOWOUT_MODEL_CHALLENGERS: tuple[str, ...] = ("lightgbm_classifier",)

BLOWOUT_SELECTION_CUTOFF = "2024-12-01"
BLOWOUT_SELECTION_TRAIN_SHARE: float = 0.70

# ---- C. season stakes ----
# a constant rather than a count off nba_schedule, which holds 2024-25 onward
# only while team_game_logs holds all four seasons.
REGULAR_SEASON_GAMES: int = 82

LATE_SEASON_GAMES_REMAINING: int = 15

# lockedness = 1(late season) * min(1, |games over .500| / games remaining), a
# tiebreak-free proxy for "this team's season is decided".
STAKES_LOCKED_RATIO: float = 0.5

# ---- D. start rate ----
# both `started` columns are null league-wide on this truth layer, so the proxy
# is the share of recent scheduled team-games spent in the team's top 5 by
# minutes. non-appearances count as 0.
START_RATE_WINDOW: int = 10
START_RATE_TOP_N: int = 5

# ---- the v4 candidate feature columns, by family ----
MATCHUP_FEATURE_COLS: list[str] = [
    "own_pace",
    "opp_pace",
    "game_pace_mean",
    "game_pace_product",
    "own_net_rating",
    "opp_net_rating",
    # per 100 possessions, unlike the v3 OPP_DEF_FORM, which stays in the
    # contract so a refinement is not read as a removal.
    "opp_def_rating",
    "opp_fg3a_allowed_per100",
    "opp_fta_allowed_per100",
]

# roll10_MIN / (rolling team minutes / 5), so ~0.7 for a heavy-minutes starter.
SHARE_FEATURE_COLS: list[str] = ["minutes_share"]

BLOWOUT_FEATURE_COLS: list[str] = [
    BLOWOUT_PROB,
    # the marginal effect of blowout_prob changes sign with minutes_share, so
    # the product is handed over rather than left to be discovered.
    "blowout_x_minutes_share",
]

STAKES_FEATURE_COLS: list[str] = [
    "team_games_remaining",
    "team_win_pct",
    "team_games_over_500",
    "late_season",
    # signed, so "locked good" and "locked bad" stay distinguishable.
    "stakes_late_x_over500",
    "stakes_lockedness",
    "stakes_x_minutes_share",
    "stakes_x_veteran",
]

START_RATE_FEATURE_COLS: list[str] = ["top5_min_share_10"]

V4_FEATURE_COLS: list[str] = (
    MATCHUP_FEATURE_COLS
    + SHARE_FEATURE_COLS
    + BLOWOUT_FEATURE_COLS
    + STAKES_FEATURE_COLS
    + START_RATE_FEATURE_COLS
)

# promoting the candidate means assigning this to FEATURE_VERSION, which is a
# MODEL.md 13.2 re-freeze trigger.
CANDIDATE_FEATURE_VERSION = "v4"

# ---- P2's pre-registered promotion rule ----
P2_PROMOTION_FLOOR: float = 0.01
P2_PROMOTION_ENDPOINTS: tuple[str, ...] = ("minutes_mae", "availability_brier")
P2_COHORT_REGRESSION_TOLERANCE: float = 0.01

# ---- the two new descriptive cohorts ----
# blowout_prob is an as-of, out-of-fold model output, so the top-decile cut is
# computable at the forecast cutoff. the threshold is a quantile over the
# validation frame so "top decile" is the same share of rows in every origin.
V4_DESCRIPTIVE_COHORTS: tuple[tuple[str, str, str, float], ...] = (
    ("v4: blowout_prob top decile", BLOWOUT_PROB, "quantile>=", 0.90),
    ("v4: stakes-flagged (locked, late)", "stakes_lockedness", ">=", STAKES_LOCKED_RATIO),
)
V4_DESCRIPTIVE_COHORT_ORDER: tuple[str, ...] = tuple(
    label for label, *_ in V4_DESCRIPTIVE_COHORTS
)

# ---- the development origin set for P2 ----
# March-April 2025 and not 2026, because the 2026 window is the selection
# holdout (MODEL.md section 6).
LATE_SEASON_ORIGIN: tuple[str, str, str] = (
    "O6 valid=2025-03-15..04-12", "2025-03-15", "2025-04-12",
)

# ---- per-minute production rates (the minutes-propagating composition) ----
# stats served as P(play) x E[minutes | play] x rate, so a predicted minutes
# change moves the stat. percentages are deliberately absent: FG% and FT% are
# ratios of two random variables and a league scores the weekly aggregate, so
# the package ships the primitives and lets the consumer aggregate. TOV is not
# sign-flipped.
RATE_TARGETS: tuple[str, ...] = (
    "PTS", "AST", "REB", "STL", "BLK", "TOV", "FG3M", "FGM", "FGA", "FTM", "FTA",
)

# the pair the production tournament's verdict is scoped to. not served.
TOURNAMENT_RATE_TARGETS: tuple[str, ...] = ("PTS", "AST")

# ---- per-stat EWMA halflife for the production rates ----
# PTS and AST are frozen at 5 by the production tournament's verdict; the other
# nine were selected on inner folds inside each origin's training window.
RATE_HALFLIVES: dict[str, float] = {
    "PTS": 5.0,
    "AST": 5.0,
    "REB": 20.0,
    "TOV": 20.0,
    "FG3M": 20.0,
    # recorded for the audit trail; STL ships the expanding mean instead.
    "STL": 20.0,
    "FTM": 12.0,
    "FTA": 12.0,
    "BLK": 5.0,
    "FGM": 5.0,
    "FGA": 5.0,
}

# which smoother produces each stat's per-minute rate. steals is the one stat
# where the career expanding mean beat every EWMA on the grid.
RATE_ESTIMATORS: dict[str, str] = {
    **dict.fromkeys(RATE_TARGETS, "ewma"),
    "STL": "expanding",
}

# ---- coherence constraints on the emitted expectations ----
# (bounded, bound): `bounded` is clipped to at most `bound`, in this order, so
# the chain FG3M <= FGM <= FGA settles in one pass. the clip is needed because
# per-stat halflives make the two EWMAs different weighted averages, so the
# pointwise inequality does not survive averaging.
COHERENCE_CONSTRAINTS: tuple[tuple[str, str], ...] = (
    ("FGM", "FGA"),
    ("FG3M", "FGM"),
    ("FTM", "FTA"),
)

# the rate is stat / max(minutes, floor): a 2-minute cameo with one three is a
# real 1.5 pts/min observation and an absurd rate to carry forward.
RATE_MINUTES_FLOOR: float = 4.0

# ---- teammate context: the vacated-resource family (feature_version v2) ----
# the possession cost of a free-throw trip, the league-standard constant.
FT_POSSESSION_WEIGHT: float = 0.44

# career appearances (n_appearances), matching usg_ewma's own scope, before a
# player's usage may define the team's hierarchy.
STAR_USAGE_MIN_APPEARANCES: int = 15

TOP_USAGE_N: int = 3

# players.position holds comma-joined strings ("PG,SG"); the first listed
# position is the primary one.
POSITION_GROUPS: dict[str, str] = {
    "PG": "G", "SG": "G", "G": "G",
    "SF": "F", "PF": "F", "F": "F",
    "C": "C",
}
POS_GROUP_ORDER: tuple[str, ...] = ("G", "F", "C")

# ---- teammate magnitudes: the shrunk career-scoped rolling window (v3) ----
# a career-scoped rolling window over the last MAGNITUDE_WINDOW appearances,
# shrunk toward a prior with w = n / (n + MAGNITUDE_SHRINK_K), so a player with
# no history contributes the prior rather than a NaN or a 0.
MAGNITUDE_WINDOW: int = 20
MAGNITUDE_SHRINK_K: float = 10.0

# hand-set league-shape constants, not means computed from the dataset, which
# would put a little of every future game into every past row. deliberately
# replacement-level: the players who are mostly prior have almost no history.
MAGNITUDE_PRIORS: dict[str, float] = {"MIN": 10.0, "FGA": 6.0, "USG": 15.0}

# per-teammate inputs summed over a set, not model features themselves.
MAGNITUDE_COLS: tuple[str, ...] = ("tm_MIN", "tm_FGA", "tm_USG")

# ---- scenario serving for uncertain stars (predict.py --scenarios, off by default) ----
# a questionable or doubtful player whose shrunk minutes magnitude reaches this (or
# who is a top-usage player) is scored in both worlds rather than at a blended p.
SCENARIO_MIN_MAGNITUDE: float = 20.0
# 2 pivotal players is 4 scenarios per team-game; the cap bounds the refits.
SCENARIO_MAX_PIVOTAL: int = 2

# ---- the served teammate context: expectations over as-of probabilities (v3) ----
# every column is a linear functional of the teammates' play probabilities p_j
# and their as-of magnitudes m_j, so no target-game outcome enters any of them.
# exp_depth_rank is an expectation and not the rank of an expectation, so it is
# not an integer.
TEAMMATE_EXPECTED_COLS: list[str] = [
    "exp_vacated_minutes",
    "exp_vacated_fga",
    "exp_vacated_usg",
    "exp_vacated_minutes_pos",
    "exp_depth_rank",
    "exp_depth_rank_pos",
    "p_star_out",
    "exp_top3_usage_out",
]

# ---- the oracle comparator: the v2 realized-absence family ----
# still computed, never served: these depend on other players' target-game
# labels, so they are the Level-D upper bound and not a forecast.
TEAMMATE_ORACLE_COLS: list[str] = [
    "vacated_minutes",
    "vacated_fga",
    "vacated_usg",
    "vacated_minutes_pos",
    "depth_rank_available",
    "depth_rank_available_pos",
    "star_out",
    "top3_usage_out_count",
]

# ---- reliability / cold-start features (v3) ----
# shrinkage decides what number to use when history is thin; these tell the
# estimator that it is thin.
RELIABILITY_FEATURE_COLS: list[str] = [
    "magnitude_ess",
    "teammate_magnitude_ess",
    "season_appearances",
    "games_with_current_team",
    # computed backward-only, so a player who does not debut until S+1 still
    # reads rookie for his season-S rows.
    "is_rookie",
    "is_traded",
]

TEAMMATE_FEATURE_COLS: list[str] = [
    "usg_ewma",
    *TEAMMATE_EXPECTED_COLS,
    *RELIABILITY_FEATURE_COLS,
]

# ---- event cohorts for evaluation ----
# the third is a control, not a target.
EVENT_COHORTS: tuple[tuple[str, str, str, float], ...] = (
    ("event: vacated_minutes >= 30", "vacated_minutes", ">=", 30.0),
    ("event: star_out = 1", "star_out", ">=", 1.0),
    ("control: vacated_minutes < 5", "vacated_minutes", "<", 5.0),
)
EVENT_COHORT_ORDER: tuple[str, ...] = tuple(label for label, *_ in EVENT_COHORTS)

# fallback-universe only, never a model feature. see
# universe.approximate_universe for why any run that uses it is labeled BIASED.
FALLBACK_ROSTER_WINDOW_DAYS: int = 15

# ---- identifier handling ----
# the database stores nba ids as TEXT and the spike parquet as int64, so
# everything is normalised to str and frames from either source merge.
ID_COLS: tuple[str, ...] = ("PLAYER_ID", "GAME_ID", "TEAM_ID", "OPP_TEAM_ID")

# ---- minutes tiers for segment reporting ----
# roll10_MIN is a strictly prior rolling mean, so the tier label is as-of safe.
TIER_BASIS = "roll10_MIN"
TIER_EDGES: tuple[float, ...] = (10.0, 20.0, 30.0)
TIER_LABELS: tuple[str, ...] = (
    "fringe (<10)",
    "bench (10-20)",
    "starter (20-30)",
    "star (>=30)",
)
UNKNOWN_TIER = "unknown (no history)"
TIER_ORDER: tuple[str, ...] = (
    "star (>=30)",
    "starter (20-30)",
    "bench (10-20)",
    "fringe (<10)",
    UNKNOWN_TIER,
)

# named rather than derived by subtraction: it is the base model's contract and
# that model has to be provably free of teammate context.
BASE_FEATURE_COLS: list[str] = (
    [f"roll{w}_{s}" for s in ROLL_STATS for w in ROLL_WINDOWS]
    + [f"std_{s}" for s in ROLL_STATS]
    + [f"ewma_{s}" for s in ROLL_STATS]
    + [f"uncond_std_{s}" for s in UNCOND_STATS]
    + [
        "n_appearances",
        "days_since_last_app",
        "games_since_last_app",
        "avail_rate_10",
        "avail_rate_20",
        "avail_rate_std",
        "TEAM_REST_DAYS",
        "IS_B2B",
        "IS_HOME",
        "OPP_DEF_FORM",
        "OPP_REST_DAYS",
        "insufficient_history",
        "has_history",
    ]
)

# sha256("\n".join(FEATURE_COLS)) is the frozen contract digest, so any
# addition, removal or reordering here invalidates the pinned artifact.
FEATURE_COLS: list[str] = BASE_FEATURE_COLS + TEAMMATE_FEATURE_COLS

# ---- the P2 candidate contract (feature_version v4), not served ----
# appended rather than interleaved so the diff against the v3 contract stays
# readable. nothing in the serving path reads this.
FEATURE_COLS_V4: list[str] = FEATURE_COLS + V4_FEATURE_COLS

# ---- the P3 candidate contract (feature_version v5), not served ----
# the stakes family alone, per MODEL.md 15.11: no late_season and no
# stakes_lockedness column (only their interactions), no blowout, no pace.
V5_STAKES_FEATURE_COLS: list[str] = [
    "team_games_remaining",
    "team_win_pct",
    "team_games_over_500",
    "stakes_late_x_over500",
    "stakes_x_minutes_share",
    "stakes_x_veteran",
    "minutes_share",
]
FEATURE_COLS_V5_STAKES: list[str] = FEATURE_COLS + V5_STAKES_FEATURE_COLS
CANDIDATE_FEATURE_VERSION_V5 = "v5"
CANDIDATE_FEATURE_SET_V5 = "v5-stakes"

# ---- the evaluation bracket: feature sets over identical rows ----
# v1 is the no-teammate-context floor, v2-oracle is what perfect pre-tipoff
# lineup information buys, v3-honest is what ships.
FEATURE_SETS: dict[str, list[str]] = {
    "v1": list(BASE_FEATURE_COLS),
    "v3-honest": list(FEATURE_COLS),
    "v2-oracle": BASE_FEATURE_COLS + ["usg_ewma"] + TEAMMATE_ORACLE_COLS,
    "v4": list(FEATURE_COLS_V4),
    "v5-stakes": list(FEATURE_COLS_V5_STAKES),
}
SERVED_FEATURE_SET = "v3-honest"
ORACLE_FEATURE_SET = "v2-oracle"
CANDIDATE_FEATURE_SET = "v4"

# ---- stage 1/2 of the two-stage pipeline: the base availability model ----
# the expected-context features are functions of p_j, so every p_j is produced
# by a cross-fit over consecutive calendar blocks, each block scored by a base
# model fitted strictly before the block start. that is out of fold on the
# training rows as well as the validation rows, which a per-origin refit is not.
CROSS_FIT_FREQ = "MS"  # calendar-month starts

# blocks with a thinner training window fall back to the stage-0 baseline
# probability, which is a weaker p and not a leaky one.
CROSS_FIT_MIN_TRAIN_ROWS: int = 5_000

# a prior, not an estimate, and a constant so it cannot silently become a mean
# over the evaluation window.
CONTEXT_P_PRIOR: float = 0.70

P_CONTEXT = "P_CONTEXT"
P_CONTEXT_CUTOFF = "P_CONTEXT_CUTOFF"

# outcome columns that must never appear in FEATURE_COLS. LISTED_INACTIVE is
# the exact complement of PLAYED on this truth layer, so it is carried only to
# build a player's TEAMMATES' absence set, never his own features.
TARGET_COLS: frozenset[str] = frozenset(
    {"PLAYED", "MIN", "PTS", "AST", "REB", "FGA", "FGM", "FTA", "STL", "BLK", "TOV",
     "FG3M", "FTM", "TEAM_PTS", "TEAM_PTS_ALLOWED", "LISTED_INACTIVE",
     "TEAM_MIN", "TEAM_FGA", "TEAM_FTA", "TEAM_TOV",
     BLOWOUT_TARGET, BLOWOUT_MARGIN_COL}
)

AVAILABILITY_TARGET = "PLAYED"

# identical to RATE_TARGETS by construction: a stat with a per-minute rate is a
# stat the composition can serve.
PRODUCTION_TARGETS: tuple[str, ...] = RATE_TARGETS
MINUTES_TARGET = "MIN"

# ---- promoted path ----
# "composition" names how the promoted estimators are combined rather than an
# estimator. it is the only one of the four that is a function of predicted
# minutes.
CHAMPIONS: dict[str, str] = {
    "availability": "lightgbm",
    "minutes": "lightgbm",
    "production": "ewma",
    "composition": "decomposed_p_x_minutes_x_ppm",
}
CHALLENGERS: dict[str, tuple[str, ...]] = {
    "availability": ("logistic",),
    "minutes": ("ewma", "ridge"),
    "production": ("ridge", "lightgbm"),
    "composition": ("decomposed_p_x_ewma", "decomposed_p_x_lightgbm", "direct_lightgbm"),
}

COMPOSITION_PARITY_TOLERANCE: float = 0.01

# ---- cutoff policy ----
CUTOFF_POLICY = "prediction-run timestamp; features use games strictly before it"

# ---- forecast horizons ----
# a horizon is defined by the measured offset between the run's information
# boundary and tipoff, not by a label someone typed. (lo, hi] in hours before
# tipoff; the buckets partition (0, inf) so every run lands in exactly one.
HORIZON_WINDOWS: dict[str, tuple[float, float]] = {
    "lock": (0.0, 2.0),
    "gameday": (2.0, 12.0),
    "early": (12.0, 48.0),
}

# the nominal centre of each window; a label on the bucket, not the definition.
HORIZONS: dict[str, str] = {
    "early": "T-24h",
    "gameday": "T-6h",
    "lock": "T-60m",
}
DEFAULT_HORIZON = "gameday"

# written into the registry's per-run entry (and, for the scalar ones, into
# prediction_runs.notes) by predict.py, so the horizon question stays answerable
# from a run row rather than from its label alone.
HORIZON_RUN_METADATA: tuple[str, ...] = (
    "hours_to_tip_min",
    "hours_to_tip_median",
    "hours_to_tip_max",
    "latest_report_at",
    "report_age_hours",
    "report_count",
    "first_deadline_passed",
    "horizon_measured",
)

# the league's initial participation-report deadline, as a local-time hour on
# the day before the game. used only for `first_deadline_passed`.
INITIAL_REPORT_DEADLINE_HOUR: int = 17

# injury-report resolution switches (MODEL.md 20.2). the defaults reproduce the
# frozen v2 serving behaviour; the v3 re-freeze flips both.
# True: the 72-hour expiry drops out/suspended/g_league too. False: only the
# fast-decaying game designations (questionable, doubtful, probable) expire.
EXPIRE_UNAVAILABLE_STATUSES: bool = True
# True: resolve per (player, game) with official-over-cbs precedence.
# False: newest report per player regardless of game or source.
GAME_SCOPED_STATUS_RESOLUTION: bool = False


def horizon_label(horizon: str) -> str:
    """'gameday' -> 'gameday (T-6h)'. raises on an unknown horizon name."""
    if horizon not in HORIZONS:
        raise ValueError(
            f"unknown forecast horizon {horizon!r}; expected one of "
            f"{', '.join(sorted(HORIZONS))}"
        )
    return f"{horizon} ({HORIZONS[horizon]})"


def horizon_for_offset(hours_to_tip: float) -> str:
    """the horizon bucket a measured offset falls into, or '' if it falls outside.

    an offset at or below zero and an offset beyond the widest window both
    return '' rather than being clamped into the nearest bucket.
    """
    if not (hours_to_tip > 0):
        return ""
    for name, (lo, hi) in HORIZON_WINDOWS.items():
        if lo < hours_to_tip <= hi:
            return name
    return ""


def resolve_cutoff(run_at: pd.Timestamp | str | None = None) -> pd.Timestamp:
    """normalise a prediction-run timestamp into the training/feature cutoff."""
    ts = pd.Timestamp.now("UTC") if run_at is None else pd.Timestamp(run_at)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts.normalize()


# ---- rolling-origin evaluation schedule ----
# forward chaining, no random splits: train is everything strictly before the
# validation window. the months after O5 are the selection holdout.
ORIGINS: list[tuple[str, str, str]] = [
    ("O1 valid=2024-12", "2024-12-01", "2024-12-31"),
    ("O2 valid=2025-01", "2025-01-01", "2025-01-31"),
    ("O3 valid=2025-02", "2025-02-01", "2025-02-28"),
    ("O4 valid=2025-12", "2025-12-01", "2025-12-31"),
    ("O5 valid=2026-01", "2026-01-01", "2026-01-31"),
]

# ORIGINS stays at five, because every champion decision was made on exactly
# those five and tests/test_teammates_v3.py pins the length. the P2 bracket runs
# on this superset.
DEV_ORIGINS: list[tuple[str, str, str]] = [*ORIGINS, LATE_SEASON_ORIGIN]

# season-start questions only (MODEL.md 20.1), never in ORIGINS or DEV_ORIGINS.
# the start is a regular-season opener and is clamped to the opener found in the
# data at runtime, so a mistyped date cannot pull last season's games in.
SEASON_START_ORIGINS: list[tuple[str, str, str]] = [
    ("S1 valid=2024-10/11", "2024-10-22", "2024-11-30"),
    ("S2 valid=2025-10/11", "2025-10-21", "2025-11-30"),
]

RANDOM_STATE = 17

LGBM_PARAMS: dict[str, object] = {
    "n_estimators": 400,
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_child_samples": 50,
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.8,
    "random_state": RANDOM_STATE,
    "verbosity": -1,
    "n_jobs": -1,
}

# ---------------------------------------------------------------------------
# P3: the contextual residual rate challenger. nothing below is served.
# ---------------------------------------------------------------------------
# small on purpose: four stats means four chances to fit noise.
RATE_MODEL_TARGETS: tuple[str, ...] = ("PTS", "AST", "REB", "FGA")

# the shared context columns. the model also reads the row's served rate for
# the stat, a minutes column and a one-hot POS_GROUP, which rate_model adds
# because their names depend on the stat or the scoring stage.
RATE_CONTEXT_COLS: list[str] = [
    "usg_ewma",
    "exp_vacated_usg",
    "p_star_out",
    "exp_top3_usage_out",
    "exp_depth_rank",
    "IS_HOME",
    "TEAM_REST_DAYS",
    "IS_B2B",
    "OPP_DEF_FORM",
    "own_pace",
    "opp_pace",
    "opp_def_rating",
    "opp_fg3a_allowed_per100",
    "opp_fta_allowed_per100",
    "minutes_share",
]

# LGBM_PARAMS overfit the P2 blowout frame badly, and a residual target is
# mostly noise, so this is deliberately shallow and heavily regularised.
RATE_MODEL_PARAMS: dict[str, object] = {
    "n_estimators": 200,
    "learning_rate": 0.05,
    "num_leaves": 15,
    "min_child_samples": 200,
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.8,
    "random_state": RANDOM_STATE,
    "verbosity": -1,
    "n_jobs": -1,
}

RATE_MODEL_CUTOFF = "RATE_MODEL_CUTOFF"

# ---- P3's pre-registered promotion rule, written before any result ----
P3_PROMOTION_FLOOR: float = 0.01
P3_COHORT_REGRESSION_TOLERANCE: float = 0.01
P3_V5_GATED_ENDPOINTS: tuple[str, ...] = ("availability_brier", "minutes_mae")
P3_RATE_GATED_ENDPOINTS: tuple[str, ...] = ("cond_pts_mae", "uncond_pts_mae")


def season_tag(season: str) -> str:
    """'2023-24' -> '2023_24', the suffix used by the parquet fixtures."""
    return season.replace("-", "_")


RATE_HALFLIFE_GRID: tuple[float, ...] = (3.0, 5.0, 8.0, 12.0, 20.0)

# what a stat falls back to when the inner folds cannot separate two halflives.
RATE_HALFLIFE_DEFAULT: float = EWMA_HALFLIFE


def rate_halflife(target: str) -> float:
    """the selected EWMA halflife for one production rate, or the default."""
    return float(RATE_HALFLIVES.get(target, RATE_HALFLIFE_DEFAULT))


def rate_estimator(target: str) -> str:
    """'ewma' or 'expanding': which smoother produces this stat's rate."""
    kind = RATE_ESTIMATORS.get(target, "ewma")
    if kind not in ("ewma", "expanding"):
        raise ValueError(
            f"unknown rate estimator {kind!r} for {target}; expected 'ewma' or "
            f"'expanding'"
        )
    return kind
