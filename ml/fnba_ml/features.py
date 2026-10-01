"""leakage-safe feature construction.

as-of rule: every feature on the row for game G may use information from strictly
before G, enforced by exactly two mechanisms - ``merge_asof(...,
allow_exact_matches=False)`` for anything derived from the appearance history, and
an explicit ``.shift(1)`` before every rolling/expanding window computed directly
on the universe or schedule frames.

there are FIVE as-of joins, not one, because the scope of the join must match the
scope of the window: career-scoped rolling windows, season-scoped season-to-date
means, and three career-scoped frames over narrower row sets. teammate context
runs last, after every as-of join.

the served teammate family (``exp_*``, ``p_star_out``) reads only as-of
probabilities and magnitudes and is what FEATURE_COLS names; the oracle family
(``vacated_*``, ``star_out``, ...) takes its absent-teammate SET from the target
game and is never served.

the per-minute rate columns and the teammate magnitudes are deliberately not in
config.FEATURE_COLS: they are inputs to the composition and to sums, not per-row
predictors.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .config import (
    AVAIL_WINDOWS,
    CONTEXT_P_PRIOR,
    EWMA_HALFLIFE,
    FEATURE_COLS,
    FEATURE_SETS,
    MAGNITUDE_PRIORS,
    OPP_FORM_MIN_PERIODS,
    OPP_FORM_WINDOW,
    P_CONTEXT,
    P_CONTEXT_CUTOFF,
    RATE_MINUTES_FLOOR,
    RATE_TARGETS,
    ROLL_STATS,
    ROLL_WINDOWS,
    TIER_BASIS,
    TIER_EDGES,
    TIER_LABELS,
    UNCOND_STATS,
    UNKNOWN_TIER,
    rate_halflife,
)
from .teammates import (
    MAGNITUDE_ESS,
    MAGNITUDE_SOURCES,
    expected_vacated_features,
    magnitude_features,
    roster_context_features,
    usage_rate_features,
    vacated_features,
)

log = logging.getLogger(__name__)

MIN_APPEARANCES_FOR_HISTORY = 3


def rate_column(target: str) -> str:
    return f"ewma_{target}_per_min"


def expanding_rate_column(target: str) -> str:
    """the baseline rate column: a career expanding mean of the per-minute ratio."""
    return f"exp_{target}_per_min"


def rate_columns(target: str) -> tuple[str, str]:
    """(ewma column, expanding column) for one production rate."""
    return rate_column(target), expanding_rate_column(target)


def per_minute_rate_features(universe: pd.DataFrame) -> pd.DataFrame:
    """career-scoped EWMA of per-minute production, ready to be as-of joined.

    one row per appearance with minutes > 0; zero-minute rows are dropped rather
    than imputed. the ratio's denominator is floored rather than the rows filtered,
    because for a fringe player the cameos are most of the history there is. the
    EWMA is inclusive of the current appearance; the shift comes from the
    allow_exact_matches=False join in build_features.
    """
    app = universe[(universe["PLAYED"] == 1) & (universe["MIN"] > 0)].copy()
    app = app.sort_values(["PLAYER_ID", "GAME_DATE"]).reset_index(drop=True)

    cols = ["PLAYER_ID", "GAME_DATE"]
    denominator = app["MIN"].clip(lower=RATE_MINUTES_FLOOR)
    for target in RATE_TARGETS:
        if target not in app.columns:
            log.warning("no %s column; skipping its per-minute rate", target)
            continue
        ratio = (app[target] / denominator).groupby(app["PLAYER_ID"])
        ewma_col = rate_column(target)
        halflife = rate_halflife(target)
        app[ewma_col] = ratio.transform(
            lambda s, h=halflife: s.ewm(halflife=h, adjust=True).mean()
        )
        cols.append(ewma_col)

        exp_col = expanding_rate_column(target)
        app[exp_col] = ratio.transform(lambda s: s.expanding(min_periods=1).mean())
        cols.append(exp_col)

    return app[cols].sort_values("GAME_DATE").reset_index(drop=True)


def attach_per_minute_rates(frame: pd.DataFrame) -> pd.DataFrame:
    """as-of join the per-minute rate columns onto a feature frame.

    a no-op on a frame built by the current pipeline; it exists to backfill
    datasets written before the rate columns did. row order is preserved.
    """
    wanted = [c for t in RATE_TARGETS for c in rate_columns(t)]
    if all(col in frame.columns for col in wanted):
        return frame
    if not {"PLAYED", "MIN", "PLAYER_ID", "GAME_DATE"} <= set(frame.columns):
        log.warning(
            "cannot backfill per-minute rates: the frame carries no appearance "
            "history (PLAYED/MIN/PLAYER_ID/GAME_DATE)"
        )
        return frame

    log.info("backfilling per-minute rate columns %s", ", ".join(wanted))
    out = frame.copy()
    out["_row_order"] = np.arange(len(out))
    # both sides of a merge_asof must carry real datetimes; a parquet round-trip can
    # hand back GAME_DATE as object dtype
    out["GAME_DATE"] = pd.to_datetime(out["GAME_DATE"])
    out = out.sort_values("GAME_DATE").reset_index(drop=True)
    out = pd.merge_asof(
        out,
        per_minute_rate_features(out),
        on="GAME_DATE",
        by="PLAYER_ID",
        direction="backward",
        allow_exact_matches=False,  # the leakage guard
    )
    return (
        out.sort_values("_row_order")
        .drop(columns=["_row_order"])
        .reset_index(drop=True)
    )


def player_appearance_features(
    universe: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """rolling/expanding stats on the appearance frame, ready to be as-of joined.

    returns (career, season) because the two have different scopes and therefore
    need different as-of join keys: PLAYER_ID alone, and PLAYER_ID + SEASON.
    """
    app = universe[universe["PLAYED"] == 1].copy()
    app = app.sort_values(["PLAYER_ID", "GAME_DATE"]).reset_index(drop=True)

    career_cols = ["PLAYER_ID", "GAME_DATE"]
    season_cols = ["PLAYER_ID", "SEASON", "GAME_DATE"]

    for stat in ROLL_STATS:
        grp = app.groupby("PLAYER_ID")[stat]
        for w in ROLL_WINDOWS:
            col = f"roll{w}_{stat}"
            # inclusive of this appearance; the as-of join supplies the shift
            app[col] = grp.transform(lambda s, w=w: s.rolling(w, min_periods=1).mean())
            career_cols.append(col)

        col = f"std_{stat}"
        app[col] = app.groupby(["PLAYER_ID", "SEASON"])[stat].transform(
            lambda s: s.expanding(min_periods=1).mean()
        )
        season_cols.append(col)

        col = f"ewma_{stat}"
        app[col] = grp.transform(lambda s: s.ewm(halflife=EWMA_HALFLIFE, adjust=True).mean())
        career_cols.append(col)

    app["n_appearances"] = app.groupby("PLAYER_ID").cumcount() + 1
    career_cols.append("n_appearances")

    app["season_appearances"] = (
        app.groupby(["PLAYER_ID", "SEASON"]).cumcount() + 1
    )
    season_cols.append("season_appearances")

    app["LAST_APP_DATE"] = app["GAME_DATE"]
    career_cols.append("LAST_APP_DATE")

    career = app[career_cols].sort_values("GAME_DATE").reset_index(drop=True)
    season = app[season_cols].sort_values("GAME_DATE").reset_index(drop=True)
    return career, season


SCHEDULE_COLS: tuple[str, ...] = (
    "SEASON", "TEAM_ID", "GAME_ID", "GAME_DATE", "TEAM_PTS_ALLOWED",
)


def schedule_features(
    universe: pd.DataFrame, schedule: pd.DataFrame | None = None
) -> pd.DataFrame:
    """one row per (season, team, game): rest days, b2b, shifted defensive form.

    ``schedule`` is a team-game frame (``SCHEDULE_COLS``) that may hold games the
    universe does not, such as other future dates; ``None`` derives it from the
    universe. unplayed games carry a null ``TEAM_PTS_ALLOWED``.
    """
    source = universe if schedule is None else schedule
    sched = (
        source[list(SCHEDULE_COLS)]
        .drop_duplicates(["SEASON", "TEAM_ID", "GAME_ID"])
        .sort_values(["TEAM_ID", "GAME_DATE"])
        .reset_index(drop=True)
    )
    if schedule is not None:
        sched["GAME_DATE"] = pd.to_datetime(sched["GAME_DATE"])

    grp = sched.groupby(["TEAM_ID", "SEASON"])

    prev_date = grp["GAME_DATE"].shift(1)
    sched["TEAM_REST_DAYS"] = (sched["GAME_DATE"] - prev_date).dt.days
    sched["IS_B2B"] = (sched["TEAM_REST_DAYS"] == 1).astype(float)
    sched.loc[sched["TEAM_REST_DAYS"].isna(), "IS_B2B"] = np.nan

    if schedule is None:
        # shift(1) FIRST so the target game's own points allowed is excluded
        sched["DEF_FORM"] = grp["TEAM_PTS_ALLOWED"].transform(
            lambda s: s.shift(1).rolling(OPP_FORM_WINDOW, min_periods=OPP_FORM_MIN_PERIODS).mean()
        )
    else:
        # a null still fills a rolling window slot, so roll over played games and carry forward
        played = sched[sched["TEAM_PTS_ALLOWED"].notna()]
        sched["_form_through"] = played.groupby(["TEAM_ID", "SEASON"])[
            "TEAM_PTS_ALLOWED"
        ].transform(lambda s: s.rolling(OPP_FORM_WINDOW, min_periods=OPP_FORM_MIN_PERIODS).mean())
        sched["DEF_FORM"] = sched.groupby(["TEAM_ID", "SEASON"])["_form_through"].transform(
            lambda s: s.shift(1).ffill()
        )

    return sched[["SEASON", "TEAM_ID", "GAME_ID", "TEAM_REST_DAYS", "IS_B2B", "DEF_FORM"]]


def _availability_history(universe: pd.DataFrame) -> pd.DataFrame:
    """availability rates over SCHEDULED rows, every window explicitly shifted."""
    g = universe.groupby("PLAYER_ID")["PLAYED"]
    for w in AVAIL_WINDOWS:
        universe[f"avail_rate_{w}"] = g.transform(
            lambda s, w=w: s.shift(1).rolling(w, min_periods=1).mean()
        )
    universe["avail_rate_std"] = universe.groupby(["PLAYER_ID", "SEASON"])["PLAYED"].transform(
        lambda s: s.shift(1).expanding(min_periods=1).mean()
    )

    # over ALL scheduled rows, misses counting as 0; contrast std_PTS, which is
    # conditional on appearing
    for stat in UNCOND_STATS:
        universe[f"uncond_std_{stat}"] = universe.groupby(["PLAYER_ID", "SEASON"])[
            stat
        ].transform(lambda s: s.shift(1).expanding(min_periods=1).mean())

    # the block id increments immediately AFTER each appearance
    prior = universe.groupby("PLAYER_ID")["PLAYED"].shift(1).fillna(0)
    universe["_block"] = prior.groupby(universe["PLAYER_ID"]).cumsum()
    universe["games_since_last_app"] = universe.groupby(["PLAYER_ID", "_block"]).cumcount()
    ever_played = (
        universe.groupby("PLAYER_ID")["PLAYED"]
        .shift(1)
        .groupby(universe["PLAYER_ID"])
        .cummax()
    )
    universe.loc[ever_played.fillna(0) == 0, "games_since_last_app"] = np.nan
    return universe.drop(columns=["_block"])


def assign_minutes_tier(frame: pd.DataFrame) -> pd.Series:
    """segment label from a strictly prior rolling mean, so it is as-of safe."""
    tier = pd.cut(
        frame[TIER_BASIS],
        bins=[-np.inf, *TIER_EDGES, np.inf],
        labels=list(TIER_LABELS),
    ).astype(object)
    return tier.fillna(UNKNOWN_TIER)


def stage0_context_probability(frame: pd.DataFrame) -> pd.Series:
    """the fallback p_j: the shifted appearance rate, with a constant prior.

    weaker than the base model's p but as-of safe: avail_rate_10 is explicitly
    shifted. CONTEXT_P_PRIOR is hand-set so it cannot become a mean over the
    evaluation window.
    """
    if "avail_rate_10" in frame.columns:
        return (
            pd.to_numeric(frame["avail_rate_10"], errors="coerce")
            .fillna(CONTEXT_P_PRIOR)
            .clip(0.0, 1.0)
        )
    return pd.Series(CONTEXT_P_PRIOR, index=frame.index, dtype=float)


def attach_expected_context(
    features: pd.DataFrame,
    probability: pd.Series | np.ndarray | None = None,
    cutoff: pd.Series | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """(re)build the served teammate-context columns from a given p_j.

    ``cutoff`` is stamped onto every row so models.validate_out_of_fold can prove
    the probability that built the features was itself out of fold. a scalar
    applies to every row; a Series must be positionally aligned.
    """
    out = features.copy()
    p = stage0_context_probability(out) if probability is None else pd.Series(
        np.asarray(probability, dtype=float), index=out.index
    )
    out[P_CONTEXT] = p.to_numpy(dtype=float)
    if cutoff is None:
        # the stage-0 baseline reads only strictly-prior games, so its information
        # boundary is the row's own game date
        out[P_CONTEXT_CUTOFF] = pd.to_datetime(out["GAME_DATE"])
    elif isinstance(cutoff, pd.Series):
        out[P_CONTEXT_CUTOFF] = pd.to_datetime(cutoff.to_numpy())
    else:
        out[P_CONTEXT_CUTOFF] = pd.Timestamp(cutoff)
    return expected_vacated_features(out, out[P_CONTEXT])


def build_features(
    universe: pd.DataFrame,
    availability_probability: pd.Series | np.ndarray | None = None,
    *,
    schedule: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """the full feature frame for a universe. pure: no io, no globals.

    ``availability_probability`` is the stage-2 out-of-fold p_j the expected
    teammate-context features are built from. ``None`` uses the stage-0 baseline
    (:func:`stage0_context_probability`), which is what every test and every
    fixture-mode build wants; ``build_dataset.py`` supplies the cross-fit
    probabilities and rebuilds the block via :func:`attach_expected_context`.

    ``schedule`` is the known team-game schedule the rest, b2b and defensive form
    columns are read from (see :func:`schedule_features`); ``None`` uses the
    universe's own games, which is what training wants.
    """
    universe = universe.copy()
    universe["GAME_DATE"] = pd.to_datetime(universe["GAME_DATE"])
    universe = universe.sort_values(["PLAYER_ID", "GAME_DATE", "GAME_ID"]).reset_index(drop=True)

    universe = _availability_history(universe)
    # roster context wants the same (player, date) sort the availability windows
    # want, so it runs before the joins re-sort the frame
    universe = roster_context_features(universe)

    career_feats, season_feats = player_appearance_features(universe)
    rate_feats = per_minute_rate_features(universe)
    usage_feats = usage_rate_features(universe)
    magnitude_feats = magnitude_features(universe)
    universe = universe.sort_values("GAME_DATE").reset_index(drop=True)

    # career-scoped
    universe = pd.merge_asof(
        universe,
        career_feats,
        on="GAME_DATE",
        by="PLAYER_ID",
        direction="backward",
        allow_exact_matches=False,  # the leakage guard
    )
    # season-scoped
    universe = pd.merge_asof(
        universe,
        season_feats,
        on="GAME_DATE",
        by=["PLAYER_ID", "SEASON"],
        direction="backward",
        allow_exact_matches=False,  # the leakage guard
    )
    # career-scoped over appearances with minutes > 0
    universe = pd.merge_asof(
        universe,
        rate_feats,
        on="GAME_DATE",
        by="PLAYER_ID",
        direction="backward",
        allow_exact_matches=False,  # the leakage guard
    )
    # career-scoped over the same narrower row set as the rates, kept on its own
    # join because this one IS a model feature
    universe = pd.merge_asof(
        universe,
        usage_feats,
        on="GAME_DATE",
        by="PLAYER_ID",
        direction="backward",
        allow_exact_matches=False,  # the leakage guard
    )
    # career-scoped over appearances: the shrunk teammate magnitudes
    universe = pd.merge_asof(
        universe,
        magnitude_feats,
        on="GAME_DATE",
        by="PLAYER_ID",
        direction="backward",
        allow_exact_matches=False,  # the leakage guard
    )

    universe["days_since_last_app"] = (
        universe["GAME_DATE"] - universe["LAST_APP_DATE"]
    ).dt.days

    sf = schedule_features(universe, schedule)
    universe = universe.merge(
        sf[["SEASON", "TEAM_ID", "GAME_ID", "TEAM_REST_DAYS", "IS_B2B"]],
        on=["SEASON", "TEAM_ID", "GAME_ID"],
        how="left",
    )
    opp = sf[["SEASON", "TEAM_ID", "GAME_ID", "DEF_FORM", "TEAM_REST_DAYS"]].rename(
        columns={
            "TEAM_ID": "OPP_TEAM_ID",
            "DEF_FORM": "OPP_DEF_FORM",
            "TEAM_REST_DAYS": "OPP_REST_DAYS",
        }
    )
    universe = universe.merge(opp, on=["SEASON", "OPP_TEAM_ID", "GAME_ID"], how="left")

    universe["has_history"] = universe["roll5_MIN"].notna().astype(int)
    universe["insufficient_history"] = (
        universe["roll5_MIN"].isna()
        | universe["n_appearances"].isna()
        | (universe["n_appearances"].fillna(0) < MIN_APPEARANCES_FOR_HISTORY)
    ).astype(int)

    universe["MIN_TIER"] = assign_minutes_tier(universe)

    # a player's FIRST scheduled row has no appearance at all, so the as-of join
    # leaves him unmatched; the prior and an ess of 0 keep every teammate sum finite
    for stat, column in MAGNITUDE_SOURCES.items():
        if column in universe.columns:
            universe[column] = universe[column].fillna(float(MAGNITUDE_PRIORS[stat]))
    if MAGNITUDE_ESS in universe.columns:
        universe[MAGNITUDE_ESS] = universe[MAGNITUDE_ESS].fillna(0.0)

    # both teammate families must come last: every magnitude they aggregate has to
    # be as-of joined already
    universe = vacated_features(universe)
    universe = universe.sort_values(
        ["GAME_DATE", "GAME_ID", "TEAM_ID", "PLAYER_ID"]
    ).reset_index(drop=True)

    return attach_expected_context(universe, availability_probability)


def attach_cross_fit_context(features: pd.DataFrame) -> pd.DataFrame:
    """stage 2 + 3: replace the stage-0 baseline p with cross-fit base-model p.

    one iteration only; the final availability model's own probabilities are never
    fed back in. joined on the row key rather than positionally, because the
    cross-fit sorts by date and pandas' default sort is not stable.
    """
    from .models import cross_fit_base_probabilities  # local import avoids a cycle

    key = ["PLAYER_ID", "GAME_ID", "TEAM_ID"]
    probabilities = cross_fit_base_probabilities(features)
    merged = features.merge(
        probabilities[[*key, P_CONTEXT, P_CONTEXT_CUTOFF, "P_CONTEXT_SOURCE"]],
        on=key, how="left", suffixes=("_stage0", ""),
    )
    if len(merged) != len(features):
        raise ValueError(
            f"the cross-fit join changed the row count ({len(features)} -> "
            f"{len(merged)}); the row key is not unique"
        )
    unscored = int(merged[P_CONTEXT].isna().sum())
    if unscored:
        raise ValueError(
            f"{unscored} scheduled rows came back from the cross-fit with no "
            f"probability; every row must be scored by some block"
        )
    merged = merged.drop(columns=[c for c in merged.columns if c.endswith("_stage0")])
    return attach_expected_context(
        merged, merged[P_CONTEXT], merged[P_CONTEXT_CUTOFF]
    )


def build_dataset(source, with_v4_candidate: bool = True) -> pd.DataFrame:
    """source -> universe -> features -> cross-fit context, the offline pipeline.

    ``with_v4_candidate`` appends the P2 matchup / blowout / stakes / start-rate
    columns; it is purely additive, since FEATURE_COLS names none of them.
    """
    from .universe import build_universe  # local import avoids a cycle

    universe = build_universe(source)
    feats = build_features(universe)
    feats = attach_cross_fit_context(feats)
    if with_v4_candidate:
        from .matchup import attach_v4_features  # local import avoids a cycle

        feats = attach_v4_features(feats, source.load_team_game_logs())
    log.info("features: %d rows, %d feature columns", len(feats), len(FEATURE_COLS))
    return feats


def available_features(frame: pd.DataFrame) -> list[str]:
    """the configured feature list, restricted to what this frame actually has."""
    return [c for c in FEATURE_COLS if c in frame.columns]


def feature_set_columns(frame: pd.DataFrame, name: str) -> list[str]:
    """one of ``config.FEATURE_SETS``, restricted to what this frame actually has.

    a list rather than a filtered frame, so the cohort definitions can still read
    the oracle columns and every pass partitions the validation rows identically.
    """
    if name not in FEATURE_SETS:
        raise ValueError(
            f"unknown feature set {name!r}; expected one of {', '.join(FEATURE_SETS)}"
        )
    columns = [c for c in FEATURE_SETS[name] if c in frame.columns]
    absent = [c for c in FEATURE_SETS[name] if c not in frame.columns]
    if absent:
        log.warning(
            "feature set %s is missing %d column(s) from this frame: %s",
            name, len(absent), ", ".join(absent),
        )
    return columns
