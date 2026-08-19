"""Pitcher pillar computation from Statcast pitch-level data."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import STUFF_WEIGHTS
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
    # Spin was pulled from Statcast from the start but never actually used.
    # Losing spin on the put-away pitch is one of the clearest tells that a
    # pitcher is running out of gas, so it belongs in the proxy.
    d["_spin"] = pd.to_numeric(d.get("release_spin_rate"), errors="coerce")

    keys = ["pitcher", "pitch_type"]
    parts = []
    for col, w in STUFF_WEIGHTS.items():
        if col not in d.columns:
            continue
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
        frame = frame.rename(columns={
            "delta": col, "league_delta": f"{col}__lg", "n": f"{col}__n"})
        out = frame if out is None else out.merge(frame, on=g, how="outer")

    # Does the put-away pitch keep its shape?
    ts = d[d["two_strike"]]
    merge(weighted_delta(ts, g, "stuff", min_n=60), "two_strike_stuff_delta")

    # Attacking vs. nibbling: zone rate with runners on minus bases empty.
    # One of the stickiest pitcher traits there is, and the most dawg-coded.
    d["_runners_on"] = d[["on_1b", "on_2b", "on_3b"]].notna().any(axis=1)
    # out_of_zone inherits pd.NA from a nullable `zone`, so this is a
    # nullable boolean. pandas 3 converts NA to nan on .astype(float);
    # pandas 2 is not guaranteed to, and the container, the dev Mac and
    # the CI runner do not all run the same major version. Convert
    # explicitly so the behaviour is the same everywhere: a pitch whose
    # zone we do not know stays null and drops out of the mean rather
    # than silently counting as "not in the zone".
    d["_in_zone"] = (~d["out_of_zone"]).astype("Float64").to_numpy(
        dtype="float64", na_value=np.nan)
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
    g["post_hr_bounceback__lg"] = -(g["rv"] - float(pa["rv"].mean()))
    return g.rename(columns={"n": "post_hr_bounceback__n"})[
        ["pitcher", "post_hr_bounceback", "post_hr_bounceback__lg",
         "post_hr_bounceback__n"]
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
    out["stuff_after_75__lg"] = out["stuff"] - float(early.mean())
    out = out.rename(columns={"n": "stuff_after_75__n"})[
        ["pitcher", "stuff_after_75", "stuff_after_75__lg", "stuff_after_75__n"]
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
        t3["third_time_through__lg"] = -(t3["rv"] - float(pa["rv"].mean()))
        out = out.merge(
            t3.rename(columns={"n": "third_time_through__n"})[
                ["pitcher", "third_time_through", "third_time_through__lg",
                 "third_time_through__n"]
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

    Escaping jams is read TWO ways, and deliberately so. `jam_escape_process`
    asks whether he kept executing with traffic on -- chase rate, immune to
    what the defense did behind him. `jam_escape_runs` asks what the
    scoreboard says. Process alone was the original design, on the argument
    that strand rate is too noisy to grade; that argument was right about
    strand rate and wrong as a reason to ignore outcomes altogether, because
    a run-expectancy-relative measure is far steadier than LOB% and a
    pitcher who blows up every time he gets in trouble should not be able to
    grade out fine on the strength of his swing-and-miss.
    """
    d = tag_pitch_events(stuff_proxy(p))
    g = "pitcher"
    out = None

    def merge(frame, col):
        nonlocal out
        frame = frame.rename(columns={
            "delta": col, "league_delta": f"{col}__lg", "n": f"{col}__n"})
        out = frame if out is None else out.merge(frame, on=g, how="outer")

    risp = d[d[["on_2b", "on_3b"]].notna().any(axis=1)]
    merge(weighted_delta(risp, g, "stuff", min_n=50), "risp_stuff_delta")

    ts = d[d["two_strike"]].copy()
    ts["_putaway"] = ts["events"].astype(str).eq("strikeout").astype(float)
    merge(weighted_delta(ts, g, "_putaway", min_n=50), "putaway_lev")

    runners = d[d[["on_1b", "on_2b", "on_3b"]].notna().any(axis=1)].copy()
    runners["_chase_induced"] = runners["is_chase"].astype("Float64").to_numpy(
        dtype="float64", na_value=np.nan)
    merge(weighted_delta(runners, g, "_chase_induced", min_n=60), "jam_escape_process")

    runs = jam_escape_runs(p)
    if not runs.empty:
        out = runs if out is None else out.merge(runs, on=g, how="outer")

    return out if out is not None else pd.DataFrame(columns=[g])


def half_inning_pas(p: pd.DataFrame) -> pd.DataFrame:
    """One row per plate appearance, with the half-inning bookkeeping attached.

    Two derived quantities come out of this and nothing else in the package
    could produce them:

    `outs_recorded` -- how many outs this plate appearance actually produced.
    Statcast ships the out count at the START of a PA and never the end, so
    it is recovered from the transition to the next PA in the same half
    inning; the last PA of a half inning is assumed to have finished it at
    three. That is how many outs a pitcher recorded in an outing, which is
    how innings pitched exist at all here.

    `rest_of_inning_rv` -- the run value banked from this PA through the end
    of the half inning. Because `delta_run_exp` is a RE24 term, summing it to
    the end of an inning (where run expectancy is zero by definition) gives
    exactly `runs actually scored - runs expected from the state we were in`.
    That is what makes an outcome-based jam grade possible without an
    externally sourced run expectancy table.
    """
    d = p.sort_values(["game_pk", "at_bat_number", "pitch_number"])
    first = d.drop_duplicates(["game_pk", "at_bat_number"], keep="first").copy()
    rv = (
        d.groupby(["game_pk", "at_bat_number"])["delta_run_exp"]
        .sum().rename("rv").reset_index()
    )
    pa = first.merge(rv, on=["game_pk", "at_bat_number"], how="left")

    # Everything that gets compared or arithmetic'd is forced out of the
    # nullable extension dtypes first -- pd.NA in a comparison returns NA,
    # not False, and it poisons the whole chain three functions later.
    pa["_outs"] = pd.to_numeric(pa["outs_when_up"], errors="coerce").to_numpy(
        dtype="float64", na_value=np.nan)
    pa["rv"] = pd.to_numeric(pa["rv"], errors="coerce").fillna(0.0).to_numpy(
        dtype="float64", na_value=0.0)
    pa["_half"] = (
        pa["game_pk"].astype(str) + "|" + pa["inning"].astype(str)
        + "|" + pa["inning_topbot"].astype(str)
    )
    pa = pa.sort_values(["game_pk", "at_bat_number"])

    grp = pa.groupby("_half", sort=False)
    pa["outs_recorded"] = (
        grp["_outs"].shift(-1).fillna(3.0) - pa["_outs"]
    ).clip(lower=0)
    # Reverse cumulative sum within the half inning. Reversing a Series keeps
    # its index labels, so the values realign on assignment.
    pa["rest_of_inning_rv"] = grp["rv"].transform(
        lambda s: s[::-1].cumsum()[::-1]
    )
    return pa


def _is_jam(pa: pd.DataFrame) -> np.ndarray:
    """Genuine trouble: two or more aboard, or a runner on third with an out to give."""
    on = pa[["on_1b", "on_2b", "on_3b"]].notna()
    n_on = on.sum(axis=1).to_numpy(dtype="float64")
    on3 = on["on_3b"].to_numpy(dtype=bool)
    outs = pd.to_numeric(pa["outs_when_up"], errors="coerce").to_numpy(
        dtype="float64", na_value=np.nan)
    return (n_on >= 2) | (on3 & (outs < 2))


def jam_escape_runs(p: pd.DataFrame, pa: pd.DataFrame | None = None) -> pd.DataFrame:
    """What actually HAPPENED once he got himself into trouble.

    The original jam grade was pure process -- chase rate with runners on --
    on the reasoning that strand rate is one of the noisiest numbers in the
    sport. That reasoning holds for strand rate and does not hold for this:
    run value against the run expectancy of the exact state he was in is a
    far better behaved measure, and leaving the outcome out entirely meant a
    pitcher who induced good swings and still gave up four runs graded the
    same as one who got out of it.

    Charged from the first jam in each half inning through the END of that
    half inning, including the runs that scored after he was pulled. That is
    deliberate and it is the tough part: the runners were his, and a pitcher
    who reliably hands a two-on mess to the bullpen has not escaped anything.
    The reliever who cleans it up is separately credited by
    `inherited_runners`, so the work is counted once for each of them and
    nobody's ledger is silently doubled.
    """
    pa = half_inning_pas(p) if pa is None else pa
    jams = pa[_is_jam(pa)].drop_duplicates("_half", keep="first").copy()
    if jams.empty:
        return pd.DataFrame(columns=["pitcher", "jam_escape_runs",
                                     "jam_escape_runs__lg", "jam_escape_runs__n"])
    # Run value is from the batting team's view, so negate: fewer runs than
    # the state expected is a positive number here, like every other pitcher
    # component.
    jams["_escape"] = -jams["rest_of_inning_rv"]
    out = weighted_delta(jams, "pitcher", "_escape", min_n=8)
    return out.rename(columns={
        "delta": "jam_escape_runs",
        "league_delta": "jam_escape_runs__lg",
        "n": "jam_escape_runs__n",
    })


def workhorse(p: pd.DataFrame, pa: pd.DataFrame | None = None) -> pd.DataFrame:
    """Length of start, in both directions. Starters only.

    This exists because the rest of pitcher GRIT is CONDITIONED ON SURVIVING.
    `stuff_after_75` is only defined for a pitcher who reached pitch 76, and
    `third_time_through` only for one who faced the order a third time -- so
    a starter who gets knocked out in the second inning contributes nothing
    to either, and is graded exclusively on the nights he lasted. His
    disasters were structurally invisible. That is a selection bias big
    enough to invert a pitcher's score, and it is why the eye test and the
    leaderboard disagreed on the blow-up-prone starters.

    Two components rather than one blended rate, because they are different
    claims and the breakdown panel should be able to say which one applies:

      long_start_rate  share of starts reaching 15 outs. The innings eater who
                       saves the bullpen is a dawg, full stop.
      blowup_rate      share of starts failing to reach 9 outs. Inverted in
                       config -- this is the one that finally costs a pitcher
                       something for not getting out of the first.

    Relievers get no row at all, so both terms shrink to exactly zero for
    them rather than grading them on a standard that does not apply.
    """
    pa = half_inning_pas(p) if pa is None else pa

    # The starter for a side is whoever threw to that side's first batter.
    firsts = (
        pa.sort_values("at_bat_number")
        .drop_duplicates(["game_pk", "inning_topbot"], keep="first")
        [["game_pk", "inning_topbot", "pitcher"]]
        .assign(is_start=True)
    )
    app = pa.groupby(["pitcher", "game_pk"]).agg(
        outs=("outs_recorded", "sum"),
        inning_topbot=("inning_topbot", "first"),
    ).reset_index()
    app = app.merge(firsts, on=["game_pk", "inning_topbot", "pitcher"], how="left")
    app["is_start"] = app["is_start"].fillna(False).to_numpy(dtype=bool)

    # An OPENER is indistinguishable from a starter who got knocked out --
    # both threw to the first batter and both left inside two innings, and
    # nothing in the pitch data says which one was the plan. So the terms are
    # restricted to pitchers who are actually starters: a majority of their
    # appearances are starts, and there are at least three of them. A reliever
    # who opened twice is not graded on it; a genuine opener used that way all
    # year still is, which is the one case this gets wrong and cannot fix from
    # this data alone.
    share = app.groupby("pitcher")["is_start"].agg(["mean", "sum"])
    real = share[(share["mean"] >= 0.5) & (share["sum"] >= 3)].index
    starts = app[app["is_start"] & app["pitcher"].isin(real)]
    if starts.empty:
        return pd.DataFrame(columns=["pitcher", "long_start_rate",
                                     "long_start_rate__n", "blowup_rate",
                                     "blowup_rate__n"])

    outs = pd.to_numeric(starts["outs"], errors="coerce").fillna(0.0)
    starts = starts.assign(
        _long=(outs >= 15).astype(float),
        _blow=(outs < 9).astype(float),
    )
    g = starts.groupby("pitcher").agg(
        long_start_rate=("_long", "mean"),
        blowup_rate=("_blow", "mean"),
        n=("_long", "size"),
    ).reset_index()
    g["long_start_rate__n"] = g["n"]
    g["blowup_rate__n"] = g["n"]
    return g[["pitcher", "long_start_rate", "long_start_rate__n",
              "blowup_rate", "blowup_rate__n"]]


def inherited_runners(p: pd.DataFrame) -> pd.DataFrame:
    """Relievers cleaning up someone else's mess -- dirty work nobody credits.

    Identified as an appearance where the pitcher's first batter faced came
    with runners already aboard.
    """
    d = p.sort_values(["game_pk", "pitcher", "at_bat_number", "pitch_number"])
    # drop_duplicates, NOT groupby().first(): pandas' .first() returns the
    # first NON-NULL value in each column independently, so a starter who
    # entered with bases empty picked up whichever runners appeared later in
    # his outing and was credited with inheriting them. That made every
    # starter score on a reliever-only component -- Michael Wacha, who has
    # started every game, was showing +0.55 here.
    first = d.drop_duplicates(["game_pk", "pitcher"], keep="first")
    first = first.assign(
        _inherited=first[["on_1b", "on_2b", "on_3b"]].notna().any(axis=1)
    )

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
