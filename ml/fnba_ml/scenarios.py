"""scenario serving for uncertain stars: average the outputs, not the inputs.

the single-run path scores a questionable star's teammates once, with his p_j
blended into their expected-vacated features. the final models are nonlinear, so
f(E[p]) is not E[f(p)]: the backup's projection at p = 0.5 is neither the night the
star plays nor the night he sits, nor their average. this module scores each
team-game with pivotal players once per world (each pivotal player forced to play
or to sit) and mixes the outputs by the worlds' probabilities.

pure helpers plus one orchestrator. the orchestrator takes the scoring functions
as arguments so it never imports predict.py, which imports it.
"""

from __future__ import annotations

import itertools
import json
import logging
import re
from typing import Callable

import numpy as np
import pandas as pd

from .config import (
    MINUTES_TARGET,
    SCENARIO_MAX_PIVOTAL,
    SCENARIO_MIN_MAGNITUDE,
    STAR_USAGE_MIN_APPEARANCES,
    TOP_USAGE_N,
)
from .models import MIN_PRED, P_PLAY, coherence_clip_frame
from .overrides import (
    DEFAULT_POLICY,
    P_PLAY_MODEL,
    STATUS_DOUBTFUL,
    STATUS_NORMALIZED,
    STATUS_QUESTIONABLE,
    StatusPolicy,
    apply_status_overrides,
    latest_statuses,
)

log = logging.getLogger(__name__)

PIVOTAL_STATUSES: frozenset[str] = frozenset({STATUS_QUESTIONABLE, STATUS_DOUBTFUL})

# the p_j a forced "plays" world hands the teammate sums; "sits" uses the OUT rule.
FORCED_PLAYS_PROBABILITY: float = 1.0

PIVOTAL_COLUMNS: tuple[str, ...] = (
    "GAME_ID", "TEAM_ID", "PLAYER_ID", "p", "status", "magnitude", "row",
)

AUDIT_COLUMNS: tuple[str, ...] = (
    "GAME_ID", "TEAM_ID", "PLAYER_ID", "STATUS", "P_PLAY", "MAGNITUDE_MIN",
    "N_PIVOTAL", "N_SCENARIOS", "SCENARIO_WEIGHTS", "BACKUP_PLAYER_ID",
    "BACKUP_E_MIN_COND_PLAYS", "BACKUP_E_MIN_COND_SITS", "BACKUP_MIN_DELTA",
)

QUANTILE_PATTERN = re.compile(r"^Q(\d{2})_(.+)$")

Scenario = tuple[dict[str, int], float]
RebuildFn = Callable[..., tuple[pd.DataFrame, pd.DataFrame]]
ScoreFn = Callable[..., pd.DataFrame]


def _empty_pivotal() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=object) for c in PIVOTAL_COLUMNS})


def _usage_top_n(magnitudes: pd.DataFrame, group: np.ndarray) -> np.ndarray:
    """True where the row is among its team-game's top TOP_USAGE_N by tm_USG."""
    if "tm_USG" not in magnitudes.columns:
        return np.zeros(len(magnitudes), dtype=bool)
    usage = pd.to_numeric(magnitudes["tm_USG"], errors="coerce").reset_index(drop=True)
    established = usage.notna()
    # the same established rule p_star_out ranks by, so "star" means one thing.
    if "n_appearances" in magnitudes.columns:
        established &= (
            pd.to_numeric(magnitudes["n_appearances"], errors="coerce")
            .fillna(0.0).reset_index(drop=True)
            >= STAR_USAGE_MIN_APPEARANCES
        )
    ranked = usage.where(established).groupby(group).rank(
        ascending=False, method="first"
    )
    return (ranked <= TOP_USAGE_N).fillna(False).to_numpy(dtype=bool)


def pivotal_players(
    upcoming: pd.DataFrame,
    statuses_latest: pd.DataFrame,
    magnitudes: pd.DataFrame,
    policy: StatusPolicy = DEFAULT_POLICY,
    *,
    min_magnitude: float = SCENARIO_MIN_MAGNITUDE,
    max_per_team_game: int = SCENARIO_MAX_PIVOTAL,
) -> pd.DataFrame:
    """the questionable or doubtful players worth scoring in two worlds.

    ``upcoming`` carries GAME_ID, TEAM_ID, PLAYER_ID and the model's own P(play)
    (``P_PLAY_MODEL``, else ``P_PLAY``) that the policy blends with.
    ``statuses_latest`` is :func:`overrides.latest_statuses` output, so the as-of
    boundary has already been applied. ``magnitudes`` is positionally aligned to
    ``upcoming`` and carries tm_MIN, tm_USG and n_appearances.

    returns one row per pivotal player with his policy probability ``p`` and his
    position ``row`` in ``upcoming``; at most ``max_per_team_game`` per team-game,
    the largest minutes magnitudes kept.
    """
    if len(magnitudes) != len(upcoming):
        raise ValueError(
            f"magnitudes has {len(magnitudes)} rows for {len(upcoming)} upcoming "
            f"rows; it must be positionally aligned"
        )
    if statuses_latest is None or statuses_latest.empty or upcoming.empty:
        return _empty_pivotal()

    by_player = statuses_latest.set_index(
        statuses_latest["nba_player_id"].astype(str)
    )[STATUS_NORMALIZED]
    frame = upcoming.reset_index(drop=True)
    status = frame["PLAYER_ID"].astype(str).map(by_player)
    uncertain = status.isin(PIVOTAL_STATUSES).to_numpy()
    if not uncertain.any():
        return _empty_pivotal()

    keys = frame["GAME_ID"].astype(str) + "\x1f" + frame["TEAM_ID"].astype(str)
    group = pd.factorize(keys)[0]
    magnitude = pd.to_numeric(
        magnitudes["tm_MIN"], errors="coerce"
    ).reset_index(drop=True).to_numpy(dtype=float)
    large = np.nan_to_num(magnitude, nan=-np.inf) >= float(min_magnitude)
    star = _usage_top_n(magnitudes, group)
    hit = uncertain & (large | star)
    if not hit.any():
        return _empty_pivotal()

    model_column = P_PLAY_MODEL if P_PLAY_MODEL in frame.columns else P_PLAY
    model_p = frame[model_column].to_numpy(dtype=float)
    rows = np.flatnonzero(hit)
    candidates = pd.DataFrame({
        "GAME_ID": frame["GAME_ID"].to_numpy()[rows],
        "TEAM_ID": frame["TEAM_ID"].to_numpy()[rows],
        "PLAYER_ID": frame["PLAYER_ID"].astype(str).to_numpy()[rows],
        "p": [
            float(np.clip(policy.probability(status.iloc[r], model_p[r]), 0.0, 1.0))
            for r in rows
        ],
        "status": status.to_numpy()[rows],
        "magnitude": magnitude[rows],
        "row": rows,
    })
    candidates = candidates.sort_values(
        ["GAME_ID", "TEAM_ID", "magnitude"], ascending=[True, True, False],
        kind="stable",
    )
    rank = candidates.groupby(["GAME_ID", "TEAM_ID"]).cumcount()
    dropped = candidates[rank >= max_per_team_game]
    for _, row in dropped.iterrows():
        log.info(
            "scenario cap: %s (%s, tm_MIN %.1f) in game %s team %s is pivotal but "
            "beyond the %d largest; served at his blended p",
            row["PLAYER_ID"], row["status"], row["magnitude"], row["GAME_ID"],
            row["TEAM_ID"], max_per_team_game,
        )
    return candidates[rank < max_per_team_game].reset_index(drop=True)


def enumerate_scenarios(pivotal_for_team_game: pd.DataFrame) -> list[Scenario]:
    """every play/sit assignment of one team-game's pivotal players, with weights.

    ASSUMPTION: the pivotal players of one team-game play or sit independently, so
    a world's weight is the product of p (plays) or 1 - p (sits). two questionable
    players on one team are not independent in general (a team resting both for
    the same back-to-back is one decision), and this does not model that.
    """
    players = [str(p) for p in pivotal_for_team_game["PLAYER_ID"]]
    probabilities = [float(p) for p in pivotal_for_team_game["p"]]
    scenarios: list[Scenario] = []
    for outcome in itertools.product((1, 0), repeat=len(players)):
        weight = 1.0
        for plays, p in zip(outcome, probabilities):
            weight *= p if plays else 1.0 - p
        scenarios.append((dict(zip(players, outcome)), weight))
    return scenarios


def _column_groups(frame: pd.DataFrame) -> tuple[list[str], list[str], list[str]]:
    """(probability, conditional, unconditional) output columns on a scored frame."""
    probability = [c for c in (P_PLAY, P_PLAY_MODEL) if c in frame.columns]
    conditional = [c for c in frame.columns if c == MIN_PRED or (
        c.startswith("E_") and c.endswith("_COND")
    ) or QUANTILE_PATTERN.match(c)]
    unconditional = [
        c for c in frame.columns
        if c.startswith("E_") and not c.endswith("_COND")
        and f"{c}_COND" in frame.columns
    ]
    return probability, conditional, unconditional


def _weighted(runs: list[pd.DataFrame], weights: list[float], column: str) -> np.ndarray:
    total = float(sum(weights))
    stacked = np.stack([run[column].to_numpy(dtype=float) for run in runs])
    return (np.asarray(weights, dtype=float)[:, None] * stacked).sum(axis=0) / total


def sort_quantiles(frame: pd.DataFrame) -> pd.DataFrame:
    """row-wise ascending sort of every target's Q columns, by level."""
    out = frame.copy()
    by_target: dict[str, list[tuple[int, str]]] = {}
    for column in out.columns:
        match = QUANTILE_PATTERN.match(column)
        if match:
            by_target.setdefault(match.group(2), []).append((int(match.group(1)), column))
    for levels in by_target.values():
        columns = [c for _, c in sorted(levels)]
        if len(columns) > 1:
            out[columns] = np.sort(out[columns].to_numpy(dtype=float), axis=1)
    return out


def _coherent(frame: pd.DataFrame) -> pd.DataFrame:
    """the clip build_predictions applies, then the non-crossing sort."""
    templates = ["E_{target}_COND", "E_{target}"]
    templates += sorted({
        f"Q{m.group(1)}_{{target}}"
        for m in (QUANTILE_PATTERN.match(c) for c in frame.columns) if m
    })
    out = frame
    for template in templates:
        out, _ = coherence_clip_frame(out, template)
    return sort_quantiles(out)


def mix_team_game(
    baseline_rows: pd.DataFrame,
    runs: list[pd.DataFrame],
    scenarios: list[Scenario],
) -> pd.DataFrame:
    """one team-game's outputs, mixed over its scenario runs.

    non-pivotal rows: every output column is the weight average over worlds.
    pivotal rows: P(play) stays the single-run blended number, the conditional
    columns come from the worlds he plays in (re-weighted over the other pivotal
    players), and the unconditional ones are P(play) x conditional.
    """
    out = baseline_rows.reset_index(drop=True).copy()
    weights = [w for _, w in scenarios]
    probability, conditional, unconditional = _column_groups(out)
    players = out["PLAYER_ID"].astype(str).to_numpy()
    pivotal = set(scenarios[0][0])
    is_pivotal = np.isin(players, list(pivotal))

    for column in probability + conditional + unconditional:
        mixed = _weighted(runs, weights, column)
        values = out[column].to_numpy(dtype=float).copy()
        values[~is_pivotal] = mixed[~is_pivotal]
        out[column] = values

    for player in pivotal:
        rows = np.flatnonzero(players == player)
        plays = [i for i, (a, _) in enumerate(scenarios) if a[player] == 1]
        sub_runs = [runs[i] for i in plays]
        sub_weights = [weights[i] for i in plays]
        if float(sum(sub_weights)) <= 0.0:
            # a certain sit leaves no world to read his conditional from; keep the
            # single run's rather than divide by zero.
            continue
        for column in conditional:
            values = out[column].to_numpy(dtype=float).copy()
            values[rows] = _weighted(sub_runs, sub_weights, column)[rows]
            out[column] = values
        p = out[P_PLAY].to_numpy(dtype=float)
        for column in unconditional:
            values = out[column].to_numpy(dtype=float).copy()
            cond = out[f"{column}_COND"].to_numpy(dtype=float)
            values[rows] = np.clip(p[rows] * cond[rows], 0.0, None)
            out[column] = values
    return _coherent(out)


def _backup_delta(
    rows: pd.DataFrame,
    runs: list[pd.DataFrame],
    scenarios: list[Scenario],
    player: str,
) -> tuple[object, float, float, float]:
    """(backup id, his E_MIN_COND when the player plays, when he sits, the delta)."""
    column = f"E_{MINUTES_TARGET}_COND"
    pivotal = set(scenarios[0][0])
    ids = rows["PLAYER_ID"].astype(str).to_numpy()
    others = ~np.isin(ids, list(pivotal))
    weights = [w for _, w in scenarios]
    plays = [i for i, (a, _) in enumerate(scenarios) if a[player] == 1]
    sits = [i for i, (a, _) in enumerate(scenarios) if a[player] == 0]
    plays_w = [weights[i] for i in plays]
    sits_w = [weights[i] for i in sits]
    if not others.any() or sum(plays_w) <= 0.0 or sum(sits_w) <= 0.0:
        return None, float("nan"), float("nan"), float("nan")
    m_plays = _weighted([runs[i] for i in plays], plays_w, column)
    m_sits = _weighted([runs[i] for i in sits], sits_w, column)
    delta = np.where(others, m_sits - m_plays, -np.inf)
    best = int(np.argmax(delta))
    return (
        rows["PLAYER_ID"].iloc[best], float(m_plays[best]), float(m_sits[best]),
        float(delta[best]),
    )


def _empty_audit() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=object) for c in AUDIT_COLUMNS})


def score_with_scenarios(
    upcoming: pd.DataFrame,
    base_model,
    model,
    minutes_model,
    metadata: dict,
    statuses: pd.DataFrame | None,
    as_of: pd.Timestamp,
    policy: StatusPolicy = DEFAULT_POLICY,
    *,
    baseline: pd.DataFrame | None = None,
    rebuild: RebuildFn | None = None,
    score: ScoreFn | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(predictions, audit): the single-run output with pivotal team-games mixed.

    ``upcoming`` is the raw slate (before the context rebuild). ``baseline`` is
    the single-run prediction frame for it, positionally aligned; it is computed
    here when not supplied. ``rebuild`` and ``score`` are predict.rebuild_context
    and predict.build_predictions. rows of team-games with no pivotal player are
    returned exactly as the single run produced them.
    """
    if rebuild is None or score is None:
        import predict  # noqa: PLC0415  predict.py imports this module at top level

        rebuild = rebuild or predict.rebuild_context
        score = score or predict.build_predictions

    frame = upcoming.reset_index(drop=True)
    if baseline is None:
        rebuilt, _ = rebuild(frame, base_model, statuses, as_of, policy)
        baseline = apply_status_overrides(
            score(rebuilt, model, minutes_model, metadata), statuses, policy,
            as_of=as_of,
        )
    if len(baseline) != len(frame):
        raise ValueError(
            f"baseline has {len(baseline)} rows for {len(frame)} upcoming rows; "
            f"they must be positionally aligned"
        )

    latest = (
        latest_statuses(statuses, as_of)
        if statuses is not None and len(statuses) > 0 else pd.DataFrame()
    )
    probe = frame[["GAME_ID", "TEAM_ID", "PLAYER_ID"]].copy()
    model_column = P_PLAY_MODEL if P_PLAY_MODEL in baseline.columns else P_PLAY
    probe[P_PLAY_MODEL] = baseline[model_column].to_numpy(dtype=float)
    pivotal = pivotal_players(probe, latest, frame, policy)
    if pivotal.empty:
        log.info("scenarios: no pivotal player on the slate; single run stands")
        return baseline, _empty_audit()

    out = baseline.copy()
    clip_counts = baseline.attrs.get("coherence_clips")
    audit_rows: list[dict[str, object]] = []
    game_ids = frame["GAME_ID"].to_numpy()
    team_ids = frame["TEAM_ID"].to_numpy()
    for (game, team), group in pivotal.groupby(["GAME_ID", "TEAM_ID"], sort=True):
        positions = np.flatnonzero((game_ids == game) & (team_ids == team))
        sub = frame.iloc[positions]
        scenarios = enumerate_scenarios(group)
        runs: list[pd.DataFrame] = []
        for assignment, _ in scenarios:
            forced = {
                player: FORCED_PLAYS_PROBABILITY if plays else policy.out_probability
                for player, plays in assignment.items()
            }
            rebuilt, _ = rebuild(
                sub, base_model, statuses, as_of, policy, forced_probabilities=forced
            )
            run = apply_status_overrides(
                score(rebuilt, model, minutes_model, metadata), statuses, policy,
                as_of=as_of,
            )
            if run["PLAYER_ID"].astype(str).tolist() != sub["PLAYER_ID"].astype(str).tolist():
                raise ValueError(f"scenario run for {game}/{team} reordered its rows")
            runs.append(run)

        rows = baseline.iloc[positions]
        mixed = mix_team_game(rows, runs, scenarios)
        columns = [c for c in mixed.columns if c in out.columns]
        for column in columns:
            values = out[column].to_numpy().copy()
            values[positions] = mixed[column].to_numpy()
            out[column] = values

        weights = json.dumps([
            {"plays": assignment, "weight": round(weight, 6)}
            for assignment, weight in scenarios
        ])
        for _, player in group.iterrows():
            backup, m_plays, m_sits, delta = _backup_delta(
                rows.reset_index(drop=True), runs, scenarios, str(player["PLAYER_ID"])
            )
            audit_rows.append({
                "GAME_ID": game,
                "TEAM_ID": team,
                "PLAYER_ID": player["PLAYER_ID"],
                "STATUS": player["status"],
                "P_PLAY": float(player["p"]),
                "MAGNITUDE_MIN": float(player["magnitude"]),
                "N_PIVOTAL": len(group),
                "N_SCENARIOS": len(scenarios),
                "SCENARIO_WEIGHTS": weights,
                "BACKUP_PLAYER_ID": None if backup is None else str(backup),
                "BACKUP_E_MIN_COND_PLAYS": m_plays,
                "BACKUP_E_MIN_COND_SITS": m_sits,
                "BACKUP_MIN_DELTA": delta,
            })

    audit = pd.DataFrame(audit_rows, columns=list(AUDIT_COLUMNS))
    log.info(
        "scenarios: %d pivotal players across %d team-games re-scored by world",
        len(pivotal), audit[["GAME_ID", "TEAM_ID"]].drop_duplicates().shape[0],
    )
    # attached last: attrs do not reliably survive the column writes above.
    if clip_counts is not None:
        out.attrs["coherence_clips"] = clip_counts
    return out, audit


def scenario_summary(audit: pd.DataFrame) -> dict[str, object]:
    """the run-level numbers predict.py prints and stores in notes."""
    if audit.empty:
        return {"team_games": 0, "pivotal_players": 0, "scenarios": 0,
                "mean_backup_min_delta": None, "max_backup_min_delta": None}
    per_team_game = audit.drop_duplicates(["GAME_ID", "TEAM_ID"])
    delta = pd.to_numeric(audit["BACKUP_MIN_DELTA"], errors="coerce")
    return {
        "team_games": int(len(per_team_game)),
        "pivotal_players": int(len(audit)),
        "scenarios": int(per_team_game["N_SCENARIOS"].sum()),
        "mean_backup_min_delta": None if delta.isna().all() else round(float(delta.mean()), 3),
        "max_backup_min_delta": None if delta.isna().all() else round(float(delta.max()), 3),
    }
