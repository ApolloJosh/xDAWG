"""Property tests for the xDAWG math.

These check the things that would silently produce a wrong leaderboard
rather than an exception -- the failure mode that actually matters here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xdawg.aggregate import compute, shrink, zscore  # noqa: E402
from xdawg.config import COMPONENTS, SCALE  # noqa: E402
from xdawg.fight import fight_weight, pythag  # noqa: E402
from xdawg.leverage import weighted_delta  # noqa: E402

RNG = np.random.default_rng(7)


def test_zscore_handles_zero_variance():
    assert (zscore(pd.Series([3.0] * 10)) == 0).all()


def test_shrink_pulls_small_samples_toward_zero():
    z = pd.Series([2.0, 2.0])
    n = pd.Series([10.0, 10_000.0])
    out = shrink(z, n, k=100.0)
    assert out.iloc[0] < 0.3, "tiny sample should be heavily regressed"
    assert out.iloc[1] > 1.9, "huge sample should be nearly untouched"


def test_weighted_delta_is_zero_under_uniform_weights():
    """The single most important property.

    If leverage is flat, the leverage-weighted mean equals the flat mean, so
    every delta must be zero. A non-zero result here would mean xDAWG is
    picking up raw talent instead of change-under-pressure -- exactly the
    failure the whole design is built to avoid.
    """
    df = pd.DataFrame({
        "batter": np.repeat([1, 2, 3], 100),
        "v": RNG.normal(0, 1, 300),
        "li": 1.0,
    })
    out = weighted_delta(df, "batter", "v")
    assert np.allclose(out["delta"], 0.0, atol=1e-12)


def test_weighted_delta_detects_a_real_clutch_signal():
    """A hitter who genuinely improves under leverage must score positive.

    Half the pitches come at li=2.5 carrying a +0.6 mean shift, half at
    li=0.5 with none, so the expected answer is analytic: the flat mean is
    0.3, the leverage-weighted mean is (2.5 * 0.6) / (2.5 + 0.5) = 0.5, and
    the delta is 0.2. Asserting a band around that rather than a bare floor
    catches the estimate drifting high as well as low.

    Uses its own generator instead of the module-level RNG: sharing one
    generator makes every test's draws depend on which tests ran before it,
    so a test can pass in a full run and fail when run alone. n is also
    large enough that sampling error is small next to the band -- at the
    original n=400 the estimate fell outside it on roughly one seed in ten,
    which made this test a coin flip rather than a check.
    """
    rng = np.random.default_rng(20260818)
    n = 8_000
    li = rng.choice([0.5, 2.5], n)
    v = np.where(li > 1, rng.normal(0.6, 1, n), rng.normal(0.0, 1, n))
    df = pd.DataFrame({"batter": 1, "v": v, "li": li})

    delta = weighted_delta(df, "batter", "v")["delta"].iloc[0]
    assert 0.15 < delta < 0.25, f"expected a delta near 0.2, got {delta:.4f}"


def test_scale_is_centered_on_100():
    role = "hitter"
    n = 300
    cols = {}
    for comps in COMPONENTS[role].values():
        for c, cfg in comps.items():
            cols[c] = RNG.normal(0, 1, n)
            cols[f"{c}__n"] = np.full(n, cfg["k"] * 50.0)  # negligible shrinkage
    df = pd.DataFrame(cols)
    df["opportunities"] = 500.0
    out = compute(df, role)

    assert abs(out["xDAWG"].mean() - 100) < 1.0
    assert abs(out["xDAWG"].std(ddof=0) - SCALE) < 1.5
    assert out["xDAWG"].is_monotonic_decreasing, "output must be sorted"


def test_fight_weight_ordering():
    """Beating a bad team must count for LESS than a neutral game."""
    quality = pd.Series({"GOOD": 2.0, "AVG": 0.0, "BAD": -2.0})
    opp = pd.Series(["GOOD", "AVG", "BAD"])
    own = pd.Series(["NYY", "NYY", "NYY"])
    pct = pd.Series([0.1, 0.1, 0.1])

    w = fight_weight(opp, own, pct, quality)
    assert w.iloc[0] > w.iloc[1] > w.iloc[2]
    assert w.iloc[2] < 1.0, "a bad opponent must score below neutral, not merely equal"


def test_fight_weight_division_and_late_season_stack():
    quality = pd.Series({"BOS": 1.5})
    base = fight_weight(pd.Series(["BOS"]), pd.Series(["LAD"]),
                        pd.Series([0.1]), quality).iloc[0]
    divis = fight_weight(pd.Series(["BOS"]), pd.Series(["NYY"]),
                         pd.Series([0.1]), quality).iloc[0]
    septem = fight_weight(pd.Series(["BOS"]), pd.Series(["NYY"]),
                          pd.Series([1.0]), quality).iloc[0]
    assert divis > base, "division games must weigh more"
    assert septem > divis, "late-season stakes must stack on top"
    assert septem / base > 1.5


def test_pythag_is_sane():
    assert abs(pythag(700, 700) - 0.5) < 1e-9
    assert pythag(900, 600) > 0.66
    assert pythag(600, 900) < 0.34


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
