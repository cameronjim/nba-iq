"""score stored prediction runs against what happened (MODEL.md 13.3).

pure: frames in, frames out, no database. ``score_runs.py`` is the loader.

input shapes:
  predictions  long, exactly as player_game_predictions holds it: run_id,
               nba_player_id, nba_game_id, game_date, stat, quantile, value,
               conditional.
  runs         prediction_runs: id, model_version, feature_version,
               predicted_at, forecast_cutoff_at, notes, and channel /
               information_as_of / history_through when migration 015 is applied.
  truth        one row per predicted player-game in a completed game:
               nba_player_id, nba_game_id, game_date, season_type, played,
               minutes and the eleven box stats. ``played`` is null when no
               player_game_status row exists, which is a coverage miss.

  baseline     optional, one row per (player, game), from :func:`build_baselines`
               and :func:`seeded_rate_baselines`: the free comparators of the
               13.4 ladder, rebuilt from the truth layer as of each game's date.

the ladder rung ``expanding`` x the stored conditional minutes is not rebuilt
for E5: E5 is defined against ``ewma_total`` alone (13.3). the expanding family
is rebuilt only for F7 and F8, seeded from the artifact's ``ewma_state``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from .config import (
    EVENT_COHORTS,
    HISTORY_SEASON_TYPES,
    PROSPECTIVE_FALSIFICATION,
    PROSPECTIVE_LOOKS,
    PROSPECTIVE_RATE_HALFLIVES,
    PROSPECTIVE_RATE_TARGETS,
    PROSPECTIVE_RUN_NOTE_LABEL,
    RATE_HALFLIFE_DEFAULT,
    RATE_HISTORY_INCLUDES_POSTSEASON,
    RATE_MINUTES_FLOOR,
    TIER_BASIS,
    TIER_ORDER,
    TRAINING_SEASON_TYPES,
    is_cold_start,
)
from .features import assign_minutes_tier
from .intervals import QUANTILE_LEVELS, quantile_columns
from .promotion import BLOCK_DAYS, moving_block_bootstrap
from .store import PROB_ACTIVE, PROB_ACTIVE_MODEL, STAT_NAMES, UNCOND_SUFFIX

# db stat name -> the SCREAMING_SNAKE name predict.py's columns use.
STATS: dict[str, str] = {db: internal for internal, db in STAT_NAMES.items()}

PROBABILITIES: tuple[str, ...] = (PROB_ACTIVE, PROB_ACTIVE_MODEL)

KEY: list[str] = ["nba_player_id", "nba_game_id"]
RESULT_COLUMNS: list[str] = ["run_id", "cohort", "endpoint", "stat", "n", "value"]

# the bare prefix, not the versioned label, so every re-freeze pools with v1.
PROSPECTIVE_PREFIX = PROSPECTIVE_RUN_NOTE_LABEL.rsplit("_v", 1)[0]

# migration 015 may not be applied yet; these note tokens are the fallback.
SHADOW_NOTES: tuple[str, ...] = ("shadow=true", "channel=shadow")

SCORED = "scored"
COVERAGE_MISS = "coverage_miss"
LOG_MISS = "log_miss"
PENDING = "pending"
OUTCOMES: tuple[str, ...] = (SCORED, COVERAGE_MISS, LOG_MISS, PENDING)

COHORT_ALL = "ALL"

ENDPOINT_FAMILIES: dict[str, tuple[str, ...]] = {
    "coverage": ("coverage",),
    "E1 availability": ("E1_brier", "E1_brier_skill", "override_increment"),
    "E2 calibration": ("E2_calibration_slope", "E2_calibration_intercept"),
    "E3 minutes": ("E3_minutes_mae",),
    "E4 unconditional MAE": ("E4_uncond_mae",),
    "interval coverage P10-P90": ("interval_coverage",),
    "mean bias": ("bias_uncond", "bias_cond"),
    "E5 vs ewma_total (relative %, positive = better)": ("E5_rel_improvement_pct",),
    "frozen baselines (relative %, positive = better)": (
        "minutes_vs_ewma_rel_improvement_pct",
        "stl_expanding_vs_h20_pct",
        "h20_vs_expanding_pct",
    ),
}

_EPS = 1e-6

# 13.3's endpoints read regular-season games only; any other graded season type
# (the preseason, MODEL.md 20.5) is reported as its own split beside them.
ENDPOINT_SEASON_TYPES: tuple[str, ...] = TRAINING_SEASON_TYPES
SEASON_TYPE_COHORT_PREFIX = "season_type="

# baseline column names; roll10_MIN is config.TIER_BASIS so the tier is assigned
# by the same function the retrospective reports use.
AVAIL_RATE = "avail_rate_10"
AVAIL_WINDOW = 10
TIER_WINDOW = 10
ROLL10_MIN = TIER_BASIS
EWMA_MIN = "ewma_MIN"
VACATED = "vacated_minutes"
VACATED_WINDOW = 20
MINUTES_TIER = "minutes_tier"
EWMA_TOTAL_HALFLIFE = RATE_HALFLIFE_DEFAULT

# the eleven shipped rate stats, by db name: E5 averages over these, not minutes.
E5_STATS: tuple[str, ...] = tuple(STAT_NAMES[t] for t in PROSPECTIVE_RATE_TARGETS)
E5_AGGREGATE = "aggregate"

# F7 is STL's shipped expanding rate against h20; F8 is the h20 trio against expanding.
F7_STATS: tuple[str, ...] = ("stl",)
F8_STATS: tuple[str, ...] = ("reb", "tov", "fg3m")
RATE_FAMILY_STATS: tuple[str, ...] = (*F7_STATS, *F8_STATS)
RATE_FAMILIES: tuple[str, ...] = ("ewma", "exp")
F8_MEAN = "mean"


def ewma_total_column(stat: str) -> str:
    return f"ewma_total_{stat}"


def rate_family_column(stat: str, family: str) -> str:
    """the ewma_state snapshot's own column name, e.g. exp_STL_per_min."""
    return f"{family}_{STATS[stat]}_per_min"


def expected_column(stat: str, conditional: bool) -> str:
    internal = STATS[stat]
    return f"E_{internal}_COND" if conditional else f"E_{internal}"


def actual_column(stat: str) -> str:
    return f"A_{STATS[stat]}"


def _pivot_column(stat: str, quantile: float | None) -> str | None:
    if stat in PROBABILITIES:
        return stat if quantile is None else None
    if stat.endswith(UNCOND_SUFFIX):
        base = stat[: -len(UNCOND_SUFFIX)]
        return expected_column(base, False) if base in STATS and quantile is None else None
    if stat not in STATS:
        return None
    if quantile is None:
        return expected_column(stat, True)
    return quantile_columns(STATS[stat], (round(quantile, 2),))[round(quantile, 2)]


def pivot_predictions(long: pd.DataFrame) -> pd.DataFrame:
    """long store rows -> one row per (run_id, player, game), predict.py column names."""
    index = ["run_id", *KEY, "game_date"]
    if long.empty:
        return pd.DataFrame(columns=[*index, *PROBABILITIES])
    frame = long.copy()
    frame["nba_player_id"] = frame["nba_player_id"].astype(str)
    frame["nba_game_id"] = frame["nba_game_id"].astype(str)
    frame["game_date"] = pd.to_datetime(frame["game_date"]).dt.normalize()
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce").astype(float)
    quantiles = pd.to_numeric(frame["quantile"], errors="coerce").astype(float)
    frame["column"] = [
        _pivot_column(str(stat), None if pd.isna(q) else float(q))
        for stat, q in zip(frame["stat"], quantiles)
    ]
    frame = frame[frame["column"].notna()]
    wide = frame.pivot_table(
        index=index, columns="column", values="value", aggfunc="first"
    ).reset_index()
    wide.columns.name = None
    for column in PROBABILITIES:
        if column not in wide.columns:
            wide[column] = np.nan
    return wide


def _played(values: pd.Series) -> pd.Series:
    mapped = values.map(lambda v: np.nan if v is None or pd.isna(v) else float(bool(v)))
    return mapped.astype(float)


def align_truth(pivot: pd.DataFrame, truth: pd.DataFrame) -> pd.DataFrame:
    """attach outcomes; every pivot row is kept and labelled with an ``outcome``.

    pending: no truth row (game not complete). coverage_miss: completed game, no
    status row. log_miss: played, but no box line. scored: everything else.
    """
    stat_columns = [s for s in STATS if s in truth.columns]
    right = truth[[*KEY, "season_type", "played", *stat_columns]].copy()
    right["nba_player_id"] = right["nba_player_id"].astype(str)
    right["nba_game_id"] = right["nba_game_id"].astype(str)
    right["played"] = _played(right["played"])
    right["_truth"] = True
    frame = pivot.merge(right, on=KEY, how="left", validate="many_to_one")

    played = frame["played"]
    actuals = pd.DataFrame(
        {s: pd.to_numeric(frame[s], errors="coerce").astype(float) for s in stat_columns},
        index=frame.index,
    )
    # a non-appearance is a realized zero for every unconditional endpoint.
    actuals.loc[played == 0.0] = 0.0
    log_missing = (played == 1.0) & actuals.isna().any(axis=1)

    frame["outcome"] = SCORED
    frame.loc[log_missing, "outcome"] = LOG_MISS
    frame.loc[frame["_truth"].notna() & played.isna(), "outcome"] = COVERAGE_MISS
    frame.loc[frame["_truth"].isna(), "outcome"] = PENDING

    for stat in stat_columns:
        frame[actual_column(stat)] = actuals[stat]
    frame["cold_start"] = is_cold_start(frame["game_date"]).to_numpy()
    return frame.drop(columns=[*stat_columns, "_truth"])


def brier(p: pd.Series, y: pd.Series) -> float:
    return float(np.mean((p.to_numpy(float) - y.to_numpy(float)) ** 2))


def calibration(p: pd.Series, y: pd.Series) -> tuple[float, float]:
    """(intercept, slope) of an unpenalised logistic fit of y on logit(p)."""
    target = y.to_numpy(int)
    if len(target) < 2 or len(np.unique(target)) < 2:
        return float("nan"), float("nan")
    clipped = np.clip(p.to_numpy(float), _EPS, 1 - _EPS)
    logit = np.log(clipped / (1 - clipped)).reshape(-1, 1)
    if np.ptp(logit) == 0:
        return float("nan"), float("nan")
    model = LogisticRegression(C=np.inf, max_iter=1000).fit(logit, target)
    return float(model.intercept_[0]), float(model.coef_[0, 0])


def _row(cohort: str, endpoint: str, stat: str, n: int, value: float) -> dict[str, object]:
    return {"cohort": cohort, "endpoint": endpoint, "stat": stat, "n": int(n), "value": value}


def _availability_rows(
    frame: pd.DataFrame, cohort: str, baseline: pd.Series | None
) -> list[dict[str, object]]:
    known = frame[frame["outcome"].isin((SCORED, LOG_MISS))]
    rows: list[dict[str, object]] = []
    briers: dict[str, float] = {}
    for prob in PROBABILITIES:
        rated = known[known[prob].notna()]
        value = brier(rated[prob], rated["played"]) if len(rated) else float("nan")
        briers[prob] = value
        rows.append(_row(cohort, "E1_brier", prob, len(rated), value))
        if baseline is None:
            rows.append(_row(cohort, "E1_brier_skill", prob, 0, float("nan")))
        else:
            base = baseline.reindex(rated.index)
            both = rated[base.notna()]
            reference = brier(base[both.index], both["played"]) if len(both) else 0.0
            # a perfect reference leaves the skill undefined, not infinite.
            skill = (
                1 - brier(both[prob], both["played"]) / reference
                if reference > 0 else float("nan")
            )
            rows.append(_row(cohort, "E1_brier_skill", prob, len(both), skill))
        intercept, slope = calibration(rated[prob], rated["played"]) if len(rated) else (
            float("nan"), float("nan")
        )
        rows.append(_row(cohort, "E2_calibration_slope", prob, len(rated), slope))
        rows.append(_row(cohort, "E2_calibration_intercept", prob, len(rated), intercept))
    paired = known[known[PROB_ACTIVE].notna() & known[PROB_ACTIVE_MODEL].notna()]
    increment = (
        brier(paired[PROB_ACTIVE], paired["played"])
        - brier(paired[PROB_ACTIVE_MODEL], paired["played"])
        if len(paired) else float("nan")
    )
    rows.append(_row(cohort, "override_increment", PROB_ACTIVE, len(paired), increment))
    return rows


def _stat_rows(frame: pd.DataFrame, cohort: str) -> list[dict[str, object]]:
    scored = frame[frame["outcome"] == SCORED]
    appeared = scored[scored["played"] == 1.0]
    rows: list[dict[str, object]] = []
    for stat, internal in STATS.items():
        actual = actual_column(stat)
        if actual not in frame.columns:
            continue
        uncond, cond = expected_column(stat, False), expected_column(stat, True)
        if uncond in frame.columns:
            rated = scored[scored[uncond].notna()]
            error = rated[uncond] - rated[actual]
            rows.append(_row(cohort, "E4_uncond_mae", stat, len(rated), _mean(error.abs())))
            rows.append(_row(cohort, "bias_uncond", stat, len(rated), _mean(error)))
        if cond in frame.columns:
            rated = appeared[appeared[cond].notna()]
            error = rated[cond] - rated[actual]
            if stat == "minutes":
                rows.append(_row(cohort, "E3_minutes_mae", stat, len(rated), _mean(error.abs())))
            rows.append(_row(cohort, "bias_cond", stat, len(rated), _mean(error)))
        bands = quantile_columns(internal, QUANTILE_LEVELS)
        low, high = bands[min(QUANTILE_LEVELS)], bands[max(QUANTILE_LEVELS)]
        if low in frame.columns and high in frame.columns:
            rated = appeared[appeared[low].notna() & appeared[high].notna()]
            inside = (rated[actual] >= rated[low]) & (rated[actual] <= rated[high])
            rows.append(_row(cohort, "interval_coverage", stat, len(rated), _mean(inside)))
    return rows


def _mean(values: pd.Series) -> float:
    return float(values.astype(float).mean()) if len(values) else float("nan")


def relative_improvement_pct(model_mae: float, baseline_mae: float) -> float:
    """100 x (baseline - model) / baseline: positive means the model beat the baseline."""
    if not np.isfinite(baseline_mae) or baseline_mae <= 0 or not np.isfinite(model_mae):
        return float("nan")
    return 100.0 * (baseline_mae - model_mae) / baseline_mae


def model_probability(frame: pd.DataFrame) -> pd.Series:
    """prob_active_model, else prob_active for a run that predates the model column.

    the composition comparisons are scored on the model probability (13.3), so
    the override layer cannot move them.
    """
    return frame[PROB_ACTIVE_MODEL].where(frame[PROB_ACTIVE_MODEL].notna(), frame[PROB_ACTIVE])


def _paired_improvement(
    actual: pd.Series, model: pd.Series, baseline: pd.Series
) -> tuple[int, float]:
    both = actual.notna() & model.notna() & baseline.notna()
    if not both.any():
        return 0, float("nan")
    model_mae = float((model[both] - actual[both]).abs().mean())
    base_mae = float((baseline[both] - actual[both]).abs().mean())
    return int(both.sum()), relative_improvement_pct(model_mae, base_mae)


def _baseline_rows(frame: pd.DataFrame, cohort: str) -> list[dict[str, object]]:
    """E5, the minutes-vs-EWMA row (F5) and the rate-family rows (F7, F8).

    every pair is scored on the rows where both sides exist. a row with no
    prior history has no baseline, so it is left out of that comparison, not
    filled with a league mean.
    """
    scored = frame[frame["outcome"] == SCORED]
    rows: list[dict[str, object]] = []
    p = model_probability(scored)

    improvements: list[float] = []
    counts: list[int] = []
    for stat in E5_STATS:
        cond, base, actual = expected_column(stat, True), ewma_total_column(stat), actual_column(stat)
        if not {cond, base, actual} <= set(scored.columns):
            continue
        # p x ewma_total is the pre-composition estimator: no minutes term at all.
        n, value = _paired_improvement(
            scored[actual], (p * scored[cond]).clip(lower=0.0), (p * scored[base]).clip(lower=0.0)
        )
        rows.append(_row(cohort, "E5_rel_improvement_pct", stat, n, value))
        if np.isfinite(value):
            improvements.append(value)
            counts.append(n)
    if improvements:
        rows.append(_row(cohort, "E5_rel_improvement_pct", E5_AGGREGATE, min(counts),
                         float(np.mean(improvements))))

    appeared = scored[scored["played"] == 1.0]
    minutes_cond = expected_column("minutes", True)
    if {minutes_cond, EWMA_MIN, actual_column("minutes")} <= set(appeared.columns):
        n, value = _paired_improvement(
            appeared[actual_column("minutes")], appeared[minutes_cond], appeared[EWMA_MIN]
        )
        rows.append(_row(cohort, "minutes_vs_ewma_rel_improvement_pct", "minutes", n, value))

    if minutes_cond in scored.columns:
        rows.extend(_rate_family_rows(scored, p, cohort))
    return rows


def _rate_family_rows(
    scored: pd.DataFrame, p: pd.Series, cohort: str
) -> list[dict[str, object]]:
    """both rate families composed through the same p x E[MIN | plays]."""
    minutes = scored[expected_column("minutes", True)]
    rows: list[dict[str, object]] = []

    def composed(stat: str, family: str) -> pd.Series:
        return (p * minutes * scored[rate_family_column(stat, family)]).clip(lower=0.0)

    for stat in F7_STATS:
        if not {rate_family_column(stat, f) for f in RATE_FAMILIES} <= set(scored.columns):
            continue
        n, value = _paired_improvement(
            scored[actual_column(stat)], composed(stat, "exp"), composed(stat, "ewma")
        )
        rows.append(_row(cohort, "stl_expanding_vs_h20_pct", stat, n, value))

    values: list[float] = []
    counts: list[int] = []
    for stat in F8_STATS:
        if not {rate_family_column(stat, f) for f in RATE_FAMILIES} <= set(scored.columns):
            continue
        n, value = _paired_improvement(
            scored[actual_column(stat)], composed(stat, "ewma"), composed(stat, "exp")
        )
        rows.append(_row(cohort, "h20_vs_expanding_pct", stat, n, value))
        if np.isfinite(value):
            values.append(value)
            counts.append(n)
    if len(values) == len(F8_STATS):
        rows.append(_row(cohort, "h20_vs_expanding_pct", F8_MEAN, min(counts),
                         float(np.mean(values))))
    return rows


def _coverage_rows(frame: pd.DataFrame) -> list[dict[str, object]]:
    counts = frame["outcome"].value_counts()
    total = len(frame)
    return [
        _row(COHORT_ALL, "coverage", outcome, int(counts.get(outcome, 0)),
             int(counts.get(outcome, 0)) / total if total else float("nan"))
        for outcome in OUTCOMES
    ]


def cohorts(frame: pd.DataFrame) -> dict[str, pd.Series]:
    """cohort name -> boolean mask; cold_start rows stay in ALL (13.3)."""
    masks: dict[str, pd.Series] = {COHORT_ALL: pd.Series(True, index=frame.index)}
    masks["cold_start=true"] = frame["cold_start"].astype(bool)
    masks["cold_start=false"] = ~frame["cold_start"].astype(bool)
    for season_type in sorted(frame["season_type"].dropna().unique()):
        masks[f"{SEASON_TYPE_COHORT_PREFIX}{season_type}"] = frame["season_type"] == season_type
    if MINUTES_TIER in frame.columns:
        for tier in TIER_ORDER:
            masks[tier] = frame[MINUTES_TIER] == tier
    for label, column, op, threshold in EVENT_COHORTS:
        # star_out needs usg_ewma from team box totals, which no baseline rebuilds.
        if column not in frame.columns:
            continue
        values = pd.to_numeric(frame[column], errors="coerce")
        masks[label] = values >= threshold if op == ">=" else values < threshold
    return masks


def attach_baselines(frame: pd.DataFrame, baseline: pd.DataFrame | None) -> pd.DataFrame:
    """merge the baseline columns on by key and label the minutes tier.

    a legacy ``baseline_prob`` column is read as ``avail_rate_10``.
    """
    if baseline is None:
        return frame
    keyed = baseline.rename(columns={"baseline_prob": AVAIL_RATE}).assign(
        nba_player_id=baseline["nba_player_id"].astype(str),
        nba_game_id=baseline["nba_game_id"].astype(str),
    )
    columns = [c for c in keyed.columns if c not in (*KEY, "game_date") and c not in frame.columns]
    out = frame.merge(keyed[[*KEY, *columns]].drop_duplicates(KEY), on=KEY, how="left")
    out.index = frame.index
    if ROLL10_MIN in out.columns:
        out[MINUTES_TIER] = assign_minutes_tier(out)
    return out


def score_run(
    pivot: pd.DataFrame,
    truth: pd.DataFrame,
    baseline: pd.DataFrame | None = None,
    run_label: str | None = None,
) -> pd.DataFrame:
    """every endpoint for one run (or one pooled set) as tidy long rows.

    ``baseline`` is optional, keyed by nba_player_id, nba_game_id; see
    :func:`build_baselines`. without it E1_brier_skill rows carry n=0 and NaN,
    and E5, the frozen-baseline rows and the tier and event cohorts are absent.
    """
    frame = attach_baselines(align_truth(pivot, truth), baseline)
    base = frame[AVAIL_RATE] if AVAIL_RATE in frame.columns else None

    rows = _coverage_rows(frame)
    for cohort, mask in cohorts(frame).items():
        subset = frame[mask]
        rows.extend(_availability_rows(subset, cohort, None if base is None else base[mask]))
        rows.extend(_stat_rows(subset, cohort))
        rows.extend(_baseline_rows(subset, cohort))

    if run_label is None:
        ids = pivot["run_id"].unique() if "run_id" in pivot.columns else []
        run_label = str(ids[0]) if len(ids) == 1 else "mixed"
    results = pd.DataFrame(rows)
    results.insert(0, "run_id", run_label)
    return results[RESULT_COLUMNS]


def split_endpoint_rows(
    predictions: pd.DataFrame,
    truth: pd.DataFrame,
    season_types: tuple[str, ...] = ENDPOINT_SEASON_TYPES,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """(predictions the endpoints read, excluded player-games per season type).

    a game is judged by its truth row's season type; a game with no truth row is
    pending and kept, since a pending row never reaches an endpoint anyway.
    """
    games = truth.assign(nba_game_id=truth["nba_game_id"].astype(str))
    by_game = games.drop_duplicates("nba_game_id").set_index("nba_game_id")["season_type"]
    game_type = predictions["nba_game_id"].astype(str).map(by_game)
    excluded = game_type.notna() & ~game_type.isin(season_types)
    keys = predictions.loc[excluded, KEY].astype(str).assign(season_type=game_type[excluded])
    counts = keys.drop_duplicates(KEY)["season_type"].value_counts().sort_index()
    return (
        predictions[~excluded].reset_index(drop=True),
        {str(t): int(n) for t, n in counts.items()},
    )


def excluded_season_type_rows(
    results: pd.DataFrame, season_types: tuple[str, ...] = ENDPOINT_SEASON_TYPES
) -> pd.DataFrame:
    """the season-type cohort rows for every type the endpoints do not read."""
    cohort = results["cohort"].astype(str)
    split = cohort.str.startswith(SEASON_TYPE_COHORT_PREFIX)
    endpoint_cohorts = {f"{SEASON_TYPE_COHORT_PREFIX}{t}" for t in season_types}
    return results[split & ~cohort.isin(endpoint_cohorts)].reset_index(drop=True)


def channel_of(runs: pd.DataFrame) -> pd.Series:
    """the run's channel, from migration 015's column or else from the notes."""
    notes = runs["notes"].fillna("").astype(str)
    shadow = np.logical_or.reduce([notes.str.contains(t, regex=False) for t in SHADOW_NOTES])
    derived = np.where(shadow, "shadow", "production")
    if "channel" not in runs.columns:
        return pd.Series(derived, index=runs.index)
    return runs["channel"].where(runs["channel"].notna(), derived).astype(str)


def is_prospective(runs: pd.DataFrame) -> pd.Series:
    return runs["notes"].fillna("").astype(str).str.contains(PROSPECTIVE_PREFIX, regex=False)


def pool_label(channel: str, prospective: bool) -> str:
    return f"pooled:{channel}:{'prospective' if prospective else 'not_prospective'}"


def score_runs(
    predictions: pd.DataFrame,
    runs: pd.DataFrame,
    truth: pd.DataFrame,
    baseline: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """every run on its own, then one pooled view per (channel, prospective).

    a pool keeps only the latest-predicted run's row per player-game, so a game
    forecast by several overlapping daily runs is counted once, at its last
    pre-tip forecast.
    """
    pivot = pivot_predictions(predictions)
    if pivot.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    meta = pd.DataFrame({
        "run_id": runs["id"].to_numpy(),
        "predicted_at": pd.to_datetime(runs["predicted_at"], utc=True).to_numpy(),
        "channel": channel_of(runs).to_numpy(),
        "prospective": is_prospective(runs).to_numpy(),
    })
    pivot = pivot.merge(meta, on="run_id", how="inner")

    parts = [
        score_run(group, truth, baseline, run_label=str(run_id))
        for run_id, group in pivot.groupby("run_id", sort=True)
    ]
    for (channel, prospective), group in pivot.groupby(["channel", "prospective"], sort=True):
        latest = group.sort_values("predicted_at").drop_duplicates(KEY, keep="last")
        parts.append(score_run(latest, truth, baseline, run_label=pool_label(channel, prospective)))
    return pd.concat(parts, ignore_index=True)


def summarise(results: pd.DataFrame, per_run_cohorts: tuple[str, ...] = (COHORT_ALL,)) -> str:
    """markdown: one table per endpoint family; pools get every cohort, runs only ALL."""
    if results.empty:
        return "_nothing scored._\n"
    pooled = results["run_id"].astype(str).str.startswith("pooled:")
    shown = results[pooled | results["cohort"].isin(per_run_cohorts)]
    sections: list[str] = []
    for family, endpoints in ENDPOINT_FAMILIES.items():
        part = shown[shown["endpoint"].isin(endpoints)]
        if part.empty:
            continue
        index = ["run_id", "cohort", "endpoint"]
        stats = list(dict.fromkeys(part["stat"]))
        table = part.set_index([*index, "stat"])["value"].unstack("stat")[stats]
        order = pd.MultiIndex.from_frame(part[index].drop_duplicates())
        counts = part.groupby(index, sort=False)["n"].max()
        table = table.reindex(order)
        table.insert(0, "n", counts.reindex(order).astype(int))
        sections.append(f"### {family}\n\n{table.reset_index().to_markdown(index=False, floatfmt='.4f')}\n")
    notes: list[str] = []
    e5 = results[results["endpoint"] == "E5_rel_improvement_pct"]
    if e5.empty or (e5["n"] == 0).all():
        notes.append("E5 is not computed: no ewma_total baseline rows were supplied.")
    skill = results[results["endpoint"] == "E1_brier_skill"]
    if not skill.empty and (skill["n"] == 0).all():
        notes.append("E1 Brier skill is not computed: no shifted-appearance-rate baseline was supplied.")
    if not results["cohort"].isin(TIER_ORDER).any():
        notes.append("tier and event cohorts are not split: no baseline frame was supplied.")
    notes.append("the star_out = 1 cohort is not split: it needs usg_ewma from team box "
                 "totals, which the baseline frame does not rebuild.")
    return "\n".join(sections) + "\n" + "\n".join(f"- {note}" for note in notes) + "\n"


def _ids(values: pd.Series) -> pd.Series:
    """ids as str with nulls kept null, so a missing team never groups as 'None'."""
    return values.map(lambda v: np.nan if v is None or pd.isna(v) else str(v))


def _dates(values: pd.Series) -> pd.Series:
    dates = pd.to_datetime(values)
    if dates.dt.tz is not None:
        dates = dates.dt.tz_localize(None)
    return dates.dt.normalize().astype("datetime64[ns]")


def _asof(
    left: pd.DataFrame, state: pd.DataFrame, columns: list[str], allow_exact_matches: bool
) -> pd.DataFrame:
    """attach the latest ``state`` row per player before each left row's date.

    ``allow_exact_matches=False`` is the leakage guard, exactly as in features.py:
    a state row is inclusive of its own game, so the same-date row must not match.
    """
    out = left.copy()
    out["_order"] = np.arange(len(out))
    out = out.sort_values("game_date", kind="stable")
    right = state[["nba_player_id", "game_date", *columns]].dropna(subset=["nba_player_id"])
    right = right.sort_values("game_date", kind="stable")
    joined = pd.merge_asof(
        out, right, on="game_date", by="nba_player_id",
        direction="backward", allow_exact_matches=allow_exact_matches,
    )
    joined = joined.sort_values("_order").drop(columns="_order")
    joined.index = left.index
    return joined


def appearance_season_types(include_postseason: bool | None = None) -> tuple[str, ...]:
    """the season types a baseline's appearance history reads, following the model's switch."""
    if include_postseason is None:
        include_postseason = RATE_HISTORY_INCLUDES_POSTSEASON
    return HISTORY_SEASON_TYPES if include_postseason else TRAINING_SEASON_TYPES


def baseline_history_label(include_postseason: bool | None = None) -> str:
    return ", ".join(appearance_season_types(include_postseason))


def _of_season_types(frame: pd.DataFrame, season_types: tuple[str, ...]) -> pd.DataFrame:
    """rows of those season types; a frame with no season_type column is kept whole."""
    if "season_type" not in frame.columns:
        return frame
    return frame[frame["season_type"].isin(season_types)]


def _targets(targets: pd.DataFrame) -> pd.DataFrame:
    out = targets[[*KEY, "game_date"]].copy()
    out["nba_player_id"] = out["nba_player_id"].astype(str)
    out["nba_game_id"] = out["nba_game_id"].astype(str)
    out["game_date"] = _dates(out["game_date"])
    return out.drop_duplicates(KEY).reset_index(drop=True)


def _normalise_history(history: pd.DataFrame) -> pd.DataFrame:
    frame = history.copy()
    for column in ("nba_player_id", "nba_game_id", "team_id"):
        frame[column] = _ids(frame[column]) if column in frame.columns else np.nan
    frame["game_date"] = _dates(frame["game_date"])
    frame["played"] = _played(frame["played"])
    inactive = frame["listed_inactive"] if "listed_inactive" in frame.columns else False
    frame["listed_inactive"] = pd.Series(inactive, index=frame.index).map(
        lambda v: bool(v) if v is not None and not pd.isna(v) else False
    ).astype(bool)
    for column in ("minutes", *E5_STATS):
        frame[column] = (
            pd.to_numeric(frame[column], errors="coerce").astype(float)
            if column in frame.columns else np.nan
        )
    return frame.sort_values(["nba_player_id", "game_date", "nba_game_id"]).reset_index(drop=True)


def build_baselines(
    targets: pd.DataFrame,
    history: pd.DataFrame,
    allow_exact_matches: bool = False,
    include_postseason: bool | None = None,
) -> pd.DataFrame:
    """the free comparators for every scored (player, game), as of the game's date.

    ``targets``: nba_player_id, nba_game_id, game_date. ``history``: one row per
    player_game_status row (nba_player_id, nba_game_id, team_id, game_date,
    played, listed_inactive, minutes and the eleven box stats, null when he did
    not play), covering the scored games and a lookback before them.

    returns the key plus avail_rate_10, roll10_MIN, ewma_MIN, ewma_total_<stat>
    and vacated_minutes. only rows dated strictly before the game are read, except
    that vacated_minutes reads WHO sat out the game itself: it is the oracle
    cohort selector of 13.3, never a feature.

    with a ``season_type`` column the history is scoped as features.py scopes it:
    scheduled rows are regular season only, appearances follow
    ``include_postseason`` (default config.RATE_HISTORY_INCLUDES_POSTSEASON).
    """
    out = _targets(targets)
    if history.empty:
        return out
    h = _normalise_history(history)

    # features.py's avail_rate_10: the player's own last ten scheduled rows.
    sched = _of_season_types(h, TRAINING_SEASON_TYPES)[
        ["nba_player_id", "game_date", "played"]
    ].copy()
    sched[AVAIL_RATE] = sched.groupby("nba_player_id")["played"].transform(
        lambda s: s.rolling(AVAIL_WINDOW, min_periods=1).mean()
    )
    out = _asof(out, sched, [AVAIL_RATE], allow_exact_matches)

    played = _of_season_types(h, appearance_season_types(include_postseason))
    app = played[played["played"] == 1.0].copy()
    minutes = app.groupby("nba_player_id")["minutes"]
    app[ROLL10_MIN] = minutes.transform(lambda s: s.rolling(TIER_WINDOW, min_periods=1).mean())
    app[EWMA_MIN] = minutes.transform(
        lambda s: s.ewm(halflife=float(EWMA_TOTAL_HALFLIFE), adjust=True).mean()
    )
    app["_vacated_prior"] = minutes.transform(
        lambda s: s.rolling(VACATED_WINDOW, min_periods=1).mean()
    )
    out = _asof(out, app, [ROLL10_MIN, EWMA_MIN], allow_exact_matches)

    # eval_rates' ewma_total: whole-game totals over appearances with minutes > 0.
    positive = app[app["minutes"] > 0].copy()
    totals = [ewma_total_column(stat) for stat in E5_STATS]
    for stat, column in zip(E5_STATS, totals):
        positive[column] = positive.groupby("nba_player_id")[stat].transform(
            lambda s: s.ewm(halflife=float(EWMA_TOTAL_HALFLIFE), adjust=True).mean()
        )
    out = _asof(out, positive, totals, allow_exact_matches)

    vacated = vacated_minutes(h, app, out["nba_game_id"].unique(), allow_exact_matches)
    return out.merge(vacated, on=KEY, how="left")


def vacated_minutes(
    history: pd.DataFrame,
    appearances: pd.DataFrame,
    game_ids: np.ndarray,
    allow_exact_matches: bool = False,
) -> pd.DataFrame:
    """oracle vacated_minutes per (player, game): the absent teammates' prior minutes.

    ``history`` is normalised status rows; ``appearances`` carries each played
    row's inclusive ``_vacated_prior``. absent is teammates.absence_mask's rule.
    """
    rows = history[history["nba_game_id"].isin(set(game_ids))][
        ["nba_player_id", "nba_game_id", "team_id", "game_date", "played", "listed_inactive"]
    ]
    if rows.empty:
        return pd.DataFrame(columns=[*KEY, VACATED])
    # teammates.py sums the season-to-date std_MIN; this is a 20-appearance rolling
    # mean inside the history window, unshrunk as std_MIN is.
    rows = _asof(rows, appearances, ["_vacated_prior"], allow_exact_matches)
    absent = (rows["played"] == 0.0) | rows["listed_inactive"].astype(bool)
    # a null prior contributes 0 rather than poisoning the team-game sum.
    contribution = rows["_vacated_prior"].fillna(0.0).where(absent, 0.0)
    team_game = rows["nba_game_id"].astype(str) + "|" + rows["team_id"].fillna("").astype(str)
    total = contribution.groupby(team_game).transform("sum")
    rows[VACATED] = (total - contribution).where(rows["team_id"].notna())
    return rows[[*KEY, VACATED]].drop_duplicates(KEY)


def _decay(stat: str, halflives: dict[str, float]) -> float:
    """pandas' ewm(halflife=h) keeps (1 - alpha) = 0.5 ** (1 / h) of the old weight."""
    return 0.5 ** (1.0 / float(halflives.get(STATS[stat], RATE_HALFLIFE_DEFAULT)))


def seeded_rate_baselines(
    targets: pd.DataFrame,
    appearances: pd.DataFrame,
    snapshot: pd.DataFrame,
    training_start: str | pd.Timestamp,
    stats: tuple[str, ...] = RATE_FAMILY_STATS,
    halflives: dict[str, float] = PROSPECTIVE_RATE_HALFLIVES,
    floor: float = RATE_MINUTES_FLOOR,
    allow_exact_matches: bool = False,
    include_postseason: bool | None = None,
) -> pd.DataFrame:
    """both per-minute rate families per (player, game), seeded from ewma_state.

    a snapshot row is the artifact's state just before its ``AS_OF`` date, so
    appearances on or after ``AS_OF`` are replayed onto it. its seed weight is
    the player's appearance count from ``training_start`` to ``AS_OF``, which
    makes the replay equal the career EWMA (adjust=True) and the expanding mean
    exactly. a player the snapshot does not hold is replayed from
    ``training_start``.

    ``appearances``: nba_player_id, game_date, minutes and the stats (db names),
    plus ``season_type`` when the caller has it, scoped as in :func:`build_baselines`.
    """
    out = _targets(targets)
    columns = [rate_family_column(s, f) for s in stats for f in RATE_FAMILIES]
    start = pd.Timestamp(training_start).normalize()
    app = _of_season_types(appearances, appearance_season_types(include_postseason)).copy()
    app["nba_player_id"] = app["nba_player_id"].astype(str)
    app["game_date"] = _dates(app["game_date"])
    app["minutes"] = pd.to_numeric(app["minutes"], errors="coerce").astype(float)
    for stat in stats:
        app[stat] = pd.to_numeric(app[stat], errors="coerce").astype(float)
    app = app[(app["minutes"] > 0) & (app["game_date"] >= start)]
    app = app.sort_values(["nba_player_id", "game_date"]).reset_index(drop=True)
    seeds = snapshot.rename(columns={"PLAYER_ID": "nba_player_id"}).copy()
    seeds["nba_player_id"] = seeds["nba_player_id"].astype(str)
    seeds["AS_OF"] = _dates(seeds["AS_OF"])
    seeds = seeds.drop_duplicates("nba_player_id", keep="last").set_index("nba_player_id")
    by_player = dict(tuple(app.groupby("nba_player_id", sort=False)))

    records: list[dict[str, object]] = []
    for player in out["nba_player_id"].unique():
        history = by_player.get(player, app.iloc[0:0])
        seed = seeds.loc[player] if player in seeds.index else None
        if seed is not None:
            n_prior = int((history["game_date"] < seed["AS_OF"]).sum())
            replay = history[history["game_date"] >= seed["AS_OF"]]
        else:
            n_prior, replay = 0, history
        state: dict[str, tuple[float, float]] = {}
        seed_record: dict[str, object] = {}
        for stat in stats:
            for family in RATE_FAMILIES:
                column = rate_family_column(stat, family)
                value = np.nan if seed is None else pd.to_numeric(seed.get(column), errors="coerce")
                n = max(n_prior, 1) if pd.notna(value) else 0
                if family == "ewma":
                    decay = _decay(stat, halflives)
                    weight = (1.0 - decay**n) / (1.0 - decay)
                else:
                    weight = float(n)
                state[column] = (0.0 if n == 0 else float(value) * weight, weight)
                seed_record[column] = value
        if seed is not None:
            records.append({"nba_player_id": player,
                            "game_date": seed["AS_OF"] - pd.Timedelta(days=1), **seed_record})
        for game in replay.itertuples(index=False):
            record: dict[str, object] = {"nba_player_id": player, "game_date": game.game_date}
            for stat in stats:
                ratio = getattr(game, stat) / max(game.minutes, floor)
                for family in RATE_FAMILIES:
                    column = rate_family_column(stat, family)
                    total, weight = state[column]
                    if np.isfinite(ratio):
                        if family == "ewma":
                            decay = _decay(stat, halflives)
                            total, weight = ratio + decay * total, 1.0 + decay * weight
                        else:
                            total, weight = total + ratio, weight + 1.0
                        state[column] = (total, weight)
                    record[column] = total / weight if weight > 0 else np.nan
            records.append(record)

    if not records:
        return out.assign(**{c: np.nan for c in columns})
    frame = pd.DataFrame.from_records(records, columns=["nba_player_id", "game_date", *columns])
    frame["game_date"] = frame["game_date"].astype("datetime64[ns]")
    for column in columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype(float)
    return _asof(out, frame, columns, allow_exact_matches)


def snapshot_missing_families(
    snapshot: pd.DataFrame, stats: tuple[str, ...] = RATE_FAMILY_STATS
) -> list[str]:
    """the rate-family columns F7 and F8 need that the snapshot does not carry."""
    return [rate_family_column(s, f) for s in stats for f in RATE_FAMILIES
            if rate_family_column(s, f) not in snapshot.columns]


def merge_baselines(*frames: pd.DataFrame) -> pd.DataFrame:
    """outer-join baseline frames on the key, keeping each column once."""
    merged: pd.DataFrame | None = None
    for frame in frames:
        if merged is None:
            merged = frame
            continue
        extra = [c for c in frame.columns if c not in merged.columns]
        merged = merged.merge(frame[[*KEY, *extra]], on=KEY, how="outer")
    return pd.DataFrame(columns=KEY) if merged is None else merged


SHADOW_FEATURE_TOKEN = "feature_set=v1"
PAIR_TOLERANCE = pd.Timedelta(minutes=10)
PAIR_COLUMNS: list[str] = [
    "production_run_id", "shadow_run_id", "boundary_gap_minutes", "prospective",
]
COMPARISON_COLUMNS: list[str] = [
    "endpoint", "n_rows", "n_dates", "n_pairs", "v3", "v1", "delta_pct",
    "lo_pct", "hi_pct", "p_value",
]
# the config.PROSPECTIVE_FALSIFICATION keys F2 to F4 read.
COMPARISON_ENDPOINTS: tuple[str, ...] = (
    "availability_brier_v3_vs_v1",
    "minutes_mae_v3_vs_v1",
    "pts_uncond_mae_v3_vs_v1",
)
COMPARISON_ORIGIN = "prospective"
COMPARISON_RUN_LABEL = "paired:production_vs_shadow"


def information_boundary(runs: pd.DataFrame) -> pd.Series:
    """information_as_of (migration 015), else forecast_cutoff_at, in utc."""
    cutoff = pd.to_datetime(runs["forecast_cutoff_at"], utc=True)
    if "information_as_of" not in runs.columns:
        return cutoff
    information = pd.to_datetime(runs["information_as_of"], utc=True)
    return information.where(information.notna(), cutoff)


def is_shadow_comparator(runs: pd.DataFrame) -> pd.Series:
    notes = runs["notes"].fillna("").astype(str)
    return (channel_of(runs) == "shadow") | notes.str.contains(SHADOW_FEATURE_TOKEN, regex=False)


def pair_runs(
    pivot: pd.DataFrame, runs: pd.DataFrame, tolerance: pd.Timedelta = PAIR_TOLERANCE
) -> pd.DataFrame:
    """pair each v1 shadow with the served run on its slate and boundary.

    same slate is the same set of game dates; same boundary is information
    boundaries within ``tolerance``. a shadow at another boundary is a horizon
    comparison wearing a feature-set label (13.4), so it stays unpaired.
    """
    if pivot.empty or runs.empty:
        return pd.DataFrame(columns=PAIR_COLUMNS)
    slates = pivot.groupby("run_id")["game_date"].agg(lambda d: frozenset(pd.to_datetime(d)))
    meta = pd.DataFrame({
        "run_id": runs["id"].to_numpy(),
        "boundary": information_boundary(runs).to_numpy(),
        "shadow": is_shadow_comparator(runs).to_numpy(),
        "prospective": is_prospective(runs).to_numpy(),
    })
    meta = meta[meta["run_id"].isin(slates.index)]
    served = meta[~meta["shadow"]]
    pairs: list[dict[str, object]] = []
    taken: set[object] = set()
    for _, shadow in meta[meta["shadow"]].sort_values("run_id").iterrows():
        slate = slates[shadow["run_id"]]
        gaps = (served["boundary"] - shadow["boundary"]).abs()
        same_slate = served["run_id"].map(lambda r: slates[r] == slate)
        match = served[(gaps <= tolerance) & same_slate & ~served["run_id"].isin(taken)]
        if match.empty:
            continue
        best = match.assign(gap=gaps[match.index]).sort_values(["gap", "run_id"]).iloc[0]
        taken.add(best["run_id"])
        pairs.append({
            "production_run_id": best["run_id"],
            "shadow_run_id": shadow["run_id"],
            "boundary_gap_minutes": float(best["gap"] / pd.Timedelta(minutes=1)),
            "prospective": bool(best["prospective"] and shadow["prospective"]),
        })
    return pd.DataFrame(pairs, columns=PAIR_COLUMNS)


def _comparison_losses(frame: pd.DataFrame) -> pd.DataFrame:
    """per-row loss for F2 to F4's endpoints, NaN where the row does not count."""
    known = frame["outcome"].isin((SCORED, LOG_MISS))
    scored = frame["outcome"] == SCORED
    played = frame["played"]
    p_model = frame[PROB_ACTIVE_MODEL]
    missing = pd.Series(np.nan, index=frame.index)
    minutes = frame.get(expected_column("minutes", True), missing)
    pts = frame.get(expected_column("pts", True), missing)
    return pd.DataFrame({
        "availability_brier_v3_vs_v1": ((p_model - played) ** 2).where(known),
        "minutes_mae_v3_vs_v1": (minutes - frame.get(actual_column("minutes"), missing))
        .abs().where(scored & (played == 1.0)),
        "pts_uncond_mae_v3_vs_v1": ((p_model * pts).clip(lower=0.0)
                                    - frame.get(actual_column("pts"), missing)).abs().where(scored),
    }, index=frame.index)


def compare_served_shadow(
    predictions: pd.DataFrame,
    runs: pd.DataFrame,
    truth: pd.DataFrame,
    prospective_only: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(pairs, comparison): v3 served vs v1 shadow on identical rows, bootstrapped.

    delta_pct is 100 x (v3 - v1) / v1, negative = v3 better, 13.5's sign; the
    interval is the paired 7-day moving-block bootstrap's, flipped to that sign.
    a player-game forecast by several pairs keeps its latest pair.
    """
    pivot = pivot_predictions(predictions)
    pairs = pair_runs(pivot, runs)
    if prospective_only:
        pairs = pairs[pairs["prospective"].astype(bool)]
    empty = pd.DataFrame(columns=COMPARISON_COLUMNS)
    if pairs.empty:
        return pairs, empty
    predicted_at = dict(zip(runs["id"], pd.to_datetime(runs["predicted_at"], utc=True)))
    matched: list[pd.DataFrame] = []
    for pair in pairs.itertuples(index=False):
        served = align_truth(pivot[pivot["run_id"] == pair.production_run_id], truth)
        shadow = align_truth(pivot[pivot["run_id"] == pair.shadow_run_id], truth)
        left = _comparison_losses(served).join(served[[*KEY, "game_date"]])
        right = _comparison_losses(shadow).join(shadow[KEY])
        joined = left.merge(right, on=KEY, suffixes=("_v3", "_v1"), validate="one_to_one")
        joined["_predicted_at"] = predicted_at.get(pair.production_run_id)
        matched.append(joined)
    rows = pd.concat(matched, ignore_index=True).sort_values("_predicted_at", kind="stable")
    rows = rows.drop_duplicates(KEY, keep="last")

    out: list[dict[str, object]] = []
    for endpoint in COMPARISON_ENDPOINTS:
        both = rows[rows[f"{endpoint}_v3"].notna() & rows[f"{endpoint}_v1"].notna()]
        if both.empty:
            continue
        loss_v3 = both[f"{endpoint}_v3"].to_numpy(float)
        loss_v1 = both[f"{endpoint}_v1"].to_numpy(float)
        # the tournament's sign: delta = incumbent - candidate, positive = v3 better.
        result = moving_block_bootstrap(
            delta=loss_v1 - loss_v3, base_abs=loss_v1, dates=both["game_date"],
            origins=pd.Series(COMPARISON_ORIGIN, index=both.index), block=BLOCK_DAYS,
        )
        out.append({
            "endpoint": endpoint, "n_rows": result.n_rows, "n_dates": result.n_dates,
            "n_pairs": len(pairs), "v3": float(loss_v3.mean()), "v1": float(loss_v1.mean()),
            "delta_pct": -100.0 * result.theta, "lo_pct": -100.0 * result.hi,
            "hi_pct": -100.0 * result.lo, "p_value": result.p_value,
        })
    return pairs, pd.DataFrame(out, columns=COMPARISON_COLUMNS)


def comparison_results(comparison: pd.DataFrame) -> pd.DataFrame:
    """the comparison as RESULT_COLUMNS rows, so the csv carries it too."""
    if comparison.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)
    return pd.DataFrame({
        "run_id": COMPARISON_RUN_LABEL,
        "cohort": COHORT_ALL,
        "endpoint": "v3_vs_v1_delta_pct",
        "stat": comparison["endpoint"].to_numpy(),
        "n": comparison["n_rows"].astype(int).to_numpy(),
        "value": comparison["delta_pct"].astype(float).to_numpy(),
    })[RESULT_COLUMNS]


# (row, config.PROSPECTIVE_FALSIFICATION key); F9 is two-sided, so three keys.
FALSIFICATION_ROWS: tuple[tuple[str, str], ...] = (
    ("F1", "availability_brier_skill_vs_shifted_rate"),
    ("F2", "availability_brier_v3_vs_v1"),
    ("F3", "minutes_mae_v3_vs_v1"),
    ("F4", "pts_uncond_mae_v3_vs_v1"),
    ("F5", "minutes_mae_vs_ewma_baseline"),
    ("F6", "ninecat_aggregate_vs_ewma_total"),
    ("F7", "stl_expanding_vs_h20_ewma"),
    ("F8", "rare_event_h20_vs_expanding"),
    ("F9", "availability_calibration_slope_floor"),
    ("F9", "availability_calibration_slope_ceiling"),
    ("F9", "availability_calibration_abs_intercept"),
    ("F10", "override_layer_brier_increment"),
)
FALSIFICATION_COLUMNS: list[str] = [
    "row", "endpoint", "direction", "threshold", "value", "n", "observed", "status", "note",
]

PASS, FAIL = "pass", "fail"
NON_BINDING, REPORT_ONLY, NOT_COMPUTABLE = "non-binding", "report-only", "not computable"

# (endpoint, stat) in the pooled results for each key not read from the comparison.
RESULT_SOURCES: dict[str, tuple[str, str]] = {
    "availability_brier_skill_vs_shifted_rate": ("E1_brier_skill", PROB_ACTIVE_MODEL),
    "minutes_mae_vs_ewma_baseline": ("minutes_vs_ewma_rel_improvement_pct", "minutes"),
    "ninecat_aggregate_vs_ewma_total": ("E5_rel_improvement_pct", E5_AGGREGATE),
    "stl_expanding_vs_h20_ewma": ("stl_expanding_vs_h20_pct", "stl"),
    "rare_event_h20_vs_expanding": ("h20_vs_expanding_pct", F8_MEAN),
    "availability_calibration_slope_floor": ("E2_calibration_slope", PROB_ACTIVE),
    "availability_calibration_slope_ceiling": ("E2_calibration_slope", PROB_ACTIVE),
    "availability_calibration_abs_intercept": ("E2_calibration_intercept", PROB_ACTIVE),
    "override_layer_brier_increment": ("override_increment", PROB_ACTIVE),
}

UNPAIRED = "no paired prospective production and v1 shadow runs"
DEFAULT_UNAVAILABLE: dict[str, str] = {
    "availability_brier_skill_vs_shifted_rate": "no avail_rate_10 baseline rows",
    "availability_brier_v3_vs_v1": UNPAIRED,
    "minutes_mae_v3_vs_v1": UNPAIRED,
    "pts_uncond_mae_v3_vs_v1": UNPAIRED,
    "minutes_mae_vs_ewma_baseline": "no ewma_MIN baseline rows",
    "ninecat_aggregate_vs_ewma_total": "no ewma_total baseline rows",
    "stl_expanding_vs_h20_ewma": "no rows with both ewma_state rate families",
    "rare_event_h20_vs_expanding": "no rows with both ewma_state rate families",
}


def falsification_observations(
    results: pd.DataFrame,
    comparison: pd.DataFrame,
    pool: str = pool_label("production", True),
    unavailable: dict[str, str] | None = None,
) -> dict[str, tuple[float, int, str]]:
    """key -> (value, n, note) in the units config.PROSPECTIVE_FALSIFICATION uses.

    F10's value is prob_active_model Brier minus prob_active Brier, positive when
    the override helped: the opposite sign of the override_increment endpoint.
    """
    reasons = {**DEFAULT_UNAVAILABLE, **(unavailable or {})}
    pooled = results[(results["run_id"] == pool) & (results["cohort"] == COHORT_ALL)]
    observed: dict[str, tuple[float, int, str]] = {}
    for _, key in FALSIFICATION_ROWS:
        value, n = float("nan"), 0
        if key in COMPARISON_ENDPOINTS:
            match = comparison[comparison["endpoint"] == key]
            if len(match):
                value, n = float(match.iloc[0]["delta_pct"]), int(match.iloc[0]["n_rows"])
        else:
            endpoint, stat = RESULT_SOURCES[key]
            match = pooled[(pooled["endpoint"] == endpoint) & (pooled["stat"] == stat)]
            if len(match):
                value, n = float(match.iloc[0]["value"]), int(match.iloc[0]["n"])
            if key == "availability_calibration_abs_intercept":
                value = abs(value)
            if key == "override_layer_brier_increment":
                value = -value
        computable = n > 0 and np.isfinite(value)
        if computable:
            note = ""
        elif n > 0:
            note = "undefined on these rows (a single class, or a zero-error reference)"
        else:
            note = reasons.get(key, "no rows")
        observed[key] = (value if computable else float("nan"), n, note)
    return observed


def scheduled_rows(results: pd.DataFrame, pool: str = pool_label("production", True)) -> int:
    """completed scheduled rows in the pool: what 13.6's row minimums count."""
    coverage = results[(results["run_id"] == pool) & (results["endpoint"] == "coverage")
                       & results["stat"].isin((SCORED, LOG_MISS, COVERAGE_MISS))]
    return int(coverage["n"].sum())


def _look(look: str, looks: tuple[tuple[str, str, int], ...]) -> tuple[str, int]:
    for name, date, minimum in looks:
        if name == look:
            return date, int(minimum)
    raise ValueError(f"unknown look {look!r}; expected one of {[n for n, *_ in looks]}")


def look_date(look: str, looks: tuple[tuple[str, str, int], ...] = PROSPECTIVE_LOOKS) -> str:
    return _look(look, looks)[0]


def look_minimum(look: str, looks: tuple[tuple[str, str, int], ...] = PROSPECTIVE_LOOKS) -> int:
    return _look(look, looks)[1]


def falsification_table(
    observations: dict[str, tuple[float, int, str]],
    look: str,
    rows_scored: int,
    falsification: dict[str, dict[str, object]] = PROSPECTIVE_FALSIFICATION,
    looks: tuple[tuple[str, str, int], ...] = PROSPECTIVE_LOOKS,
) -> pd.DataFrame:
    """each 13.5 row read against its frozen threshold for this look.

    ``observed`` is the reading against the bar whenever there is one. ``status``
    is what the look may act on: report-only when the look has no bar,
    non-binding when the look is short of its row minimum (13.6), else the reading.
    """
    minimum = look_minimum(look, looks)
    binding = rows_scored >= minimum
    out: list[dict[str, object]] = []
    for row, key in FALSIFICATION_ROWS:
        spec = falsification[key]
        thresholds = spec["thresholds"]
        threshold = thresholds[look] if isinstance(thresholds, dict) else None
        direction = str(spec["direction"])
        value, n, note = observations.get(key, (float("nan"), 0, "no rows"))
        observed = ""
        if not np.isfinite(value):
            status = NOT_COMPUTABLE
        elif threshold is None:
            status = REPORT_ONLY
        else:
            bar = float(threshold)
            fails = value > bar if direction == "lower_is_better" else value < bar
            observed = FAIL if fails else PASS
            status = observed if binding else NON_BINDING
            if not binding:
                note = f"{rows_scored:,} scheduled rows < {minimum:,}"
        out.append({
            "row": row, "endpoint": key, "direction": direction, "threshold": threshold,
            "value": value, "n": int(n), "observed": observed, "status": status, "note": note,
        })
    table = pd.DataFrame(out, columns=FALSIFICATION_COLUMNS)
    # object dtype, so a report-only look shows None rather than a NaN bar.
    table["threshold"] = pd.Series([r["threshold"] for r in out], dtype=object)
    return table


def render_look_report(
    look: str,
    table: pd.DataFrame,
    comparison: pd.DataFrame,
    pairs: pd.DataFrame,
    rows_scored: int,
    looks: tuple[tuple[str, str, int], ...] = PROSPECTIVE_LOOKS,
    excluded: dict[str, int] | None = None,
    split: pd.DataFrame | None = None,
    pool: str = pool_label("production", True),
) -> str:
    """the 'Look report' markdown section.

    ``excluded`` counts the player-games of season types the endpoints do not
    read; ``split`` is their cohort rows, shown apart from the table.
    """
    date, minimum = _look(look, looks)
    binding = "BINDING" if rows_scored >= minimum else "NON-BINDING"
    left_out = ", ".join(f"{t} {n:,}" for t, n in (excluded or {}).items()) or "none"
    lines = [
        f"## Look report: {look}\n",
        f"- cutoff: games strictly before {date}",
        f"- endpoint rows: {', '.join(ENDPOINT_SEASON_TYPES)} only; "
        f"excluded player-games: {left_out}",
        f"- scheduled rows (pooled prospective production): {rows_scored:,}; "
        f"minimum {minimum:,}: **{binding}**",
        f"- paired served and v1 shadow runs: {len(pairs)}",
        "- units: relative % for F2 to F8 (F2 to F4 negative = v3 better, F5 to F8 "
        "positive = better), skill for F1, slope or |intercept| for F9, and model "
        "minus served Brier for F10\n",
        "### falsification table\n",
        table.to_markdown(index=False, floatfmt=".4f"),
        "",
    ]
    if not comparison.empty:
        lines += ["### v3 served vs v1 shadow (paired 7-day moving-block bootstrap)\n",
                  comparison.to_markdown(index=False, floatfmt=".4f"), ""]
    shown = None if split is None else split[split["run_id"] == pool]
    if shown is not None and not shown.empty:
        lines += ["### season types outside the endpoints (reported, never binding)\n",
                  shown[["cohort", "endpoint", "stat", "n", "value"]]
                  .to_markdown(index=False, floatfmt=".4f"), ""]
    return "\n".join(lines) + "\n"
