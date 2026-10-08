"""the P3 decision run: the feature-set and rate challengers, each against the incumbent, one look."""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fnba_ml.cli import add_common_args, load_dataset, setup_logging  # noqa: E402
from fnba_ml.config import (  # noqa: E402
    CANDIDATE_FEATURE_SET_V5,
    CANDIDATE_FEATURE_SET_V6,
    CANDIDATE_FEATURE_SET_V7,
    CANDIDATE_FEATURE_VERSION_V5,
    CANDIDATE_FEATURE_VERSION_V6,
    CANDIDATE_FEATURE_VERSION_V7,
    CHAMPIONS,
    DATA_DIR,
    DEV_ORIGINS,
    FEATURE_COLS,
    FEATURE_VERSION,
    P3_COHORT_REGRESSION_TOLERANCE,
    P3_DECIDED_COMPARISONS,
    P3_PRESEASON_PRIOR_GATED_ENDPOINTS,
    P3_PROMOTION_FLOOR,
    P3_RATE_GATED_ENDPOINTS,
    P3_RATE_V6_GATED_ENDPOINTS,
    P3_V5_GATED_ENDPOINTS,
    P3_V6_GATED_ENDPOINTS,
    P3_V7_GATED_ENDPOINTS,
    PRESEASON_ROLE_FEATURE_COLS,
    PRESEASON_ROLE_FIRST_GAMES_SUFFIX,
    PRESEASON_ROLE_ORIGINS,
    PRESEASON_ROLE_PRIOR_GAMES,
    PRESEASON_ROLE_PRIOR_NEWCOMER_COHORTS,
    PRESEASON_ROLE_PRIOR_WEIGHT,
    RATE_CONTEXT_COLS,
    RATE_CONTEXT_COLS_V6,
    RATE_MODEL_TARGETS,
    RATE_RESIDUAL_MIN_MINUTES,
    RATE_TARGETS,
    REPORTS_DIR,
    SERVED_FEATURE_SET,
    V5_STAKES_FEATURE_COLS,
    V6_CONTEXT_FEATURE_COLS,
)
from fnba_ml.eval_core import clamp_to_opener, cohort_masks, split  # noqa: E402
from fnba_ml.features import feature_set_columns  # noqa: E402
from fnba_ml.models import (  # noqa: E402
    MIN_PRED,
    P_PLAY,
    AvailabilityModel,
    MinutesModel,
    PerMinuteRate,
    coherence_clip,
    minutes_propagated_estimate,
)
from fnba_ml.promotion import (  # noqa: E402
    BLOCK_DAYS,
    ENDPOINT_AVAILABILITY,
    ENDPOINT_MINUTES,
    ENDPOINT_UNCOND_PTS,
    N_REPLICATES,
    PromotionVerdict,
    cohort_regressions,
    decide,
    decision_table,
    paired_endpoint_bootstrap,
)
from fnba_ml.preseason_role import (  # noqa: E402
    ROSTER_COHORT_COL,
    ROSTER_COHORTS,
    SEASON_APPS_COL,
    newcomer_minutes_prior,
    preseason_role_minutes_prior,
    regular_season_apps_before,
    roster_cohort_masks,
    roster_cohorts,
)
from fnba_ml.rate_model import ResidualRateModel, residual_rate_estimates  # noqa: E402
from fnba_ml.registry import git_commit  # noqa: E402

log = logging.getLogger("run_p3_bracket")

COMPARISON_V5 = CANDIDATE_FEATURE_SET_V5
COMPARISON_RATE = "residual-rate"
COMPARISON_V6 = CANDIDATE_FEATURE_SET_V6
COMPARISON_RATE_V6 = "residual-rate-v6"
COMPARISON_V7 = CANDIDATE_FEATURE_SET_V7
COMPARISON_V6_SEASON_START = f"{CANDIDATE_FEATURE_SET_V6}@season-start"
COMPARISON_PRIOR = "preseason-role-prior"
COMPARISON_PRIOR_NEWCOMERS = "preseason-role-prior-newcomers"
PRIOR_COMPARISONS: tuple[str, ...] = (COMPARISON_PRIOR, COMPARISON_PRIOR_NEWCOMERS)

# scored on PRESEASON_ROLE_ORIGINS only: by the fade, every v7 column is neutral by
# December, so DEV_ORIGINS could not see either candidate.
SEASON_START_COMPARISONS: tuple[str, ...] = (
    COMPARISON_V7, COMPARISON_V6_SEASON_START, *PRIOR_COMPARISONS,
)
# a same-rows rerun of an already decided candidate, binding exactly as its parent is.
REFERENCE_PARENTS: dict[str, str] = {COMPARISON_V6_SEASON_START: COMPARISON_V6}

# feature-set comparisons: name -> the FEATURE_SETS entry refitted in place of the incumbent.
FEATURE_SET_COMPARISONS: dict[str, str] = {
    COMPARISON_V5: CANDIDATE_FEATURE_SET_V5,
    COMPARISON_V6: CANDIDATE_FEATURE_SET_V6,
}
# rate comparisons: name -> (context columns, fringe guard), both fixed in config.
RATE_COMPARISONS: dict[str, tuple[tuple[str, ...], float | None]] = {
    COMPARISON_RATE: (tuple(RATE_CONTEXT_COLS), None),
    COMPARISON_RATE_V6: (tuple(RATE_CONTEXT_COLS_V6), RATE_RESIDUAL_MIN_MINUTES),
}
COMPARISON_GATES: dict[str, tuple[str, ...]] = {
    COMPARISON_V5: P3_V5_GATED_ENDPOINTS,
    COMPARISON_RATE: P3_RATE_GATED_ENDPOINTS,
    COMPARISON_V6: P3_V6_GATED_ENDPOINTS,
    COMPARISON_RATE_V6: P3_RATE_V6_GATED_ENDPOINTS,
    COMPARISON_V7: P3_V7_GATED_ENDPOINTS,
    COMPARISON_V6_SEASON_START: P3_V6_GATED_ENDPOINTS,
    COMPARISON_PRIOR: P3_PRESEASON_PRIOR_GATED_ENDPOINTS,
    COMPARISON_PRIOR_NEWCOMERS: P3_PRESEASON_PRIOR_GATED_ENDPOINTS,
}
INCUMBENT_LABEL = {
    COMPARISON_V5: SERVED_FEATURE_SET,
    COMPARISON_RATE: "champion rate",
    COMPARISON_V6: SERVED_FEATURE_SET,
    COMPARISON_RATE_V6: "champion rate",
    COMPARISON_V7: SERVED_FEATURE_SET,
    COMPARISON_V6_SEASON_START: SERVED_FEATURE_SET,
    COMPARISON_PRIOR: "champion minutes",
    COMPARISON_PRIOR_NEWCOMERS: "champion minutes",
}

Losses = dict[str, tuple[np.ndarray, np.ndarray]]


def rate_endpoint(target: str, conditional: bool) -> str:
    return f"{'cond' if conditional else 'uncond'}_{target.lower()}_mae"


def is_binding(name: str) -> bool:
    """False for a comparison that already had its one look (MODEL.md 13.6)."""
    return REFERENCE_PARENTS.get(name, name) not in P3_DECIDED_COMPARISONS


def verdict_text(name: str, verdict: PromotionVerdict) -> str:
    if is_binding(name):
        return verdict.reason
    decided_at = P3_DECIDED_COMPARISONS[REFERENCE_PARENTS.get(name, name)]
    return (
        f"REFERENCE ONLY (decided at {decided_at}; this rerun is "
        f"not a second look): {verdict.reason}"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--dataset", type=Path,
                        default=DATA_DIR / "dataset_v4.parquet")
    parser.add_argument("--version", default="p3")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS_DIR)
    parser.add_argument(
        "--allow-overwrite", action="store_true",
        help="replace an existing decision csv. one look per version: use a new "
             "--version instead unless the previous run crashed",
    )
    parser.add_argument(
        "--origins-subset", default=None,
        help="sensitivity only, never a look: recompute the season-start decisions on "
             "these origin labels (comma-separated prefixes, e.g. S1,S2) from the "
             "per-row results a previous run saved under --version; fits nothing",
    )
    parser.add_argument(
        "--from-rows", type=Path, default=None,
        help="the saved per-row parquet for --origins-subset (default: "
             "<reports-dir>/<version>_p3_season_start_rows.parquet)",
    )
    return parser.parse_args(argv)


SUBSET_COMPARISONS: tuple[str, ...] = (COMPARISON_V7, COMPARISON_V6_SEASON_START)
ROWS_SUFFIX = "_p3_season_start_rows.parquet"


def season_start_rows(losses: dict[str, tuple[pd.DataFrame, pd.DataFrame]]) -> pd.DataFrame:
    """the per-row losses of the subset comparisons, one frame, for a later sensitivity."""
    frames = [
        side_frame.assign(comparison=name, side=side)
        for name in SUBSET_COMPARISONS if name in losses
        for side, side_frame in zip(("incumbent", "candidate"), losses[name])
    ]
    return pd.concat(frames, ignore_index=True)


def subset_decisions(
    rows: pd.DataFrame, prefixes: tuple[str, ...]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(decision table, per-origin table) on the origins whose label starts with a prefix."""
    keep = rows["origin"].astype(str).str.split().str[0].isin(prefixes)
    subset = rows[keep]
    if subset.empty:
        raise SystemExit(
            f"no saved row belongs to an origin starting {', '.join(prefixes)}; "
            f"the saved origins are {', '.join(sorted(rows['origin'].unique()))}"
        )
    decisions, origins = [], []
    for name in SUBSET_COMPARISONS:
        part = subset[subset["comparison"] == name]
        if part.empty:
            continue
        sides = {
            side: part[part["side"] == side].drop(columns=["comparison", "side"])
            for side in ("incumbent", "candidate")
        }
        verdict, _ = decide_comparison(
            sides["incumbent"], sides["candidate"], COMPARISON_GATES[name]
        )
        decisions.append(decision_table(verdict).assign(
            comparison=name, origins="+".join(prefixes),
            verdict=f"SENSITIVITY ONLY, not a look: {verdict.reason}",
        ))
        for endpoint in endpoints_of(sides["incumbent"]):
            origins.append(per_origin_table(
                sides["incumbent"], sides["candidate"], endpoint
            ).assign(comparison=name, endpoint=endpoint))
    return pd.concat(decisions, ignore_index=True), pd.concat(origins, ignore_index=True)


def run_origins_subset(args: argparse.Namespace) -> int:
    """the sensitivity mode: read saved per-row losses, decide on the subset, write csvs."""
    prefixes = tuple(p.strip() for p in str(args.origins_subset).split(",") if p.strip())
    path = args.from_rows or args.reports_dir / f"{args.version}{ROWS_SUFFIX}"
    if not path.exists():
        raise SystemExit(
            f"{path} does not exist: the run for {args.version} saved no per-row "
            f"results, so the subset cannot be recomputed without refitting, which "
            f"would be a second look. the per-origin numbers are in its _per_origin.csv "
            f"and in MODEL.md 24"
        )
    decisions, per_origin = subset_decisions(pd.read_parquet(path), prefixes)
    tag = "-".join(prefixes)
    args.reports_dir.mkdir(parents=True, exist_ok=True)
    decisions.to_csv(args.reports_dir / f"{args.version}_p3_subset_{tag}.csv", index=False)
    per_origin.to_csv(
        args.reports_dir / f"{args.version}_p3_subset_{tag}_per_origin.csv", index=False
    )
    print(f"sensitivity only, origins {', '.join(prefixes)} of {args.version}:")
    print(decisions.to_string(index=False))
    return 0


def fit_and_score(
    train_all: pd.DataFrame, valid_all: pd.DataFrame, feats: list[str],
    cutoff: pd.Timestamp,
) -> pd.DataFrame:
    """the validation frame with out-of-fold P_PLAY and MIN_PRED, one cutoff."""
    train_app = train_all[train_all["PLAYED"] == 1]
    availability = AvailabilityModel(kind=CHAMPIONS["availability"]).fit(
        train_all, feats, cutoff
    )
    minutes = MinutesModel(kind=CHAMPIONS["minutes"]).fit(train_app, feats, cutoff)
    return minutes.attach(availability.attach(valid_all))


def availability_minutes_losses(
    valid_all: pd.DataFrame, scored: pd.DataFrame, rate: PerMinuteRate
) -> Losses:
    """(per-row loss, rows it is scored over) for the three feature-set endpoints."""
    every_row = np.ones(len(valid_all), dtype=bool)
    appearances = (valid_all["PLAYED"] == 1).to_numpy()
    p = scored[P_PLAY].to_numpy(dtype=float)
    _, uncond = minutes_propagated_estimate(scored, rate.predict(valid_all))
    return {
        ENDPOINT_AVAILABILITY: (
            (p - valid_all["PLAYED"].to_numpy(dtype=float)) ** 2, every_row
        ),
        ENDPOINT_MINUTES: (
            np.abs(scored[MIN_PRED].to_numpy(dtype=float)
                   - valid_all["MIN"].to_numpy(dtype=float)),
            appearances,
        ),
        ENDPOINT_UNCOND_PTS: (
            np.abs(uncond - valid_all["PTS"].to_numpy(dtype=float)), every_row
        ),
    }


def rate_losses(
    valid_all: pd.DataFrame,
    estimates: dict[str, tuple[np.ndarray, np.ndarray]],
) -> Losses:
    """conditional MAE over appearances and unconditional MAE over every row."""
    every_row = np.ones(len(valid_all), dtype=bool)
    appearances = (valid_all["PLAYED"] == 1).to_numpy()
    out: Losses = {}
    for target, (cond, uncond) in estimates.items():
        y = valid_all[target].to_numpy(dtype=float)
        out[rate_endpoint(target, True)] = (np.abs(cond - y), appearances)
        out[rate_endpoint(target, False)] = (np.abs(uncond - y), every_row)
    return out


def emit(
    origin: str, valid_all: pd.DataFrame, losses: Losses,
    masks: list[tuple[str, np.ndarray]] | None = None,
) -> list[pd.DataFrame]:
    """long per-row loss frames: one block per (cohort, endpoint).

    a row belongs to several cohorts, so cohorts are extra rows rather than a column.
    ``masks`` defaults to the dataset cohorts of ``cohort_masks``.
    """
    key = (
        valid_all["PLAYER_ID"].astype(str) + "|"
        + valid_all["GAME_ID"].astype(str) + "|"
        + valid_all["TEAM_ID"].astype(str)
    ).to_numpy()
    dates = valid_all["GAME_DATE"].to_numpy()
    every_row = np.ones(len(valid_all), dtype=bool)
    out: list[pd.DataFrame] = []
    cohorts = cohort_masks(valid_all) if masks is None else masks
    for label, mask in (("ALL", every_row), *cohorts):
        if not mask.any():
            continue
        for endpoint, (loss, selector) in losses.items():
            sel = mask & selector
            if not sel.any():
                continue
            out.append(pd.DataFrame({
                "origin": origin,
                "endpoint": endpoint,
                "row_key": key[sel],
                "GAME_DATE": dates[sel],
                "cohort": label,
                "loss": loss[sel],
            }))
    return out


Estimates = dict[str, tuple[np.ndarray, np.ndarray]]


@dataclass
class BracketScores:
    """per-row losses for every comparison, plus the coherence side outputs.

    each coherence input is (origin, scored incumbent frame, {family: estimates}).
    """

    losses: dict[str, tuple[pd.DataFrame, pd.DataFrame]]
    clip_rates: pd.DataFrame
    coherence_inputs: list[tuple[str, pd.DataFrame, dict[str, Estimates]]] = field(
        default_factory=list
    )


def score_brackets(
    frame: pd.DataFrame,
    origins: list[tuple[str, str, str]],
    targets: tuple[str, ...] = RATE_MODEL_TARGETS,
    rate_params: dict[str, object] | None = None,
) -> BracketScores:
    """every comparison over identical rows, every origin.

    each residual rate is fitted on the incumbent's pieces, so its comparison
    differs from the champion's in the per-minute rate and in nothing else.
    """
    incumbent_feats = feature_set_columns(frame, SERVED_FEATURE_SET)
    candidate_feats = {
        name: feature_set_columns(frame, feature_set)
        for name, feature_set in FEATURE_SET_COMPARISONS.items()
    }
    names = [*FEATURE_SET_COMPARISONS, *RATE_COMPARISONS]
    blocks: dict[str, tuple[list[pd.DataFrame], list[pd.DataFrame]]] = {
        name: ([], []) for name in names
    }
    clip_rows: list[dict] = []
    coherence_inputs = []

    for origin, vstart, vend in origins:
        train_all, valid_all = split(frame, vstart, vend)
        train_app = train_all[(train_all["PLAYED"] == 1) & (train_all["MIN"] > 0)]
        if train_all.empty or valid_all.empty or train_app.empty:
            log.warning("origin %s has an empty side; skipped", origin)
            continue
        valid_all = valid_all.reset_index(drop=True)
        cutoff = pd.Timestamp(valid_all["GAME_DATE"].min())
        log.info("origin %s: %d train / %d valid rows", origin, len(train_all),
                 len(valid_all))

        incumbent = fit_and_score(train_all, valid_all, incumbent_feats, cutoff)
        pts_rate = PerMinuteRate("PTS").fit(train_app)
        incumbent_losses = emit(
            origin, valid_all, availability_minutes_losses(valid_all, incumbent, pts_rate)
        )
        for name, feats in candidate_feats.items():
            candidate = fit_and_score(train_all, valid_all, feats, cutoff)
            blocks[name][0].extend(incumbent_losses)
            blocks[name][1].extend(emit(
                origin, valid_all,
                availability_minutes_losses(valid_all, candidate, pts_rate),
            ))

        champion = {
            target: minutes_propagated_estimate(
                incumbent, PerMinuteRate(target).fit(train_app).predict(valid_all)
            )
            for target in RATE_TARGETS if target in valid_all.columns
        }
        champion_losses = emit(
            origin, valid_all, rate_losses(valid_all, {t: champion[t] for t in targets})
        )
        champion_uncond = {t: pair[1] for t, pair in champion.items()}
        families: dict[str, Estimates] = {"champion": champion}
        for name, (context_cols, min_minutes) in RATE_COMPARISONS.items():
            models = {
                target: ResidualRateModel(
                    cutoff=cutoff, context_cols=context_cols,
                    residual_min_minutes=min_minutes,
                    **({"params": dict(rate_params)} if rate_params else {}),
                ).fit(train_app, target)
                for target in targets
            }
            challenger = residual_rate_estimates(incumbent, models)
            blocks[name][0].extend(champion_losses)
            blocks[name][1].extend(emit(origin, valid_all, rate_losses(valid_all, challenger)))
            # the challenger family is the champion with only its own stats replaced,
            # which is what a served swap would look like downstream.
            families[name] = {**champion, **challenger}

        for family, estimates in families.items():
            values = {**champion_uncond, **{t: pair[1] for t, pair in estimates.items()}}
            _, counts = coherence_clip(values)
            for constraint, n_bound in counts.items():
                clip_rows.append({
                    "origin": origin, "family": family, "constraint": constraint,
                    "clip_rate": n_bound / len(valid_all), "n": len(valid_all),
                })
        # the scored incumbent frame carries P_PLAY and MIN_PRED, which the
        # coherence endpoints need to rebuild team minute sums.
        coherence_inputs.append((origin, incumbent, families))

    if not blocks[COMPARISON_V5][0]:
        raise SystemExit("no origin produced results; check the dataset's date range")
    losses = {
        name: (pd.concat(inc, ignore_index=True), pd.concat(cand, ignore_index=True))
        for name, (inc, cand) in blocks.items()
    }
    return BracketScores(losses, pd.DataFrame(clip_rows), coherence_inputs)


def first_games_losses(losses: Losses, first: np.ndarray) -> Losses:
    """the same losses restricted to rows inside each player's first prior games."""
    return {
        f"{endpoint}{PRESEASON_ROLE_FIRST_GAMES_SUFFIX}": (loss, selector & first)
        for endpoint, (loss, selector) in losses.items()
    }


def with_first_games(losses: Losses, first: np.ndarray) -> Losses:
    return {**losses, **first_games_losses(losses, first)}


def minutes_and_points(losses: Losses) -> Losses:
    """the two endpoints a minutes-only change can move; availability is untouched."""
    return {e: losses[e] for e in (ENDPOINT_MINUTES, ENDPOINT_UNCOND_PTS)}


def stamp_season_start(frame: pd.DataFrame) -> pd.DataFrame:
    """the frame with the two evaluation-only helper columns the v7 look reads."""
    out = frame.copy()
    out[SEASON_APPS_COL] = regular_season_apps_before(out)
    out[ROSTER_COHORT_COL] = roster_cohorts(out)
    return out


def score_season_start(
    frame: pd.DataFrame, origins: list[tuple[str, str, str]],
) -> dict[str, tuple[pd.DataFrame, pd.DataFrame]]:
    """the v7 comparisons over identical rows, every season-start origin.

    each origin fits the incumbent, v7-preseason-role and the v6-context reference;
    the prior is the incumbent's minutes blended toward the preseason role.
    """
    frame = stamp_season_start(frame)
    incumbent_feats = feature_set_columns(frame, SERVED_FEATURE_SET)
    refits = {
        COMPARISON_V7: feature_set_columns(frame, CANDIDATE_FEATURE_SET_V7),
        COMPARISON_V6_SEASON_START: feature_set_columns(frame, CANDIDATE_FEATURE_SET_V6),
    }
    blocks: dict[str, tuple[list[pd.DataFrame], list[pd.DataFrame]]] = {
        name: ([], []) for name in SEASON_START_COMPARISONS
    }
    for origin, vstart, vend in origins:
        train_all, valid_all = split(frame, vstart, vend)
        train_app = train_all[(train_all["PLAYED"] == 1) & (train_all["MIN"] > 0)]
        if train_all.empty or valid_all.empty or train_app.empty:
            log.warning("season-start origin %s has an empty side; skipped", origin)
            continue
        valid_all = valid_all.reset_index(drop=True)
        cutoff = pd.Timestamp(valid_all["GAME_DATE"].min())
        log.info("season-start origin %s: %d train / %d valid rows", origin,
                 len(train_all), len(valid_all))
        masks = [*cohort_masks(valid_all), *roster_cohort_masks(valid_all)]
        first = (
            valid_all[SEASON_APPS_COL].to_numpy(dtype=float) < PRESEASON_ROLE_PRIOR_GAMES
        )
        pts_rate = PerMinuteRate("PTS").fit(train_app)

        incumbent = fit_and_score(train_all, valid_all, incumbent_feats, cutoff)
        incumbent_losses = availability_minutes_losses(valid_all, incumbent, pts_rate)
        incumbent_rows = emit(origin, valid_all,
                              with_first_games(incumbent_losses, first), masks)
        for name, feats in refits.items():
            candidate = fit_and_score(train_all, valid_all, feats, cutoff)
            blocks[name][0].extend(incumbent_rows)
            blocks[name][1].extend(emit(
                origin, valid_all,
                with_first_games(
                    availability_minutes_losses(valid_all, candidate, pts_rate), first
                ),
                masks,
            ))

        champion_minutes = incumbent[MIN_PRED].to_numpy(dtype=float)
        everyone = preseason_role_minutes_prior(
            champion_minutes,
            valid_all["pre_min_share"].to_numpy(dtype=float),
            valid_all["pre_games_played"].to_numpy(dtype=float),
            valid_all[SEASON_APPS_COL].to_numpy(dtype=float),
        )
        prior_minutes = {
            COMPARISON_PRIOR: everyone,
            COMPARISON_PRIOR_NEWCOMERS: newcomer_minutes_prior(
                champion_minutes, everyone, valid_all[ROSTER_COHORT_COL].to_numpy()
            ),
        }
        incumbent_prior_rows = emit(
            origin, valid_all,
            with_first_games(minutes_and_points(incumbent_losses), first), masks,
        )
        for name, minutes in prior_minutes.items():
            blended = incumbent.copy()
            blended[MIN_PRED] = minutes
            blocks[name][0].extend(incumbent_prior_rows)
            blocks[name][1].extend(emit(
                origin, valid_all,
                with_first_games(minutes_and_points(
                    availability_minutes_losses(valid_all, blended, pts_rate)
                ), first),
                masks,
            ))

    if not blocks[COMPARISON_V7][0]:
        raise SystemExit("no season-start origin produced results; check the dataset")
    return {
        name: (pd.concat(inc, ignore_index=True), pd.concat(cand, ignore_index=True))
        for name, (inc, cand) in blocks.items()
    }


def pooled(frame: pd.DataFrame, endpoint: str) -> pd.DataFrame:
    """the ALL-cohort rows for one endpoint, in a canonical order for pairing."""
    sub = frame[(frame["endpoint"] == endpoint) & (frame["cohort"] == "ALL")]
    return sub.sort_values(["origin", "GAME_DATE", "row_key"]).reset_index(drop=True)


def cohort_table(
    incumbent: pd.DataFrame, candidate: pd.DataFrame, endpoint: str
) -> pd.DataFrame:
    """the cohort breakdown for one endpoint, pooled over origins."""
    order = ["cohort", "origin", "GAME_DATE", "row_key"]
    a = incumbent[incumbent["endpoint"] == endpoint].sort_values(order).reset_index(
        drop=True
    )
    b = candidate[candidate["endpoint"] == endpoint].sort_values(order).reset_index(
        drop=True
    )
    if not a["row_key"].equals(b["row_key"]):
        raise SystemExit(
            f"the two passes' cohort rows for {endpoint} are not aligned; a cohort "
            f"comparison over different rows is not a comparison"
        )
    return cohort_regressions(a, b, tolerance=P3_COHORT_REGRESSION_TOLERANCE)


def per_origin_table(
    incumbent: pd.DataFrame, candidate: pd.DataFrame, endpoint: str
) -> pd.DataFrame:
    """one row per origin: the two means and the relative change."""
    a, b = pooled(incumbent, endpoint), pooled(candidate, endpoint)
    joint = pd.DataFrame({
        "origin": a["origin"].to_numpy(),
        "base": a["loss"].to_numpy(dtype=float),
        "cand": b["loss"].to_numpy(dtype=float),
    })
    rows = []
    for origin, group in joint.groupby("origin", sort=True):
        base, cand = float(group["base"].mean()), float(group["cand"].mean())
        rows.append({
            "origin": origin, "n": int(len(group)), "incumbent": base,
            "candidate": cand,
            "delta_pct": (cand - base) / base if base > 0 else float("nan"),
        })
    return pd.DataFrame(rows)


def endpoints_of(frame: pd.DataFrame) -> list[str]:
    return list(dict.fromkeys(frame["endpoint"]))


def decide_comparison(
    incumbent: pd.DataFrame, candidate: pd.DataFrame, gates: tuple[str, ...]
) -> tuple[PromotionVerdict, pd.DataFrame]:
    """apply the P3 bar to one comparison. returns (verdict, gated cohort table)."""
    decisions = [
        paired_endpoint_bootstrap(
            pooled(incumbent, endpoint), pooled(candidate, endpoint), endpoint,
            gates=gates, floor=P3_PROMOTION_FLOOR,
        )
        for endpoint in endpoints_of(incumbent)
    ]
    gated = [
        cohort_table(incumbent, candidate, endpoint).assign(endpoint=endpoint)
        for endpoint in gates if endpoint in set(incumbent["endpoint"])
    ]
    gated_cohorts = pd.concat(gated, ignore_index=True) if gated else pd.DataFrame()
    verdict = decide(decisions, gated_cohorts, floor=P3_PROMOTION_FLOOR,
                     tolerance=P3_COHORT_REGRESSION_TOLERANCE)
    return verdict, gated_cohorts


def _coherence_frame(scored: pd.DataFrame, estimates: Estimates) -> pd.DataFrame:
    """the prediction-shaped frame eval_coherence scores: E_<stat>_COND and E_<stat>."""
    frame = scored.reset_index(drop=True).copy()
    minutes = frame[MIN_PRED].to_numpy(dtype=float)
    frame["E_MIN_COND"] = minutes
    frame["E_MIN"] = np.clip(frame[P_PLAY].to_numpy(dtype=float) * minutes, 0.0, None)
    for target, (conditional, unconditional) in estimates.items():
        frame[f"E_{target}_COND"] = np.asarray(conditional, dtype=float)
        frame[f"E_{target}"] = np.asarray(unconditional, dtype=float)
    return frame


def coherence_report(scores: BracketScores) -> tuple[str, pd.DataFrame | None]:
    """the serving coherence corrections, scored on the same validation rows."""
    try:
        from fnba_ml.eval_coherence import coherence_endpoints  # noqa: PLC0415
    except ImportError:
        return "not available (fnba_ml.eval_coherence is not installed)", None
    frames: list[pd.DataFrame] = []
    for origin, scored, families in scores.coherence_inputs:
        for family, estimates in families.items():
            result = coherence_endpoints(_coherence_frame(scored, estimates))
            frames.append(result.assign(origin=origin, family=family))
    if not frames:
        return "available, no origins scored", None
    return "available", pd.concat(frames, ignore_index=True)


def prior_roster_table(all_cohorts: pd.DataFrame) -> pd.DataFrame:
    """each prior's gated first-10 endpoints within the three roster cohorts."""
    if all_cohorts.empty:
        return all_cohorts
    keep = (
        all_cohorts["comparison"].isin(PRIOR_COMPARISONS)
        & all_cohorts["endpoint"].isin(P3_PRESEASON_PRIOR_GATED_ENDPOINTS)
        & all_cohorts["cohort"].isin(ROSTER_COHORTS)
    )
    return all_cohorts[keep].reset_index(drop=True)


def _md(frame: pd.DataFrame) -> str:
    if frame is None or frame.empty:
        return "_(none)_"
    return frame.to_markdown(index=False, floatfmt=".4f")


def write_reports(
    scores: BracketScores, reports_dir: Path, version: str, header: list[str]
) -> dict[str, PromotionVerdict]:
    """decide every comparison and write the csvs and the markdown."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{version}_p3"
    verdicts: dict[str, PromotionVerdict] = {}
    decision_frames, gated_frames, origin_frames, cohort_frames = [], [], [], []

    for name, (incumbent, candidate) in scores.losses.items():
        verdict, gated = decide_comparison(incumbent, candidate, COMPARISON_GATES[name])
        verdicts[name] = verdict
        decision_frames.append(decision_table(verdict).assign(
            comparison=name, incumbent_label=INCUMBENT_LABEL[name],
            binding=is_binding(name),
            promoted=verdict.promoted and is_binding(name),
            verdict=verdict_text(name, verdict),
        ))
        if not gated.empty:
            gated_frames.append(gated.assign(comparison=name))
        for endpoint in endpoints_of(incumbent):
            origin_frames.append(per_origin_table(incumbent, candidate, endpoint).assign(
                comparison=name, endpoint=endpoint
            ))
            cohort_frames.append(cohort_table(incumbent, candidate, endpoint).assign(
                comparison=name, endpoint=endpoint
            ))

    decisions = pd.concat(decision_frames, ignore_index=True)
    gated_cohorts = (
        pd.concat(gated_frames, ignore_index=True) if gated_frames else pd.DataFrame()
    )
    per_origin = pd.concat(origin_frames, ignore_index=True)
    all_cohorts = pd.concat(cohort_frames, ignore_index=True)
    coherence_status, coherence = coherence_report(scores)

    decisions.to_csv(reports_dir / f"{stem}_decision.csv", index=False)
    gated_cohorts.to_csv(reports_dir / f"{stem}_cohorts.csv", index=False)
    per_origin.to_csv(reports_dir / f"{stem}_per_origin.csv", index=False)
    all_cohorts.to_csv(reports_dir / f"{stem}_cohorts_all_endpoints.csv", index=False)
    roster = prior_roster_table(all_cohorts)
    roster.to_csv(reports_dir / f"{stem}_prior_roster_cohorts.csv", index=False)
    scores.clip_rates.to_csv(reports_dir / f"{stem}_clip_rates.csv", index=False)
    if coherence is not None:
        coherence.to_csv(reports_dir / f"{stem}_coherence.csv", index=False)

    lines = [f"# P3 decision run ({version})", "", *header, ""]
    for name, verdict in verdicts.items():
        lines += [
            f"## {name} vs {INCUMBENT_LABEL[name]}", "",
            f"**{verdict_text(name, verdict)}**", "",
            "positive relative_improvement = the candidate is better.", "",
            _md(decision_table(verdict)), "",
            "### per origin (positive delta_pct = the candidate is worse)", "",
            _md(per_origin[per_origin["comparison"] == name].drop(
                columns=["comparison"])), "",
            "### cohorts, gated endpoints (positive delta_pct = worse)", "",
            _md(gated_cohorts[gated_cohorts["comparison"] == name].drop(
                columns=["comparison"]) if not gated_cohorts.empty else gated_cohorts),
            "",
        ]
    lines += [
        "## both priors: gated endpoints by roster cohort (positive delta_pct = worse)",
        "", "reported only; the gate is the pooled decision and the cohort rule above.",
        "", _md(roster), "",
    ]
    lines += [
        "## coherence", "",
        f"eval_coherence.coherence_endpoints: {coherence_status}", "",
        "clip rates of the unconditional estimates under COHERENCE_CONSTRAINTS:", "",
        _md(scores.clip_rates.groupby(["family", "constraint"], as_index=False)[
            "clip_rate"].mean() if not scores.clip_rates.empty else scores.clip_rates),
        "",
    ]
    if coherence is not None:
        lines += [_md(coherence), ""]
    (reports_dir / f"{stem}.md").write_text("\n".join(lines), encoding="utf-8")
    return verdicts


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)
    if args.origins_subset:
        return run_origins_subset(args)

    decision_path = args.reports_dir / f"{args.version}_p3_decision.csv"
    if decision_path.exists() and not args.allow_overwrite:
        raise SystemExit(
            f"{decision_path} already exists. one look per version: pass a new "
            f"--version, or --allow-overwrite if the previous run did not finish"
        )

    frame = load_dataset(args.dataset)
    needed = [*V5_STAKES_FEATURE_COLS, *RATE_CONTEXT_COLS, *V6_CONTEXT_FEATURE_COLS,
              *RATE_CONTEXT_COLS_V6, *PRESEASON_ROLE_FEATURE_COLS]
    missing = sorted({c for c in needed if c not in frame.columns})
    if missing:
        raise SystemExit(
            f"the dataset is missing {len(missing)} candidate column(s): "
            f"{', '.join(missing[:8])}. rebuild it with build_dataset.py."
        )

    decided = ", ".join(
        f"{name} ({stem})" for name, stem in P3_DECIDED_COMPARISONS.items()
    )
    season_start = clamp_to_opener(frame, PRESEASON_ROLE_ORIGINS)
    windows = "; ".join(f"{name} {vstart}..{vend}" for name, vstart, vend in season_start)
    header = [
        f"- git commit: {git_commit() or 'unknown'}",
        f"- dataset: {args.dataset} ({len(frame):,} rows)",
        f"- served contract: feature_version {FEATURE_VERSION}, {len(FEATURE_COLS)} "
        f"columns, UNCHANGED BY THIS RUN",
        f"- candidate 1: {CANDIDATE_FEATURE_SET_V5} (feature_version "
        f"{CANDIDATE_FEATURE_VERSION_V5}), {len(FEATURE_COLS)} + "
        f"{len(V5_STAKES_FEATURE_COLS)} columns",
        f"- candidate 2: residual rate on the incumbent's pieces, stats "
        f"{', '.join(RATE_MODEL_TARGETS)}",
        f"- candidate 3: {CANDIDATE_FEATURE_SET_V6} (feature_version "
        f"{CANDIDATE_FEATURE_VERSION_V6}), {CANDIDATE_FEATURE_SET_V5} + "
        f"{len(V6_CONTEXT_FEATURE_COLS)} box-detail columns",
        f"- candidate 4: {COMPARISON_RATE_V6}, the residual rate with "
        f"{len(RATE_CONTEXT_COLS_V6)} context columns and the residual set to 0 where "
        f"MIN_PRED < {RATE_RESIDUAL_MIN_MINUTES:g}",
        f"- candidate 5: {CANDIDATE_FEATURE_SET_V7} (feature_version "
        f"{CANDIDATE_FEATURE_VERSION_V7}), {CANDIDATE_FEATURE_SET_V6} + "
        f"{len(PRESEASON_ROLE_FEATURE_COLS)} preseason-role columns, against "
        f"{SERVED_FEATURE_SET}; {COMPARISON_V6_SEASON_START} is the same-rows reference",
        f"- candidate 6: {COMPARISON_PRIOR}, the incumbent's E[MIN|plays] blended toward "
        f"pre_min_share * 240 with weight {PRESEASON_ROLE_PRIOR_WEIGHT:g} * max(0, 1 - "
        f"k / {PRESEASON_ROLE_PRIOR_GAMES}) over each player's first "
        f"{PRESEASON_ROLE_PRIOR_GAMES} appearances",
        f"- candidate 7: {COMPARISON_PRIOR_NEWCOMERS}, the same blend on the "
        f"{' and '.join(PRESEASON_ROLE_PRIOR_NEWCOMER_COHORTS)} cohorts only; same-team "
        f"players keep the champion minutes",
        f"- already decided, rerun here as same-rows references that cannot promote: "
        f"{decided}",
        f"- origins: {len(DEV_ORIGINS)} (config.DEV_ORIGINS) for candidates 1-4; "
        f"candidates 5-7 on config.PRESEASON_ROLE_ORIGINS, each start the season opener "
        f"found in the data: {windows}",
        f"- bar (config P3 block, written before this ran): paired {BLOCK_DAYS}-day "
        f"moving-block bootstrap, {N_REPLICATES} replicates, 95% CI excluding zero, "
        f"AND >= {P3_PROMOTION_FLOOR:.0%} relative improvement on a gated endpoint, "
        f"AND no gated-endpoint cohort regressing by more than "
        f"{P3_COHORT_REGRESSION_TOLERANCE:.0%}",
        f"- gates: {CANDIDATE_FEATURE_SET_V5} and {CANDIDATE_FEATURE_SET_V6} "
        f"{' or '.join(P3_V5_GATED_ENDPOINTS)}; {COMPARISON_RATE} and "
        f"{COMPARISON_RATE_V6} {' or '.join(P3_RATE_GATED_ENDPOINTS)}; "
        f"{COMPARISON_V7} {' or '.join(P3_V7_GATED_ENDPOINTS)}; both priors "
        f"{' or '.join(P3_PRESEASON_PRIOR_GATED_ENDPOINTS)}; everything else is "
        f"reported only",
    ]
    for line in header:
        print(line)

    scores = score_brackets(frame, DEV_ORIGINS)
    scores.losses.update(score_season_start(frame, season_start))
    verdicts = write_reports(scores, args.reports_dir, args.version, header)
    season_start_rows(scores.losses).to_parquet(
        args.reports_dir / f"{args.version}{ROWS_SUFFIX}", index=False
    )

    print()
    for name, verdict in verdicts.items():
        print(f"{name}: {verdict_text(name, verdict)}")
    print(f"reports -> {args.reports_dir / f'{args.version}_p3.md'} and csvs")
    # exit 0 either way: a null result is a valid outcome of a pre-registered test.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
