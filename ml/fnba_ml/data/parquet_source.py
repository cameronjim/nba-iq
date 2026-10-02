"""parquet data source, matching the phase-0 spike's file shapes.

expected layout in ``data_dir``::

    player_logs_2023_24.parquet          nba_api PlayerGameLogs shape
    team_logs_2023_24.parquet            nba_api TeamGameLogs shape
    player_game_status_2023_24.parquet   OPTIONAL, canonical status shape
    player_positions.parquet             OPTIONAL, PLAYER_ID + POSITION

the schedule is reconstructed from the team logs (two rows per game, MATCHUP
tells us which side is home), which is exact — team logs are complete.

there is deliberately no roster/inactive source in this format. when
``load_status`` returns None the caller falls back to
:func:`fnba_ml.universe.approximate_universe`, which is BIASED; see that
function's docstring and REPORT.md section 5.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from ..config import COMPETITION_BY_SEASON_TYPE, COMPETITION_COL, SEASONS, season_tag
from .schema import (
    BOX_DETAIL_COLS,
    PLAYER_LOG_COLS,
    POSITION_COLS,
    SCHEDULE_COLS,
    STAT_COLS,
    STATUS_COLS,
    TEAM_LOG_COLS,
    TEAM_LOG_OPTIONAL_COLS,
    normalise_dates,
    normalise_ids,
    require_columns,
)

log = logging.getLogger(__name__)

DEFAULT_SEASON_TYPE = "Regular Season"
DEFAULT_COMPETITION = COMPETITION_BY_SEASON_TYPE[DEFAULT_SEASON_TYPE]
SEASON_TYPE_BY_COMPETITION = {c: t for t, c in COMPETITION_BY_SEASON_TYPE.items()}


def _competition(raw: pd.DataFrame) -> pd.Series:
    """a file's own COMPETITION column, else regular season: the nba_api exports carry none."""
    if COMPETITION_COL in raw.columns:
        return raw[COMPETITION_COL].astype(object)
    return pd.Series(DEFAULT_COMPETITION, index=raw.index, dtype=object)


class ParquetSource:
    """reads the canonical frames out of a directory of parquet files."""

    name = "parquet"

    def __init__(self, data_dir: str | Path, seasons: list[str] | None = None) -> None:
        self.data_dir = Path(data_dir)
        self.seasons = list(seasons or SEASONS)
        if not self.data_dir.is_dir():
            raise FileNotFoundError(f"parquet data dir not found: {self.data_dir}")

    # ------------------------------------------------------------------
    def _read_seasons(self, prefix: str) -> tuple[pd.DataFrame, list[str]]:
        frames, found = [], []
        for season in self.seasons:
            path = self.data_dir / f"{prefix}_{season_tag(season)}.parquet"
            if not path.exists():
                continue
            df = pd.read_parquet(path)
            df["SEASON"] = season
            frames.append(df)
            found.append(season)
        if not frames:
            return pd.DataFrame(), found
        return pd.concat(frames, ignore_index=True), found

    # ------------------------------------------------------------------
    def load_player_game_logs(self) -> pd.DataFrame:
        raw, found = self._read_seasons("player_logs")
        if raw.empty:
            raise FileNotFoundError(
                f"no player_logs_*.parquet for seasons {self.seasons} in {self.data_dir}"
            )
        require_columns(raw, ("PLAYER_ID", "GAME_ID", "GAME_DATE", "TEAM_ID"), "player log")

        keep = ["PLAYER_ID", "PLAYER_NAME", "GAME_ID", "SEASON", "GAME_DATE",
                "TEAM_ID", "TEAM_ABBREVIATION"] + [c for c in STAT_COLS if c in raw.columns]
        if "PLUS_MINUS" in raw.columns:
            keep.append("PLUS_MINUS")
        out = raw[keep].copy()

        # the nba_api export carries no season type, no starter flag and no dnp
        # reason. they are declared so the frame shape matches the postgres
        # source, and left null so nothing downstream can quietly rely on them.
        out[COMPETITION_COL] = _competition(raw)
        out["SEASON_TYPE"] = out[COMPETITION_COL].map(SEASON_TYPE_BY_COMPETITION)
        out["STARTED"] = pd.NA
        out["DNP_REASON"] = pd.NA

        out = normalise_ids(normalise_dates(out))
        require_columns(out, PLAYER_LOG_COLS, "canonical player log")
        log.info("parquet player logs: %d rows, seasons %s", len(out), found)
        return out.sort_values(["PLAYER_ID", "GAME_DATE"]).reset_index(drop=True)

    # ------------------------------------------------------------------
    def load_team_game_logs(self) -> pd.DataFrame:
        raw, found = self._read_seasons("team_logs")
        if raw.empty:
            raise FileNotFoundError(
                f"no team_logs_*.parquet for seasons {self.seasons} in {self.data_dir}"
            )
        require_columns(raw, ("TEAM_ID", "GAME_ID", "GAME_DATE", "PTS", "MATCHUP"), "team log")

        # FG3A is read IF PRESENT and never required. the spike's own nba_api
        # exports predate the P2 matchup family; a directory without it produces a
        # null ``opp_fg3a_allowed_per100`` and a warning from
        # :mod:`fnba_ml.matchup`, rather than failing an offline run over one column
        # of a candidate feature set.
        optional = [c for c in TEAM_LOG_OPTIONAL_COLS if c in raw.columns]
        keep = ["TEAM_ID", "TEAM_ABBREVIATION", "GAME_ID", "SEASON", "GAME_DATE",
                "MATCHUP", "PTS", "MIN", "FGA", "FTA", "TOV", *optional]
        out = raw[keep].copy()
        out[COMPETITION_COL] = _competition(raw)
        out = normalise_ids(normalise_dates(out))
        for col in ("PTS", "MIN", "FGA", "FTA", "TOV", *optional):
            out[col] = pd.to_numeric(out[col], errors="coerce").astype(float)
        require_columns(out, TEAM_LOG_COLS, "canonical team log")
        log.info("parquet team logs: %d rows, seasons %s", len(out), found)
        return out.reset_index(drop=True)

    # ------------------------------------------------------------------
    def load_player_positions(self) -> pd.DataFrame | None:
        """positions from an optional ``player_positions.parquet``, else None.

        THIS FORMAT HAS NO POSITION SOURCE. the nba_api game-log exports the
        parquet layout mirrors carry no position column at all, and there is
        nothing in a game log to derive one from. a directory that supplies the
        file (the test fixtures do, synthetically) exercises the positional half
        of the teammate features; the spike's own data dir does not, and the
        consequence is documented rather than papered over: ``POS_GROUP`` is
        null, so ``vacated_minutes_pos`` and ``depth_rank_available_pos`` are
        null for every row and LightGBM treats them as missing.

        no season suffix - a position is reference data, not a per-game fact,
        so it is one file rather than one per season.
        """
        path = self.data_dir / "player_positions.parquet"
        if not path.exists():
            log.warning(
                "no player_positions.parquet in %s - POS_GROUP will be null and the "
                "positional teammate features (vacated_minutes_pos, "
                "depth_rank_available_pos) will be null for every row",
                self.data_dir,
            )
            return None
        raw = pd.read_parquet(path)
        require_columns(raw, POSITION_COLS, "player positions")
        out = normalise_ids(raw[list(POSITION_COLS)].copy())
        log.info("parquet player positions: %d rows", len(out))
        return out.drop_duplicates("PLAYER_ID").reset_index(drop=True)

    # ------------------------------------------------------------------
    def load_schedule(self) -> pd.DataFrame:
        """reconstruct the nba_schedule shape from the team logs.

        a team log row exists for both sides of every played game and MATCHUP
        contains '@' for the away side. a handful of neutral-site games (NBA
        Cup, international) mark BOTH sides as away; those are paired by team id
        so the team-game is not lost, at the cost of an arbitrary IS_HOME. five
        such games exist across 2023-24 and 2024-25.
        """
        tm = self.load_team_game_logs().copy()
        tm["_IS_HOME"] = ~tm["MATCHUP"].astype(str).str.contains("@")

        sizes = tm.groupby("GAME_ID").size()
        incomplete = sizes[sizes != 2].index
        if len(incomplete):
            log.warning("dropping %d games without exactly two team-log rows", len(incomplete))
            tm = tm[~tm["GAME_ID"].isin(incomplete)]

        ambiguous = tm.groupby("GAME_ID")["_IS_HOME"].sum()
        n_ambiguous = int((ambiguous != 1).sum())
        if n_ambiguous:
            log.warning(
                "%d games have no unambiguous home side (neutral site); home/away "
                "assigned by team id so the team-games are kept",
                n_ambiguous,
            )

        ordered = tm.sort_values(
            ["GAME_ID", "_IS_HOME", "TEAM_ID"], ascending=[True, False, True]
        )
        side = ordered.groupby("GAME_ID").cumcount()
        home = ordered[side == 0][
            ["GAME_ID", "SEASON", "GAME_DATE", "TEAM_ID", COMPETITION_COL]
        ].rename(
            columns={"TEAM_ID": "HOME_TEAM_ID"}
        )
        away = ordered[side == 1][["GAME_ID", "TEAM_ID"]].rename(
            columns={"TEAM_ID": "AWAY_TEAM_ID"}
        )

        sched = home.merge(away, on="GAME_ID", how="inner")
        sched["SEASON_TYPE"] = sched[COMPETITION_COL].map(SEASON_TYPE_BY_COMPETITION)
        sched["SCHEDULED_AT"] = sched["GAME_DATE"]
        sched["GAME_STATUS"] = "Final"
        sched = normalise_ids(sched)
        require_columns(sched, SCHEDULE_COLS, "canonical schedule")
        return sched.reset_index(drop=True)

    # ------------------------------------------------------------------
    def load_box_details(self) -> pd.DataFrame | None:
        """an optional ``box_details_<season>.parquet`` per season, else None.

        the nba_api exports carry no starter flag or rebound split, so only a
        directory that supplies the file (the test fixtures do) has the v6 family.
        """
        raw, found = self._read_seasons("box_details")
        if raw.empty:
            log.warning("no box_details_*.parquet in %s; v6 columns skipped",
                        self.data_dir)
            return None
        require_columns(raw, BOX_DETAIL_COLS, "box details")
        out = raw[list(BOX_DETAIL_COLS)].copy()
        out[COMPETITION_COL] = _competition(raw)
        for col in ("MIN", "PLUS_MINUS", "OREB", "DREB", "PF"):
            out[col] = pd.to_numeric(out[col], errors="coerce").astype(float)
        out["STARTED"] = out["STARTED"].astype("boolean")
        out = normalise_ids(normalise_dates(out))
        log.info("parquet box details: %d rows, seasons %s", len(out), found)
        return out.reset_index(drop=True)

    # ------------------------------------------------------------------
    def load_player_game_status(self) -> pd.DataFrame | None:
        """the real roster/inactive table, if the fixtures happen to carry one.

        returns None for the spike's own data dir, which has no such file. the
        caller must then use the labeled fallback approximation.
        """
        raw, found = self._read_seasons("player_game_status")
        if raw.empty:
            log.warning(
                "no player_game_status_*.parquet in %s - the universe will be "
                "built from the BIASED game-log-presence approximation",
                self.data_dir,
            )
            return None
        require_columns(raw, ("PLAYER_ID", "GAME_ID", "TEAM_ID", "ROSTERED"), "status")
        for col in STATUS_COLS:
            if col not in raw.columns:
                raw[col] = pd.NA
        out = normalise_ids(raw[list(STATUS_COLS)].copy())
        log.info("parquet player_game_status: %d rows, seasons %s", len(out), found)
        return out.reset_index(drop=True)
