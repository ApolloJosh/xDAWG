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
    df: pd.DataFrame, role: str, pillar: str
) -> tuple[pd.Series, pd.DataFrame]:
    """Roll a pillar's components into one shrunk, standardized score.

    Components absent from `df` are skipped and the remaining weights are
    renormalized, so a missing Savant leaderboard degrades the metric
    gracefully instead of crashing the run.
    """
    spec = COMPONENTS[role][pillar]
    parts, weights, detail = [], [], {}

    for name, cfg in spec.items():
        if name not in df.columns:
            continue
        raw = pd.to_numeric(df[name], errors="coerce")
        if raw.notna().sum() < 2:
            continue

        z = zscore(raw.fillna(raw.mean()))
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
    return zscore(total), pd.DataFrame(detail)


def compute(df: pd.DataFrame, role: str) -> pd.DataFrame:
    """Full pipeline: components -> pillars -> xDAWG+ and DAWG.

    `df` is one row per player with raw component columns (and optional
    `<component>__n` opportunity counts). Returns the scored frame.
    """
    out = df.copy()
    pillar_scores, details = {}, {}

    for pillar in ("bite", "grit", "hunt", "fight"):
        score, detail = score_pillar(df, role, pillar)
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
    out["xDAWG"] = 100.0 + SCALE * z_total

    # Counting version. Zero is LEAGUE AVERAGE here, not replacement level --
    # so a negative DAWG means actively not-a-dawg, which is both useful
    # and funny.
    opp = pd.to_numeric(out.get("opportunities", pd.Series(1.0, index=out.index)),
                        errors="coerce").fillna(0.0)
    mean_opp = opp[opp > 0].mean()
    out["DAWG"] = z_total * (opp / mean_opp if mean_opp else 1.0)

    return out.sort_values("xDAWG", ascending=False).reset_index(drop=True)
