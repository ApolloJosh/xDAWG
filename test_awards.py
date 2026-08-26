"""DAWG of the Day / Week / Month.

The failure mode here is not a crash, it is a plausible wrong winner: a
sign flip that crowns whoever lost the game, a week that runs Sunday to
Saturday, a one-plate-appearance cameo beating a four-hit night. None of
those raise anything.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xdawg import awards  # noqa: E402
from xdawg.aggregate import score_pillar  # noqa: E402
from xdawg.config import COMPONENTS  # noqa: E402

HOME_HERO = 500_001
ROAD_HERO = 500_002
CAMEO = 500_003


def _game(game_pk, date, home="HOU", away="SEA", rows=None):
    out = []
    for i, (batter, pitcher, topbot, wpa) in enumerate(rows, start=1):
        out.append({
            "game_pk": game_pk, "at_bat_number": i, "pitch_number": 1,
            "game_date": date, "batter": batter, "pitcher": pitcher,
            "inning_topbot": topbot, "home_team": home, "away_team": away,
            "delta_home_win_exp": wpa, "inning": 9, "li": 2.0,
            "delta_run_exp": 0.5, "events": "single",
        })
    return out


def _frame():
    """Two heroes, one at home and one on the road, plus a one-trip cameo.

    Both heroes did the same good thing for their own club, so any
    difference between their scores is a sign error rather than a finding:
    `delta_home_win_exp` is signed for the HOME team, and a road hitter's
    contribution is its negative.
    """
    rows = []
    # Home hitter drives the home win probability UP.
    rows += _game(1, "2026-06-15", rows=[
        (HOME_HERO, 900_001, "Bot", +0.20),
        (HOME_HERO, 900_001, "Bot", +0.15),
        # A different arm for the third out, so pitcher 900_001 faced
        # nobody but the hero and the mirror test compares like to like.
        (700_777, 900_004, "Bot", -0.02),
    ])
    # Road hitter drives it DOWN, which is the same thing from his side.
    rows += _game(2, "2026-06-15", rows=[
        (ROAD_HERO, 900_002, "Top", -0.20),
        (ROAD_HERO, 900_002, "Top", -0.15),
        (700_888, 900_002, "Top", +0.02),
    ])
    # One plate appearance, one enormous swing.
    rows += _game(3, "2026-06-15", rows=[
        (CAMEO, 900_003, "Bot", +0.60),
        (700_999, 900_003, "Bot", -0.01),
        (700_999, 900_003, "Bot", -0.01),
    ])
    return pd.DataFrame(rows)


def _names(df):
    ids = set(df["batter"]) | set(df["pitcher"])
    return {int(i): f"P{i}" for i in ids}


def _build(df):
    from xdawg import ingest
    old = ingest.load_standings
    ingest.load_standings = lambda *a, **k: None      # flat FIGHT weights
    try:
        return awards.build_awards(df, 2026, _names(df), {})
    finally:
        ingest.load_standings = old


def test_week_runs_monday_to_sunday():
    """Monday-to-Sunday was asked for explicitly, and it is easy to get wrong.

    2026-06-15 is a Monday, so it opens its own week and the Sunday before
    it belongs to the previous one.
    """
    mon = pd.Timestamp("2026-06-15")
    assert mon.weekday() == 0, "fixture assumes this date is a Monday"
    assert awards.window_key(mon, "week") == "2026-06-15"
    assert awards.window_key(mon + dt.timedelta(days=6), "week") == "2026-06-15"
    assert awards.window_key(mon - dt.timedelta(days=1), "week") == "2026-06-08"
    assert awards.window_key(mon + dt.timedelta(days=7), "week") == "2026-06-22"


def test_month_is_the_calendar_month_not_a_trailing_window():
    assert awards.window_key(pd.Timestamp("2026-06-01"), "month") == "2026-06"
    assert awards.window_key(pd.Timestamp("2026-06-30"), "month") == "2026-06"
    assert awards.window_key(pd.Timestamp("2026-07-01"), "month") == "2026-07"


def test_a_road_hero_scores_the_same_as_a_home_hero():
    """The sign test. Gets this wrong and every away player is inverted."""
    aw = _build(_frame())
    day = {(r["id"], r["role"]): r for r in aw["boards"]["day"]["2026-06-15"]}

    home = day[(HOME_HERO, "hitter")]
    road = day[(ROAD_HERO, "hitter")]
    assert home["score"] > 0 and road["score"] > 0, (
        f"both heroes helped their own club; got {home['score']} and {road['score']}"
    )
    assert abs(home["score"] - road["score"]) < 1e-9, (
        "identical contributions on opposite sides of the ledger must score "
        f"identically; got {home['score']} vs {road['score']}"
    )


def test_the_pitcher_is_the_mirror_of_the_batter():
    aw = _build(_frame())
    day = {(r["id"], r["role"]): r for r in aw["boards"]["day"]["2026-06-15"]}
    hero = day[(HOME_HERO, "hitter")]
    victim = day[(900_001, "pitcher")]
    assert np.isclose(hero["wpa"], -victim["wpa"], atol=1e-9), (
        "a plate appearance is one event: what the hitter gains the pitcher loses"
    )


def test_a_one_trip_cameo_cannot_win_the_day():
    """Without a minimum, the award goes to whoever had one lucky swing.

    The cameo's single plate appearance is worth more win probability than
    either hero's whole night, so he outranks them on raw contribution and
    is excluded only by the eligibility floor.
    """
    df = _frame()
    aw = _build(df)
    rows = aw["boards"]["day"]["2026-06-15"]
    ids = [r["id"] for r in rows if r["role"] == "hitter"]

    assert CAMEO not in ids, "one plate appearance must not qualify for the day"
    assert awards.MIN_PA["day"] >= 2
    assert ids[0] in (HOME_HERO, ROAD_HERO)


def test_windows_carry_their_own_labels():
    aw = _build(_frame())
    assert aw["labels"]["day"]["2026-06-15"] == "June 15, 2026"

    # The week and month boards are legitimately empty here: two plate
    # appearances is nowhere near the 8-trip weekly or 30-trip monthly floor,
    # so those windows produce no winner and therefore no label. That the
    # eligibility floors bite before the labeller is the point -- an award
    # with nobody eligible must be absent, not blank.
    assert aw["boards"]["week"] == {}, "a 2-PA fixture must not award a week"
    assert aw["boards"]["month"] == {}, "or a month"
    assert awards.window_label("2026-06-15", "week") == "June 15–21, 2026"
    assert awards.window_label("2026-06-29", "week") == "June 29–July 5, 2026"
    assert awards.window_label("2026-06", "month") == "June 2026"


def test_latest_window_keeps_the_full_board_and_past_ones_are_trimmed():
    """The archive is trimmed for payload size, and that must be visible.

    A trimmed row has no `best` detail, so if this ever inverted -- full
    rows everywhere -- the payload would quietly balloon back to megabytes.
    """
    df = _frame()
    older = df.copy()
    older["game_date"] = "2026-06-08"
    older["game_pk"] = older["game_pk"] + 100
    aw = _build(pd.concat([df, older], ignore_index=True))

    assert aw["latest"]["day"] == "2026-06-15"
    newest = aw["boards"]["day"]["2026-06-15"]
    past = aw["boards"]["day"]["2026-06-08"]
    assert any("best" in r for r in newest), "the featured day needs its detail"
    assert all("best" not in r for r in past), "past days must be trimmed"


def test_raw_pillars_collapse_on_a_short_window():
    """The honesty check behind the pillar bars on the awards page.

    `score_pillar` normally ends with a z-score, which re-standardizes a
    pillar to sd 1 no matter how little evidence built it -- so a single
    day would print bars as confident as a full season's. `raw=True` skips
    that, leaving the shrinkage visible. Same components, same values, only
    the opportunity counts differ between these two frames.
    """
    n = 120
    rng = np.random.default_rng(9)
    cols, tiny = {}, {}
    for c, cfg in COMPONENTS["hitter"]["bite"].items():
        if cfg["weight"] <= 0:
            continue
        v = rng.normal(0, 1, n)
        cols[c], tiny[c] = v, v
        cols[f"{c}__n"] = np.full(n, cfg["k"] * 50.0)     # a full season
        tiny[f"{c}__n"] = np.full(n, 3.0)                 # one day
    big = score_pillar(pd.DataFrame(cols), "hitter", "bite", raw=True)[0]
    small = score_pillar(pd.DataFrame(tiny), "hitter", "bite", raw=True)[0]

    assert big.abs().max() > 0.8, "a full season must produce real spread"
    assert small.abs().max() < 0.1, (
        "a day's worth of evidence must shrink to nearly nothing, "
        f"got {small.abs().max():.3f}"
    )
    # And the standardized path must be unchanged -- the season leaderboard
    # depends on it.
    z = score_pillar(pd.DataFrame(cols), "hitter", "bite")[0]
    assert abs(z.std(ddof=0) - 1.0) < 1e-9


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
