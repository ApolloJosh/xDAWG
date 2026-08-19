"""The metric must not charge a player for not being a different player.

A speedster posts speed numbers and a slugger posts power numbers, and
neither fact is a statement about whether the man competes. These check the
two places that principle is enforced -- the baserunning baseline and the
scope of the wDAWG family -- both of which fail silently by producing a
plausible-looking number rather than by raising.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xdawg.aggregate import score_pillar  # noqa: E402
from xdawg.config import COMPONENTS  # noqa: E402
from xdawg.pillars.hitters import _speed_buckets, xbt_frame  # noqa: E402

RNG = np.random.default_rng(4242)

BURNER = 601_001    # top speed group, advances a lot
PLODDER = 601_002   # bottom speed group, advances rarely -- but no less than
                    # any other slow runner, which is the whole point


def _bases(n_games: int = 900) -> pd.DataFrame:
    """Singles with a runner on first, and where that runner ended up.

    Built as consecutive plate appearances in a game, because that is how
    `xbt_frame` recovers baserunning -- from how the runner ids on base
    change between one PA and the next.
    """
    rows = []
    # A league of ordinary runners in five speed groups, so the buckets have
    # something to be cut from and the per-cell minimum is cleared.
    pool = list(range(602_000, 602_120))
    speeds = {}
    for i, pid in enumerate(pool):
        speeds[pid] = 24.0 + (i % 5) * 1.5          # five distinct tiers
    speeds[BURNER] = 30.5
    speeds[PLODDER] = 24.0

    def advance_prob(pid):
        # Advancement is PURELY a function of speed here: nobody in this
        # league has any baserunning instinct at all. Every runner is exactly
        # as aggressive as his wheels, so a speed-fair measure must score
        # every one of them at zero.
        return 0.10 + (speeds[pid] - 24.0) / 6.5 * 0.55

    game = 0
    for pid in pool + [BURNER, PLODDER]:
        reps = 40 if pid in (BURNER, PLODDER) else 12
        for _ in range(reps):
            game += 1
            took = RNG.random() < advance_prob(pid)
            # PA 1: he is on first, a single is hit.
            rows.append({"game_pk": game, "at_bat_number": 1, "pitch_number": 1,
                         "on_1b": float(pid), "on_2b": np.nan, "on_3b": np.nan,
                         "events": "single"})
            # PA 2: where he is standing now tells us what he did.
            rows.append({"game_pk": game, "at_bat_number": 2, "pitch_number": 1,
                         "on_1b": np.nan,
                         "on_2b": np.nan if took else float(pid),
                         "on_3b": float(pid) if took else np.nan,
                         "events": "field_out"})
    return pd.DataFrame(rows)


def _sprint(df: pd.DataFrame) -> pd.DataFrame:
    ids = sorted(set(pd.concat([df["on_1b"], df["on_2b"], df["on_3b"]])
                     .dropna().astype(int)))
    speeds = []
    for pid in ids:
        if pid == BURNER: speeds.append(30.5)
        elif pid == PLODDER: speeds.append(24.0)
        else: speeds.append(24.0 + ((pid - 602_000) % 5) * 1.5)
    return pd.DataFrame({"batter": ids, "sprint_speed": speeds})


def test_speed_buckets_split_the_league():
    s = pd.DataFrame({"batter": range(100),
                      "sprint_speed": np.linspace(23, 31, 100)})
    b = _speed_buckets(s)
    assert set(b.dropna().unique()) == {0.0, 1.0, 2.0, 3.0, 4.0}
    assert b.loc[0] == 0 and b.loc[99] == 4, "slowest and fastest must anchor the ends"


def test_baserunning_no_longer_charges_a_man_for_being_slow():
    """The regression test for the Alvarez complaint.

    In this league advancement is purely a function of speed -- literally
    nobody has any baserunning skill. The unadjusted baseline reads that as
    the slow runner being bad and the fast one being good. The speed-group
    baseline correctly reads both as ordinary.
    """
    df = _bases()
    sprint = _sprint(df)

    naive = xbt_frame(df).set_index("batter")["extra_bases_taken"]
    fair = xbt_frame(df, sprint).set_index("batter")["extra_bases_taken"]

    assert naive.loc[PLODDER] < -0.15, (
        "fixture is only meaningful if the OLD baseline punished the slow "
        f"runner; got {naive.loc[PLODDER]:.3f}"
    )
    assert naive.loc[BURNER] > 0.15, "and rewarded the fast one"

    assert abs(fair.loc[PLODDER]) < 0.08, (
        "a slow runner who advances like other slow runners must score ~0, "
        f"got {fair.loc[PLODDER]:+.3f}"
    )
    assert abs(fair.loc[BURNER]) < 0.08, (
        "and speed alone must not earn credit either, "
        f"got {fair.loc[BURNER]:+.3f}"
    )


def test_baserunning_still_finds_a_genuinely_aggressive_slow_runner():
    """Speed-fair must not mean signal-free.

    The same plodder, except this one goes first-to-third far more often
    than his wheels predict. That is exactly the thing the component exists
    to catch, and it has to survive the adjustment.
    """
    df = _bases()
    sprint = _sprint(df)
    # Promote the plodder's advancement without touching his sprint speed.
    mask = (df["at_bat_number"] == 2) & (df["on_2b"] == float(PLODDER))
    flip = df.index[mask][: int(mask.sum() * 0.8)]
    df.loc[flip, "on_3b"] = float(PLODDER)
    df.loc[flip, "on_2b"] = np.nan

    fair = xbt_frame(df, sprint).set_index("batter")["extra_bases_taken"]
    assert fair.loc[PLODDER] > 0.25, (
        "a slow runner who genuinely takes the extra base must still score "
        f"well above zero, got {fair.loc[PLODDER]:+.3f}"
    )


def test_missing_sprint_speed_degrades_to_the_old_behaviour():
    df = _bases()
    with_none = xbt_frame(df, None).set_index("batter")["extra_bases_taken"]
    naive = xbt_frame(df).set_index("batter")["extra_bases_taken"]
    assert np.allclose(with_none.loc[PLODDER], naive.loc[PLODDER])


def _hitter_frame(n=200):
    """One row per player with every hitter component, deltas twinned."""
    cols = {}
    for pillar, comps in COMPONENTS["hitter"].items():
        for c, cfg in comps.items():
            if cfg["weight"] <= 0:
                continue
            cols[c] = RNG.normal(0, 1, n)
            cols[f"{c}__n"] = np.full(n, cfg["k"] * 50.0)
    # Only the leverage deltas carry a league twin; the absolute-level
    # measures have none, which is exactly what `self_only` keys on.
    for c in ("whiff_delta", "chase_contact", "ev_situational",
              "post_k_bounceback", "pitches_per_pa_delta",
              "two_strike_foul_delta", "wpa_clutch_delta",
              "contender_rv_delta", "division_rv_delta", "fight_process_delta"):
        cols[f"{c}__lg"] = RNG.normal(0, 1, n)
    return pd.DataFrame(cols)


ABSOLUTE = ("hustle_ratio", "hbp_above_expected", "extra_bases_taken",
            "availability", "oaa_situational")


def test_self_only_drops_the_absolute_components():
    df = _hitter_frame()
    for pillar in ("grit", "hunt"):
        _, full = score_pillar(df, "hitter", pillar)
        _, only = score_pillar(df, "hitter", pillar, self_only=True)
        for c in ABSOLUTE:
            if c in full.columns:
                assert c not in only.columns, (
                    f"{c} has no self-referenced reading and must not appear "
                    "in the wDAWG scoring"
                )
        assert len(only.columns) >= 1, f"{pillar} lost every component"


def test_self_only_renormalizes_rather_than_shrinking_the_pillar():
    """Dropping components must not quietly shrink the pillar toward zero.

    `score_pillar` divides by the weights it actually used, so a GRIT with
    two surviving components is still a full-strength pillar rather than one
    scaled to 35% of itself. If that renormalization broke, wDAWG+ would
    collapse toward 100 for everybody and look like it was working.
    """
    df = _hitter_frame()
    score, _ = score_pillar(df, "hitter", "grit", self_only=True)
    assert abs(score.std(ddof=0) - 1.0) < 1e-9, "pillar output must stay standardized"
    assert score.abs().max() > 1.0, "a renormalized pillar must still have spread"


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
