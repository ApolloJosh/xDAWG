"""
Z-scoring, shrinkage, pillar rollup, and the final xDAWG+ / DAWG scale.

This module is deliberately generic: it reads the component definitions out
of config.py and knows nothing about baseball. Adding or removing a component
means editing config, not this file.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import COMPONENTS, PILLAR_WEIGHTS, SCALE


def zscore(s: pd.Series) -> pd.Series:
    """Standardize, tolerating degenerate (zero-variance) input."""
    sd = s.std(ddof=0)
    if not np.isfinite(sd) or sd == 0:
        return pd.Series(0.0, index=s.index)
    return (s - s.mean()) / sd


def shrink(z: pd.Series, n: pd.Series, k: float) -> pd.Series:
    """Regress toward the mean by sample size.

    Without this, a player with 12 high-leverage plate appearances and one
    good night tops every leaderboard. `k` is the component's stabilization
    constant -- the opportunity count at which we apply half weight.
    """
    n = n.reindex(z.index).fillna(0.0)
    return z * (n / (n + k))


def score_pillar(
    df: pd.DataFrame, role: str, pillar: str, self_only: bool = False,
    raw: bool = False,
) -> tuple[pd.Series, pd.DataFrame]:
    """Roll a pillar's components into one shrunk, standardized score.

    Components absent from `df` are skipped and the remaining weights are
    renormalized, so a missing Savant leaderboard degrades the metric
    gracefully instead of crashing the run.

    `self_only` drops every component that has no self-referenced version --
    the ones measured on absolute level, which ship one number and no
    `<name>__lg` twin because there is no split to take. Those are exactly
    right for DAWG+, which is supposed to include how good you are, and
    wrong for wDAWG+, which claims to measure only how much you beat your
    OWN baseline when it mattered. They were 26% of a hitter's wDAWG+ and
    25% of a pitcher's, identical in both stats.

    That mattered most for the players the two numbers disagree about. A
    hitter with no real clutch CHANGE has his self-referenced deltas sit
    near zero however great he is -- so that leftover quarter decided his
    whole wDAWG+, and for a slugger who is slow and does not field, wDAWG+
    stopped being "does he rise to the moment" and became a baserunning and
    availability score. Dropping them and renormalizing makes the two stats
    genuinely different questions instead of 26% the same one.
    """
    spec = COMPONENTS[role][pillar]
    parts, weights, detail = [], [], {}

    for name, cfg in spec.items():
        if name not in df.columns:
            continue
        if self_only and f"{name}{LEAGUE_SUFFIX}" not in df.columns:
            continue
        # Named `vals`, not `raw`: this used to be `raw`, which silently
        # shadowed the `raw` argument added above and turned the return
        # expression into a truth test on a Series.
        vals = pd.to_numeric(df[name], errors="coerce")
        if vals.notna().sum() < 2:
            continue

        z = zscore(vals.fillna(vals.mean()))
        if cfg["invert"]:
            z = -z

        n_col = f"{name}__n"
        n = pd.to_numeric(df[n_col], errors="coerce") if n_col in df.columns \
            else pd.Series(float(cfg["k"]), index=df.index)

        zs = shrink(z, n, float(cfg["k"]))
        detail[name] = zs
        parts.append(zs * cfg["weight"])
        weights.append(cfg["weight"])

    if not parts:
        return pd.Series(0.0, index=df.index), pd.DataFrame(index=df.index)

    total = sum(parts) / sum(weights)
    # `raw` returns the shrunk weighted average WITHOUT the final
    # standardization. That final zscore is right for a season -- it is what
    # puts pillars on a common scale -- but it also re-inflates a pillar to
    # sd 1 no matter how little evidence went into it. Over a single day,
    # every component has been shrunk to nearly nothing and the honest
    # answer is "we know almost nothing," which only the raw total says. The
    # season leaderboard is unaffected: it never asks for raw.
    return (total if raw else zscore(total)), pd.DataFrame(detail)


LEAGUE_SUFFIX = "__lg"


def league_view(df: pd.DataFrame, role: str) -> pd.DataFrame:
    """Swap in the league-baselined version of every component that has one.

    Components come in two flavours. A leverage delta is computed against
    the player's OWN flat mean, and its `<name>__lg` twin against the
    league's -- those get swapped here. Everything measured on absolute
    level already (hustle, availability, OAA) has no separate league
    version, because z-scoring across players is itself a comparison to the
    league; those pass through untouched and read the same in both stats.
    """
    out = df.copy()
    for comps in COMPONENTS[role].values():
        for name in comps:
            lg = f"{name}{LEAGUE_SUFFIX}"
            if lg in out.columns:
                out[name] = out[lg]
    return out


def compute(
    df: pd.DataFrame,
    role: str,
    rate_name: str = "DAWG+",
    count_name: str = "DAWG",
    self_only: bool = False,
) -> pd.DataFrame:
    """Full pipeline: components -> pillars -> a rate stat and a counting stat.

    `df` is one row per player with raw component columns (and optional
    `<component>__n` opportunity counts). Returns the scored frame.
    """
    out = df.copy()
    pillar_scores, details = {}, {}

    for pillar in ("bite", "grit", "hunt", "fight"):
        score, detail = score_pillar(df, role, pillar, self_only=self_only)
        pillar_scores[pillar] = score
        out[pillar.upper()] = score
        for c in detail.columns:
            details[f"_c_{c}"] = detail[c]

    for c, v in details.items():
        out[c] = v

    w = PILLAR_WEIGHTS[role]
    z_total = sum(pillar_scores[p] * w[p] for p in w) / sum(w.values())
    z_total = zscore(z_total)

    out["z_total"] = z_total
    out[rate_name] = 100.0 + SCALE * z_total

    # Counting version. Zero is LEAGUE AVERAGE here, not replacement level --
    # so a negative score means actively not-a-dawg, which is both useful
    # and funny.
    opp = pd.to_numeric(out.get("opportunities", pd.Series(1.0, index=out.index)),
                        errors="coerce").fillna(0.0)
    mean_opp = opp[opp > 0].mean()
    out[count_name] = z_total * (opp / mean_opp if mean_opp else 1.0)

    return out.sort_values(rate_name, ascending=False).reset_index(drop=True)
