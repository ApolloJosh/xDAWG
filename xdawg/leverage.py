"""
Empirical Leverage Index.

Rather than shipping Tom Tango's static LI table, we derive LI from the data
itself. For every game state (inning, half, outs, base state, clipped score
differential) we measure the mean absolute win-expectancy swing produced by a
plate appearance from that state, then normalize so the league mean is 1.0.

That is precisely what leverage means -- how much the game can move right
now -- and computing it in-house means the numbers track the actual run
environment of the season being measured instead of a table baked in 2006.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import LEVERAGE


def _base_state(df: pd.DataFrame) -> pd.Series:
    """Encode occupied bases as a 3-bit integer, 0 (empty) through 7 (loaded)."""
    on1 = df["on_1b"].notna().astype(int)
    on2 = df["on_2b"].notna().astype(int)
    on3 = df["on_3b"].notna().astype(int)
    return on1 + 2 * on2 + 4 * on3


def add_leverage(pitches: pd.DataFrame) -> pd.DataFrame:
    """Attach a per-pitch `li` column to a Statcast pitch-level frame.

    Requires: inning, inning_topbot, outs_when_up, on_1b/2b/3b, bat_score,
    fld_score, delta_home_win_exp, game_pk, at_bat_number.
    """
    df = pitches.copy()
    clip = LEVERAGE["score_diff_clip"]

    df["_base"] = _base_state(df)
    df["_score_diff"] = (df["bat_score"] - df["fld_score"]).clip(-clip, clip)
    df["_inning"] = df["inning"].clip(upper=10)  # extras collapse into "10"

    # One row per plate appearance: total WE swing attributable to that PA.
    pa = (
        df.groupby(["game_pk", "at_bat_number"], sort=False)
        .agg(
            swing=("delta_home_win_exp", "sum"),
            _inning=("_inning", "first"),
            inning_topbot=("inning_topbot", "first"),
            outs_when_up=("outs_when_up", "first"),
            _base=("_base", "first"),
            _score_diff=("_score_diff", "first"),
        )
        .reset_index()
    )
    pa["abs_swing"] = pa["swing"].abs()

    state_cols = ["_inning", "inning_topbot", "outs_when_up", "_base", "_score_diff"]

    # Fine-grained state average, with a coarser fallback for thin states.
    fine = pa.groupby(state_cols)["abs_swing"].agg(["mean", "size"])
    coarse_cols = ["_inning", "outs_when_up", "_base"]
    coarse = pa.groupby(coarse_cols)["abs_swing"].mean().rename("coarse_mean")

    fine = fine.reset_index().merge(coarse.reset_index(), on=coarse_cols, how="left")
    thin = fine["size"] < LEVERAGE["min_state_sample"]
    fine["state_mean"] = np.where(thin, fine["coarse_mean"], fine["mean"])

    league_mean = pa["abs_swing"].mean()
    if not np.isfinite(league_mean) or league_mean <= 0:
        df["li"] = 1.0
        return df.drop(columns=["_base", "_score_diff", "_inning"])

    fine["li"] = (fine["state_mean"] / league_mean).clip(upper=LEVERAGE["max_li"])

    out = df.merge(
        fine[state_cols + ["li"]], on=state_cols, how="left", validate="many_to_one"
    )
    out["li"] = out["li"].fillna(1.0)
    return out.drop(columns=["_base", "_score_diff", "_inning"])


def weighted_delta(
    df: pd.DataFrame,
    group: str,
    value: str,
    weight: str = "li",
    min_n: int = 20,
) -> pd.DataFrame:
    """The core xDAWG operation: leverage-weighted mean minus flat mean.

    This is what makes the metric talent-neutral. Because each player is
    compared against HIS OWN baseline, park effects, era, and raw ability
    cancel out algebraically -- what survives is only the change under
    pressure.
    """
    d = df[[group, value, weight]].dropna()
    if d.empty:
        return pd.DataFrame(columns=[group, "delta", "n"])

    d["_wv"] = d[value] * d[weight]
    g = d.groupby(group).agg(
        _sum_wv=("_wv", "sum"),
        _sum_w=(weight, "sum"),
        flat=(value, "mean"),
        n=(value, "size"),
    )
    g = g[g["n"] >= min_n]
    g["delta"] = (g["_sum_wv"] / g["_sum_w"]) - g["flat"]
    return g.reset_index()[[group, "delta", "n"]]
