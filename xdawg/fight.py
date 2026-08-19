"""
FIGHT -- the opponent-quality engine.

Every plate appearance gets a weight reflecting how much the opponent, the
division, and the calendar made it matter. Beating up on a last-place club
in April is worth roughly a sixth of a September division game against a
100-win team.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import FIGHT, TEAMS


def pythag(rs: float, ra: float, exp: float | None = None) -> float:
    """Pythagorean win expectation -- more stable than actual win%."""
    exp = exp or FIGHT["pythag_exponent"]
    if rs <= 0 and ra <= 0:
        return 0.5
    return rs ** exp / (rs ** exp + ra ** exp)


def opponent_quality(standings: pd.DataFrame) -> pd.Series:
    """Z-scored Pythagorean win% by team. `standings` needs team/rs/ra."""
    py = standings.apply(lambda r: pythag(r["rs"], r["ra"]), axis=1)
    sd = py.std(ddof=0)
    q = (py - 0.500) / sd if sd > 0 else py * 0.0
    return pd.Series(q.values, index=standings["team"].values, name="Q")


ALL_TERMS = ("quality", "division", "late")


def fight_weight(
    opp_team: pd.Series,
    player_team: pd.Series,
    season_pct: pd.Series,
    quality: pd.Series,
    stakes: pd.Series | None = None,
    terms: tuple[str, ...] = ALL_TERMS,
) -> pd.Series:
    """Multiplicative FIGHT weight per event.

        PT = (1 + A*Q) * (1 + B*DIV) * (1 + C*LATE*STAKES)

    Q is deliberately NOT clamped at zero, so facing a bad team scores below
    a neutral game rather than merely failing to add credit.

    `terms` selects which multiplicands apply. Blending opponent quality and
    divisional rivalry into one number made them impossible to read apart on
    the site -- beating the best team in the league and beating the team you
    play nineteen times are different kinds of dawg, so FIGHT now computes a
    delta under each weighting separately. The late-season ramp rides along
    with both, because September matters to each of them equally.
    """
    q = opp_team.map(quality).fillna(0.0)

    div_opp = opp_team.map(lambda t: TEAMS.get(t, (None, None))[1])
    div_ply = player_team.map(lambda t: TEAMS.get(t, (None, None))[1])
    is_div = (div_opp == div_ply) & div_opp.notna()

    start = FIGHT["late_season_start_pct"]
    late = ((season_pct - start) / (1.0 - start)).clip(0.0, 1.0)
    if stakes is None:
        stakes = pd.Series(1.0, index=opp_team.index)

    w = pd.Series(1.0, index=opp_team.index)
    if "quality" in terms:
        w = w * (1.0 + FIGHT["opponent_quality_coef"] * q)
    if "division" in terms:
        w = w * (1.0 + FIGHT["division_coef"] * is_div.astype(float))
    if "late" in terms:
        w = w * (1.0 + FIGHT["late_stakes_coef"] * late * stakes.fillna(0.0))
    return w.clip(lower=FIGHT["min_weight"])


def fight_delta(
    events: pd.DataFrame,
    group: str,
    value: str,
    weight_col: str = "fight_w",
    min_n: int = 40,
) -> pd.DataFrame:
    """FIGHT-weighted mean minus flat mean, per player.

    Note this pillar is allowed to use OUTCOMES rather than process, unlike
    BITE/HUNT. The justification is sample size: high-leverage PA number
    ~80 a season, but divisional and quality-opponent PA number 300-400 --
    four to five times the sample, close enough to stabilization that run
    value carries real signal.

    Returns both baselines, exactly as `leverage.weighted_delta` does:
    `delta` against the player's own flat mean, `league_delta` against the
    league's.
    """
    d = events[[group, value, weight_col]].dropna()
    if d.empty:
        return pd.DataFrame(columns=[group, "delta", "league_delta", "n"])

    league_flat = float(d[value].mean())
    d["_wv"] = d[value] * d[weight_col]
    g = d.groupby(group).agg(
        _sum_wv=("_wv", "sum"),
        _sum_w=(weight_col, "sum"),
        flat=(value, "mean"),
        n=(value, "size"),
    )
    g = g[g["n"] >= min_n]
    weighted = g["_sum_wv"] / g["_sum_w"]
    g["delta"] = weighted - g["flat"]
    g["league_delta"] = weighted - league_flat
    return g.reset_index()[[group, "delta", "league_delta", "n"]]
