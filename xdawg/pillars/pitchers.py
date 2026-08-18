"""Pitcher pillar computation from Statcast pitch-level data."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..leverage import weighted_delta
from .hitters import tag_pitch_events


def stuff_proxy(p: pd.DataFrame) -> pd.DataFrame:
    """A lightweight in-house stuff metric.

    FanGraphs' Stuff+ is not available split by count or pitch number, which
    is exactly how we need it. So we build our own from raw Statcast physical
    components -- velocity, induced movement, extension -- z-scored against
    EACH PITCHER'S OWN baseline for EACH PITCH TYPE.

    Self-referencing matters here: we are not asking "is his slider good," we
    are asking "is his slider still his slider in the 7th inning." That makes
    it a pure process metric, immune to talent level.
    """
    d = p.copy()
    d["_move"] = np.hypot(
        pd.to_numeric(d.get("pfx_x"), errors="coerce"),
        pd.to_numeric(d.get("pfx_z"), errors="coerce"),
    )
    d["_velo"] = pd.to_numeric(d.get("release_speed"), errors="coerce")
    d["_ext"] = pd.to_numeric(d.get("release_extension"), errors="coerce")

    keys = ["pitcher", "pitch_type"]
    parts = []
    for col, w in (("_velo", 0.45), ("_move", 0.40), ("_ext", 0.15)):
        grp = d.groupby(keys)[col]
        mu, sd = grp.transform("mean"), grp.transform("std")
        parts.append(((d[col] - mu) / sd.replace(0, np.nan)).fillna(0.0) * w)

    d["stuff"] = sum(parts)
    return d


def bite(p: pd.DataFrame) -> pd.DataFrame:
    """BITE -- execution under pressure. Attacking rather than nibbling."""
    d = tag_pitch_events(stuff_proxy(p))
    g = "pitcher"
    out = None

    def merge(frame, col):
        nonlocal out
        frame = frame.rename(columns={"delta": col, "n": f"{col}__n"})
        out = frame if out is None else out.merge(frame, on=g, how="outer")

    # Does the put-away pitch keep its shape?
    ts = d[d["two_strike"]]
    merge(weighted_delta(ts, g, "stuff", min_n=60), "two_strike_stuff_delta")

    # Attacking vs. nibbling: zone rate with runners on minus bases empty.
    # One of the stickiest pitcher traits there is, and the most dawg-coded.
    d["_runners_on"] = d[["on_1b", "on_2b", "on_3b"]].notna().any(axis=1)
    d["_in_zone"] = (~d["out_of_zone"]).astype(float)
    z = d.groupby([g, "_runners_on"])["_in_zone"].agg(["mean", "size"]).reset_index()
    piv = z.pivot(index=g, columns="_runners_on", values="mean")
    cnt = z.pivot(index=g, columns="_runners_on", values="size")
    if True in piv.columns and False in piv.columns:
        att = pd.DataFrame({
            g: piv.index,
            "attack_delta": (piv[True] - piv[False]).values,
            "attack_delta__n": cnt.min(axis=1).values,
        })
        out = att if out is None else out.merge(att, on=g, how="outer")

    # The free pass in a tight spot is the anti-dawg outcome.
    pa = (
        d.groupby([g, "game_pk", "at_bat_number"])
        .agg(events=("events", "last"), li=("li", "first"))
        .reset_index()
    )
    pa["is_bb"] = pa["events"].astype(str).isin({"walk"}).astype(float)
    merge(weighted_delta(pa, g, "is_bb", min_n=50), "bb_delta_lev")

    return out if out is not None else pd.DataFrame(columns=[g])


def post_hr_bounceback(p: pd.DataFrame) -> pd.DataFrame:
    """Performance against the very next batter after allowing a home run."""
    pa = (
        p.groupby(["pitcher", "game_pk", "at_bat_number"])
        .agg(events=("events", "last"), rv=("delta_run_exp", "sum"),
             li=("li", "first"))
        .reset_index()
        .sort_values(["pitcher", "game_pk", "at_bat_number"])
    )
    pa["prev_hr"] = (
        pa.groupby(["pitcher", "game_pk"])["events"].shift(1).astype(str).eq("home_run")
    )
    after = pa[pa["prev_hr"]].copy()
    if after.empty:
        return pd.DataFrame(columns=["pitcher", "post_hr_bounceback", "post_hr_bounceback__n"])

    # Leverage-weighted, same reasoning as the hitters' post-strikeout term:
    # giving up a homer and then holding the line in a one-run game is the
    # trait being measured; doing it up nine in the 8th is not the same thing.
    after["_wv"] = after["rv"] * after["li"]
    base = pa.groupby("pitcher")["rv"].mean()
    g = after.groupby("pitcher").agg(
        _sum_wv=("_wv", "sum"), _sum_w=("li", "sum"), n=("rv", "size")
    ).reset_index()
    g["rv"] = (g["_sum_wv"] / g["_sum_w"].where(g["_sum_w"] > 0)).fillna(0.0)
    # Run value is from the batting team's view, so lower is better for the
    # pitcher -- negate so higher is always more dawg.
    g["post_hr_bounceback"] = -(g["rv"] - g["pitcher"].map(base))
    return g.rename(columns={"n": "post_hr_bounceback__n"})[
        ["pitcher", "post_hr_bounceback", "post_hr_bounceback__n"]
    ]


def grit(p: pd.DataFrame) -> pd.DataFrame:
    """GRIT -- durability and dirty work.

    Centerpiece is stuff retention deep into an outing: velocity and movement
    on pitches 76+ measured against that same pitcher's own pitches 1-25.
    This is the cleanest available operationalization of "he's still got it
    in the 7th."
    """
    d = stuff_proxy(p).sort_values(["game_pk", "pitcher", "at_bat_number", "pitch_number"])
    d["_pc"] = d.groupby(["game_pk", "pitcher"]).cumcount() + 1

    early = d[d["_pc"] <= 25].groupby("pitcher")["stuff"].mean()
    late = d[d["_pc"] >= 76].groupby("pitcher").agg(
        stuff=("stuff", "mean"), n=("stuff", "size")
    )
    out = late.reset_index()
    out["stuff_after_75"] = out["stuff"] - out["pitcher"].map(early)
    out = out.rename(columns={"n": "stuff_after_75__n"})[
        ["pitcher", "stuff_after_75", "stuff_after_75__n"]
    ]

    # Third time through the order -- holding up once they've seen him twice.
    d["_tto"] = d.groupby(["game_pk", "pitcher", "batter"]).cumcount() + 1
    pa = d.groupby(["pitcher", "game_pk", "at_bat_number"]).agg(
        tto=("_tto", "first"), rv=("delta_run_exp", "sum")
    ).reset_index()
    base = pa.groupby("pitcher")["rv"].mean()
    t3 = pa[pa["tto"] >= 3].groupby("pitcher").agg(rv=("rv", "mean"), n=("rv", "size"))
    if not t3.empty:
        t3 = t3.reset_index()
        t3["third_time_through"] = -(t3["rv"] - t3["pitcher"].map(base))
        out = out.merge(
            t3.rename(columns={"n": "third_time_through__n"})[
                ["pitcher", "third_time_through", "third_time_through__n"]
            ], on="pitcher", how="outer")

    # Workload willingness -- multi-inning outings and short rest.
    app = d.groupby(["pitcher", "game_pk"]).agg(
        pitches=("_pc", "max"), innings=("inning", "nunique"),
        date=("game_date", "first")
    ).reset_index()
    app["date"] = pd.to_datetime(app["date"], errors="coerce")
    app = app.sort_values(["pitcher", "date"])
    app["rest"] = app.groupby("pitcher")["date"].diff().dt.days
    wl = app.groupby("pitcher").agg(
        multi=("innings", lambda s: (s >= 2).mean()),
        short=("rest", lambda s: (s <= 1).mean()),
        n=("game_pk", "size"),
    ).reset_index()
    wl["workload"] = wl["multi"].fillna(0) * 0.6 + wl["short"].fillna(0) * 0.4
    out = out.merge(
        wl.rename(columns={"n": "workload__n"})[["pitcher", "workload", "workload__n"]],
        on="pitcher", how="outer")

    # Willingness to work the inner half against same-handed hitters.
    #
    # Everything here is forced out of pandas' nullable extension dtypes and
    # into plain numpy before any comparison. Statcast ships `plate_x` as
    # Float64 and `stand` as string, both of which carry pd.NA rather than
    # NaN -- so `px < -0.55` returns pd.NA, np.where propagates it into an
    # object array, and `.astype(float)` then dies with "float() argument
    # must be a real number, not 'NAType'" forty minutes into a build.
    same_mask = (d["stand"] == d["p_throws"]).fillna(False).to_numpy(dtype=bool)
    same = d[same_mask].copy()
    if not same.empty:
        px = pd.to_numeric(same["plate_x"], errors="coerce").to_numpy(
            dtype="float64", na_value=np.nan
        )
        is_rhb = (same["stand"] == "R").fillna(False).to_numpy(dtype=bool)
        inside = np.where(is_rhb, px < -0.55, px > 0.55).astype(float)
        # A pitch with no tracking data is unknown, not "not inside" -- leave
        # it null so it is skipped by the mean rather than dragging it down.
        same["_inside"] = np.where(np.isnan(px), np.nan, inside)
        ins = same.groupby("pitcher").agg(
            pitching_inside=("_inside", "mean"), n=("_inside", "count")
        ).reset_index().rename(columns={"n": "pitching_inside__n"})
        out = out.merge(ins, on="pitcher", how="outer")

    return out


def hunt(p: pd.DataFrame) -> pd.DataFrame:
    """HUNT -- the kill shot.

    Note that escaping jams is measured through PROCESS (chase rate, ground
    balls with runners on) rather than strand rate, because raw LOB% is one
    of the noisiest figures in the sport.
    """
    d = tag_pitch_events(stuff_proxy(p))
    g = "pitcher"
    out = None

    def merge(frame, col):
        nonlocal out
        frame = frame.rename(columns={"delta": col, "n": f"{col}__n"})
        out = frame if out is None else out.merge(frame, on=g, how="outer")

    risp = d[d[["on_2b", "on_3b"]].notna().any(axis=1)]
    merge(weighted_delta(risp, g, "stuff", min_n=50), "risp_stuff_delta")

    ts = d[d["two_strike"]].copy()
    ts["_putaway"] = ts["events"].astype(str).eq("strikeout").astype(float)
    merge(weighted_delta(ts, g, "_putaway", min_n=50), "putaway_lev")

    runners = d[d[["on_1b", "on_2b", "on_3b"]].notna().any(axis=1)].copy()
    runners["_chase_induced"] = runners["is_chase"].astype(float)
    merge(weighted_delta(runners, g, "_chase_induced", min_n=60), "jam_escape_process")

    return out if out is not None else pd.DataFrame(columns=[g])


def inherited_runners(p: pd.DataFrame) -> pd.DataFrame:
    """Relievers cleaning up someone else's mess -- dirty work nobody credits.

    Identified as an appearance where the pitcher's first batter faced came
    with runners already aboard.
    """
    d = p.sort_values(["game_pk", "pitcher", "at_bat_number", "pitch_number"])
    first = d.groupby(["game_pk", "pitcher"]).first().reset_index()
    first["_inherited"] = first[["on_1b", "on_2b", "on_3b"]].notna().any(axis=1)

    pa = d.groupby(["pitcher", "game_pk", "at_bat_number"]).agg(
        rv=("delta_run_exp", "sum")
    ).reset_index()
    entry = first[first["_inherited"]][["game_pk", "pitcher", "at_bat_number"]]
    entry = entry.rename(columns={"at_bat_number": "_entry_ab"})
    pa = pa.merge(entry, on=["game_pk", "pitcher"], how="inner")
    clean = pa[pa["at_bat_number"] >= pa["_entry_ab"]]
    if clean.empty:
        return pd.DataFrame(columns=["pitcher", "inherited_runners", "inherited_runners__n"])

    g = clean.groupby("pitcher").agg(rv=("rv", "mean"), n=("rv", "size")).reset_index()
    g["inherited_runners"] = -g["rv"]
    return g.rename(columns={"n": "inherited_runners__n"})[
        ["pitcher", "inherited_runners", "inherited_runners__n"]
    ]
