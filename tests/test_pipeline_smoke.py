"""Offline end-to-end run of the whole pipeline on synthetic Statcast data.

The real ingestion path can only be exercised on a machine with a network
route to Savant, which in practice means a CI runner and a 30-60 minute
build. That made every crash cost a full build to find and a full build to
confirm fixed, one bug at a time.

This runs the entire scoring path -- both pillar sets, leverage, FIGHT,
aggregation, export -- against a generated frame that carries the same
columns, dtypes and missingness as the real feed, in about a second.

The missingness is the point. Statcast ships nullable dtypes and real nulls:
`stand` is missing on some rows, `plate_x` on pitches with no tracking,
`events` on every pitch that does not end a plate appearance. Those nulls
are what produced `TypeError: float() argument must be ... not 'NAType'`
deep inside pitcher GRIT, and they are invisible to any test built from
tidy synthetic data.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xdawg import ingest  # noqa: E402
from xdawg.config import TEAMS  # noqa: E402

RNG = np.random.default_rng(20260818)

TEAM_LIST = sorted(TEAMS)
N_PITCHES = 24_000
N_BATTERS = 60
N_PITCHERS = 40


def _synthetic_statcast() -> pd.DataFrame:
    """A pitch frame shaped like the real feed, nulls and all."""
    n = N_PITCHES
    batters = RNG.integers(600_000, 600_000 + N_BATTERS, n)
    pitchers = RNG.integers(700_000, 700_000 + N_PITCHERS, n)
    games = RNG.integers(1, 220, n)

    # at_bat_number restarts every game, exactly like the real feed.
    ab = RNG.integers(1, 78, n)

    home = RNG.choice(TEAM_LIST, n)
    away = RNG.choice(TEAM_LIST, n)

    df = pd.DataFrame({
        "game_pk": games,
        "game_date": pd.to_datetime("2026-03-27")
        + pd.to_timedelta(RNG.integers(0, 150, n), unit="D"),
        "at_bat_number": ab,
        "pitch_number": RNG.integers(1, 9, n),
        "pitch_type": RNG.choice(["FF", "SL", "CH", "CU", "SI", None], n),
        "batter": batters,
        "pitcher": pitchers,
        "player_name": "Doe, John",
        "events": RNG.choice(
            ["strikeout", "single", "home_run", "field_out", "walk", None],
            n, p=[0.08, 0.06, 0.02, 0.14, 0.05, 0.65],
        ),
        "description": RNG.choice(
            ["ball", "called_strike", "swinging_strike", "foul",
             "hit_into_play", "foul_tip"], n,
        ),
        "type": RNG.choice(["B", "S", "X"], n),
        "zone": RNG.choice([1, 5, 9, 11, 13, 14, np.nan], n),
        "plate_x": np.where(RNG.random(n) < 0.03, np.nan, RNG.normal(0, 0.9, n)),
        "plate_z": np.where(RNG.random(n) < 0.03, np.nan, RNG.normal(2.4, 0.8, n)),
        "balls": RNG.integers(0, 4, n),
        "strikes": RNG.integers(0, 3, n),
        # Real nulls here: this is what broke pitcher GRIT.
        "stand": RNG.choice(["L", "R", None], n, p=[0.42, 0.55, 0.03]),
        "p_throws": RNG.choice(["L", "R", None], n, p=[0.28, 0.69, 0.03]),
        "launch_speed": np.where(RNG.random(n) < 0.7, np.nan, RNG.normal(88, 14, n)),
        "launch_angle": np.where(RNG.random(n) < 0.7, np.nan, RNG.normal(12, 25, n)),
        "release_speed": np.where(RNG.random(n) < 0.02, np.nan, RNG.normal(93, 5, n)),
        "pfx_x": RNG.normal(0, 0.8, n),
        "pfx_z": RNG.normal(1.0, 0.7, n),
        "release_spin_rate": np.where(
            RNG.random(n) < 0.05, np.nan, RNG.normal(2300, 250, n)
        ),
        "release_extension": RNG.normal(6.3, 0.4, n),
        "delta_home_win_exp": RNG.normal(0, 0.03, n),
        "delta_run_exp": RNG.normal(0, 0.28, n),
        "inning": RNG.integers(1, 12, n),
        "inning_topbot": RNG.choice(["Top", "Bot"], n),
        "outs_when_up": RNG.integers(0, 3, n),
        "on_1b": np.where(RNG.random(n) < 0.68, np.nan, RNG.integers(1, 9e5, n)),
        "on_2b": np.where(RNG.random(n) < 0.80, np.nan, RNG.integers(1, 9e5, n)),
        "on_3b": np.where(RNG.random(n) < 0.90, np.nan, RNG.integers(1, 9e5, n)),
        "bat_score": RNG.integers(0, 9, n),
        "fld_score": RNG.integers(0, 9, n),
        "home_team": home,
        "away_team": np.where(away == home, "NYY", away),
    })

    # Fielder attribution, mostly null (only balls actually fielded).
    df["hit_location"] = np.where(
        RNG.random(n) < 0.78, np.nan, RNG.integers(2, 10, n)
    )
    # Fielders are the same people as batters; drawing from a separate id
    # pool would make HUNT merge onto nothing and read as a dead pillar.
    for pos in range(2, 10):
        df[f"fielder_{pos}"] = RNG.integers(600_000, 600_000 + N_BATTERS, n)

    # Statcast really does hand back nullable extension dtypes. Reproduce
    # that, because `.astype(float)` on one containing pd.NA is precisely
    # the failure this test exists to catch.
    df["stand"] = df["stand"].astype("string")
    df["p_throws"] = df["p_throws"].astype("string")
    df["events"] = df["events"].astype("string")
    df["pitch_type"] = df["pitch_type"].astype("string")
    df["zone"] = df["zone"].astype("Float64")
    df["plate_x"] = df["plate_x"].astype("Float64")
    df["plate_z"] = df["plate_z"].astype("Float64")
    df["launch_speed"] = df["launch_speed"].astype("Float64")
    df["launch_angle"] = df["launch_angle"].astype("Float64")
    df["release_speed"] = df["release_speed"].astype("Float64")
    df["release_spin_rate"] = df["release_spin_rate"].astype("Float64")
    df["hit_location"] = df["hit_location"].astype("Float64")
    return df


def _synthetic_sprint() -> pd.DataFrame:
    ids = np.arange(600_000, 600_000 + N_BATTERS)
    return pd.DataFrame({
        "last_name, first_name": "Doe, John",
        "batter": ids,
        "team": RNG.choice(TEAM_LIST, len(ids)),
        "hp_to_1b": RNG.normal(4.35, 0.18, len(ids)),
        "sprint_speed": RNG.normal(27.2, 1.4, len(ids)),
    })


def _synthetic_oaa() -> pd.DataFrame:
    """Matches the real all-position leaderboard: no opportunity column."""
    ids = np.arange(600_000, 600_000 + N_BATTERS)
    return pd.DataFrame({
        "last_name, first_name": "Doe, John",
        "player_id": ids,
        "display_team_name": "Nationals",
        "year": np.nan,
        "primary_pos_formatted": RNG.choice(["SS", "CF", "C", "1B", "RF"], len(ids)),
        "fielding_runs_prevented": RNG.integers(-14, 20, len(ids)),
        "outs_above_average": RNG.integers(-18, 24, len(ids)),
        "actual_success_rate_formatted": [f"{v}%" for v in RNG.integers(60, 95, len(ids))],
        "adj_estimated_success_rate_formatted": [f"{v}%" for v in RNG.integers(60, 95, len(ids))],
        "diff_success_rate_formatted": [f"{v}%" for v in RNG.integers(-6, 7, len(ids))],
    })


def _synthetic_standings() -> pd.DataFrame:
    return pd.DataFrame({
        "team": TEAM_LIST,
        "rs": RNG.integers(520, 830, len(TEAM_LIST)).astype(float),
        "ra": RNG.integers(520, 830, len(TEAM_LIST)).astype(float),
    })


@pytest.fixture
def offline(monkeypatch):
    """Swap every network loader for a synthetic frame."""
    monkeypatch.setattr(ingest, "load_statcast",
                        lambda *a, **k: _synthetic_statcast())
    monkeypatch.setattr(ingest, "load_sprint_speed",
                        lambda *a, **k: _synthetic_sprint())
    monkeypatch.setattr(ingest, "load_oaa", lambda *a, **k: _synthetic_oaa())
    monkeypatch.setattr(ingest, "load_catch_probability", lambda *a, **k: None)
    monkeypatch.setattr(ingest, "load_standings",
                        lambda *a, **k: _synthetic_standings())
    monkeypatch.setattr(ingest, "player_names",
                        lambda p: {int(i): f"Player {i}" for i in
                                   set(p["batter"]) | set(p["pitcher"])})


def test_full_pipeline_runs_on_nullable_statcast(offline):
    """The whole scoring path must survive real-world nulls end to end.

    Deliberately asserts nothing about the numbers -- this is a crash test.
    Every bug it has caught so far was a TypeError or a KeyError, not a
    wrong value.
    """
    from xdawg.pipeline import run

    hitters, pitchers = run(season=2026, min_pa=1, min_bf=1)

    assert not hitters.empty, "no hitters scored"
    assert not pitchers.empty, "no pitchers scored"
    for frame, label in ((hitters, "hitters"), (pitchers, "pitchers")):
        assert "xDAWG" in frame.columns, f"{label} missing xDAWG"
        assert frame["xDAWG"].notna().any(), f"{label} xDAWG all null"


def test_payload_builds_and_pillars_are_alive(offline):
    """A built payload must not contain a silently dead pillar."""
    from xdawg.export import build_payload
    from xdawg.pipeline import run

    hitters, pitchers = run(season=2026, min_pa=1, min_bf=1)
    payload = build_payload(hitters, pitchers, season=2026, synthetic=False)

    assert payload["players"], "payload has no players"
    for role in ("hitter", "pitcher"):
        rows = [p for p in payload["players"] if p["role"] == role]
        assert rows, f"no {role}s in payload"
        for pillar in ("BITE", "GRIT", "HUNT", "FIGHT"):
            zs = [
                p["pillars"][pillar]["z"] for p in rows
                if p["pillars"][pillar]["z"] is not None
            ]
            assert zs, f"{role} {pillar} has no values at all"
            assert np.std(zs) > 1e-9, (
                f"{role} {pillar} has zero spread -- every component "
                "dropped out, which verify_build would hard-fail"
            )
