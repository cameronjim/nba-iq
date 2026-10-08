"""P2: matchup, blowout and season-stakes features (the v4 CANDIDATE family).

the v4 set is not served. its stakes subset (config.V5_STAKES_FEATURE_COLS) is part
of the served v7-preseason-role contract, built for unplayed rows by
attach_serving_stakes.

as-of contract: every column is a function of games strictly before the target
game, enforced by an explicit .shift(1) before every rolling / expanding window on
a frame sorted by date within its group. team_margin, blowout and team_won are
OUTCOMES of the target game, not features.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .config import (
    BLOWOUT_CROSS_FIT_MIN_TRAIN_ROWS,
    BLOWOUT_MODEL_KIND,
    BLOWOUT_MODEL_CHALLENGERS,
    BLOWOUT_SELECTION_CUTOFF,
    BLOWOUT_SELECTION_TRAIN_SHARE,
    BLOWOUT_MARGIN,
    BLOWOUT_MARGIN_COL,
    BLOWOUT_MODEL_FEATURES,
    BLOWOUT_PRIOR,
    BLOWOUT_PROB,
    BLOWOUT_PROB_CUTOFF,
    BLOWOUT_TARGET,
    COMPETITION_COL,
    CROSS_FIT_FREQ,
    FT_POSSESSION_WEIGHT,
    LATE_SEASON_GAMES_REMAINING,
    PACE_MIN_PERIODS,
    PACE_WINDOW,
    REGULAR_SEASON_GAMES,
    START_RATE_TOP_N,
    START_RATE_WINDOW,
    STAKES_LOCKED_RATIO,
    TRAINING_COMPETITIONS,
)
from .data.schema import training_rows

log = logging.getLogger(__name__)

TEAM_GAME_KEY: tuple[str, ...] = ("SEASON", "TEAM_ID", "GAME_ID")

CTX_PREFIX = "tmctx_"
CTX_PACE = f"{CTX_PREFIX}pace"
CTX_OFF_RATING = f"{CTX_PREFIX}off_rating"
CTX_DEF_RATING = f"{CTX_PREFIX}def_rating"
CTX_NET_RATING = f"{CTX_PREFIX}net_rating"
CTX_FG3A_ALLOWED = f"{CTX_PREFIX}fg3a_allowed_per100"
CTX_FTA_ALLOWED = f"{CTX_PREFIX}fta_allowed_per100"
CTX_SLOT_MINUTES = f"{CTX_PREFIX}slot_minutes"
CTX_REST_DAYS = f"{CTX_PREFIX}rest_days"
CTX_IS_B2B = f"{CTX_PREFIX}is_b2b"

ROLLING_CTX_COLS: tuple[str, ...] = (
    CTX_PACE,
    CTX_OFF_RATING,
    CTX_DEF_RATING,
    CTX_FG3A_ALLOWED,
    CTX_FTA_ALLOWED,
    CTX_SLOT_MINUTES,
)

# own team only: an opponent's standing does not decide whether my coach rests my
# starters
STAKES_CTX_COLS: tuple[str, ...] = (
    "team_games_played",
    "team_games_remaining",
    "team_win_pct",
    "team_games_over_500",
    "late_season",
    "stakes_late_x_over500",
    "stakes_lockedness",
)

OUTCOME_COLS: tuple[str, ...] = (BLOWOUT_MARGIN_COL, BLOWOUT_TARGET, "team_won")

START_RATE_COL = "top5_min_share_10"


def _pair_team_games(team_logs: pd.DataFrame) -> pd.DataFrame:
    """one row per (team, game) carrying BOTH sides' box-score totals.

    games without exactly two team-log rows are dropped: a one-sided game has no
    opponent to compute a margin or a pace against.
    """
    required = {"TEAM_ID", "GAME_ID", "SEASON", "GAME_DATE", "PTS", "MIN", "FGA",
                "FTA", "TOV"}
    missing = sorted(required - set(team_logs.columns))
    if missing:
        raise ValueError(
            f"the team log frame is missing columns the matchup features need: "
            f"{missing}"
        )

    optional = [c for c in ("FG3A",) if c in team_logs.columns]
    if not optional:
        log.warning(
            "team logs carry no FG3A; %s will be null on every row. the column is "
            "optional on purpose - the spike's parquet exports predate it - and a "
            "null column is routed as missing by LightGBM rather than being wrong",
            CTX_FG3A_ALLOWED,
        )

    cols = ["SEASON", "GAME_ID", "GAME_DATE", "TEAM_ID", "PTS", "MIN", "FGA", "FTA",
            "TOV", *optional]
    own = team_logs[cols].drop_duplicates(["GAME_ID", "TEAM_ID"]).copy()
    own["GAME_DATE"] = pd.to_datetime(own["GAME_DATE"])
    for col in ("PTS", "MIN", "FGA", "FTA", "TOV", *optional):
        own[col] = pd.to_numeric(own[col], errors="coerce").astype(float)

    sides = own.groupby("GAME_ID")["TEAM_ID"].transform("size")
    incomplete = int((sides != 2).sum())
    if incomplete:
        log.warning(
            "%d team-log rows belong to games without exactly two sides and are "
            "DROPPED from the matchup context; a one-sided game has no opponent to "
            "compute a pace or a margin against",
            incomplete,
        )
        own = own[sides == 2].copy()

    opponent = own[["GAME_ID", "TEAM_ID", "PTS", "FGA", "FTA", "TOV", *optional]].rename(
        columns={
            "TEAM_ID": "OPP_TEAM_ID",
            "PTS": "OPP_PTS",
            "FGA": "OPP_FGA",
            "FTA": "OPP_FTA",
            "TOV": "OPP_TOV",
            **{c: f"OPP_{c}" for c in optional},
        }
    )
    paired = own.merge(opponent, on="GAME_ID", how="inner")
    paired = paired[paired["TEAM_ID"] != paired["OPP_TEAM_ID"]].reset_index(drop=True)
    if len(paired) != len(own):
        raise ValueError(
            f"pairing team-games changed the row count ({len(own)} -> {len(paired)}); "
            f"a GAME_ID with two identical TEAM_IDs is a truth-layer bug"
        )
    return paired


def _per_game_rates(paired: pd.DataFrame) -> pd.DataFrame:
    """the target game's own possession arithmetic. all of it is an OUTCOME.

    nothing here may be used as a feature: every quantity reads the target game's
    box score. these columns exist to be shifted and rolled by team_game_context.
    """
    out = paired.copy()
    w = FT_POSSESSION_WEIGHT

    out["_poss"] = out["FGA"] + w * out["FTA"] + out["TOV"]
    out["_opp_poss"] = out["OPP_FGA"] + w * out["OPP_FTA"] + out["OPP_TOV"]

    # MIN is the team's TOTAL minutes, 240 in regulation and more after overtime, so
    # a lineup slot is minutes/5 rather than a hard-coded 48
    out["_slot_minutes"] = out["MIN"] / 5.0

    with np.errstate(invalid="ignore", divide="ignore"):
        out["_pace"] = out["_poss"] / out["_slot_minutes"] * 48.0
        # the defensive rating's denominator is the OPPONENT's possessions
        out["_off_rating"] = out["_poss"].rdiv(out["PTS"]) * 100.0
        out["_def_rating"] = out["OPP_PTS"] / out["_opp_poss"] * 100.0
        if "OPP_FG3A" in out.columns:
            out["_fg3a_allowed_per100"] = out["OPP_FG3A"] / out["_opp_poss"] * 100.0
        else:
            out["_fg3a_allowed_per100"] = np.nan
        out["_fta_allowed_per100"] = out["OPP_FTA"] / out["_opp_poss"] * 100.0

    out[BLOWOUT_MARGIN_COL] = out["PTS"] - out["OPP_PTS"]
    out["team_won"] = (out[BLOWOUT_MARGIN_COL] > 0).astype(float)
    out[BLOWOUT_TARGET] = (
        out[BLOWOUT_MARGIN_COL].abs() >= BLOWOUT_MARGIN
    ).astype(float)
    return out


_RAW_TO_CTX: dict[str, str] = {
    "_pace": CTX_PACE,
    "_off_rating": CTX_OFF_RATING,
    "_def_rating": CTX_DEF_RATING,
    "_fg3a_allowed_per100": CTX_FG3A_ALLOWED,
    "_fta_allowed_per100": CTX_FTA_ALLOWED,
    "_slot_minutes": CTX_SLOT_MINUTES,
}


def team_game_context(
    team_logs: pd.DataFrame,
    home_flags: pd.DataFrame | None = None,
    window: int = PACE_WINDOW,
    min_periods: int = PACE_MIN_PERIODS,
) -> pd.DataFrame:
    """one row per (season, team, game): as-of pace, ratings, style and stakes.

    every rolling column is ``group.shift(1).rolling(...)`` within (TEAM_ID,
    SEASON); the shift happens BEFORE the own/opponent merge, so an opponent column
    is a function of the opponent's prior games only. windows are season-scoped, so
    the first min_periods team-games of a season carry nulls. ``home_flags`` is an
    optional (GAME_ID, TEAM_ID, IS_HOME) frame; absent, bo_is_home is null. the
    returned frame carries OUTCOME_COLS alongside the features.
    """
    # season-scoped windows over regular-season games, as the universe is.
    paired = _per_game_rates(_pair_team_games(training_rows(team_logs)))
    paired = paired.sort_values(["TEAM_ID", "SEASON", "GAME_DATE", "GAME_ID"])
    paired = paired.reset_index(drop=True)

    grp = paired.groupby(["TEAM_ID", "SEASON"], sort=False)

    for raw, column in _RAW_TO_CTX.items():
        paired[column] = grp[raw].transform(
            lambda s, w=window, m=min_periods: (
                s.shift(1).rolling(w, min_periods=m).mean()
            )
        )
    paired[CTX_NET_RATING] = paired[CTX_OFF_RATING] - paired[CTX_DEF_RATING]

    # rest at team-game grain, duplicating features.schedule_features because the
    # blowout classifier runs before any player row exists
    prev = grp["GAME_DATE"].shift(1)
    paired[CTX_REST_DAYS] = (paired["GAME_DATE"] - prev).dt.days
    paired[CTX_IS_B2B] = (paired[CTX_REST_DAYS] == 1).astype(float)
    paired.loc[paired[CTX_REST_DAYS].isna(), CTX_IS_B2B] = np.nan

    # cumcount is 0-based, so it counts games strictly before this one and needs no
    # shift
    paired["team_games_played"] = grp.cumcount().astype(float)
    paired["team_wins_to_date"] = grp["team_won"].transform(
        lambda s: s.shift(1).expanding(min_periods=1).sum()
    ).fillna(0.0)

    played = paired["team_games_played"]
    wins = paired["team_wins_to_date"]
    with np.errstate(invalid="ignore", divide="ignore"):
        paired["team_win_pct"] = np.where(played > 0, wins / played, np.nan)
    # games over .500 = (W - L)/2 = (2W - G)/2, signed
    paired["team_games_over_500"] = (2.0 * wins - played) / 2.0
    paired["team_games_remaining"] = (
        float(REGULAR_SEASON_GAMES) - played
    ).clip(lower=0.0)
    paired["late_season"] = (
        paired["team_games_remaining"] <= float(LATE_SEASON_GAMES_REMAINING)
    ).astype(float)
    paired["stakes_late_x_over500"] = (
        paired["late_season"] * paired["team_games_over_500"]
    )
    # the clinch proxy: |games over .500| in units of the games left to change it,
    # capped at 1 and zero outside the late-season window
    remaining = paired["team_games_remaining"].clip(lower=1.0)
    paired["stakes_lockedness"] = (
        paired["late_season"]
        * (paired["team_games_over_500"].abs() / remaining).clip(upper=1.0)
    )

    paired["bo_own_net_rating"] = paired[CTX_NET_RATING]
    paired["bo_own_rest_days"] = paired[CTX_REST_DAYS]
    paired["bo_own_is_b2b"] = paired[CTX_IS_B2B]
    if home_flags is not None and len(home_flags):
        flags = home_flags[["GAME_ID", "TEAM_ID", "IS_HOME"]].drop_duplicates(
            ["GAME_ID", "TEAM_ID"]
        )
        paired = paired.merge(flags, on=["GAME_ID", "TEAM_ID"], how="left")
        paired["bo_is_home"] = pd.to_numeric(paired["IS_HOME"], errors="coerce")
        paired = paired.drop(columns=["IS_HOME"])
    else:
        log.info("no home flags supplied; bo_is_home will be null")
        paired["bo_is_home"] = np.nan

    # the opponent half, by a self-join on the SAME shifted columns; this is the
    # merge that would leak if the shift above were missing
    opp_side = paired[["GAME_ID", "TEAM_ID", CTX_NET_RATING, CTX_PACE, CTX_REST_DAYS,
                       CTX_IS_B2B]].rename(
        columns={
            "TEAM_ID": "OPP_TEAM_ID",
            CTX_NET_RATING: "bo_opp_net_rating",
            CTX_PACE: "_opp_pace",
            CTX_REST_DAYS: "bo_opp_rest_days",
            CTX_IS_B2B: "bo_opp_is_b2b",
        }
    )
    opp_side = opp_side.merge(
        paired[["GAME_ID", "TEAM_ID", "team_win_pct"]].rename(
            columns={"TEAM_ID": "OPP_TEAM_ID", "team_win_pct": "_opp_win_pct"}
        ),
        on=["GAME_ID", "OPP_TEAM_ID"], how="left",
    )
    paired = paired.merge(opp_side, on=["GAME_ID", "OPP_TEAM_ID"], how="left")
    paired["bo_net_rating_gap"] = (
        paired["bo_own_net_rating"] - paired["bo_opp_net_rating"]
    ).abs()
    paired["bo_win_pct_gap"] = (
        paired["team_win_pct"] - paired["_opp_win_pct"]
    ).abs()
    paired["bo_win_pct_sum"] = paired["team_win_pct"] + paired["_opp_win_pct"]
    paired["bo_pace_mean"] = (paired[CTX_PACE] + paired["_opp_pace"]) / 2.0

    keep = [
        *TEAM_GAME_KEY, "GAME_DATE", "OPP_TEAM_ID",
        *ROLLING_CTX_COLS, CTX_NET_RATING, CTX_REST_DAYS, CTX_IS_B2B,
        *STAKES_CTX_COLS, "team_wins_to_date",
        *BLOWOUT_MODEL_FEATURES,
        *OUTCOME_COLS,
    ]
    out = paired[keep].sort_values(["GAME_DATE", "GAME_ID", "TEAM_ID"])
    return out.reset_index(drop=True)


def cross_fit_blowout_probabilities(
    context: pd.DataFrame,
    freq: str = CROSS_FIT_FREQ,
    min_train_rows: int = BLOWOUT_CROSS_FIT_MIN_TRAIN_ROWS,
    kind: str = BLOWOUT_MODEL_KIND,
    peek: bool = False,
) -> pd.DataFrame:
    """strictly out-of-fold ``P(blowout)`` for every team-game, by forward chaining.

    the same block scheme as models.cross_fit_base_probabilities: one model per
    calendar block fitted on rows strictly before the block start, each probability
    stamped with its block start. ``peek=True`` is the leaky negative control and
    must never reach a dataset. ``kind`` names an entry in models.ESTIMATORS; blocks
    too thin to fit fall back to config.BLOWOUT_PRIOR.
    """
    from .models import ESTIMATORS, validate_out_of_fold  # noqa: PLC0415

    if kind not in ESTIMATORS:
        raise ValueError(
            f"unknown blowout estimator {kind!r}; expected one of "
            f"{sorted(ESTIMATORS)}"
        )
    make_model = ESTIMATORS[kind]

    frame = context.copy()
    frame["GAME_DATE"] = pd.to_datetime(frame["GAME_DATE"])
    frame = frame.sort_values(["GAME_DATE", "GAME_ID", "TEAM_ID"]).reset_index(drop=True)

    feats = [c for c in BLOWOUT_MODEL_FEATURES if c in frame.columns]
    if not feats:
        raise ValueError(
            "the blowout classifier has none of its features on the frame; "
            f"expected some of {list(BLOWOUT_MODEL_FEATURES)}"
        )
    if BLOWOUT_TARGET not in frame.columns:
        raise ValueError(f"the blowout classifier needs its target {BLOWOUT_TARGET!r}")

    forbidden = sorted(set(feats) & set(OUTCOME_COLS))
    if forbidden:
        raise ValueError(
            f"the blowout classifier was handed outcome columns ({forbidden}); its "
            f"whole claim is that it is PREGAME"
        )

    y = frame[BLOWOUT_TARGET].to_numpy(dtype=float)
    p = np.full(len(frame), np.nan)
    cutoff = np.full(len(frame), np.datetime64("NaT", "ns"), dtype="datetime64[ns]")
    source = np.array(["prior"] * len(frame), dtype=object)

    if peek:
        model = make_model().fit(frame[feats], y.astype(int))
        p[:] = model.predict_proba(frame[feats])[:, 1]
        cutoff[:] = frame["GAME_DATE"].to_numpy()
        source[:] = "PEEKED"
        log.warning(
            "PEEKED blowout probabilities: one model fitted on every row including "
            "its own outcome. This is a negative control and must never reach a "
            "dataset"
        )
    else:
        dates = frame["GAME_DATE"].to_numpy()
        first = frame["GAME_DATE"].min().normalize()
        last = frame["GAME_DATE"].max().normalize() + pd.Timedelta(days=1)
        edges = pd.DatetimeIndex(
            sorted(set([first, *pd.date_range(first, last, freq=freq), last]))
        )
        fitted = 0
        for start, end in zip(edges[:-1], edges[1:]):
            block = (dates >= np.datetime64(start)) & (dates < np.datetime64(end))
            if not block.any():
                continue
            train_mask = dates < np.datetime64(start)
            cutoff[block] = np.datetime64(start)
            if int(train_mask.sum()) < min_train_rows:
                p[block] = BLOWOUT_PRIOR
                continue
            train = frame.loc[train_mask]
            if train[BLOWOUT_TARGET].nunique() < 2:
                p[block] = BLOWOUT_PRIOR
                continue
            model = make_model().fit(
                train[feats], train[BLOWOUT_TARGET].astype(int)
            )
            p[block] = model.predict_proba(frame.loc[block, feats])[:, 1]
            source[block] = "model"
            fitted += 1
        log.info(
            "cross-fit blowout probabilities (%s): %d blocks fitted, %d/%d "
            "team-games from the model (%.1f%%), the rest from BLOWOUT_PRIOR",
            kind, fitted, int((source == "model").sum()), len(frame),
            100.0 * float((source == "model").mean()),
        )

    out = pd.DataFrame({
        "SEASON": frame["SEASON"].to_numpy(),
        "TEAM_ID": frame["TEAM_ID"].to_numpy(),
        "GAME_ID": frame["GAME_ID"].to_numpy(),
        "GAME_DATE": frame["GAME_DATE"].to_numpy(),
        BLOWOUT_PROB: p,
        BLOWOUT_PROB_CUTOFF: cutoff,
        "BLOWOUT_SOURCE": source,
    })
    return validate_out_of_fold(
        out, BLOWOUT_PROB, BLOWOUT_PROB_CUTOFF, "P(blowout)"
    )


def auc(y_true, p) -> float:
    """ROC AUC by the Mann-Whitney identity, tied scores taking average ranks."""
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(p, dtype=float)
    keep = np.isfinite(y) & np.isfinite(p)
    y, p = y[keep], p[keep]
    n_pos, n_neg = float(y.sum()), float(len(y) - y.sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    ranks = pd.Series(p).rank(method="average").to_numpy()
    return float((ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def select_blowout_estimator(
    context: pd.DataFrame,
    cutoff: str = BLOWOUT_SELECTION_CUTOFF,
    train_share: float = BLOWOUT_SELECTION_TRAIN_SHARE,
    kinds: tuple[str, ...] = (BLOWOUT_MODEL_KIND, *BLOWOUT_MODEL_CHALLENGERS),
) -> pd.DataFrame:
    """the inner-fold pass that chose ``config.BLOWOUT_MODEL_KIND``.

    rerunning this checks the choice rather than changing it. ``cutoff`` is the first
    development origin's validation start, so every row seen here is in the training
    window of every reported origin; the split inside it is time-ordered.
    """
    from .models import ESTIMATORS, brier, skill_score  # noqa: PLC0415

    frame = context.copy()
    frame["GAME_DATE"] = pd.to_datetime(frame["GAME_DATE"])
    inner = frame[frame["GAME_DATE"] < pd.Timestamp(cutoff)]
    inner = inner.sort_values(["GAME_DATE", "GAME_ID", "TEAM_ID"]).reset_index(drop=True)
    if len(inner) < 100:
        log.warning("only %d rows before %s; the selection is not meaningful",
                    len(inner), cutoff)
        return pd.DataFrame()

    split = int(len(inner) * train_share)
    train, valid = inner.iloc[:split], inner.iloc[split:]
    feats = [c for c in BLOWOUT_MODEL_FEATURES if c in inner.columns]
    y_train = train[BLOWOUT_TARGET].astype(int).to_numpy()
    y_valid = valid[BLOWOUT_TARGET].astype(float).to_numpy()
    base_rate = float(y_train.mean())
    reference = brier(y_valid, np.full(len(y_valid), base_rate))

    rows = [{
        "kind": "constant base rate", "n_train": len(train), "n_valid": len(valid),
        "auc": 0.5, "brier": reference, "brier_skill": 0.0,
    }]
    for kind in kinds:
        if kind not in ESTIMATORS:
            log.warning("unknown blowout estimator %r; skipped", kind)
            continue
        model = ESTIMATORS[kind]().fit(train[feats], y_train)
        p = model.predict_proba(valid[feats])[:, 1]
        score = brier(y_valid, p)
        rows.append({
            "kind": kind, "n_train": len(train), "n_valid": len(valid),
            "auc": auc(y_valid, p), "brier": score,
            "brier_skill": skill_score(score, reference),
        })
    return pd.DataFrame(rows).sort_values("brier").reset_index(drop=True)


def blowout_model_quality(
    context: pd.DataFrame, probabilities: pd.DataFrame
) -> dict[str, float]:
    """AUC, Brier, Brier skill and a calibration fit for the blowout classifier.

    the skill baseline is the base rate over the same rows; the calibration fit is
    ``logit(y) ~ a + b*logit(p)`` by least squares on decile bins.
    """
    from .models import brier, skill_score  # noqa: PLC0415

    merged = context.merge(
        probabilities[["SEASON", "TEAM_ID", "GAME_ID", BLOWOUT_PROB]],
        on=["SEASON", "TEAM_ID", "GAME_ID"], how="inner",
    )
    y = merged[BLOWOUT_TARGET].to_numpy(dtype=float)
    p = merged[BLOWOUT_PROB].to_numpy(dtype=float)
    keep = np.isfinite(y) & np.isfinite(p)
    y, p = y[keep], p[keep]
    if len(y) == 0 or len(np.unique(y)) < 2:
        return {"n": float(len(y))}

    base = float(y.mean())
    out = {
        "n": float(len(y)),
        "base_rate": base,
        "auc": auc(y, p),
        "brier": brier(y, p),
        "brier_base": brier(y, np.full(len(y), base)),
    }
    out["brier_skill"] = skill_score(out["brier"], out["brier_base"])

    # binned rather than per-row because logit(y) is undefined at y in {0, 1}
    bins = pd.qcut(pd.Series(p), 10, duplicates="drop")
    grouped = pd.DataFrame({"p": p, "y": y}).groupby(bins, observed=True).mean()
    eps = 1e-6
    px = np.log(grouped["p"].clip(eps, 1 - eps) / (1 - grouped["p"].clip(eps, 1 - eps)))
    py = np.log(grouped["y"].clip(eps, 1 - eps) / (1 - grouped["y"].clip(eps, 1 - eps)))
    if len(px) >= 2:
        slope, intercept = np.polyfit(px.to_numpy(), py.to_numpy(), 1)
        out["calibration_slope"] = float(slope)
        out["calibration_intercept"] = float(intercept)
    return out


def start_rate_features(
    universe: pd.DataFrame,
    window: int = START_RATE_WINDOW,
    top_n: int = START_RATE_TOP_N,
) -> pd.DataFrame:
    """``top5_min_share_10``: share of recent games the player led his team in minutes.

    the per-game top-n label reads the target game's own minutes, so the rolling
    mean is shifted; that shift is the only as-of guard here. career-scoped.
    """
    for col in ("PLAYER_ID", "GAME_ID", "TEAM_ID", "GAME_DATE", "PLAYED", "MIN"):
        if col not in universe.columns:
            raise ValueError(f"start_rate_features needs {col!r} on the universe")

    frame = universe[
        ["PLAYER_ID", "GAME_ID", "TEAM_ID", "GAME_DATE", "PLAYED", "MIN"]
    ].copy()
    frame["GAME_DATE"] = pd.to_datetime(frame["GAME_DATE"])

    minutes = pd.to_numeric(frame["MIN"], errors="coerce")
    # rank only among appearances; a non-appearance gets no rank and therefore a 0
    appeared = (frame["PLAYED"] == 1) & minutes.notna() & (minutes > 0)
    ranked = minutes.where(appeared)
    frame["_rank"] = ranked.groupby(
        [frame["GAME_ID"], frame["TEAM_ID"]]
    ).rank(method="first", ascending=False)
    frame["_is_top_n"] = (frame["_rank"] <= float(top_n)).astype(float)

    frame = frame.sort_values(["PLAYER_ID", "GAME_DATE", "GAME_ID"])
    frame[START_RATE_COL] = frame.groupby("PLAYER_ID")["_is_top_n"].transform(
        lambda s, w=window: s.shift(1).rolling(w, min_periods=1).mean()
    )
    return frame[["PLAYER_ID", "GAME_ID", "TEAM_ID", START_RATE_COL]].reset_index(
        drop=True
    )


def attach_matchup_features(
    features: pd.DataFrame,
    context: pd.DataFrame,
    probabilities: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """merge own-team and opponent-team context onto a scheduled-player-game frame.

    row order is preserved; callers hold positional alignment with arrays computed
    from the frame. ``probabilities=None`` attaches every column except
    ``blowout_prob`` and its interaction.
    """
    out = features.copy()
    out["_row_order"] = np.arange(len(out))
    out["GAME_DATE"] = pd.to_datetime(out["GAME_DATE"])

    ctx = context.copy()

    own_cols = {
        CTX_PACE: "own_pace",
        CTX_NET_RATING: "own_net_rating",
        CTX_SLOT_MINUTES: "own_slot_minutes",
    }
    own = ctx[[*TEAM_GAME_KEY, *own_cols, *STAKES_CTX_COLS, *OUTCOME_COLS]].rename(
        columns=own_cols
    )
    before = len(out)
    out = out.merge(own, on=list(TEAM_GAME_KEY), how="left")
    if len(out) != before:
        raise ValueError(
            f"attaching own-team context changed the row count ({before} -> "
            f"{len(out)}); (SEASON, TEAM_ID, GAME_ID) is not unique in the context"
        )

    # the opponent's stakes and outcome columns are deliberately not taken: the
    # outcomes are the same game's and would be a second copy of the label
    opp_cols = {
        CTX_PACE: "opp_pace",
        CTX_NET_RATING: "opp_net_rating",
        CTX_DEF_RATING: "opp_def_rating",
        CTX_FG3A_ALLOWED: "opp_fg3a_allowed_per100",
        CTX_FTA_ALLOWED: "opp_fta_allowed_per100",
    }
    opp = ctx[["SEASON", "TEAM_ID", "GAME_ID", *opp_cols]].rename(
        columns={"TEAM_ID": "OPP_TEAM_ID", **opp_cols}
    )
    before = len(out)
    out = out.merge(opp, on=["SEASON", "OPP_TEAM_ID", "GAME_ID"], how="left")
    if len(out) != before:
        raise ValueError(
            f"attaching opponent context changed the row count ({before} -> "
            f"{len(out)})"
        )

    out["game_pace_mean"] = (out["own_pace"] + out["opp_pace"]) / 2.0
    # scaled by 100 so the product lands in the same order of magnitude as the mean
    out["game_pace_product"] = out["own_pace"] * out["opp_pace"] / 100.0

    # fraction of one lineup slot's minutes this player usually takes
    if "roll10_MIN" in out.columns:
        slot = pd.to_numeric(out["own_slot_minutes"], errors="coerce")
        out["minutes_share"] = pd.to_numeric(out["roll10_MIN"], errors="coerce") / slot
    else:
        log.warning("no roll10_MIN on the frame; minutes_share will be null")
        out["minutes_share"] = np.nan

    out["stakes_x_minutes_share"] = out["stakes_lockedness"] * out["minutes_share"]
    veteran = np.log1p(pd.to_numeric(out.get("n_appearances"), errors="coerce"))
    out["stakes_x_veteran"] = out["stakes_lockedness"] * veteran

    if probabilities is not None and len(probabilities):
        prob = probabilities[
            ["SEASON", "TEAM_ID", "GAME_ID", BLOWOUT_PROB, BLOWOUT_PROB_CUTOFF]
        ].drop_duplicates(["SEASON", "TEAM_ID", "GAME_ID"])
        before = len(out)
        out = out.merge(prob, on=list(TEAM_GAME_KEY), how="left")
        if len(out) != before:
            raise ValueError(
                f"attaching blowout probabilities changed the row count ({before} -> "
                f"{len(out)})"
            )
        out["blowout_x_minutes_share"] = out[BLOWOUT_PROB] * out["minutes_share"]
    else:
        log.info(
            "no blowout probabilities supplied; %s and its interaction will be "
            "absent from the frame", BLOWOUT_PROB,
        )

    out = out.drop(columns=["own_slot_minutes"])
    return (
        out.sort_values("_row_order")
        .drop(columns=["_row_order"])
        .reset_index(drop=True)
    )


def attach_start_rate(features: pd.DataFrame) -> pd.DataFrame:
    """merge :func:`start_rate_features` onto a frame, preserving row order."""
    out = features.copy()
    out["_row_order"] = np.arange(len(out))
    proxy = start_rate_features(out)
    before = len(out)
    out = out.merge(proxy, on=["PLAYER_ID", "GAME_ID", "TEAM_ID"], how="left")
    if len(out) != before:
        raise ValueError(
            f"attaching the start-rate proxy changed the row count ({before} -> "
            f"{len(out)}); (PLAYER_ID, GAME_ID, TEAM_ID) is not unique"
        )
    return (
        out.sort_values("_row_order")
        .drop(columns=["_row_order"])
        .reset_index(drop=True)
    )


def future_team_log_rows(
    future: pd.DataFrame, like: pd.DataFrame | None = None
) -> pd.DataFrame:
    """both sides of every future team-game as team-log rows with no box score."""
    games = future[["SEASON", "GAME_ID", "GAME_DATE", "TEAM_ID", "OPP_TEAM_ID"]]
    mirrored = games.rename(columns={"TEAM_ID": "OPP_TEAM_ID", "OPP_TEAM_ID": "TEAM_ID"})
    sides = pd.concat([games, mirrored], ignore_index=True).drop_duplicates(
        ["GAME_ID", "TEAM_ID"]
    )
    sides = sides.drop(columns=["OPP_TEAM_ID"]).reset_index(drop=True)
    sides["GAME_DATE"] = pd.to_datetime(sides["GAME_DATE"])
    for column in ("PTS", "MIN", "FGA", "FTA", "TOV", "FG3A"):
        sides[column] = np.nan
    if like is not None and COMPETITION_COL in like.columns:
        sides[COMPETITION_COL] = TRAINING_COMPETITIONS[0]
    return sides


def attach_serving_stakes(
    features: pd.DataFrame, team_logs: pd.DataFrame, cutoff: pd.Timestamp
) -> pd.DataFrame:
    """the own-team stakes and minutes_share columns for unplayed rows, row order kept.

    each future date is appended alone to the team logs dated before ``cutoff``, the
    rule build_prospective_features applies, so a row reads only played games and the
    columns equal what team_game_context gives the same row once it is history.
    """
    logs = team_logs[pd.to_datetime(team_logs["GAME_DATE"]) < pd.Timestamp(cutoff)]
    dates = pd.to_datetime(features["GAME_DATE"])
    contexts: list[pd.DataFrame] = []
    for game_date in sorted(dates.unique()):
        day = future_team_log_rows(features[dates == game_date], like=logs)
        context = team_game_context(pd.concat([logs, day], ignore_index=True))
        contexts.append(context[context["GAME_ID"].isin(set(day["GAME_ID"]))])
    out = attach_matchup_features(features, pd.concat(contexts, ignore_index=True))
    # the placeholder rows carry no box score, so their outcome columns mean nothing.
    return out.drop(columns=[c for c in OUTCOME_COLS if c in out.columns])


def attach_v4_features(
    features: pd.DataFrame,
    team_logs: pd.DataFrame,
    with_blowout: bool = True,
) -> pd.DataFrame:
    """the whole v4 candidate family, in one call, from a feature frame + team logs."""
    home = (
        features[["GAME_ID", "TEAM_ID", "IS_HOME"]].drop_duplicates(
            ["GAME_ID", "TEAM_ID"]
        )
        if "IS_HOME" in features.columns else None
    )
    context = team_game_context(team_logs, home_flags=home)
    probabilities = (
        cross_fit_blowout_probabilities(context) if with_blowout else None
    )
    out = attach_matchup_features(features, context, probabilities)
    return attach_start_rate(out)
