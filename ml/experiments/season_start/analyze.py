"""do offseason-shaped inputs push the minutes model down at the start of a season?

the 2026 preseason slate projects every starter about five minutes under his usual
(stars 32.8 usual vs 27.5 projected over 68 rows). the hypothesis under test is that
the inputs a season opener carries (days_since_last_app near 170, no season
appearances yet, NaN team and opponent rest) read to the minutes model like a long
injury absence. three measurements:

(a) retrospective: for each season with a prior season in the dataset, fit the
    champion minutes model with a cutoff at that season's opener and compare mean
    predicted vs realized minutes, by tier, for each team's games 1-3 vs games 11-20.
(b) counterfactual: on team-game-1 rows, hold every history column fixed, move ONLY
    one offseason input to an in-season value, and re-score (a partial-dependence
    ablation), for the frozen artifact and for each refit.
(c) preseason: realized Pre Season minutes from player_game_logs, by tier, as the
    measured target a preseason projection should be judged against.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

EXPERIMENT_DIR = Path(__file__).resolve().parent
ML_ROOT = EXPERIMENT_DIR.parents[1]
if str(ML_ROOT) not in sys.path:
    sys.path.insert(0, str(ML_ROOT))

from fnba_ml.config import (  # noqa: E402
    CHAMPIONS,
    DATA_DIR,
    MINUTES_TARGET,
    MODELS_DIR,
    PROSPECTIVE_MODEL_VERSION,
    ROLL_STATS,
    SERVED_FEATURE_SET,
    TIER_ORDER,
    UNCOND_STATS,
)
from fnba_ml.features import assign_minutes_tier, feature_set_columns  # noqa: E402
from fnba_ml.models import MinutesModel  # noqa: E402

log = logging.getLogger("season_start")

TIER = "MIN_TIER"
STAR = "star (>=30)"
ALL = "ALL"
GAME_NO = "TEAM_GAME_NO"
WINDOW = "WINDOW"
OPENER = "opener"
MIDSEASON = "midseason"
PRED = "MIN_PRED"
PRIOR = "prior_season"

# pre-registered, fixed before the first run.
OPENER_GAMES: tuple[int, int] = (1, 3)
MIDSEASON_GAMES: tuple[int, int] = (11, 20)
COUNTERFACTUAL_GAME = 1
STAR_SHIFT_BAR = 2.5
PRESEASON_TOLERANCE = 3.0
HYPOTHESIS_VARIANT = "days_since_last_app=3"
MIN_TRAIN_APPEARANCES = 5_000

# the 2026 preseason slate numbers that prompted the question, quoted from the brief.
PRESEASON_REFERENCE: dict[str, dict[str, float]] = {
    STAR: {"usual": 32.8, "projected": 27.5, "rows": 68},
}

VARIANTS: dict[str, dict[str, object]] = {
    "days_since_last_app=3": {"days_since_last_app": 3.0},
    "season_appearances=prior-season mean": {"season_appearances": PRIOR},
    "TEAM_REST_DAYS=2, IS_B2B=0": {"TEAM_REST_DAYS": 2.0, "IS_B2B": 0.0},
    "OPP_REST_DAYS=2": {"OPP_REST_DAYS": 2.0},
}
VARIANTS["all four together"] = {k: v for spec in VARIANTS.values() for k, v in spec.items()}

SEASON_SCOPED_COLS: tuple[str, ...] = (
    *(f"std_{s}" for s in ROLL_STATS),
    *(f"uncond_std_{s}" for s in UNCOND_STATS),
    "avail_rate_std",
)
EXPLORATORY_VARIANTS: dict[str, dict[str, object]] = {
    "season-scoped history from prior season": {c: PRIOR for c in SEASON_SCOPED_COLS},
}

PROFILE_COLS: tuple[str, ...] = (
    "days_since_last_app",
    "games_since_last_app",
    "season_appearances",
    "games_with_current_team",
    "TEAM_REST_DAYS",
    "IS_B2B",
    "OPP_REST_DAYS",
    "avail_rate_10",
    "avail_rate_std",
    "std_MIN",
)

READING_RULE = f"""\
## Pre-registered reading rule

Written before the first run and fixed in `analyze.py` as constants.

1. **The offseason-input hypothesis is SUPPORTED** if, in the counterfactual (b),
   setting `days_since_last_app` alone to 3 raises the frozen artifact's mean
   predicted minutes for star-tier (`roll10_MIN >= 30`) team-game-1 rows by more than
   **{STAR_SHIFT_BAR} minutes**. A shift of {STAR_SHIFT_BAR} or less, or a downward
   shift, is NOT SUPPORTED. If the frozen artifact cannot be loaded, the pooled refit
   models are read instead and the report says so.
2. **The preseason numbers are "accidentally right"** if realized Pre Season minutes
   for star-tier appearances (from `player_game_logs`, `season_type = 'Pre Season'`,
   minutes > 0, tier from the player's regular-season `roll10_MIN` as of the game)
   sit within **{PRESEASON_TOLERANCE} minutes** of the projected
   {PRESEASON_REFERENCE[STAR]['projected']} (the 2026 preseason slate). If no
   preseason rows exist, this is NOT MEASURABLE.
3. The retrospective (a) and every other variant are descriptive. They say how the
   two verdicts should be read; they do not move either bar.
"""


def next_season(season: str) -> str:
    """'2023-24' -> '2024-25'."""
    start = int(str(season)[:4]) + 1
    return f"{start}-{str(start + 1)[-2:]}"


def assign_tiers(frame: pd.DataFrame) -> pd.Series:
    """the frozen minutes tiers off the strictly prior roll10_MIN."""
    return assign_minutes_tier(frame)


def team_game_numbers(frame: pd.DataFrame) -> pd.Series:
    """each row's 1-based game number within its team's season, by date."""
    games = (
        frame[["SEASON", "TEAM_ID", "GAME_ID", "GAME_DATE"]]
        .drop_duplicates(["SEASON", "TEAM_ID", "GAME_ID"])
        .sort_values(["SEASON", "TEAM_ID", "GAME_DATE", "GAME_ID"])
    )
    games[GAME_NO] = games.groupby(["SEASON", "TEAM_ID"]).cumcount() + 1
    keyed = frame[["SEASON", "TEAM_ID", "GAME_ID"]].merge(
        games[["SEASON", "TEAM_ID", "GAME_ID", GAME_NO]],
        on=["SEASON", "TEAM_ID", "GAME_ID"],
        how="left",
        validate="many_to_one",
    )
    return pd.Series(keyed[GAME_NO].to_numpy(), index=frame.index, name=GAME_NO)


def season_window(
    game_numbers: pd.Series,
    opener: tuple[int, int] = OPENER_GAMES,
    midseason: tuple[int, int] = MIDSEASON_GAMES,
) -> pd.Series:
    """'opener', 'midseason' or None per row, from the team game number."""
    out = pd.Series([None] * len(game_numbers), index=game_numbers.index, dtype=object)
    out[game_numbers.between(*opener)] = OPENER
    out[game_numbers.between(*midseason)] = MIDSEASON
    return out.rename(WINDOW)


def season_openers(frame: pd.DataFrame) -> pd.Series:
    """the first regular-season game date of each season."""
    return ns_dates(frame["GAME_DATE"]).groupby(frame["SEASON"]).min().sort_index()


def prior_season_values(frame: pd.DataFrame) -> pd.DataFrame:
    """per (PLAYER_ID, SEASON): the values the counterfactual borrows from season S-1.

    season_appearances is the mean of that column over the player's prior-season rows;
    std_X is his prior-season mean of X over appearances, uncond_std_X over every
    scheduled row with a miss as 0, and avail_rate_std his prior-season played rate.
    """
    played = frame["PLAYED"] == 1
    keys = [frame["PLAYER_ID"], frame["SEASON"]]
    columns: dict[str, pd.Series] = {}
    if "season_appearances" in frame.columns:
        columns["season_appearances"] = frame["season_appearances"].groupby(keys).mean()
    for stat in ROLL_STATS:
        if stat in frame.columns:
            columns[f"std_{stat}"] = frame[stat].where(played).groupby(keys).mean()
    for stat in UNCOND_STATS:
        if stat in frame.columns:
            columns[f"uncond_std_{stat}"] = frame[stat].fillna(0.0).groupby(keys).mean()
    columns["avail_rate_std"] = frame["PLAYED"].astype(float).groupby(keys).mean()
    agg = pd.DataFrame(columns)
    agg.index = agg.index.set_names(["PLAYER_ID", "SEASON"])
    agg = agg.reset_index()
    agg["SEASON"] = agg["SEASON"].map(next_season)
    return agg


def with_prior_season(rows: pd.DataFrame, prior: pd.DataFrame) -> pd.DataFrame:
    """only the rows whose player has a prior-season entry to borrow from."""
    keys = pd.MultiIndex.from_frame(prior[["PLAYER_ID", "SEASON"]])
    mask = pd.MultiIndex.from_frame(rows[["PLAYER_ID", "SEASON"]]).isin(keys)
    return rows[mask]


def resolve_overrides(
    spec: dict[str, object], rows: pd.DataFrame, prior: pd.DataFrame
) -> dict[str, object]:
    """turn a variant spec into concrete per-column values for these rows."""
    resolved: dict[str, object] = {}
    keyed = rows[["PLAYER_ID", "SEASON"]].merge(
        prior, on=["PLAYER_ID", "SEASON"], how="left", validate="many_to_one"
    )
    for column, value in spec.items():
        if value == PRIOR:
            if column not in keyed.columns:
                raise KeyError(f"no prior-season value is defined for {column!r}")
            resolved[column] = pd.Series(keyed[column].to_numpy(), index=rows.index)
        else:
            resolved[column] = value
    return resolved


def changed_columns(before: pd.DataFrame, after: pd.DataFrame) -> list[str]:
    """the columns whose values differ, NaN equal to NaN."""
    if list(before.columns) != list(after.columns):
        raise ValueError("the two frames do not carry the same columns")
    return [c for c in before.columns if not before[c].equals(after[c])]


def build_counterfactual(frame: pd.DataFrame, overrides: dict[str, object]) -> pd.DataFrame:
    """a copy with only the named columns replaced; raises if anything else moved."""
    missing = [c for c in overrides if c not in frame.columns]
    if missing:
        raise KeyError(f"counterfactual names columns the frame lacks: {missing}")
    out = frame.copy()
    for column, value in overrides.items():
        if isinstance(value, pd.Series):
            out[column] = value.reindex(frame.index).astype(float)
        else:
            out[column] = float(value)
    moved = set(changed_columns(frame, out)) - set(overrides)
    if moved:
        raise AssertionError(f"the counterfactual changed unnamed columns: {sorted(moved)}")
    return out


def with_all_tier(frame: pd.DataFrame) -> pd.DataFrame:
    """the frame twice: once per tier and once relabelled ALL."""
    return pd.concat([frame.assign(**{TIER: ALL}), frame], ignore_index=True)


def tier_sort_key(series: pd.Series) -> pd.Series:
    order = {label: i for i, label in enumerate([ALL, *TIER_ORDER])}
    return series.map(lambda t: order.get(t, len(order)))


def minutes_summary(frame: pd.DataFrame, keys: list[str],
                    pred: str = PRED, actual: str = MINUTES_TARGET) -> pd.DataFrame:
    """rows, mean predicted, mean realized, bias (pred - realized) and MAE per group."""
    work = with_all_tier(frame[[*{*keys, TIER}, pred, actual]])
    work["_err"] = work[pred] - work[actual]
    work["_abs"] = work["_err"].abs()
    out = work.groupby(keys, dropna=False).agg(
        rows=(pred, "size"),
        mean_pred=(pred, "mean"),
        mean_realized=(actual, "mean"),
        bias=("_err", "mean"),
        mae=("_abs", "mean"),
    ).reset_index()
    return out.sort_values(
        keys, key=lambda s: tier_sort_key(s) if s.name == TIER else s
    ).reset_index(drop=True)


def opener_contrast(summary: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """per group: opener bias, midseason bias and their difference."""
    wide = summary.pivot_table(
        index=by, columns=WINDOW, values=["rows", "bias", "mean_realized", "mean_pred"],
        aggfunc="first",
    )
    out = pd.DataFrame(index=wide.index)
    for window in (OPENER, MIDSEASON):
        out[f"{window}_rows"] = wide.get(("rows", window))
        out[f"{window}_pred"] = wide.get(("mean_pred", window))
        out[f"{window}_realized"] = wide.get(("mean_realized", window))
        out[f"{window}_bias"] = wide.get(("bias", window))
    out["bias_opener_minus_midseason"] = out[f"{OPENER}_bias"] - out[f"{MIDSEASON}_bias"]
    out = out.reset_index()
    return out.sort_values(
        by, key=lambda s: tier_sort_key(s) if s.name == TIER else s
    ).reset_index(drop=True)


def shift_table(base_pred: np.ndarray, cf_pred: np.ndarray, tiers: pd.Series,
                model: str, variant: str) -> pd.DataFrame:
    """mean predicted minutes before and after one counterfactual, per tier."""
    frame = pd.DataFrame({
        TIER: tiers.to_numpy(),
        "base": np.asarray(base_pred, dtype=float),
        "counterfactual": np.asarray(cf_pred, dtype=float),
    })
    frame["shift"] = frame["counterfactual"] - frame["base"]
    out = with_all_tier(frame).groupby(TIER).agg(
        rows=("shift", "size"),
        mean_base=("base", "mean"),
        mean_counterfactual=("counterfactual", "mean"),
        mean_shift=("shift", "mean"),
        mean_abs_shift=("shift", lambda s: float(s.abs().mean())),
    ).reset_index()
    out.insert(0, "variant", variant)
    out.insert(0, "model", model)
    return out.sort_values(TIER, key=tier_sort_key).reset_index(drop=True)


def input_profile(frame: pd.DataFrame, columns: tuple[str, ...] = PROFILE_COLS) -> pd.DataFrame:
    """median, mean and null share of each offseason-sensitive input, per window."""
    rows = []
    for window, group in frame.groupby(WINDOW):
        for column in columns:
            if column not in group.columns:
                continue
            values = pd.to_numeric(group[column], errors="coerce")
            rows.append({
                WINDOW: window,
                "column": column,
                "median": float(values.median()),
                "mean": float(values.mean()),
                "null_share": float(values.isna().mean()),
            })
    return pd.DataFrame(rows, columns=[WINDOW, "column", "median", "mean", "null_share"])


def ns_dates(values: pd.Series) -> pd.Series:
    """dates at one fixed resolution, since pandas refuses to merge mixed ones."""
    return pd.to_datetime(values, errors="coerce").astype("datetime64[ns]")


def usual_minutes_asof(history: pd.DataFrame, query: pd.DataFrame) -> pd.Series:
    """the mean of each player's last 10 regular-season appearances strictly before the date."""
    app = history.loc[history["PLAYED"] == 1, ["PLAYER_ID", "GAME_DATE", MINUTES_TARGET]].copy()
    app["GAME_DATE"] = ns_dates(app["GAME_DATE"])
    app = app.sort_values(["PLAYER_ID", "GAME_DATE"])
    app["usual"] = app.groupby("PLAYER_ID")[MINUTES_TARGET].transform(
        lambda s: s.rolling(10, min_periods=1).mean()
    )
    app["PLAYER_ID"] = app["PLAYER_ID"].astype(str)
    q = query[["PLAYER_ID", "GAME_DATE"]].copy()
    q["PLAYER_ID"] = q["PLAYER_ID"].astype(str)
    q["GAME_DATE"] = ns_dates(q["GAME_DATE"])
    q["_row"] = np.arange(len(q))
    merged = pd.merge_asof(
        q.sort_values("GAME_DATE"),
        app[["PLAYER_ID", "GAME_DATE", "usual"]].sort_values("GAME_DATE"),
        on="GAME_DATE",
        by="PLAYER_ID",
        direction="backward",
        allow_exact_matches=False,
    ).sort_values("_row")
    return pd.Series(merged["usual"].to_numpy(), index=query.index, name="usual")


def preseason_table(pre: pd.DataFrame) -> pd.DataFrame:
    """realized preseason minutes on appearances, by season and tier, plus a pooled block."""
    app = pre[pd.to_numeric(pre[MINUTES_TARGET], errors="coerce") > 0].copy()
    app[MINUTES_TARGET] = app[MINUTES_TARGET].astype(float)
    if "STARTED" in app.columns:
        started = app["STARTED"].astype("boolean").fillna(False).astype(bool)
        app["_started_min"] = app[MINUTES_TARGET].where(started)
    else:
        app["_started_min"] = np.nan
    pooled = app.assign(SEASON=ALL)
    work = with_all_tier(pd.concat([pooled, app], ignore_index=True))
    out = work.groupby(["SEASON", TIER]).agg(
        rows=(MINUTES_TARGET, "size"),
        players=("PLAYER_ID", "nunique"),
        mean_usual=("usual", "mean"),
        mean_realized=(MINUTES_TARGET, "mean"),
        started_rows=("_started_min", "count"),
        mean_realized_started=("_started_min", "mean"),
    ).reset_index()
    out["realized_minus_usual"] = out["mean_realized"] - out["mean_usual"]
    out["_season_key"] = out["SEASON"].map(lambda s: "" if s == ALL else s)
    out = out.sort_values(["_season_key", TIER], key=lambda s: tier_sort_key(s) if s.name == TIER else s)
    return out.drop(columns="_season_key").reset_index(drop=True)


def hypothesis_verdict(shifts: pd.DataFrame, model: str,
                       variant: str = HYPOTHESIS_VARIANT,
                       bar: float = STAR_SHIFT_BAR) -> dict[str, object]:
    """rule 1: the star-tier shift from days_since_last_app alone against the bar."""
    row = shifts[(shifts["model"] == model) & (shifts["variant"] == variant)
                 & (shifts[TIER] == STAR)]
    if row.empty or not np.isfinite(float(row["mean_shift"].iloc[0])):
        return {"verdict": "NOT MEASURED", "shift": float("nan"), "rows": 0, "model": model}
    shift = float(row["mean_shift"].iloc[0])
    return {
        "verdict": "SUPPORTED" if shift > bar else "NOT SUPPORTED",
        "shift": shift,
        "rows": int(row["rows"].iloc[0]),
        "model": model,
    }


def preseason_verdict(table: pd.DataFrame | None,
                      reference: dict[str, dict[str, float]] = PRESEASON_REFERENCE,
                      tolerance: float = PRESEASON_TOLERANCE) -> dict[str, object]:
    """rule 2: pooled realized star preseason minutes against the projected number."""
    projected = float(reference[STAR]["projected"])
    if table is None or table.empty:
        return {"verdict": "NOT MEASURABLE", "realized": float("nan"),
                "projected": projected, "rows": 0}
    row = table[(table["SEASON"] == ALL) & (table[TIER] == STAR)]
    if row.empty or int(row["rows"].iloc[0]) == 0:
        return {"verdict": "NOT MEASURABLE", "realized": float("nan"),
                "projected": projected, "rows": 0}
    realized = float(row["mean_realized"].iloc[0])
    return {
        "verdict": ("ACCIDENTALLY RIGHT" if abs(realized - projected) <= tolerance
                    else "NOT ACCIDENTALLY RIGHT"),
        "realized": realized,
        "projected": projected,
        "rows": int(row["rows"].iloc[0]),
    }


def _md(frame: pd.DataFrame | None) -> str:
    if frame is None or frame.empty:
        return "_no rows_"
    return frame.to_markdown(index=False, floatfmt=".2f")


def render_report(facts: dict[str, object], tables: dict[str, pd.DataFrame | None],
                  verdicts: dict[str, dict[str, object]]) -> str:
    """the markdown report: the reading rule first, then the measurements."""
    h, p = verdicts["hypothesis"], verdicts["preseason"]
    ref = PRESEASON_REFERENCE[STAR]
    lines = [
        "# Season-start minutes: offseason inputs vs the minutes model",
        "",
        f"generated {facts['generated_at']} · dataset `{facts['dataset']}` · "
        f"frozen artifact `{facts['version']}`",
        "",
        "**The question.** The 2026 preseason slate projects star-tier players at "
        f"{ref['projected']} minutes against a usual {ref['usual']} ({int(ref['rows'])} rows). "
        "The hypothesis is that offseason-shaped inputs read like a long injury absence.",
        "",
        READING_RULE,
        "## Verdicts",
        "",
        "| rule | measured | bar | verdict |",
        "|---|---:|---:|:-:|",
        f"| 1. star shift from `days_since_last_app` = 3 ({h['model']}, {h['rows']} rows) "
        f"| {h['shift']:+.2f} min | > +{STAR_SHIFT_BAR} | **{h['verdict']}** |",
        f"| 2. realized preseason star minutes vs projected {p['projected']} ({p['rows']} rows) "
        f"| {p['realized']:.2f} min | within {PRESEASON_TOLERANCE} | **{p['verdict']}** |",
        "",
        "## (a) Retrospective: season openers vs games 11-20",
        "",
        "One champion minutes model per season, fitted on appearances strictly before "
        "that season's opener with the served feature set and `LGBM_PARAMS`, scoring both "
        "windows. Appearance rows only, so this is E[minutes | plays]. Bias is predicted "
        "minus realized: negative means the model under-predicts.",
        "",
        f"Seasons scored: {facts['retro_seasons'] or 'none'}. Skipped: {facts['retro_skipped'] or 'none'}.",
        "",
        "Pooled over seasons:",
        "",
        _md(tables.get("contrast_pooled")),
        "",
        "Star tier, per season:",
        "",
        _md(tables.get("contrast_star_by_season")),
        "",
        "Input profile of the offseason-sensitive columns, opener vs midseason rows:",
        "",
        _md(tables.get("profile")),
        "",
        "`games_with_current_team` is a cumulative count over every season with the team, "
        "so it does not reset at an opener and is not varied below.",
        "",
        "## (b) Counterfactual: one offseason input at a time",
        "",
        f"Team-game-{COUNTERFACTUAL_GAME} rows with history and a prior season in the "
        "dataset. Every column except the named ones is asserted unchanged; the teammate "
        "context is not rebuilt, so this is the minutes model's own response. Shift is "
        "counterfactual minus base: positive means the offseason value was holding the "
        "prediction down.",
        "",
        _md(tables.get("shift_report")),
        "",
        "Exploratory, outside the reading rule: the season-scoped history columns "
        f"({', '.join(SEASON_SCOPED_COLS)}) are also NaN at an opener. Filled from the "
        "player's prior season:",
        "",
        _md(tables.get("exploratory_report")),
        "",
        "## (c) Realized preseason minutes",
        "",
        facts["preseason_note"],
        "",
        _md(tables.get("preseason")),
        "",
        "Tier is the player's regular-season `roll10_MIN` strictly before the preseason "
        "game, so it is the same label the projection was made under.",
        "",
        "## Files",
        "",
        *[f"- `{name}`" for name in facts["csvs"]],
        "",
    ]
    return "\n".join(lines)


PRESEASON_SQL = """
SELECT
    pgl.nba_player_id AS "PLAYER_ID",
    pgl.nba_game_id   AS "GAME_ID",
    pgl.season        AS "SEASON",
    pgl.game_date     AS "GAME_DATE",
    pgl.team_id       AS "TEAM_ID",
    pgl.started       AS "STARTED",
    pgl.minutes       AS "MIN"
FROM player_game_logs pgl
WHERE pgl.season_type = %(season_type)s
ORDER BY pgl.game_date, pgl.nba_player_id
"""


def load_preseason_logs() -> tuple[pd.DataFrame | None, str]:
    """the Pre Season rows from postgres, or None and the reason they are absent."""
    try:
        import psycopg2  # noqa: PLC0415 - only needed on the database path

        from fnba_ml.data.postgres_source import load_database_url  # noqa: PLC0415

        url = load_database_url()
    except (ImportError, RuntimeError) as exc:
        return None, f"no database available ({exc})"
    with psycopg2.connect(url) as conn:
        frame = pd.read_sql_query(PRESEASON_SQL, conn, params={"season_type": "Pre Season"})
    if frame.empty:
        return None, "player_game_logs holds no rows with season_type 'Pre Season'"
    frame["PLAYER_ID"] = frame["PLAYER_ID"].astype(str)
    frame["GAME_DATE"] = ns_dates(frame["GAME_DATE"])
    frame[MINUTES_TARGET] = pd.to_numeric(frame[MINUTES_TARGET], errors="coerce").astype(float)
    return frame, f"{len(frame):,} Pre Season rows across {frame['SEASON'].nunique()} season(s)"


def fit_minutes_before(features: pd.DataFrame, cutoff: pd.Timestamp,
                       feature_cols: list[str]) -> MinutesModel | None:
    train = features[(features["GAME_DATE"] < cutoff) & (features["PLAYED"] == 1)]
    if len(train) < MIN_TRAIN_APPEARANCES:
        return None
    return MinutesModel(kind=CHAMPIONS["minutes"]).fit(train, feature_cols, cutoff)


def load_frozen_minutes(version: str, models_dir: Path) -> tuple[MinutesModel | None, str]:
    try:
        from predict import load_version  # noqa: PLC0415

        _, minutes_model, _, _ = load_version(version, models_dir)
    except (SystemExit, OSError, ValueError, ImportError) as exc:
        return None, f"frozen artifact {version} could not be loaded: {exc}"
    return minutes_model, f"frozen artifact {version}"


def counterfactual_shifts(model: MinutesModel, label: str, rows: pd.DataFrame,
                          prior: pd.DataFrame,
                          variants: dict[str, dict[str, object]]) -> pd.DataFrame:
    base = model.predict(rows)
    frames = []
    for name, spec in variants.items():
        present = {c: v for c, v in spec.items() if c in rows.columns}
        cf = build_counterfactual(rows, resolve_overrides(present, rows, prior))
        table = shift_table(base, model.predict(cf), rows[TIER], label, name)
        table["inputs_in_model"] = all(c in model.feature_cols for c in present)
        frames.append(table)
    return pd.concat(frames, ignore_index=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", type=Path, default=DATA_DIR / "dataset_v4.parquet")
    parser.add_argument("--out", type=Path, default=EXPERIMENT_DIR / "REPORT.md")
    parser.add_argument("--version", default=PROSPECTIVE_MODEL_VERSION)
    parser.add_argument("--models-dir", type=Path, default=MODELS_DIR)
    parser.add_argument("--no-db", action="store_true",
                        help="skip the preseason query even if DATABASE_URL is set")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)-7s %(name)s: %(message)s")

    from fnba_ml.cli import load_dataset  # noqa: PLC0415

    features = load_dataset(args.dataset)
    features[TIER] = assign_tiers(features)
    features[GAME_NO] = team_game_numbers(features)
    features[WINDOW] = season_window(features[GAME_NO])
    feature_cols = feature_set_columns(features, SERVED_FEATURE_SET)
    prior = prior_season_values(features)
    openers = season_openers(features)

    retro_frames, shift_frames, explore_frames = [], [], []
    retro_seasons, retro_skipped = [], []
    cf_rows_all = []
    for season, opener in openers.items():
        season_rows = features[features["SEASON"] == season]
        cf_rows = with_prior_season(season_rows[
            (season_rows[GAME_NO] == COUNTERFACTUAL_GAME)
            & (season_rows["has_history"] == 1)
        ], prior)
        cf_rows_all.append(cf_rows)
        model = fit_minutes_before(features, opener, feature_cols)
        if model is None:
            retro_skipped.append(f"{season} (fewer than {MIN_TRAIN_APPEARANCES:,} "
                                 f"appearances before {opener.date()})")
            continue
        retro_seasons.append(f"{season} (cutoff {opener.date()})")
        log.info("season %s: minutes model fitted before %s", season, opener.date())
        scored = season_rows[season_rows[WINDOW].notna() & (season_rows["PLAYED"] == 1)].copy()
        scored[PRED] = model.predict(scored)
        retro_frames.append(scored)
        if not cf_rows.empty:
            label = f"refit < {season} opener"
            shift_frames.append(counterfactual_shifts(model, label, cf_rows, prior, VARIANTS))
            explore_frames.append(
                counterfactual_shifts(model, label, cf_rows, prior, EXPLORATORY_VARIANTS))

    frozen, frozen_note = load_frozen_minutes(args.version, args.models_dir)
    log.info(frozen_note)
    primary_label = f"frozen {args.version}"
    cf_pool = pd.concat(cf_rows_all) if cf_rows_all else features.iloc[0:0]
    if frozen is not None and not cf_pool.empty:
        shift_frames.insert(0, counterfactual_shifts(frozen, primary_label, cf_pool, prior, VARIANTS))
        explore_frames.insert(
            0, counterfactual_shifts(frozen, primary_label, cf_pool, prior, EXPLORATORY_VARIANTS))
    shifts = pd.concat(shift_frames, ignore_index=True) if shift_frames else pd.DataFrame(
        columns=["model", "variant", TIER, "rows", "mean_shift"])
    explore = pd.concat(explore_frames, ignore_index=True) if explore_frames else None

    if frozen is None and not shifts.empty:
        pooled_label = "refits pooled"
        pooled = (shifts.assign(_w=shifts["rows"] * shifts["mean_shift"])
                  .groupby(["variant", TIER], as_index=False)
                  .agg(rows=("rows", "sum"), _w=("_w", "sum")))
        pooled["mean_shift"] = pooled["_w"] / pooled["rows"]
        pooled["model"] = pooled_label
        shifts = pd.concat([shifts, pooled.drop(columns="_w")], ignore_index=True)
        primary_label = pooled_label

    retro = pd.concat(retro_frames, ignore_index=True) if retro_frames else None
    tables: dict[str, pd.DataFrame | None] = {}
    if retro is not None:
        by_season = minutes_summary(retro, ["SEASON", WINDOW, TIER])
        pooled_summary = minutes_summary(retro, [WINDOW, TIER])
        tables["retro_by_season"] = by_season
        tables["retro_pooled"] = pooled_summary
        tables["contrast_pooled"] = opener_contrast(pooled_summary, [TIER])
        contrast_season = opener_contrast(by_season, ["SEASON", TIER])
        tables["contrast_by_season"] = contrast_season
        tables["contrast_star_by_season"] = contrast_season[contrast_season[TIER] == STAR]
    tables["profile"] = input_profile(features[features[WINDOW].notna()])
    report_cols = ["model", "variant", TIER, "rows", "mean_base", "mean_counterfactual",
                   "mean_shift"]
    tables["shift"] = shifts
    tables["shift_report"] = shifts[[c for c in report_cols if c in shifts.columns]]
    tables["exploratory"] = explore
    tables["exploratory_report"] = (
        explore[[c for c in report_cols if c in explore.columns]] if explore is not None else None
    )

    if args.no_db:
        pre, preseason_note = None, "skipped (--no-db)"
    else:
        pre, preseason_note = load_preseason_logs()
    if pre is not None:
        pre["usual"] = usual_minutes_asof(features, pre)
        pre[TIER] = assign_tiers(pre.rename(columns={"usual": "roll10_MIN"}))
        tables["preseason"] = preseason_table(pre)
        preseason_note = f"Source: {preseason_note}. Appearance rows only (minutes > 0)."
    else:
        tables["preseason"] = None
        preseason_note = f"**Preseason rows are absent:** {preseason_note}."

    verdicts = {
        "hypothesis": hypothesis_verdict(shifts, primary_label),
        "preseason": preseason_verdict(tables["preseason"]),
    }

    out_dir = args.out.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_names = {
        "retro_by_season": "retrospective_by_season.csv",
        "retro_pooled": "retrospective_pooled.csv",
        "contrast_by_season": "opener_contrast_by_season.csv",
        "profile": "input_profile.csv",
        "shift": "counterfactual_shift.csv",
        "exploratory": "counterfactual_exploratory.csv",
        "preseason": "preseason_minutes.csv",
    }
    written = []
    for key, name in csv_names.items():
        table = tables.get(key)
        if table is not None and not table.empty:
            table.to_csv(out_dir / name, index=False)
            written.append(name)

    facts: dict[str, object] = {
        "generated_at": pd.Timestamp.now("UTC").strftime("%Y-%m-%d %H:%M UTC"),
        "dataset": args.dataset.name,
        "version": args.version if frozen is not None else f"{args.version} (not loaded: {frozen_note})",
        "retro_seasons": ", ".join(retro_seasons),
        "retro_skipped": ", ".join(retro_skipped),
        "preseason_note": preseason_note,
        "csvs": written,
    }
    args.out.write_text(render_report(facts, tables, verdicts), encoding="utf-8")

    print("--- SEASON START ---")
    print(f"rule 1 (offseason inputs): {verdicts['hypothesis']['verdict']}  "
          f"star shift {verdicts['hypothesis']['shift']:+.2f} min ({primary_label})")
    print(f"rule 2 (preseason)       : {verdicts['preseason']['verdict']}  "
          f"realized {verdicts['preseason']['realized']:.2f} vs projected "
          f"{verdicts['preseason']['projected']}")
    print(f"report -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
