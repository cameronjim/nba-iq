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

E5 (relative MAE against the frozen ``ewma_total`` baseline) needs baseline
rows the store does not hold, so it is not computed here; E4's per-stat
unconditional MAE is the numerator it will be built from.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from .config import PROSPECTIVE_RUN_NOTE_LABEL, is_cold_start
from .intervals import QUANTILE_LEVELS, quantile_columns
from .store import PROB_ACTIVE, PROB_ACTIVE_MODEL, STAT_NAMES, UNCOND_SUFFIX

# db stat name -> the SCREAMING_SNAKE name predict.py's columns use.
STATS: dict[str, str] = {db: internal for internal, db in STAT_NAMES.items()}

PROBABILITIES: tuple[str, ...] = (PROB_ACTIVE, PROB_ACTIVE_MODEL)

KEY: list[str] = ["nba_player_id", "nba_game_id"]
RESULT_COLUMNS: list[str] = ["run_id", "cohort", "endpoint", "stat", "n", "value"]

# the bare prefix, not the versioned label, so a v2 protocol is pooled with v1.
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
}

_EPS = 1e-6


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
            skill = (
                1 - brier(both[prob], both["played"]) / brier(base[both.index], both["played"])
                if len(both) else float("nan")
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
        masks[f"season_type={season_type}"] = frame["season_type"] == season_type
    return masks


def score_run(
    pivot: pd.DataFrame,
    truth: pd.DataFrame,
    baseline: pd.DataFrame | None = None,
    run_label: str | None = None,
) -> pd.DataFrame:
    """every endpoint for one run (or one pooled set) as tidy long rows.

    ``baseline`` is optional: nba_player_id, nba_game_id, baseline_prob (the
    shifted appearance rate). when None, E1_brier_skill rows carry n=0 and NaN.
    """
    frame = align_truth(pivot, truth)
    base = None
    if baseline is not None:
        keyed = baseline.assign(
            nba_player_id=baseline["nba_player_id"].astype(str),
            nba_game_id=baseline["nba_game_id"].astype(str),
        )
        base = frame[KEY].merge(keyed[[*KEY, "baseline_prob"]], on=KEY, how="left")[
            "baseline_prob"
        ].set_axis(frame.index)

    rows = _coverage_rows(frame)
    for cohort, mask in cohorts(frame).items():
        subset = frame[mask]
        rows.extend(_availability_rows(subset, cohort, None if base is None else base[mask]))
        rows.extend(_stat_rows(subset, cohort))

    if run_label is None:
        ids = pivot["run_id"].unique() if "run_id" in pivot.columns else []
        run_label = str(ids[0]) if len(ids) == 1 else "mixed"
    results = pd.DataFrame(rows)
    results.insert(0, "run_id", run_label)
    return results[RESULT_COLUMNS]


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
    notes = ["E5 is not computed: it needs the frozen ewma_total baseline rows, which the store does not hold."]
    skill = results[results["endpoint"] == "E1_brier_skill"]
    if not skill.empty and (skill["n"] == 0).all():
        notes.append("E1 Brier skill is not computed: no shifted-appearance-rate baseline was supplied.")
    return "\n".join(sections) + "\n" + "\n".join(f"- {note}" for note in notes) + "\n"
