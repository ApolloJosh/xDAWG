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
            # `half_inning_pas` needs these to reconstruct innings pitched and
            # runs allowed. Without them the pitcher line silently degrades to
            # strikeouts and walks, which is correct behaviour but leaves the
            # IP and RA9 arithmetic untested.
            "outs_when_up": min(i - 1, 2), "bat_score": 0, "fld_score": 0,
            # The process-credit path reads these. Without them it degrades
            # to win probability alone, which would leave the award's other
            # half untested from this file.
            "description": "hit_into_play", "zone": 5, "strikes": 0,
            "balls": 0, "stand": "R", "p_throws": "R", "plate_x": 0.0,
            "launch_speed": None,
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


CLOSER = 500_010
STARTER = 500_011


def test_a_partial_week_does_not_use_a_full_weeks_bar():
    """The Hader/Pecko bug, exactly as it appeared on the site.

    A week runs Monday to Sunday. On the Tuesday, two of its seven days have
    been played. A closer with one appearance behind him -- four batters, a
    good one -- was held to the eight-batter bar meant for a finished week
    and thrown off the ballot entirely, while a starter who happened to have
    made his start cleared it on volume alone and won the award with a
    NEGATIVE score, unopposed.
    """
    rows = []
    # Both pitch the top half, so their side is the HOME club and their WPA
    # is `delta_home_win_exp` as given (the batter's is its negative).
    # Monday: the starter, 10 batters faced, and it goes badly.
    rows += _game(11, "2026-08-24", rows=[
        (600_000 + i, STARTER, "Top", -0.05) for i in range(10)
    ])
    # Tuesday: the closer, four batters, and he shuts the door.
    rows += _game(12, "2026-08-25", rows=[
        (600_100 + i, CLOSER, "Top", +0.06) for i in range(4)
    ])
    df = pd.DataFrame(rows)
    aw = _build(df)

    assert awards.window_key(pd.Timestamp("2026-08-25"), "week") == "2026-08-24"
    week = aw["boards"]["week"]["2026-08-24"]
    ids = [r["id"] for r in week]

    assert CLOSER in ids, (
        "a reliever with a normal two-days-into-the-week workload must be "
        "eligible for a two-day-old week"
    )
    winner = week[0]
    assert winner["id"] == CLOSER, (
        f"the man who actually helped must win; got {winner['name']} at "
        f"{winner['score']}"
    )
    assert winner["score"] > 0

    # And the floor itself must have been prorated, not merely survived.
    floor = aw["floors"]["week"]["2026-08-24"]
    assert floor < awards.MIN_PA["week"], (
        f"a 2-of-7-day week must lower the bar; still {floor}"
    )
    assert floor >= awards.MIN_FLOOR


def test_a_finished_window_keeps_the_full_bar():
    """Prorating must not quietly weaken a completed week or month."""
    through = pd.Timestamp("2026-08-30")          # the Sunday
    assert awards.eligibility_floor("week", "2026-08-24", through) == awards.MIN_PA["week"]
    assert awards.eligibility_floor(
        "month", "2026-08", pd.Timestamp("2026-08-31")) == awards.MIN_PA["month"]
    # A day is always complete the moment it exists.
    assert awards.eligibility_floor(
        "day", "2026-08-25", pd.Timestamp("2026-08-25")) == awards.MIN_PA["day"]


def test_the_floor_scales_with_how_much_has_been_played():
    mon = pd.Timestamp("2026-08-24")
    seq = [awards.eligibility_floor("week", "2026-08-24", mon + dt.timedelta(days=i))
           for i in range(7)]
    assert seq == sorted(seq), f"the bar must rise as the week fills in: {seq}"
    assert seq[0] == awards.MIN_FLOOR, "day one of a week uses the hard minimum"
    assert seq[-1] == awards.MIN_PA["week"]
    # Never below the hard minimum, even on the first day of a long month.
    assert awards.eligibility_floor(
        "month", "2026-08", pd.Timestamp("2026-08-01")) == awards.MIN_FLOOR


def test_windows_carry_their_own_labels():
    aw = _build(_frame())
    assert aw["labels"]["day"]["2026-06-15"] == "June 15, 2026"

    # A single-day fixture means every window is one day into itself, so the
    # weekly and monthly floors prorate down to the hard minimum of two trips
    # and both windows DO produce a winner. That is the intended behaviour --
    # the alternative is what the site actually showed, a two-day-old week
    # judged against a finished week's bar.
    assert aw["boards"]["week"], "a one-day-old week should still have a board"
    assert aw["floors"]["week"]["2026-06-15"] == awards.MIN_FLOOR
    assert "–" in aw["labels"]["week"]["2026-06-15"], "a week label spans two dates"
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

    # A past window keeps `best` for its podium only -- the archive popup has
    # to explain the moment for a week three weeks back, but the fourth-place
    # finisher's biggest swing is detail nobody opens.
    assert all("best" in r for r in past if r["rank"] <= 3), (
        "the podium of a past window must keep its moment"
    )
    assert all("best" not in r for r in past if r["rank"] > 3), (
        "past windows past third place must stay trimmed"
    )
    # Every kept row carries a traditional line, featured or not.
    assert all(r.get("line") for r in past + newest), "stat lines must be attached"


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


def test_traditional_lines_are_computed_by_the_rule_book():
    """PA / HR / BB / OPS for hitters, IP / K / BB / RA9 for pitchers.

    OPS is the one with a denominator worth getting wrong: a walk is not an
    at-bat but IS a plate appearance, and a sacrifice fly leaves the on-base
    denominator alone while leaving the slugging one. This fixture gives one
    hitter a homer, a single, a walk and a strikeout, so every term is
    exercised and the answer is checkable by hand.
    """
    B = 500_020
    rows = _game(21, "2026-07-06", rows=[
        (B, 900_020, "Bot", +0.10),
        (B, 900_020, "Bot", +0.05),
        (B, 900_020, "Bot", +0.02),
        (B, 900_020, "Bot", -0.03),
    ])
    for r, ev in zip(rows, ["home_run", "single", "walk", "strikeout"]):
        r["events"] = ev
    aw = _build(pd.DataFrame(rows))

    day = {(r["id"], r["role"]): r for r in aw["boards"]["day"]["2026-07-06"]}
    line = day[(B, "hitter")]["line"]

    assert line["PA"] == 4 and line["HR"] == 1 and line["BB"] == 1
    assert line["H"] == 2
    # AB = 4 PA - 1 BB = 3. OBP = (2 H + 1 BB) / 4 = .750.
    # SLG = (4 + 1) total bases / 3 AB = 1.667. OPS = 2.417.
    assert line["AVG"] == round(2 / 3, 3)
    assert abs(line["OPS"] - (3 / 4 + 5 / 3)) < 0.001, line["OPS"]

    # Outs run 0, 1, 2, 2 across the four batters and the half inning is
    # closed out at three, so exactly one inning was recorded and nobody
    # scored: 1.0 IP, RA9 of zero rather than a null.
    pline = day[(900_020, "pitcher")]["line"]
    assert pline["BF"] == 4 and pline["K"] == 1 and pline["BB"] == 1
    assert pline["IP"] == "1.0", pline
    assert pline["outs"] == 3, pline
    assert pline["R"] == 0 and pline["RA9"] == 0.0, pline


def test_innings_are_written_in_outs_not_tenths():
    """6.1 is six innings and one third. It is not a decimal.

    A plain `outs / 3` rounded to one place gives 6.3 for nineteen outs,
    which reads as a real number and is wrong in a way nobody would question
    on a box score. The true value stays in `outs`, which is what RA9
    divides by -- "6.1" and "6.2" cannot be compared or averaged as numbers.
    """
    cases = {0: "0.0", 1: "0.1", 2: "0.2", 3: "1.0", 4: "1.1", 5: "1.2",
             6: "2.0", 19: "6.1", 20: "6.2", 21: "7.0", 27: "9.0"}
    for outs, want in cases.items():
        got = awards.innings_notation(outs)
        assert got == want, f"{outs} outs should read {want}, got {got}"
    assert awards.innings_notation(None) is None
    assert awards.innings_notation(float("nan")) is None


def test_ra9_still_divides_by_true_innings_not_the_notation():
    """The display change must not leak into the arithmetic.

    Nineteen outs is 6.333 innings, not 6.1. If RA9 ever started dividing by
    the notation it would read about 4% high and look plausible.
    """
    B = 500_040
    rows = []
    # Seven batters, six retired, one run in. Outs run 0,1,2 then the inning
    # turns over, so this is a clean two innings plus one.
    for i in range(7):
        rows.append(dict(_game(41, "2026-07-06", rows=[
            (600_500 + i, 900_040, "Top", -0.01)])[0],
            at_bat_number=i + 1, outs_when_up=min(i, 2), inning=1 + i // 3))
    aw = _build(pd.DataFrame(rows))
    line = [r for r in aw["boards"]["day"]["2026-07-06"]
            if r["role"] == "pitcher"][0]["line"]
    outs, ra9 = line["outs"], line["RA9"]
    assert line["IP"] == awards.innings_notation(outs)
    if ra9 is not None and outs:
        expected = line["R"] * 9.0 / (outs / 3.0)
        assert abs(ra9 - expected) < 0.01, (
            f"RA9 {ra9} should be {expected:.2f} from {outs} outs")


def test_ra9_is_not_labelled_era():
    """Statcast has no earned/unearned split, so calling this ERA would lie.

    Guarding the key name because the temptation to rename it for the sake of
    a familiar column header is exactly how a metric quietly starts claiming
    something it cannot compute.
    """
    B = 500_030
    rows = _game(31, "2026-07-06", rows=[
        (B, 900_030, "Bot", -0.10) for _ in range(4)
    ])
    aw = _build(pd.DataFrame(rows))
    line = [r for r in aw["boards"]["day"]["2026-07-06"]
            if r["role"] == "pitcher"][0]["line"]
    assert "RA9" in line and "ERA" not in line


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


# --------------------------------------------------------------------------
# a missing number is not a zero
# --------------------------------------------------------------------------

def _pitcher_pa(runs, outs=(3.0, 0.0)):
    return pd.DataFrame([
        {"key": "2026-08-29", "player_id": 1, "role": "pitcher",
         "events": ev, "outs_recorded": o, "runs": r}
        for ev, o, r in zip(("strikeout", "single"), outs, runs)
    ])


def test_a_feed_with_no_scores_leaves_runs_unknown_rather_than_zero():
    """A sum over nothing but nulls is 0.0, and 0.00 RA9 is a shutout.

    That is how every pitcher on the site came to look unhittable: the runs
    column was empty, pandas summed it to nought, and the card printed the
    nought in bold. Unknown has to look unknown.
    """
    line = awards._stat_lines(_pitcher_pa([np.nan, np.nan]), "key")[
        ("2026-08-29", 1, "pitcher")]
    assert line["R"] is None
    assert line["RA9"] is None


def test_a_genuine_shutout_still_reads_as_a_shutout():
    line = awards._stat_lines(_pitcher_pa([0.0, 0.0]), "key")[
        ("2026-08-29", 1, "pitcher")]
    assert line["R"] == 0
    assert line["RA9"] == 0.0


def test_runs_allowed_reach_ra9_through_true_innings():
    # Two runs in one inning is an 18.00 RA9, not 2.00 and not 6.00.
    line = awards._stat_lines(_pitcher_pa([0.0, 2.0]), "key")[
        ("2026-08-29", 1, "pitcher")]
    assert line["R"] == 2
    assert line["RA9"] == 18.0
