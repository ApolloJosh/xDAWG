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

    Returns BOTH baselines, because they are the same computation with a
    different subtrahend and running the pipeline twice to get them would
    double the expensive part for nothing:

      delta         weighted mean minus THIS PLAYER'S flat mean. Talent
                    cancels algebraically, so what survives is only the
                    change under pressure. Feeds wDAWG+.
      league_delta  weighted mean minus the LEAGUE's flat mean. Talent does
                    not cancel, so this blends "how good" with "when".
                    Feeds DAWG+.

    The two differ by exactly the player's own level above league average,
    which is the quantity xDAWG was originally built to throw away.
    """
    d = df[[group, value, weight]].dropna()
    if d.empty:
        return pd.DataFrame(columns=[group, "delta", "league_delta", "n"])

    league_flat = float(d[value].mean())
    d["_wv"] = d[value] * d[weight]
    g = d.groupby(group).agg(
        _sum_wv=("_wv", "sum"),
        _sum_w=(weight, "sum"),
        flat=(value, "mean"),
        n=(value, "size"),
    )
    g = g[g["n"] >= min_n]
    weighted = g["_sum_wv"] / g["_sum_w"]
    g["delta"] = weighted - g["flat"]
    g["league_delta"] = weighted - league_flat
    return g.reset_index()[[group, "delta", "league_delta", "n"]]


def clutch(
    pa: pd.DataFrame,
    group: str,
    wpa: str = "wpa",
    weight: str = "li",
    min_n: int = 100,
) -> pd.DataFrame:
    """FanGraphs' Clutch, computed in-house rather than scraped.

        Clutch = WPA / pLI  -  WPA/LI

    where pLI is the player's mean leverage and WPA/LI is his context-neutral
    win contribution, the sum of each plate appearance's WPA divided by the
    leverage it occurred in. The difference is how much better he did when it
    mattered than his own overall production predicts.

    Why compute it instead of pulling it: FanGraphs 403s datacentre IPs, so a
    CI build cannot reach it -- and our leverage index is derived empirically
    from the season being measured rather than from a static table, so the
    in-house version is actually the better-matched one.

    Note WPA already has leverage baked in (a homer in a tie game moves win
    expectancy far more than the same swing in a blowout), which is why this
    DIVIDES by leverage to neutralize it rather than multiplying the way
    `weighted_delta` does. Multiplying would count leverage twice.

    Returned as a per-plate-appearance rate so it does not double as a
    playing-time proxy. Under flat leverage it is exactly zero, the same
    invariant `weighted_delta` holds -- if that ever breaks, this has started
    measuring raw production instead of production-under-pressure.
    """
    d = pa[[group, wpa, weight]].copy()
    d[wpa] = pd.to_numeric(d[wpa], errors="coerce")
    d[weight] = pd.to_numeric(d[weight], errors="coerce")
    d = d.dropna()
    d = d[d[weight] > 0]
    if d.empty:
        return pd.DataFrame(columns=[group, "wpa_clutch_delta",
                                     "wpa_clutch_delta__n"])

    d["_neutral"] = d[wpa] / d[weight]
    g = d.groupby(group).agg(
        _wpa=(wpa, "sum"), _pli=(weight, "mean"),
        _neutral=("_neutral", "sum"), n=(wpa, "size"),
    )
    g = g[g["n"] >= min_n]
    if g.empty:
        return pd.DataFrame(columns=[group, "wpa_clutch_delta",
                                     "wpa_clutch_delta__n"])

    leveraged = (g["_wpa"] / g["_pli"].where(g["_pli"] > 0)) / g["n"]
    g["wpa_clutch_delta"] = (leveraged - g["_neutral"] / g["n"]).fillna(0.0)
    # League-relative variant: the same leverage-normalized production, but
    # measured against what an average hitter produces per plate appearance
    # instead of against the player's own context-neutral rate. Talent stays
    # in, which is the whole point of the DAWG+ family.
    league_neutral = float(d["_neutral"].mean())
    g["wpa_clutch_delta__lg"] = (leveraged - league_neutral).fillna(0.0)
    return g.reset_index().rename(columns={"n": "wpa_clutch_delta__n"})[
        [group, "wpa_clutch_delta", "wpa_clutch_delta__lg",
         "wpa_clutch_delta__n"]
    ]
