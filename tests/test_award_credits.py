"""The process half of the award score.

WPA says what happened to the scoreboard. These credits say whether it was a
dawg at-bat, and they exist because a one-day window cannot support a rate:
the pillars are rates, shrinkage annihilates them at four trips, and a sum of
five countable events is a real sum rather than an estimate of anything.

The failure modes here are all silent. A sign error on `plate_x` would score
a pitcher for living on the outside corner and call it working inside. An
uncentered process bucket measures playing time. A credit counted per pitch
instead of per plate appearance pays a long at-bat twice for one outcome.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xdawg import awards  # noqa: E402
from xdawg.config import AWARD_CREDITS  # noqa: E402


def _pitch(game, ab, pn, **kw):
    row = {
        "game_pk": game, "at_bat_number": ab, "pitch_number": pn,
        "game_date": "2026-07-06", "batter": 600_001, "pitcher": 700_001,
        "inning_topbot": "Top", "home_team": "HOU", "away_team": "SEA",
        "delta_home_win_exp": 0.0, "inning": 9, "li": 1.0,
        "delta_run_exp": 0.0, "events": None, "description": "ball",
        "zone": 5, "strikes": 0, "balls": 0, "stand": "R", "p_throws": "R",
        "plate_x": 0.0, "plate_z": 2.4, "launch_speed": np.nan,
        "on_1b": np.nan, "on_2b": np.nan, "on_3b": np.nan,
        "outs_when_up": 0, "bat_score": 0, "fld_score": 0,
    }
    row.update(kw)
    return row


def _credits(rows):
    return awards._process_points(pd.DataFrame(rows)).iloc[0]


def test_an_eight_pitch_walk_pays_for_every_pitch_past_the_fifth():
    """Josh's example. The grind is the point, so it compounds."""
    rows = [_pitch(1, 1, n, description="foul") for n in range(1, 8)]
    rows.append(_pitch(1, 1, 8, description="ball", events="walk"))
    c = _credits(rows)
    assert c["extra_pitch"] == 3, (
        f"pitches 6, 7 and 8 are past the fifth; got {c['extra_pitch']}"
    )
    assert AWARD_CREDITS["hitter"]["extra_pitch"] > 0


def test_reaching_after_two_strikes_pays_once_not_once_per_pitch():
    """A PA-level credit on a long at-bat must not be multiplied by its length."""
    rows = [_pitch(1, 1, n, description="foul", strikes=2) for n in range(1, 7)]
    rows.append(_pitch(1, 1, 7, description="ball", strikes=2, events="single"))
    c = _credits(rows)
    assert c["survived_two_strikes"] == 1, (
        f"credited once per plate appearance, got {c['survived_two_strikes']}"
    )


def test_taking_strike_three_is_a_debit():
    rows = [_pitch(1, 1, 1, description="called_strike", strikes=2,
                   events="strikeout")]
    c = _credits(rows)
    assert c["called_strike_three"] == 1
    assert AWARD_CREDITS["hitter"]["called_strike_three"] < 0, (
        "taking strike three in a big spot is the anti-dawg at-bat"
    )


def test_strikes_with_traffic_only_count_with_traffic():
    """The closer 'pumping strikes' -- but only when it costs him something."""
    empty = [_pitch(1, 1, 1, zone=5)]
    loaded = [_pitch(2, 1, 1, zone=5, on_1b=1.0, on_2b=2.0)]
    assert _credits(empty)["zone_with_traffic"] == 0
    assert _credits(loaded)["zone_with_traffic"] == 1
    # And a pitch outside the zone earns nothing even with men on.
    nibble = [_pitch(3, 1, 1, zone=13, on_1b=1.0, on_2b=2.0)]
    assert _credits(nibble)["zone_with_traffic"] == 0


def test_inside_is_learned_from_hit_batsmen_not_hardcoded():
    """A sign error here would reward pitching AWAY and call it courage.

    Both stances are fed HBPs on opposite sides of the plate, which is what
    the real convention looks like, and the orientation has to come back
    opposite for the two of them.
    """
    rows = []
    for i in range(30):
        rows.append(_pitch(1, i + 1, 1, stand="R", plate_x=-0.9,
                           events="hit_by_pitch", description="hit_by_pitch"))
        rows.append(_pitch(2, i + 1, 1, stand="L", plate_x=+0.9,
                           events="hit_by_pitch", description="hit_by_pitch"))
    signs = awards.inside_sign(pd.DataFrame(rows))
    assert signs["R"] == -1.0 and signs["L"] == +1.0, signs

    # Too few hit batsmen to orient: the credit is dropped, never guessed.
    assert awards.inside_sign(pd.DataFrame(rows[:4])) == {}


def test_working_inside_needs_a_same_handed_hitter_and_real_depth():
    base = dict(stand="R", p_throws="R", zone=5)
    hbps = [_pitch(9, i + 1, 1, stand="R", plate_x=-0.9,
                   events="hit_by_pitch", description="hit_by_pitch")
            for i in range(25)]
    inside = _credits(hbps + [_pitch(1, 1, 1, plate_x=-0.7, **base)])
    outside = _credits(hbps + [_pitch(1, 1, 1, plate_x=+0.7, **base)])
    opposite = _credits(hbps + [_pitch(1, 1, 1, plate_x=-0.7,
                                       stand="L", p_throws="R", zone=5)])
    assert inside["inside_same_hand"] == 1
    assert outside["inside_same_hand"] == 0, "the outer half is not courage"
    assert opposite["inside_same_hand"] == 0, "same-handed only"


def test_leverage_is_damped_not_linear():
    """A six-times-leverage moment must not be worth six times the credit.

    The empirical leverage index runs to 6. Multiplying a six-point jam
    escape by that put 68 points on a single plate appearance -- more than a
    walk-off homer is worth in win probability. Square-rooting keeps the
    ordering and caps the tail at 2x.
    """
    def one(li):
        return awards._process_points(pd.DataFrame([
            _pitch(1, 1, 1, li=li, zone=5, on_1b=1.0, on_2b=2.0)
        ])).iloc[0]["proc_pitcher"]

    neutral, huge = one(1.0), one(6.0)
    assert huge > neutral, "leverage must still matter"
    assert huge <= neutral * 2.01, (
        f"leverage is capped at 2x on a credit; got {huge / neutral:.2f}x"
    )


def test_credit_columns_are_counts_not_weighted_values():
    """The site prints these as counts, so they have to be counts.

    Scoring uses a leverage-weighted copy internally; if the weighting leaked
    into the column the page reports, "3 jams escaped" would silently become
    "4.7 jams escaped" in a high-leverage inning.
    """
    c = _credits([_pitch(1, 1, 1, li=4.0, zone=5, on_1b=1.0, on_2b=2.0)])
    assert c["zone_with_traffic"] == 1.0, c["zone_with_traffic"]


def test_the_weighted_counts_survive_for_the_points_column():
    """Scoring needs the weighted copy; so does the card.

    These used to be dropped the moment proc_hitter/proc_pitcher were
    summed. Without them a card can say "22 strikes with men on" but not
    what those 22 were worth, which is the number a reader actually weighs
    against the win probability beside it.
    """
    out = awards._process_points(pd.DataFrame([
        _pitch(1, 1, 1, li=4.0, zone=5, on_1b=1.0, on_2b=2.0)
    ])).iloc[0]
    assert out[f"{awards.CW}zone_with_traffic"] > out["zone_with_traffic"], (
        "the weighted copy should carry the leverage the count does not")


def test_a_credit_is_worth_its_value_times_its_weighted_count():
    """The points column is the count, the credit's value, and nothing else.

    Checked at neutral leverage where the damping multiplier is exactly 1,
    so the arithmetic is visible: one strike with men on at 1x leverage is
    worth exactly AWARD_CREDITS["pitcher"]["zone_with_traffic"].
    """
    out = awards._process_points(pd.DataFrame([
        _pitch(1, 1, 1, li=1.0, zone=5, on_1b=1.0, on_2b=2.0)
    ])).iloc[0]
    w = out[f"{awards.CW}zone_with_traffic"]
    assert abs(w - 1.0) < 1e-9, f"damping should be 1x at li=1, got {w}"
    # proc_pitcher is that weighted count times the credit's value, summed
    # over every credit that fired -- which here is this one and the
    # first-pitch strike it also was.
    expected = sum(out[f"{awards.CW}{c}"] * v
                   for c, v in AWARD_CREDITS["pitcher"].items()
                   if f"{awards.CW}{c}" in out.index)
    assert abs(out["proc_pitcher"] - expected) < 1e-9


def test_points_and_counts_are_different_columns():
    # A high-leverage credit must show the same COUNT and a bigger POINTS
    # figure. Conflating them is how "3 jams escaped" becomes "4.7".
    def one(li):
        return awards._process_points(pd.DataFrame([
            _pitch(1, 1, 1, li=li, zone=5, on_1b=1.0, on_2b=2.0)
        ])).iloc[0]

    lo, hi = one(1.0), one(4.0)
    assert lo["zone_with_traffic"] == hi["zone_with_traffic"] == 1.0
    assert hi[f"{awards.CW}zone_with_traffic"] > lo[f"{awards.CW}zone_with_traffic"]


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"  PASS  {fn.__name__}")
        except Exception:
            failed += 1; print(f"  FAIL  {fn.__name__}"); traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
