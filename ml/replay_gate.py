"""the October replay gate, MODEL.md section 13.7."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fnba_ml.cli import (  # noqa: E402
    add_common_args,
    default_dataset_path,
    load_dataset,
    setup_logging,
    version_dir,
)
from fnba_ml.config import (  # noqa: E402
    MINUTES_TARGET,
    MODELS_DIR,
    PROSPECTIVE_ARTIFACT_CHECKSUMS,
    PROSPECTIVE_MODEL_VERSION,
    PROSPECTIVE_OCTOBER_GATE,
    PROSPECTIVE_OCTOBER_REPLAY_WINDOW,
    PROSPECTIVE_PROTOCOL_VERSION,
    REPORTS_DIR,
)
from fnba_ml.box_context import teammate_start_features  # noqa: E402
from fnba_ml.features import attach_expected_context  # noqa: E402
from fnba_ml.models import MIN_PRED, P_PLAY  # noqa: E402
from fnba_ml.registry import sha256_file  # noqa: E402

from predict import load_version  # noqa: E402

log = logging.getLogger("replay_gate")

TIER_COL = "MIN_TIER"
INSUFFICIENT = "insufficient_history"
CONTEXT_SOURCE = "P_CONTEXT_SOURCE"

CROSS_FIT_FALLBACK = "baseline"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--dataset", type=Path, default=default_dataset_path())
    parser.add_argument("--version", default=PROSPECTIVE_MODEL_VERSION,
                        help="the artifact to replay with")
    parser.add_argument("--models-dir", type=Path, default=MODELS_DIR)
    parser.add_argument("--window", nargs=2, metavar=("START", "END"),
                        default=list(PROSPECTIVE_OCTOBER_REPLAY_WINDOW),
                        help="the October window")
    parser.add_argument("--season", default=None,
                        help="the season supplying the non-October comparison rows")
    parser.add_argument("--report", type=Path, default=None,
                        help="write the markdown report here")
    parser.add_argument("--skip-checksums", action="store_true",
                        help="run against an unpinned artifact")
    parser.add_argument("--per-date", action="store_true",
                        help="also score date by date and assert the numbers match")
    return parser.parse_args(argv)


def verify_pinned_artifact(
    version: str, models_dir: Path, pinned: dict[str, str] = PROSPECTIVE_ARTIFACT_CHECKSUMS
) -> tuple[bool, list[str]]:
    """re-hash the artifact directory against the frozen checksum set."""
    problems: list[str] = []
    if version != PROSPECTIVE_MODEL_VERSION:
        problems.append(
            f"replaying {version!r}, not the frozen {PROSPECTIVE_MODEL_VERSION!r}"
        )
        return False, problems

    directory = version_dir(version, models_dir)
    on_disk = {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()}
    for extra in sorted(on_disk - set(pinned)):
        problems.append(f"{extra}: present on disk and not in the pinned set")
    for name, expected in sorted(pinned.items()):
        path = directory / name
        if not path.exists():
            problems.append(f"{name}: missing")
            continue
        actual = sha256_file(path)
        if actual != expected:
            problems.append(f"{name}: sha256 {actual[:12]}… != pinned {expected[:12]}…")
    return not problems, problems


def score(features: pd.DataFrame, model, minutes_model, base_model) -> pd.DataFrame:
    """P(play) and E[minutes|plays] from the pinned artifact, with no refit."""
    # validate_out_of_fold is deliberately not called: every replay row predates
    # the artifact cutoff, so the guard would raise on all of them.
    base_p = base_model.predict_proba(features)
    rebuilt = attach_expected_context(features, base_p, pd.Timestamp(base_model.cutoff))
    # the v6 teammate sums read the same p_j at serving (predict.rebuild_context).
    if "started_rate_10" in rebuilt.columns:
        rebuilt = teammate_start_features(rebuilt)
    scored = minutes_model.attach(model.attach(rebuilt))
    scored["P_CONTEXT_BASE"] = base_p
    return scored


def score_per_date(features: pd.DataFrame, model, minutes_model, base_model) -> pd.DataFrame:
    """the same scoring, one game date at a time, in date order."""
    frames = []
    for game_date in sorted(pd.to_datetime(features["GAME_DATE"]).unique()):
        day = features[pd.to_datetime(features["GAME_DATE"]) == game_date]
        frames.append(score(day, model, minutes_model, base_model))
    return pd.concat(frames, ignore_index=True)


def brier(frame: pd.DataFrame) -> float:
    if frame.empty:
        return float("nan")
    y = frame["PLAYED"].to_numpy(dtype=float)
    p = frame[P_PLAY].to_numpy(dtype=float)
    return float(np.mean((p - y) ** 2))


def minutes_mae(frame: pd.DataFrame) -> float:
    """MAE of E[minutes|plays] over APPEARANCE rows only."""
    played = frame[frame["PLAYED"] == 1]
    if played.empty:
        return float("nan")
    return float(
        np.mean(np.abs(played[MIN_PRED].to_numpy(dtype=float)
                       - played[MINUTES_TARGET].to_numpy(dtype=float)))
    )


def coverage(scored: pd.DataFrame, scheduled: int) -> float:
    """share of scheduled player-games that came back with a usable prediction."""
    if scheduled == 0:
        return float("nan")
    usable = (
        np.isfinite(scored[P_PLAY].to_numpy(dtype=float))
        & np.isfinite(scored[MIN_PRED].to_numpy(dtype=float))
    )
    return float(usable.sum()) / float(scheduled)


def share(frame: pd.DataFrame, mask: pd.Series | np.ndarray) -> float:
    if frame.empty:
        return float("nan")
    return float(np.asarray(mask, dtype=float).mean())


def cohort_table(october: pd.DataFrame, rest: pd.DataFrame) -> pd.DataFrame:
    """per-minutes-tier Brier and minutes MAE on both sides, plus the ratios."""
    rows = []
    tiers = sorted(set(october.get(TIER_COL, pd.Series(dtype=object)).dropna())
                   | set(rest.get(TIER_COL, pd.Series(dtype=object)).dropna()))
    for tier in ["ALL", *tiers]:
        oct_rows = october if tier == "ALL" else october[october[TIER_COL] == tier]
        rest_rows = rest if tier == "ALL" else rest[rest[TIER_COL] == tier]
        ob, rb = brier(oct_rows), brier(rest_rows)
        om, rm = minutes_mae(oct_rows), minutes_mae(rest_rows)
        rows.append({
            "cohort": tier,
            "oct_rows": len(oct_rows),
            "rest_rows": len(rest_rows),
            "oct_brier": ob,
            "rest_brier": rb,
            "brier_ratio": ob / rb if rb else float("nan"),
            "oct_min_mae": om,
            "rest_min_mae": rm,
            "min_mae_ratio": om / rm if rm else float("nan"),
        })
    return pd.DataFrame(rows)


def evaluate_gate(
    october: pd.DataFrame,
    rest: pd.DataFrame,
    scheduled_october: int,
    gate: dict[str, float] = PROSPECTIVE_OCTOBER_GATE,
) -> pd.DataFrame:
    """the three frozen criteria, as a verdict table."""
    cov = coverage(october, scheduled_october)
    ob, rb = brier(october), brier(rest)
    om, rm = minutes_mae(october), minutes_mae(rest)
    brier_ratio = ob / rb if rb else float("nan")
    minutes_ratio = om / rm if rm else float("nan")

    return pd.DataFrame([
        {
            "criterion": "1. prediction coverage",
            "observed": cov,
            "bar": gate["min_prediction_coverage"],
            "direction": ">=",
            "pass": bool(cov >= gate["min_prediction_coverage"]),
        },
        {
            "criterion": "2. October / non-October Brier",
            "observed": brier_ratio,
            "bar": gate["max_brier_ratio"],
            "direction": "<=",
            "pass": bool(brier_ratio <= gate["max_brier_ratio"]),
        },
        {
            "criterion": "3. October / non-October minutes MAE",
            "observed": minutes_ratio,
            "bar": gate["max_minutes_mae_ratio"],
            "direction": "<=",
            "pass": bool(minutes_ratio <= gate["max_minutes_mae_ratio"]),
        },
    ])


LIMITATION = (
    "**October 2025 is inside artifact {version}'s training window "
    "(2022-10-18 -> 2026-04-12). These October numbers are IN-SAMPLE and their "
    "absolute level is optimistic.** The gate is survivable anyway because every "
    "criterion is a RATIO against non-October rows drawn from the same season, "
    "fitted by the same model, contaminated the same way -- a uniform in-sample "
    "optimism divides out of a ratio and does not divide out of a level. The "
    "residual risk it cannot rule out is a NON-uniform optimism, i.e. the model "
    "overfitting its October rows harder than its March rows, which would make "
    "the ratio too kind. A PASS therefore reads as \"no October-shaped "
    "catastrophe is visible\", not as \"October 2026 will look like this\". "
    "Refitting to a pre-October cutoff was rejected: MODEL.md 13.7 criterion 4 "
    "requires the pinned checksums, and a replay against a differently-trained "
    "artifact measures that artifact."
)


def render(
    verdict: pd.DataFrame,
    cohorts: pd.DataFrame,
    facts: dict[str, object],
    checksum_ok: bool,
    checksum_problems: list[str],
) -> str:
    lines = [
        f"# October replay gate — {facts['version']}",
        "",
        f"generated {facts['generated_at']} · protocol `{PROSPECTIVE_PROTOCOL_VERSION}` "
        f"· acceptance criteria frozen in MODEL.md 13.7",
        "",
        "## The known limitation",
        "",
        LIMITATION.format(version=facts["version"]),
        "",
        "## Verdict",
        "",
        f"**{facts['headline']}**",
        "",
        "| criterion | observed | | bar | verdict |",
        "|---|---:|:-:|---:|:-:|",
    ]
    for row in verdict.to_dict("records"):
        lines.append(
            f"| {row['criterion']} | {row['observed']:.4f} | {row['direction']} | "
            f"{row['bar']:.4f} | {'PASS' if row['pass'] else '**FAIL**'} |"
        )
    lines.append(
        f"| 4. pinned checksums | {'all 6 files match' if checksum_ok else '; '.join(checksum_problems)} "
        f"| = | frozen set | {'PASS' if checksum_ok else '**FAIL**'} |"
    )

    lines += [
        "",
        "## Supporting numbers",
        "",
        "| | October | non-October |",
        "|---|---:|---:|",
        f"| window | {facts['october_window']} | {facts['rest_window']} |",
        f"| scheduled rows | {facts['scheduled_october']:,} | {facts['scheduled_rest']:,} |",
        f"| scored rows | {facts['scored_october']:,} | {facts['scored_rest']:,} |",
        f"| appearance rows | {facts['appearances_october']:,} | {facts['appearances_rest']:,} |",
        f"| played rate | {facts['played_october']:.4f} | {facts['played_rest']:.4f} |",
        f"| availability Brier (IN-SAMPLE) | {facts['brier_october']:.4f} | {facts['brier_rest']:.4f} |",
        f"| minutes MAE (IN-SAMPLE) | {facts['mae_october']:.4f} | {facts['mae_rest']:.4f} |",
        f"| insufficient_history share | {facts['insufficient_october']:.4f} | {facts['insufficient_rest']:.4f} |",
        f"| cross-fit fallback share | {facts['fallback_october']:.4f} | {facts['fallback_rest']:.4f} |",
        f"| distinct players | {facts['players_october']:,} | {facts['players_rest']:,} |",
        "",
        "The two shares are the ones criterion 1 requires beside coverage: a replay "
        "that silently drops the rows it finds hard is measuring the wrong month, so "
        "the hard rows are counted rather than excluded.",
        "",
        "## Cohort split (the frozen minutes tiers)",
        "",
        cohorts.to_markdown(index=False, floatfmt=".4f"),
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)

    start, end = pd.Timestamp(args.window[0]), pd.Timestamp(args.window[1])
    checksum_ok, checksum_problems = verify_pinned_artifact(args.version, args.models_dir)
    if not checksum_ok and not args.skip_checksums:
        print("--- OCTOBER REPLAY GATE ---")
        print("criterion 4 FAILED before anything was scored:")
        for problem in checksum_problems:
            print(f"  {problem}")
        print("a replay against a differently-trained artifact tells us about that "
              "artifact. pass --skip-checksums to run it anyway; the verdict still "
              "reports FAIL.")
        return 1

    model, minutes_model, base_model, metadata = load_version(args.version, args.models_dir)
    features = load_dataset(args.dataset)
    features["GAME_DATE"] = pd.to_datetime(features["GAME_DATE"])

    season = args.season
    if season is None:
        in_window = features[(features["GAME_DATE"] >= start) & (features["GAME_DATE"] <= end)]
        if in_window.empty:
            raise SystemExit(
                f"no rows in {start.date()}..{end.date()} in {args.dataset}; the "
                f"replay window is outside the dataset"
            )
        season = str(in_window["SEASON"].mode().iloc[0])

    replay = features[features["SEASON"] == season].copy()
    if replay.empty:
        raise SystemExit(f"season {season!r} has no rows in {args.dataset}")

    october_mask = (replay["GAME_DATE"] >= start) & (replay["GAME_DATE"] <= end)
    scheduled_october = int(october_mask.sum())
    scheduled_rest = int((~october_mask).sum())
    if scheduled_october == 0 or scheduled_rest == 0:
        raise SystemExit(
            f"the replay needs rows on BOTH sides: {scheduled_october} October and "
            f"{scheduled_rest} non-October rows in season {season}"
        )

    log.info(
        "replaying season %s with the pinned artifact %s: %d October rows, %d non-October",
        season, args.version, scheduled_october, scheduled_rest,
    )
    scored = score(replay, model, minutes_model, base_model)

    if args.per_date:
        log.info("--per-date: rescoring one date at a time to check the single pass")
        per_date = score_per_date(replay, model, minutes_model, base_model)
        key = ["PLAYER_ID", "GAME_ID", "TEAM_ID"]
        left = scored.set_index(key)[[P_PLAY, MIN_PRED]].sort_index()
        right = per_date.set_index(key)[[P_PLAY, MIN_PRED]].sort_index()
        drift = float(np.abs(left.to_numpy() - right.to_numpy()).max())
        log.info("per-date vs single-pass max absolute drift: %.3e", drift)
        if drift > 1e-9:
            raise SystemExit(
                f"scoring date by date changed the numbers by {drift:.3e}. some "
                f"feature has acquired a cross-date dependency at serving time and "
                f"the single-pass replay is no longer equivalent."
            )

    october = scored[october_mask.to_numpy()]
    rest = scored[(~october_mask).to_numpy()]

    verdict = evaluate_gate(october, rest, scheduled_october)
    cohorts = cohort_table(october, rest)
    all_pass = bool(verdict["pass"].all()) and checksum_ok

    def _share(frame: pd.DataFrame, column: str, predicate) -> float:
        if frame.empty or column not in frame.columns:
            return float("nan")
        return float(predicate(frame[column]).mean())

    facts: dict[str, object] = {
        "generated_at": pd.Timestamp.now("UTC").strftime("%Y-%m-%d %H:%M UTC"),
        "version": args.version,
        "season": season,
        "october_window": f"{start.date()} .. {end.date()}",
        "rest_window": (
            f"{rest['GAME_DATE'].min().date()} .. {rest['GAME_DATE'].max().date()}"
        ),
        "scheduled_october": scheduled_october,
        "scheduled_rest": scheduled_rest,
        "scored_october": len(october),
        "scored_rest": len(rest),
        "appearances_october": int((october["PLAYED"] == 1).sum()),
        "appearances_rest": int((rest["PLAYED"] == 1).sum()),
        "played_october": float(october["PLAYED"].mean()),
        "played_rest": float(rest["PLAYED"].mean()),
        "brier_october": brier(october),
        "brier_rest": brier(rest),
        "mae_october": minutes_mae(october),
        "mae_rest": minutes_mae(rest),
        "insufficient_october": _share(october, INSUFFICIENT, lambda s: s == 1),
        "insufficient_rest": _share(rest, INSUFFICIENT, lambda s: s == 1),
        "fallback_october": _share(october, CONTEXT_SOURCE, lambda s: s == CROSS_FIT_FALLBACK),
        "fallback_rest": _share(rest, CONTEXT_SOURCE, lambda s: s == CROSS_FIT_FALLBACK),
        "players_october": int(october["PLAYER_ID"].nunique()),
        "players_rest": int(rest["PLAYER_ID"].nunique()),
        "headline": (
            "GATE PASSED -- all four criteria met" if all_pass
            else "GATE FAILED -- see the verdict table"
        ),
    }

    report_path = args.report or (REPORTS_DIR / f"october_replay_{args.version}.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        render(verdict, cohorts, facts, checksum_ok, checksum_problems), encoding="utf-8"
    )
    cohorts.to_csv(report_path.with_suffix(".csv"), index=False)

    print("--- OCTOBER REPLAY GATE ---")
    print(f"protocol  : {PROSPECTIVE_PROTOCOL_VERSION} (criteria frozen in MODEL.md 13.7)")
    print(f"artifact  : {args.version}  (feature_version "
          f"{metadata.get('feature_version')}, trained through "
          f"{(metadata.get('training_window') or {}).get('end')})")
    print(f"season    : {season}")
    print(f"October   : {facts['october_window']}   {scheduled_october:,} scheduled rows")
    print(f"non-Oct   : {facts['rest_window']}   {scheduled_rest:,} scheduled rows")
    print()
    print("KNOWN LIMITATION: October 2025 is INSIDE this artifact's training window.")
    print("  The absolute numbers below are in-sample and optimistic. Every frozen")
    print("  criterion is a RATIO against non-October rows contaminated the same way,")
    print("  which is what makes a degradation SHAPE readable off a contaminated level.")
    print()
    print("verdict:")
    for row in verdict.to_dict("records"):
        mark = "PASS" if row["pass"] else "FAIL"
        print(f"  [{mark}] {row['criterion']:<38s} {row['observed']:8.4f} "
              f"{row['direction']} {row['bar']:.4f}")
    mark = "PASS" if checksum_ok else "FAIL"
    detail = "all 6 files match the pinned set" if checksum_ok else "; ".join(checksum_problems)
    print(f"  [{mark}] 4. pinned checksums                    {detail}")
    print()
    print("supporting numbers (IN-SAMPLE -- see the limitation above):")
    print(f"  availability Brier   October {facts['brier_october']:.4f}   "
          f"non-October {facts['brier_rest']:.4f}")
    print(f"  minutes MAE          October {facts['mae_october']:.4f}   "
          f"non-October {facts['mae_rest']:.4f}")
    print(f"  appearance rows      October {facts['appearances_october']:,}   "
          f"non-October {facts['appearances_rest']:,}")
    print(f"  played rate          October {facts['played_october']:.4f}   "
          f"non-October {facts['played_rest']:.4f}")
    print(f"  insufficient_history October {facts['insufficient_october']:.4f}   "
          f"non-October {facts['insufficient_rest']:.4f}")
    print(f"  cross-fit fallback   October {facts['fallback_october']:.4f}   "
          f"non-October {facts['fallback_rest']:.4f}")
    print()
    print("cohort split (the frozen minutes tiers):")
    print(cohorts.to_string(index=False, float_format=lambda v: f"{v:8.4f}"))
    print()
    print(f"report    -> {report_path}")
    print(f"cohorts   -> {report_path.with_suffix('.csv')}")
    print()
    print(facts["headline"])
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
