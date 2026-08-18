"""Hitter pillar computation from Statcast pitch-level data."""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from .. import ingest
from ..leverage import weighted_delta

WHIFFS = {"swinging_strike", "swinging_strike_blocked", "missed_bunt"}
SWINGS = WHIFFS | {"foul", "foul_tip", "hit_into_play", "foul_bunt"}


def tag_pitch_events(p: pd.DataFrame) -> pd.DataFrame:
    """Add boolean swing/whiff/chase/foul flags to a pitch frame."""
    d = p.copy()
    desc = d["description"].astype(str)

    d["is_swing"] = desc.isin(SWINGS)
    d["is_whiff"] = desc.isin(WHIFFS)
    d["is_foul"] = desc.isin({"foul", "foul_tip", "foul_bunt"})
    # Statcast zones 1-9 are in the strike zone; 11-14 are the outer shadow.
    d["out_of_zone"] = pd.to_numeric(d["zone"], errors="coerce") > 9
    d["is_chase"] = d["is_swing"] & d["out_of_zone"]
    d["chase_contact"] = d["is_chase"] & ~d["is_whiff"]
    d["two_strike"] = pd.to_numeric(d["strikes"], errors="coerce") == 2
    d["hard_hit"] = pd.to_numeric(d["launch_speed"], errors="coerce") >= 95.0
    return d


def bite(p: pd.DataFrame) -> pd.DataFrame:
    """BITE -- the extended at-bat.

    The organizing idea is that chasing is not the sin; whiffing is. A hitter
    who chases a 1-2 slider and fouls it off has won something -- he is still
    alive and the pitcher has to throw another one. So whiff carries the most
    weight, and surviving a chase is scored as a POSITIVE.
    """
    d = tag_pitch_events(p)
    g = "batter"
    out = None

    def merge(frame, col, nkey):
        nonlocal out
        frame = frame.rename(columns={"delta": col, "n": f"{col}__n"})
        out = frame if out is None else out.merge(frame, on=g, how="outer")

    # Whiff rate under leverage vs. own baseline (inverted in config).
    swings = d[d["is_swing"]]
    merge(weighted_delta(swings, g, "is_whiff", min_n=40), "whiff_delta", "n")

    # Of the pitches he DOES chase, how often does he survive them?
    chases = d[d["is_chase"]].copy()
    if not chases.empty:
        cc = chases.groupby(g).agg(
            chase_contact=("chase_contact", "mean"), n=("chase_contact", "size")
        ).reset_index()
        cc = cc.rename(columns={"n": "chase_contact__n"})
        out = cc if out is None else out.merge(cc, on=g, how="outer")

    # Grinding the count -- making him throw more pitches when it matters.
    pa = (
        d.groupby([g, "game_pk", "at_bat_number"])
        .agg(pitches=("pitch_number", "max"), li=("li", "first"))
        .reset_index()
    )
    merge(weighted_delta(pa, g, "pitches", min_n=30), "pitches_per_pa_delta", "n")

    # Refusing to be put away.
    ts = d[d["two_strike"] & d["is_swing"]]
    merge(weighted_delta(ts, g, "is_foul", min_n=30), "two_strike_foul_delta", "n")

    bip = d[d["launch_speed"].notna()]
    merge(weighted_delta(bip, g, "hard_hit", min_n=40), "hard_hit_delta", "n")

    return out if out is not None else pd.DataFrame(columns=[g])


def post_k_bounceback(p: pd.DataFrame) -> pd.DataFrame:
    """Run value in the plate appearance immediately after a strikeout.

    Short memory is a dawg trait, and it turns out to be directly computable.
    """
    pa = (
        p.groupby(["batter", "game_pk", "at_bat_number"])
        .agg(events=("events", "last"), rv=("delta_run_exp", "sum"))
        .reset_index()
        .sort_values(["batter", "game_pk", "at_bat_number"])
    )
    pa["prev_k"] = (
        pa.groupby("batter")["events"].shift(1).astype(str).eq("strikeout")
    )
    after = pa[pa["prev_k"]]
    if after.empty:
        return pd.DataFrame(columns=["batter", "post_k_bounceback", "post_k_bounceback__n"])

    base = pa.groupby("batter")["rv"].mean()
    g = after.groupby("batter").agg(rv=("rv", "mean"), n=("rv", "size")).reset_index()
    g["post_k_bounceback"] = g["rv"] - g["batter"].map(base)
    return g.rename(columns={"n": "post_k_bounceback__n"})[
        ["batter", "post_k_bounceback", "post_k_bounceback__n"]
    ]


def grit(
    sprint: pd.DataFrame | None,
    hbp: pd.DataFrame | None,
    xbt: pd.DataFrame | None,
    availability: pd.DataFrame | None,
) -> pd.DataFrame:
    """GRIT -- the only pillar measured on absolute level rather than delta.

    Hustle ratio is the centerpiece: home-to-first time on routine ground
    balls as a fraction of the player's OWN maximum sprint speed. That is a
    direct physical measurement of running it out, and it is self-referenced
    so fast players get no automatic credit.
    """
    frames = []
    if sprint is not None and not sprint.empty:
        s = sprint.copy()
        if {"hp_to_1b", "sprint_speed"}.issubset(s.columns):
            # Expected time at full effort, divided by actual. >1 = hustling.
            expected = 90.0 / s["sprint_speed"].replace(0, np.nan)
            s["hustle_ratio"] = (expected / s["hp_to_1b"]).replace([np.inf, -np.inf], np.nan)
            frames.append(s[["batter", "hustle_ratio"]])
    for f, col in ((hbp, "hbp_above_expected"), (xbt, "extra_bases_taken"),
                   (availability, "availability")):
        if f is not None and not f.empty and col in f.columns:
            frames.append(f[["batter", col]])

    if not frames:
        return pd.DataFrame(columns=["batter"])
    out = frames[0]
    for f in frames[1:]:
        out = out.merge(f, on="batter", how="outer")
    return out


def hunt(catches: pd.DataFrame | None, p: pd.DataFrame) -> pd.DataFrame:
    """HUNT -- the play that wins it.

    Statcast buckets batted balls by catch probability; 5-star plays are
    0-25% likely to be caught. We weight each one by the leverage at the
    moment of the play, so a diving grab in a tie game in the 9th counts
    enormously and the same catch in a blowout counts for almost nothing.

    We also credit ATTEMPTS on low-probability balls, not just conversions.
    Selling out for a ball you probably will not reach is the dawg part, and
    attempt rate is considerably less noisy than conversion.
    """
    if catches is None or catches.empty:
        return pd.DataFrame(columns=["batter"])

    c = catches.copy()

    # Savant renames these without notice, so resolve them rather than
    # indexing directly. A missing essential drops the whole term and lets
    # the pillar weights renormalize -- the same graceful degradation the
    # other optional leaderboards already get. Indexing directly instead
    # killed a forty-minute build at the very last step.
    id_col = ingest.pick_col(c, "player_id", "entity_id", "playerid", "mlbam_id")
    prob_col = ingest.pick_col(c, "catch_probability", "catch_prob", "catchprob")
    if id_col is None or prob_col is None:
        missing = [
            n for n, v in (("player id", id_col), ("catch probability", prob_col))
            if v is None
        ]
        warnings.warn(
            f"catch probability schema drift: no {' or '.join(missing)} in "
            f"{list(c.columns)}; HUNT star term dropped"
        )
        return pd.DataFrame(columns=["batter"])

    prob = pd.to_numeric(c[prob_col], errors="coerce")
    # Savant has served this as a 0-1 fraction and as a 0-100 percentage.
    top = prob.max(skipna=True)
    if pd.notna(top) and float(top) > 1.5:
        prob = prob / 100.0

    c["star"] = pd.cut(
        prob,
        bins=[-0.01, 0.25, 0.50, 0.75, 0.90, 1.01],
        labels=[5, 4, 3, 2, 1],
    ).astype(float)

    lev = c["li"] if "li" in c.columns else 1.0

    made_col = ingest.pick_col(c, "caught", "n_caught", "catches", "plays_made")
    made = (
        pd.to_numeric(c[made_col], errors="coerce").fillna(0.0)
        if made_col is not None else pd.Series(0.0, index=c.index)
    )
    att_col = ingest.pick_col(c, "attempted", "n_attempted", "opportunities", "n_opp")
    attempted = (
        pd.to_numeric(c[att_col], errors="coerce").fillna(1.0)
        if att_col is not None else pd.Series(1.0, index=c.index)
    )

    c["_value"] = made * c["star"] * lev
    c["_attempt"] = (c["star"] >= 4).astype(float) * attempted
    c["player_id"] = c[id_col]

    g = c.groupby("player_id").agg(
        star_catch_lev=("_value", "sum"),
        attempt_rate=("_attempt", "mean"),
        n=("_value", "size"),
    ).reset_index()

    opp = g["n"].clip(lower=1)
    g["star_catch_lev"] = g["star_catch_lev"] / opp
    g["star_catch_lev__n"] = g["n"]
    g["attempt_rate__n"] = g["n"]
    return g.rename(columns={"player_id": "batter"}).drop(columns=["n"])
