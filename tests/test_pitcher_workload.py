"""Tests for the two pitcher terms that grade a start rather than a pitch.

Everything else in pitcher GRIT is conditioned on surviving: `stuff_after_75`
only exists for a pitcher who threw a 76th pitch, `third_time_through` only
for one who faced the order three times. A starter who keeps getting knocked
out early contributes to neither, so he was graded exclusively on the nights
he lasted and his disasters never appeared anywhere in the metric. These
check that the terms which close that hole actually behave -- they fail by
computing nothing or by grading everyone the same, which no crash test can
see.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xdawg.pillars.pitchers import (  # noqa: E402
    half_inning_pas, jam_escape_runs, workhorse,
)

HORSE = 700_001    # six starts, six innings each
BLOWUP = 700_002   # six starts, two innings each


def _start(game_pk: int, pitcher: int, innings: int, rv: float = 0.0,
           jam_from: int | None = None) -> list[dict]:
    """One start: `innings` clean frames of three batters each.

    Each plate appearance begins with one more out than the last and the
    half inning is closed out, so `outs_recorded` works out to exactly one
    per batter and three per inning -- which is the arithmetic the whole
    innings-pitched derivation rests on.
    """
    rows = []
    ab = 1
    for inning in range(1, innings + 1):
        for outs in (0, 1, 2):
            on1 = 1.0 if jam_from is not None and inning >= jam_from else np.nan
            on2 = 1.0 if jam_from is not None and inning >= jam_from else np.nan
            rows.append({
                "game_pk": game_pk,
                "at_bat_number": ab,
                "pitch_number": 1,
                "pitcher": pitcher,
                "inning": inning,
                "inning_topbot": "Top",
                "outs_when_up": outs,
                "on_1b": on1,
                "on_2b": on2,
                "on_3b": np.nan,
                "delta_run_exp": rv,
                "li": 1.0,
            })
            ab += 1
    return rows


def _frame(specs) -> pd.DataFrame:
    rows = []
    for spec in specs:
        rows.extend(_start(**spec))
    return pd.DataFrame(rows)


def test_outs_recorded_reconstructs_innings_pitched():
    """Statcast ships the out count at the START of a PA and never the end.

    Innings pitched only exists here because it is recovered from the
    transition to the next batter, so this is the assertion the workhorse
    terms are built on top of.
    """
    p = _frame([{"game_pk": 1, "pitcher": HORSE, "innings": 6}])
    pa = half_inning_pas(p)
    assert pa["outs_recorded"].sum() == 18.0, "six innings must be eighteen outs"
    assert (pa["outs_recorded"] == 1.0).all(), "each batter here retires one"


def test_workhorse_separates_the_innings_eater_from_the_short_start():
    p = _frame(
        [{"game_pk": g, "pitcher": HORSE, "innings": 6} for g in range(1, 7)]
        + [{"game_pk": g, "pitcher": BLOWUP, "innings": 2} for g in range(11, 17)]
    )
    out = workhorse(p).set_index("pitcher")

    assert out.loc[HORSE, "long_start_rate"] == 1.0
    assert out.loc[HORSE, "blowup_rate"] == 0.0
    assert out.loc[BLOWUP, "long_start_rate"] == 0.0
    assert out.loc[BLOWUP, "blowup_rate"] == 1.0
    assert out.loc[BLOWUP, "long_start_rate__n"] == 6, "shrinkage counts starts"


def test_an_opener_is_not_graded_as_a_failed_starter():
    """One-inning openers and blown-out starters look identical in pitch data.

    Both threw to the first batter of the game and both left inside two
    innings, and nothing in the feed says which one was the plan. The guard
    is role share: a pitcher who mostly relieves is not graded on start
    length no matter how many times he happened to open.
    """
    rows = []
    for g in range(1, 4):                    # three token starts...
        rows.extend(_start(g, 700_004, innings=1))
    for g in range(20, 32):                  # ...and twelve relief outings
        relief = _start(g, 700_004, innings=1)
        for r in relief:
            r["at_bat_number"] += 100        # someone else started
            r["inning"] += 5
        rows.append({**relief[0], "pitcher": 700_005, "at_bat_number": 1,
                     "inning": 1})           # the actual starter
        rows.extend(relief)

    out = workhorse(pd.DataFrame(rows))
    assert 700_004 not in set(out["pitcher"]), (
        "a pitcher who mostly relieves must not be graded on start length"
    )


def test_relievers_get_no_row_rather_than_a_bad_grade():
    """A reliever has no starts, so grading him on start length is meaningless.

    Leaving him out entirely is what makes `shrink` collapse both terms to
    exactly zero for him: the merged `__n` arrives null, and n=0 zeroes the
    component. Giving him a row of zeros instead would score him as the
    worst starter in the league.
    """
    p = _frame([{"game_pk": g, "pitcher": HORSE, "innings": 6}
                for g in range(1, 4)])
    # The reliever appears after the starter, in the same half innings.
    relief = _frame([{"game_pk": g, "pitcher": 700_003, "innings": 2}
                     for g in range(1, 4)])
    relief["at_bat_number"] = relief["at_bat_number"] + 100
    relief["inning"] = relief["inning"] + 6
    out = workhorse(pd.concat([p, relief], ignore_index=True))

    assert HORSE in set(out["pitcher"])
    assert 700_003 not in set(out["pitcher"]), "a reliever must not be graded here"


def test_jam_escape_charges_the_damage_to_whoever_made_the_mess():
    """The tough half of the jam grade.

    Both pitchers get into the same trouble. One gives up nothing after it,
    the other bleeds run value. The process reading (chase rate) cannot tell
    them apart at all -- this one has to.
    """
    p = _frame(
        [{"game_pk": g, "pitcher": HORSE, "innings": 6, "jam_from": 2,
          "rv": -0.05} for g in range(1, 7)]
        + [{"game_pk": g, "pitcher": BLOWUP, "innings": 6, "jam_from": 2,
            "rv": 0.30} for g in range(11, 17)]
    )
    out = jam_escape_runs(p).set_index("pitcher")

    assert set(out.index) == {HORSE, BLOWUP}
    # league_delta is the DAWG+ variant: measured against the league's jams
    # rather than the pitcher's own, so a pitcher who blows up UNIFORMLY
    # still gets punished. The self-referenced variant is zero for both by
    # construction here, which is the intended behaviour of wDAWG+ and the
    # reason the default stat is the league-baselined one.
    assert out.loc[HORSE, "jam_escape_runs__lg"] > 0
    assert out.loc[BLOWUP, "jam_escape_runs__lg"] < 0
    assert out.loc[BLOWUP, "jam_escape_runs__n"] >= 8, "min_n must be cleared"


def test_jam_escape_counts_runs_scored_after_the_pitcher_is_pulled():
    """Runners left on base are still his.

    A pitcher who reliably hands a two-on mess to the bullpen has not
    escaped anything, so the charge runs to the end of the half inning
    regardless of who finished it. The reliever is separately credited by
    `inherited_runners`, so this is not double-counting -- it is the same
    event scored once on each ledger.
    """
    rows = _start(1, HORSE, innings=1, rv=0.0, jam_from=1)
    # The starter is lifted; a reliever finishes the inning and it burns down.
    rows.append({
        "game_pk": 1, "at_bat_number": 99, "pitch_number": 1,
        "pitcher": 700_009, "inning": 1, "inning_topbot": "Top",
        "outs_when_up": 2, "on_1b": 1.0, "on_2b": 1.0, "on_3b": np.nan,
        "delta_run_exp": 2.5, "li": 1.0,
    })
    pa = half_inning_pas(pd.DataFrame(rows))
    starter_jam = pa[pa["pitcher"] == HORSE].iloc[0]

    assert starter_jam["rest_of_inning_rv"] > 2.0, (
        "damage after the hook must still be charged to the man who "
        f"loaded the bases, got {starter_jam['rest_of_inning_rv']:.2f}"
    )


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
