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
             "hit_into_play", "foul_tip", "hit_by_pitch"], n,
            p=[0.32, 0.16, 0.14, 0.19, 0.17, 0.01, 0.01],
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

    df = _assign_pitching_roles(df)
    df = _add_runner_continuity(df)

    # Fielder attribution, mostly null (only balls actually fielded).
    df["hit_location"] = np.where(
        RNG.random(n) < 0.78, np.nan, RNG.integers(2, 10, n)
    )
    # Fielders are the same people as batters; drawing from a separate id
    # pool would make HUNT merge onto nothing and read as a dead pillar.
    for pos in range(2, 10):
        df[f"fielder_{pos}"] = RNG.integers(600_000, 600_000 + N_BATTERS, n)

    return _make_nullable(df)


# Every column that can carry a null in the real feed. Statcast hands these
# back as pandas extension dtypes holding pd.NA rather than NaN, and pd.NA
# behaves differently in ways that only surface at runtime:
#
#   * `.astype(float)` on one raises "float() argument must be ... not NAType"
#   * comparing two of them yields a nullable BooleanArray, and passing that
#     to np.where raises "boolean value of NA is ambiguous"
#
# Both have already reached CI and killed a build. Converting the whole set
# here rather than column-by-column is deliberate: the first version of this
# fixture converted only the columns of the bug being chased at the time, so
# the very next nullable column to be touched -- `on_1b` -- escaped again.
_STRING_COLS = ("stand", "p_throws", "events", "pitch_type", "description",
                "type", "inning_topbot", "home_team", "away_team")
_FLOAT_COLS = ("zone", "plate_x", "plate_z", "launch_speed", "launch_angle",
               "release_speed", "release_spin_rate", "pfx_x", "pfx_z",
               "release_extension", "delta_home_win_exp", "delta_run_exp",
               "hit_location", "on_1b", "on_2b", "on_3b")
_INT_COLS = ("balls", "strikes", "inning", "outs_when_up", "bat_score",
             "fld_score", "pitch_number")


def _make_nullable(df: pd.DataFrame) -> pd.DataFrame:
    for col in _STRING_COLS:
        if col in df.columns:
            df[col] = df[col].astype("string")
    for col in _FLOAT_COLS:
        if col in df.columns:
            df[col] = df[col].astype("Float64")
    for col in _INT_COLS:
        if col in df.columns:
            df[col] = df[col].astype("Int64")
    for pos in range(2, 10):
        col = f"fielder_{pos}"
        if col in df.columns:
            df[col] = df[col].astype("Float64")
    return df


def _assign_pitching_roles(df: pd.DataFrame) -> pd.DataFrame:
    """Give the frame a real starter/reliever structure and honest innings.

    Drawing a pitcher independently per row makes everyone the starter of
    nothing. `workhorse` keys off whoever threw to a side's first batter and
    then requires that a majority of his appearances be starts -- with random
    assignment nobody clears that, both terms compute nothing, and the test
    that guards them passes vacuously. Same story for `inning`: drawn at
    random it has no relationship to `at_bat_number`, so half-innings do not
    contain consecutive batters and the outs-recorded reconstruction has
    nothing coherent to reconstruct.

    This is the same class of fixture bug as the fielders once being drawn
    from a different id pool than the batters: the code ran, computed
    nothing, and the test said fine.
    """
    starters = np.arange(700_000, 700_012)
    relief = np.arange(700_012, 700_000 + N_PITCHERS)

    ab = df["at_bat_number"].to_numpy()
    game = df["game_pk"].to_numpy()
    df["inning"] = np.minimum(1 + (ab - 1) // 5, 11)
    side = (df["inning_topbot"].to_numpy() == "Top").astype(int)

    # One designated starter per game per side, deterministic in game_pk so
    # the same club's starter is the same man across the whole game.
    mine = starters[(game * 2 + side) % len(starters)]
    early = df["inning"].to_numpy() <= 6
    df["pitcher"] = np.where(
        early, mine, relief[(game * 7 + ab) % len(relief)]
    ).astype("int64")
    return df


def _add_runner_continuity(df: pd.DataFrame) -> pd.DataFrame:
    """Make base runners persist across plate appearances within a game.

    `xbt_frame` reads baserunning out of how `on_1b`/`on_2b`/`on_3b` change
    between consecutive plate appearances, so runner ids drawn independently
    per row carry no signal at all and the component silently computes
    nothing. Walking a real base state through each game is what makes that
    code path testable.
    """
    df = df.sort_values(["game_pk", "at_bat_number"]).reset_index(drop=True)
    on1 = np.full(len(df), np.nan)
    on2 = np.full(len(df), np.nan)
    on3 = np.full(len(df), np.nan)

    bases = {}          # base -> runner id, for the game in progress
    current_game = None
    last_ab = None
    for i, (g, ab, bat, ev) in enumerate(zip(
            df["game_pk"], df["at_bat_number"], df["batter"],
            df["events"].astype(str))):
        if g != current_game:
            bases, current_game, last_ab = {}, g, None
        if ab != last_ab:
            # A new plate appearance: advance whoever was on base.
            if last_ab is not None:
                nxt = {}
                for base, rid in bases.items():
                    if RNG.random() < 0.55:          # advanced a base or more
                        moved = base + RNG.integers(1, 3)
                        if moved <= 3:
                            nxt[int(moved)] = rid
                    else:
                        nxt[base] = rid              # held
                bases = {b: r for b, r in nxt.items() if b in (1, 2, 3)}
            last_ab = ab
            if ev in ("single", "walk") and 1 not in bases:
                bases[1] = int(bat)                  # batter reaches first
        on1[i] = bases.get(1, np.nan)
        on2[i] = bases.get(2, np.nan)
        on3[i] = bases.get(3, np.nan)

    df["on_1b"], df["on_2b"], df["on_3b"] = on1, on2, on3
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
    wins = RNG.integers(55, 105, len(TEAM_LIST)).astype(float)
    return pd.DataFrame({
        "team": TEAM_LIST,
        "rs": RNG.integers(520, 830, len(TEAM_LIST)).astype(float),
        "ra": RNG.integers(520, 830, len(TEAM_LIST)).astype(float),
        # W-L is not optional in the fixture. Without it every downstream
        # comparison of DAWG against what actually happened -- the team board
        # here, the DAWG-vs-record correlations in the history view -- runs on
        # an all-null column and reports n = 0 rather than failing.
        "wins": wins,
        "losses": 162.0 - wins,
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


def _synthetic_postseason() -> pd.DataFrame:
    teams = TEAM_LIST[:12]
    rounds = [5, 4, 3, 3, 2, 2, 2, 2, 1, 1, 1, 1]
    return pd.DataFrame({
        "team": teams,
        "ps_games": RNG.integers(3, 20, len(teams)),
        "ps_wins": RNG.integers(0, 11, len(teams)),
        "round": rounds,
        "round_label": [f"Round {r}" for r in rounds],
    })


def test_history_builds_across_seasons(offline, monkeypatch):
    """The multi-season path must assemble, not just the single-season one.

    Both synthetic seasons come from the same generator, so the numbers here
    are meaningless -- what is being checked is that every season lines up on
    the same player keys and that the cross-year statistics survive a player
    who appears in one season and not another.
    """
    from xdawg import history

    monkeypatch.setattr(ingest, "load_postseason",
                        lambda *a, **k: _synthetic_postseason())
    monkeypatch.setattr(history.ingest, "load_postseason",
                        lambda *a, **k: _synthetic_postseason())

    payload = history.build_history([2025, 2026])

    assert payload["seasons"] == [2025, 2026]
    assert payload["players"], "no player careers assembled"
    assert payload["teams"], "no team careers assembled"
    assert "2025->2026" in payload["stability"]
    assert payload["stability"]["2025->2026"]["n"] > 0, (
        "no player matched across seasons -- the career key is wrong"
    )
    # A player in both seasons must carry both, and the spread must compute.
    multi = [p for p in payload["players"] if p["n_seasons"] >= 2]
    assert multi, "nobody appears in more than one season"
    assert multi[0]["sd_dawg_plus"] is not None
    # Postseason must have threaded through to the team rows.
    rounds = [s.get("round") for t in payload["teams"]
              for s in t["seasons"].values()]
    assert any(r for r in rounds), "postseason results never reached the teams"

    # Every correlation must have found pairs to correlate. These report
    # n = 0 and an r of None when a column never threads through, which
    # looks like a finding ("no relationship") rather than a plumbing bug.
    for name, c in payload["correlations"].items():
        assert c["n"] > 0, f"{name} correlated nothing -- a column is missing"

    assert history.summarize(payload)


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
        # All four stats: the league-baselined pair and the self-referenced
        # pair. They come from one pass, so a missing one means the league
        # variant of some component quietly failed to thread through.
        for col in ("DAWG+", "DAWG", "wDAWG+", "wDAWG"):
            assert col in frame.columns, f"{label} missing {col}"
            assert frame[col].notna().any(), f"{label} {col} all null"
        # If these two were identical the league baseline changed nothing.
        assert not np.allclose(frame["DAWG+"], frame["wDAWG+"]), (
            f"{label}: DAWG+ and wDAWG+ are identical -- the league "
            "baseline is not being applied"
        )


def test_payload_builds_and_pillars_are_alive(offline):
    """A built payload must not contain a silently dead pillar."""
    from xdawg.export import build_payload
    from xdawg.pipeline import run

    hitters, pitchers = run(season=2026, min_pa=1, min_bf=1)
    payload = build_payload(hitters, pitchers, season=2026, synthetic=False)

    assert payload["players"], "payload has no players"
    assert payload.get("team_table"), "no team table in the payload"
    for row in payload["team_table"]:
        assert row.get("dawg") is not None, f"{row['team']} has no cumulative total"
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

    # GRIT and FIGHT each ran on a single component until these were built,
    # which put 60% of a hitter's score on two numbers. Assert they are still
    # wired up, because they fail by silently computing nothing rather than
    # by raising.
    hitters_rows = [p for p in payload["players"] if p["role"] == "hitter"]
    present = {
        c["key"]
        for p in hitters_rows
        for pil in p["pillars"].values()
        for c in pil["components"]
    }
    for key in ("availability", "hbp_above_expected", "extra_bases_taken",
                "fight_process_delta", "ev_situational", "wpa_clutch_delta",
                "contender_rv_delta", "division_rv_delta"):
        assert key in present, f"{key} computed nothing"

    # Pitcher side. `long_start_rate` / `blowup_rate` close the survivorship
    # hole in GRIT and `jam_escape_runs` is the outcome half of the jam
    # grade; all three fail by computing nothing rather than by raising, and
    # a pitcher GRIT that quietly reverts to survivors-only is precisely the
    # bug they were added to fix.
    pitcher_rows = [p for p in payload["players"] if p["role"] == "pitcher"]
    p_present = {
        c["key"]
        for p in pitcher_rows
        for pil in p["pillars"].values()
        for c in pil["components"]
    }
    for key in ("long_start_rate", "blowup_rate", "jam_escape_runs"):
        assert key in p_present, f"{key} computed nothing"
