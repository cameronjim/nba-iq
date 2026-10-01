from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

FIXTURE_DIR = Path(__file__).resolve().parent

SEASON_STARTS = {"2023-24": "2023-11-01", "2024-25": "2024-11-01"}
N_TEAMS = 8
N_DATES = 20
GAME_INTERVAL_DAYS = 3  # wide enough that a 13-game absence exceeds +/-15 days
ROSTER_SIZE = 12
RETURNING_PER_TEAM = 10

SLOT_PROFILE = [
    (33.0, 0.94), (31.0, 0.92),
    (26.0, 0.88), (24.0, 0.86), (22.0, 0.85),
    (16.0, 0.78), (14.0, 0.74), (12.0, 0.72),
    (8.0, 0.48), (7.0, 0.44), (6.0, 0.40), (5.0, 0.36),
]

LONG_ABSENCE_SLOT = 5
LONG_ABSENCE_RANGE = range(5, 18)
LONG_ABSENCE_SEASON = "2024-25"

ROOKIE_DEBUT_INDEX = 3

# the usage formula divides by 5 to recover game length
TEAM_MINUTES = 240.0

MINUTES_REDISTRIBUTION = 0.7
POSITIONAL_REDISTRIBUTION_SHARE = 0.5

ABSENCE_PERSISTENCE = 0.5

# play-in/playoff appearances appended after each regular season. no rng: the
# regular-season numbers stay exactly what seed 17 draws.
POSTSEASON_GAME_OFFSETS_DAYS = (4, 6)
POSTSEASON_PLAYING_SLOTS = range(5)
POSTSEASON_MINUTES = 38.0
POSTSEASON_PTS_PER_MIN = 0.9

SLOT_POSITIONS: dict[int, str] = {
    0: "PG,SG", 1: "SG,SF", 2: "SF,PF", 3: "PF,C", 4: "C",
    5: "PG,SG", 6: "SF,PF", 7: "C", 8: "SG,SF", 9: "PF,C",
}


def _team_ids() -> list[str]:
    return [str(1610612700 + i) for i in range(N_TEAMS)]


def _team_abbr(team_id: str) -> str:
    return f"T{int(team_id) - 1610612700:02d}"


def _dates(season: str) -> list[pd.Timestamp]:
    start = pd.Timestamp(SEASON_STARTS[season])
    return [start + pd.Timedelta(days=GAME_INTERVAL_DAYS * i) for i in range(N_DATES)]


def _pairings(date_index: int, teams: list[str]) -> list[tuple[str, str]]:
    """rotate the team list so opponents vary across dates. returns (home, away)."""
    rotated = teams[date_index % len(teams):] + teams[: date_index % len(teams)]
    pairs = []
    for i in range(0, len(rotated), 2):
        a, b = rotated[i], rotated[i + 1]
        pairs.append((a, b) if (date_index + i) % 2 == 0 else (b, a))
    return pairs


def _rosters() -> dict[tuple[str, str], list[str]]:
    """(season, team) -> ordered player ids. slot order is the profile order."""
    rosters: dict[tuple[str, str], list[str]] = {}
    next_player = 2000
    for team in _team_ids():
        first = [str(next_player + i) for i in range(ROSTER_SIZE)]
        next_player += ROSTER_SIZE
        rosters[("2023-24", team)] = first

        newcomers = [str(next_player + i) for i in range(ROSTER_SIZE - RETURNING_PER_TEAM)]
        next_player += ROSTER_SIZE - RETURNING_PER_TEAM
        rosters[("2024-25", team)] = first[:RETURNING_PER_TEAM] + newcomers
    return rosters


def _player_skill(rng: np.random.Generator, player_ids: list[str]) -> dict[str, dict[str, float]]:
    return {
        pid: {
            "ppm": float(rng.uniform(0.35, 0.62)),
            "apm": float(rng.uniform(0.04, 0.18)),
            "rpm": float(rng.uniform(0.10, 0.28)),
        }
        for pid in player_ids
    }


def _slot_pos_group(slot: int) -> str | None:
    position = SLOT_POSITIONS.get(slot)
    if position is None:
        return None
    primary = position.split(",")[0]
    return {"PG": "G", "SG": "G", "SF": "F", "PF": "F", "C": "C"}[primary]


def _minutes_boost(availability: list[tuple[bool, bool, bool]]) -> list[float]:
    """per-slot minutes uplift from the minutes the absent slots left behind."""
    played = [flags[0] for flags in availability]
    n_played = max(sum(played), 1)

    team_pool = 0.0
    pos_pool: dict[str, float] = {}
    for slot, is_playing in enumerate(played):
        if is_playing:
            continue
        minutes = SLOT_PROFILE[slot][0]
        bucket = _slot_pos_group(slot)
        if bucket is None:
            team_pool += minutes
            continue
        team_pool += (1.0 - POSITIONAL_REDISTRIBUTION_SHARE) * minutes
        pos_pool[bucket] = pos_pool.get(bucket, 0.0) + (
            POSITIONAL_REDISTRIBUTION_SHARE * minutes
        )

    n_played_by_pos: dict[str, int] = {}
    for slot, is_playing in enumerate(played):
        bucket = _slot_pos_group(slot)
        if is_playing and bucket is not None:
            n_played_by_pos[bucket] = n_played_by_pos.get(bucket, 0) + 1

    boost = []
    for slot in range(len(availability)):
        bucket = _slot_pos_group(slot)
        share = MINUTES_REDISTRIBUTION * team_pool / n_played
        if bucket is not None and n_played_by_pos.get(bucket):
            share += (
                MINUTES_REDISTRIBUTION * pos_pool.get(bucket, 0.0)
                / n_played_by_pos[bucket]
            )
        boost.append(share)
    return boost


def _build(seed: int) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    teams = _team_ids()
    rosters = _rosters()
    all_players = sorted({p for roster in rosters.values() for p in roster})
    skill = _player_skill(rng, all_players)
    names = {pid: f"Player {pid}" for pid in all_players}

    positions = {
        player_id: SLOT_POSITIONS[slot]
        for roster in rosters.values()
        for slot, player_id in enumerate(roster)
        if slot in SLOT_POSITIONS
    }

    status_rows: list[dict] = []
    log_rows: list[dict] = []
    team_rows: list[dict] = []
    game_counter = 1
    missed_last: dict[str, bool] = {}

    for season in SEASON_STARTS:
        missed_last.clear()
        for d_idx, game_date in enumerate(_dates(season)):
            for home, away in _pairings(d_idx, teams):
                game_id = f"002{int(season[:4]) % 100:02d}{game_counter:05d}"
                game_counter += 1
                team_pts: dict[str, int] = {}
                team_totals: dict[str, dict[str, float]] = {}

                for team in (home, away):
                    roster = rosters[(season, team)]
                    pts_total = 0
                    totals = {"FGA": 0.0, "FTA": 0.0, "TOV": 0.0, "FG3A": 0.0}

                    # availability has to be settled for the whole roster before
                    # anyone's minutes can be drawn
                    availability: list[tuple[bool, bool, bool]] = []
                    for slot, roster_player in enumerate(roster):
                        _, p_play = SLOT_PROFILE[slot]
                        if missed_last.get(roster_player):
                            p_play *= ABSENCE_PERSISTENCE
                        forced_out = (
                            season == LONG_ABSENCE_SEASON
                            and slot == LONG_ABSENCE_SLOT
                            and d_idx in LONG_ABSENCE_RANGE
                        )
                        rookie_not_yet = (
                            season == LONG_ABSENCE_SEASON
                            and slot >= RETURNING_PER_TEAM
                            and d_idx < ROOKIE_DEBUT_INDEX
                        )
                        played = (
                            not forced_out
                            and not rookie_not_yet
                            and bool(rng.random() < p_play)
                        )
                        availability.append((played, forced_out, rookie_not_yet))
                        missed_last[roster_player] = not played

                    boost = _minutes_boost(availability)

                    for slot, player_id in enumerate(roster):
                        minutes_mean, p_play = SLOT_PROFILE[slot]
                        played, forced_out, rookie_not_yet = availability[slot]

                        row = {
                            "PLAYER_ID": player_id,
                            "GAME_ID": game_id,
                            "TEAM_ID": team,
                            "ROSTERED": True,
                            "LISTED_INACTIVE": bool(forced_out or (not played and slot < 8)),
                            "STARTED": bool(played and slot < 5),
                            "PLAYED": played,
                            "DNP_REASON": (
                                "Injury/Illness" if forced_out
                                else (None if played else "DNP - Coach's Decision")
                            ),
                            "MIN": None,
                        }

                        if played:
                            minutes = float(np.clip(
                                rng.normal(minutes_mean + boost[slot], 4.0), 1.0, 46.0
                            ))
                            s = skill[player_id]
                            pts = max(0.0, round(minutes * s["ppm"] + rng.normal(0, 3.0), 1))
                            ast = max(0.0, round(minutes * s["apm"] + rng.normal(0, 0.8), 1))
                            reb = max(0.0, round(minutes * s["rpm"] + rng.normal(0, 1.2), 1))
                            fga = max(0.0, round(pts / 1.15 + rng.normal(0, 1.5), 1))
                            ftm = max(0.0, round(pts / 7.0 + rng.normal(0, 0.6), 1))
                            # attempts >= makes, as the ingest validation gate requires
                            fta = round(max(ftm, ftm / 0.78 + rng.normal(0, 0.2)), 1)
                            tov = max(0.0, round(minutes * 0.05 + rng.normal(0, 0.5), 1))
                            # FGM is DERIVED, not drawn
                            fg3m = max(0.0, round(pts / 9.0 + rng.normal(0, 0.6), 1))
                            fgm = round(max(0.0, (pts - ftm - fg3m) / 2.0), 1)
                            fgm = round(min(max(fgm, fg3m), fga), 1)
                            fg3a = round(min(max(fg3m, fg3m / 0.36), fga), 1)
                            row["MIN"] = round(minutes, 1)
                            pts_total += int(round(pts))
                            totals["FGA"] += fga
                            totals["FTA"] += fta
                            totals["TOV"] += tov
                            totals["FG3A"] += fg3a
                            log_rows.append({
                                "PLAYER_ID": player_id,
                                "PLAYER_NAME": names[player_id],
                                "TEAM_ID": team,
                                "TEAM_ABBREVIATION": _team_abbr(team),
                                "GAME_ID": game_id,
                                "GAME_DATE": game_date,
                                "SEASON_KEY": season,
                                "MIN": round(minutes, 1),
                                "PTS": pts,
                                "AST": ast,
                                "REB": reb,
                                "FGA": fga,
                                "FTA": fta,
                                "FG3M": fg3m,
                                "FG3A": fg3a,
                                "FGM": fgm,
                                "FTM": ftm,
                                "TOV": tov,
                                "STL": max(0.0, round(minutes * 0.03 + rng.normal(0, 0.4), 1)),
                                "BLK": max(0.0, round(minutes * 0.02 + rng.normal(0, 0.3), 1)),
                                "PLUS_MINUS": round(float(rng.normal(0, 8)), 1),
                            })
                        status_rows.append(row)
                    team_pts[team] = pts_total
                    team_totals[team] = totals

                for team, opponent in ((home, away), (away, home)):
                    at_home = team == home
                    team_rows.append({
                        "TEAM_ID": team,
                        "TEAM_ABBREVIATION": _team_abbr(team),
                        "GAME_ID": game_id,
                        "GAME_DATE": game_date,
                        "SEASON_KEY": season,
                        "MATCHUP": (
                            f"{_team_abbr(team)} vs. {_team_abbr(opponent)}" if at_home
                            else f"{_team_abbr(team)} @ {_team_abbr(opponent)}"
                        ),
                        "WL": "W" if team_pts[team] >= team_pts[opponent] else "L",
                        "PTS": team_pts[team],
                        "MIN": TEAM_MINUTES,
                        "FGA": round(team_totals[team]["FGA"], 1),
                        "FTA": round(team_totals[team]["FTA"], 1),
                        "TOV": round(team_totals[team]["TOV"], 1),
                        "FG3A": round(team_totals[team]["FG3A"], 1),
                    })

    return {
        "player_logs": pd.DataFrame(log_rows),
        "team_logs": pd.DataFrame(team_rows),
        "player_game_status": pd.DataFrame(status_rows),
        "player_positions": pd.DataFrame(
            [{"PLAYER_ID": pid, "POSITION": pos} for pid, pos in sorted(positions.items())]
        ),
    }


def _postseason_line(minutes: float) -> dict[str, float]:
    pts = round(minutes * POSTSEASON_PTS_PER_MIN, 1)
    return {
        "MIN": minutes, "PTS": pts, "AST": round(minutes * 0.2, 1),
        "REB": round(minutes * 0.25, 1), "FGA": 24.0, "FTA": 8.0, "FG3M": 3.0,
        "FG3A": 8.0, "FGM": 13.0, "FTM": 6.0, "TOV": 2.0, "STL": 1.0, "BLK": 1.0,
        "PLUS_MINUS": 5.0,
    }


def _postseason(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """two playoff games per season between the first two teams, appended last."""
    teams = _team_ids()
    home, away = teams[0], teams[1]
    rosters = _rosters()
    logs, team_rows, status = [], [], []
    for season in SEASON_STARTS:
        last = _dates(season)[-1]
        for n, offset in enumerate(POSTSEASON_GAME_OFFSETS_DAYS, start=1):
            game_id = f"004{int(season[:4]) % 100:02d}{n:05d}"
            game_date = last + pd.Timedelta(days=offset)
            line = _postseason_line(POSTSEASON_MINUTES)
            for team, opponent in ((home, away), (away, home)):
                for slot, player_id in enumerate(rosters[(season, team)]):
                    played = slot in POSTSEASON_PLAYING_SLOTS
                    status.append({
                        "PLAYER_ID": player_id, "GAME_ID": game_id, "TEAM_ID": team,
                        "ROSTERED": True, "LISTED_INACTIVE": False,
                        "STARTED": played, "PLAYED": played,
                        "DNP_REASON": None if played else "DNP - Coach's Decision",
                        "MIN": line["MIN"] if played else None,
                        "SEASON_KEY": season,
                    })
                    if played:
                        logs.append({
                            "PLAYER_ID": player_id, "PLAYER_NAME": f"Player {player_id}",
                            "TEAM_ID": team, "TEAM_ABBREVIATION": _team_abbr(team),
                            "GAME_ID": game_id, "GAME_DATE": game_date,
                            "SEASON_KEY": season, **line,
                        })
                n_played = len(POSTSEASON_PLAYING_SLOTS)
                team_rows.append({
                    "TEAM_ID": team, "TEAM_ABBREVIATION": _team_abbr(team),
                    "GAME_ID": game_id, "GAME_DATE": game_date, "SEASON_KEY": season,
                    "MATCHUP": (
                        f"{_team_abbr(team)} vs. {_team_abbr(opponent)}" if team == home
                        else f"{_team_abbr(team)} @ {_team_abbr(opponent)}"
                    ),
                    "WL": "W" if team == home else "L",
                    "PTS": int(round(line["PTS"] * n_played)) + (1 if team == home else 0),
                    "MIN": TEAM_MINUTES,
                    "FGA": line["FGA"] * n_played, "FTA": line["FTA"] * n_played,
                    "TOV": line["TOV"] * n_played, "FG3A": line["FG3A"] * n_played,
                })

    out = dict(frames)
    for name, extra in (("player_logs", logs), ("team_logs", team_rows),
                        ("player_game_status", status)):
        regular = frames[name].assign(COMPETITION="regular")
        out[name] = pd.concat(
            [regular, pd.DataFrame(extra).assign(COMPETITION="playoffs")],
            ignore_index=True,
        )
    return out


def generate(
    out_dir: Path | None = None, seed: int = 17, with_postseason: bool = True
) -> Path:
    """write the fixture parquet files and return the directory."""
    out_dir = Path(out_dir or FIXTURE_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = _build(seed)
    if with_postseason:
        frames = _postseason(frames)

    status_games = frames["team_logs"][["GAME_ID", "SEASON_KEY"]].drop_duplicates()
    frames["player_game_status"] = frames["player_game_status"].drop(
        columns=["SEASON_KEY"], errors="ignore"
    ).merge(status_games, on="GAME_ID", how="left")

    # positions are reference data: one file, no season split
    frames.pop("player_positions").to_parquet(
        out_dir / "player_positions.parquet", index=False
    )

    for name, frame in frames.items():
        for season in SEASON_STARTS:
            part = frame[frame["SEASON_KEY"] == season].drop(columns=["SEASON_KEY"])
            path = out_dir / f"{name}_{season.replace('-', '_')}.parquet"
            part.reset_index(drop=True).to_parquet(path, index=False)
    return out_dir


if __name__ == "__main__":
    print(generate())
