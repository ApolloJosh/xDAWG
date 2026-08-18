"""
Data ingestion with an aggressive local cache.

A full season of Statcast is roughly 700k pitches and takes a long while to
pull. We cache to Parquet on first fetch and never ask twice. Every optional
leaderboard degrades gracefully -- if Savant is unreachable or a schema has
drifted, the affected components drop out and their pillar weights
renormalize rather than crashing the run.
"""

from __future__ import annotations

import datetime as _dt
import os
import warnings
from pathlib import Path

import pandas as pd

CACHE = Path(os.environ.get("XDAWG_CACHE", Path.home() / ".xdawg_cache"))
CACHE.mkdir(parents=True, exist_ok=True)

STATCAST_COLS = [
    "game_pk", "game_date", "at_bat_number", "pitch_number", "pitch_type",
    "batter", "pitcher", "player_name", "events", "description", "type", "zone",
    "plate_x", "plate_z", "balls", "strikes", "stand", "p_throws",
    "launch_speed", "launch_angle", "release_speed", "pfx_x", "pfx_z",
    "release_spin_rate", "release_extension", "delta_home_win_exp",
    "delta_run_exp", "inning", "inning_topbot", "outs_when_up",
    "on_1b", "on_2b", "on_3b", "bat_score", "fld_score",
    "home_team", "away_team",
]


def _cache_path(name: str) -> Path:
    return CACHE / f"{name}.parquet"


def season_dates(season: int) -> tuple[str, str]:
    """Regular season window, clipped to today for an in-progress season."""
    start, end = f"{season}-03-15", f"{season}-10-05"
    today = _dt.date.today().isoformat()
    return start, min(end, today)


def load_statcast(season: int, refresh: bool = False) -> pd.DataFrame:
    """Pull a full season of pitch-level Statcast, cached to Parquet.

    The first run for a season is slow -- pybaseball chunks the request by
    date and Savant rate-limits. Subsequent runs read from cache instantly.
    """
    path = _cache_path(f"statcast_{season}")
    if path.exists() and not refresh:
        return pd.read_parquet(path)

    from pybaseball import statcast  # imported lazily: heavy, and optional

    start, end = season_dates(season)
    df = statcast(start_dt=start, end_dt=end, verbose=True)

    keep = [c for c in STATCAST_COLS if c in df.columns]
    missing = set(STATCAST_COLS) - set(keep)
    if missing:
        warnings.warn(f"Statcast schema drift, missing columns: {sorted(missing)}")

    df = df[keep].copy()
    df.to_parquet(path, index=False)
    return df


def load_sprint_speed(season: int) -> pd.DataFrame | None:
    """Sprint speed and home-to-first, for the GRIT hustle ratio."""
    path = _cache_path(f"sprint_{season}")
    if path.exists():
        return pd.read_parquet(path)
    try:
        from pybaseball import statcast_sprint_speed

        df = statcast_sprint_speed(season, min_opp=5)
        df = df.rename(columns={"player_id": "batter"})
        df.to_parquet(path, index=False)
        return df
    except Exception as e:  # noqa: BLE001 - any failure means "degrade"
        warnings.warn(f"sprint speed unavailable ({e}); GRIT hustle term dropped")
        return None


def load_catch_probability(season: int) -> pd.DataFrame | None:
    """Outfield catch probability, for HUNT star catches."""
    path = _cache_path(f"catch_{season}")
    if path.exists():
        return pd.read_parquet(path)
    try:
        import io
        import requests

        url = (
            "https://baseballsavant.mlb.com/leaderboard/catch_probability"
            f"?type=player&min=10&year={season}&total=5&csv=true"
        )
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        df.to_parquet(path, index=False)
        return df
    except Exception as e:  # noqa: BLE001
        warnings.warn(f"catch probability unavailable ({e}); HUNT star term dropped")
        return None


def load_standings(season: int) -> pd.DataFrame | None:
    """Team runs scored / allowed, for the FIGHT opponent-quality term."""
    path = _cache_path(f"standings_{season}")
    if path.exists():
        return pd.read_parquet(path)
    try:
        from pybaseball import team_batting, team_pitching

        tb = team_batting(season)[["Team", "R"]].rename(columns={"R": "rs"})
        tp = team_pitching(season)[["Team", "R"]].rename(columns={"R": "ra"})
        df = tb.merge(tp, on="Team").rename(columns={"Team": "team"})
        df.to_parquet(path, index=False)
        return df
    except Exception as e:  # noqa: BLE001
        warnings.warn(f"standings unavailable ({e}); FIGHT falls back to flat weights")
        return None


def player_names(pitches: pd.DataFrame) -> dict[int, str]:
    """Statcast's `player_name` is the PITCHER, so batters need a lookup."""
    names: dict[int, str] = {}
    if "player_name" in pitches.columns:
        for pid, nm in pitches.dropna(subset=["player_name"]).groupby("pitcher")[
            "player_name"
        ].first().items():
            names[int(pid)] = str(nm)
    try:
        from pybaseball import playerid_reverse_lookup

        need = sorted(set(pitches["batter"].dropna().astype(int)) - set(names))
        if need:
            lk = playerid_reverse_lookup(need, key_type="mlbam")
            for _, r in lk.iterrows():
                names[int(r["key_mlbam"])] = (
                    f"{r['name_first']} {r['name_last']}".title()
                )
    except Exception as e:  # noqa: BLE001
        warnings.warn(f"name lookup unavailable ({e}); showing raw MLBAM ids")
    return names
