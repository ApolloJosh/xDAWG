"""Hitter pillar computation from Statcast pitch-level data."""

from __future__ import annotations

import re
import warnings

import numpy as np
import pandas as pd

from .. import ingest
from ..leverage import clutch, weighted_delta

# Fallback opportunity count when no source publishes one. Roughly a
# regular's season of chances, so shrinkage stays in a sane range instead of
# collapsing the pillar to zero.
HUNT_NOMINAL_OPPORTUNITIES = 200

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
    #
    # Leverage-weighted against his own flat rate, not a raw season mean.
    # As a raw mean this was the one BITE component that scored a chase
    # survived in a 2-0 game in the 2nd identically to the same pitch
    # survived with two on in the 9th -- which is the entire thing the
    # metric is supposed to distinguish. It was also the only BITE term
    # measured on absolute level, so it leaked raw contact ability into a
    # pillar built from deltas.
    chases = d[d["is_chase"]]
    merge(weighted_delta(chases, g, "chase_contact", min_n=30), "chase_contact", "n")

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

    # Exit velocity under pressure, continuous rather than a >=95mph flag.
    # Squaring one up and lining out is still a good process outcome, which
    # is the whole reason this belongs in BITE alongside the whiff terms.
    bip = d[d["launch_speed"].notna()].copy()
    bip["_ev"] = pd.to_numeric(bip["launch_speed"], errors="coerce").to_numpy(
        dtype="float64", na_value=np.nan)
    merge(weighted_delta(bip, g, "_ev", min_n=40), "ev_situational", "n")

    return out if out is not None else pd.DataFrame(columns=[g])


def post_k_bounceback(p: pd.DataFrame) -> pd.DataFrame:
    """Run value in the plate appearance immediately after a strikeout.

    Short memory is a dawg trait, and it turns out to be directly computable.
    """
    pa = (
        p.groupby(["batter", "game_pk", "at_bat_number"])
        .agg(events=("events", "last"), rv=("delta_run_exp", "sum"),
             li=("li", "first"))
        .reset_index()
        .sort_values(["batter", "game_pk", "at_bat_number"])
    )
    pa["prev_k"] = (
        pa.groupby("batter")["events"].shift(1).astype(str).eq("strikeout")
    )
    after = pa[pa["prev_k"]].copy()
    if after.empty:
        return pd.DataFrame(columns=["batter", "post_k_bounceback", "post_k_bounceback__n"])

    # Still measured against his own overall baseline, but the response is
    # leverage-weighted: answering a strikeout with two on in the 9th is the
    # short memory this is trying to capture, and a flat mean scored it the
    # same as a bounce-back in a blowout.
    after["_wv"] = after["rv"] * after["li"]
    base = pa.groupby("batter")["rv"].mean()
    g = after.groupby("batter").agg(
        _sum_wv=("_wv", "sum"), _sum_w=("li", "sum"), n=("rv", "size")
    ).reset_index()
    g["rv"] = (g["_sum_wv"] / g["_sum_w"].where(g["_sum_w"] > 0)).fillna(0.0)
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
            # Carry the opportunity count when the source supplies one.
            # Without it, score_pillar falls back to n == k and shrinks every
            # player by a flat one half, which erases the distinction between
            # a full season of evidence and a dozen games of it.
            cols = ["batter", col] + ([f"{col}__n"] if f"{col}__n" in f.columns else [])
            frames.append(f[cols])

    if not frames:
        return pd.DataFrame(columns=["batter"])
    out = frames[0]
    for f in frames[1:]:
        out = out.merge(f, on="batter", how="outer")
    return out


def hunt(
    oaa: pd.DataFrame | None,
    context: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """HUNT -- the play that wins it.

    Built on Outs Above Average, scaled by the situations the fielder was
    actually exposed to. OAA is a season total, so no individual play can be
    weighted; `context` (from pipeline._fielding_context) instead measures
    the mean leverage and opponent quality across the batted balls that
    player handled, normalized to a league mean of 1.0.

    Two components:

      oaa_rate         OAA per fielding opportunity. Raw defensive value.
      oaa_situational  The same, times the fielder's context index.

    A caveat worth keeping in view: OAA is a talent metric, and unlike every
    other xDAWG component this one is not self-referenced against the
    player's own baseline. The situational scaling tilts it toward the games
    that mattered, but it does not cancel talent the way a delta does -- so
    if the leaderboard starts correlating with WAR, this is the first place
    to look. Swapping the pillar back to a delta means comparing each
    fielder's OAA rate in high-context plays against his own rate in low ones,
    which needs per-play OAA that Savant does not publish.
    """
    if oaa is None or oaa.empty:
        return pd.DataFrame(columns=["batter"])

    c = oaa.copy()

    # Resolve rather than index: Savant renames these without notice, and a
    # KeyError here killed a forty-minute build at its very last step.
    id_col = ingest.pick_col(c, "player_id", "entity_id", "playerid", "mlbam_id")
    oaa_col = ingest.pick_col(c, "outs_above_average", "oaa")
    if id_col is None or oaa_col is None:
        missing = [
            n for n, v in (("player id", id_col), ("OAA", oaa_col)) if v is None
        ]
        warnings.warn(
            f"OAA schema drift: no {' or '.join(missing)} in "
            f"{list(c.columns)}; HUNT dropped"
        )
        return pd.DataFrame(columns=["batter"])

    g = pd.DataFrame({
        "player_id": pd.to_numeric(c[id_col], errors="coerce"),
        "_oaa": pd.to_numeric(c[oaa_col], errors="coerce"),
    }).dropna(subset=["player_id"])

    # --- opportunity count, in descending order of trust ------------------
    #
    # This matters more than it looks. `n` is the shrinkage denominator
    # (z * n / (n + k)), so if every player ends up with n = 0 then every
    # HUNT z shrinks to exactly 0.0 -- a silently dead pillar rather than an
    # error. The all-position OAA leaderboard ships NO opportunity column at
    # all, so the fallbacks below are the normal path, not an edge case.

    # 1. Per-star buckets, checked FIRST. The outfield frame splits
    #    opportunities across n_opp_1stars..n_opp_5stars with no total, and a
    #    loose name match would seize one bucket and treat it as the whole
    #    season -- which inflated a 108-chance fielder's rate sevenfold.
    buckets = [
        x for x in c.columns
        if re.fullmatch(r"n_opp_\d+stars?", str(x).strip().lower())
    ]
    opp_col = ingest.pick_col(
        c, "n_fielding_opportunities", "opportunities", "attempts", "n_opp"
    )
    source = None
    if len(buckets) >= 2:
        g["_opp"] = (
            c[buckets].apply(pd.to_numeric, errors="coerce").sum(axis=1).to_numpy()
        )
        source = f"summed {len(buckets)} star buckets"
    elif opp_col is not None:
        g["_opp"] = pd.to_numeric(c[opp_col], errors="coerce").to_numpy()
        source = f"column {opp_col!r}"
    else:
        g["_opp"] = np.nan

    g["player_id"] = g["player_id"].astype("int64")
    g = g.groupby("player_id", as_index=False).agg(
        _oaa=("_oaa", "sum"), _opp=("_opp", "sum")
    )

    # Attach the situational context now -- its play count doubles as the
    # best available opportunity proxy when the leaderboard has none.
    if context is not None and not context.empty and "context" in context.columns:
        ctx = context[["player_id", "context", "context__n"]].copy()
        ctx["player_id"] = pd.to_numeric(ctx["player_id"], errors="coerce")
        ctx = ctx.dropna(subset=["player_id"])
        ctx["player_id"] = ctx["player_id"].astype("int64")
        g = g.merge(ctx, on="player_id", how="left")
        # A fielder we could not attribute gets a neutral 1.0 rather than
        # being dropped from the pillar entirely.
        g["context"] = pd.to_numeric(g["context"], errors="coerce").fillna(1.0)
        g["context__n"] = pd.to_numeric(g["context__n"], errors="coerce").fillna(0.0)
    else:
        g["context"] = 1.0
        g["context__n"] = 0.0

    # 2. Balls we actually attributed to him in the pitch data. Counts plays
    #    handled rather than true chances (a ball he never reached has no
    #    hit_location pointing at him), but it is measured from our own data
    #    and cannot divide by zero.
    if g["_opp"].isna().all() or (g["_opp"].fillna(0) <= 0).all():
        if (g["context__n"] > 0).any():
            g["_opp"] = g["context__n"].where(g["context__n"] > 0)
            source = "batted balls attributed from the pitch data"
        else:
            # 3. Back it out of the published rates: OAA is roughly
            #    (actual - expected) * chances, so chances ~ OAA / diff.
            #    Rounded to whole percent upstream, so this is coarse -- fine
            #    for a shrinkage denominator, not for a headline number.
            diff_col = ingest.pick_col(
                c, "diff_success_rate_formatted", "diff_success_rate"
            )
            est = None
            if diff_col is not None:
                diff = pd.to_numeric(
                    c[diff_col].astype(str).str.replace("%", "", regex=False),
                    errors="coerce",
                ) / 100.0
                ids = pd.to_numeric(c[id_col], errors="coerce")
                frame = pd.DataFrame({"player_id": ids, "_d": diff}).dropna()
                frame["player_id"] = frame["player_id"].astype("int64")
                frame = frame.groupby("player_id", as_index=False)["_d"].mean()
                merged = g.merge(frame, on="player_id", how="left")
                # Only trust it where the rounded diff is big enough to divide by.
                usable = merged["_d"].abs() >= 0.005
                est = (merged["_oaa"] / merged["_d"].where(usable)).abs()
            if est is not None and est.notna().any():
                g["_opp"] = est
                source = f"estimated from {diff_col!r}"
            else:
                # 4. Last resort: a flat nominal count. Deliberately NOT zero
                #    -- zero would shrink the entire pillar to nothing and
                #    read as "no dawgs in baseball" rather than as a failure.
                g["_opp"] = float(HUNT_NOMINAL_OPPORTUNITIES)
                source = (
                    f"nominal {HUNT_NOMINAL_OPPORTUNITIES} (no opportunity "
                    "data anywhere)"
                )
                warnings.warn(
                    "HUNT has no opportunity count from any source; using a "
                    "flat nominal value, so shrinkage no longer distinguishes "
                    "full-time fielders from part-timers"
                )

    print(f"[xdawg] hunt: opportunities from {source}")

    # Rate, not total, so a part-time fielder is not automatically penalised.
    denom = g["_opp"].where(g["_opp"] > 0)
    g["oaa_rate"] = (g["_oaa"] / denom).fillna(0.0)
    g["oaa_rate__n"] = g["_opp"].fillna(0.0).clip(lower=0)

    g["oaa_situational"] = g["oaa_rate"] * g["context"]
    # Confidence in the situational term is limited by BOTH the fielding
    # sample and how many of his plays we could attribute.
    g["oaa_situational__n"] = np.minimum(
        g["oaa_rate__n"], g["context__n"].where(g["context__n"] > 0, g["oaa_rate__n"])
    )

    return g.rename(columns={"player_id": "batter"})[[
        "batter", "oaa_rate", "oaa_rate__n",
        "oaa_situational", "oaa_situational__n",
    ]]


# ---------------------------------------------------------------------------
# GRIT inputs derived from the pitch feed.
#
# GRIT is the one pillar measured on absolute level rather than as a
# leverage delta -- running it out and staying on the field are not things
# you do more of when the game is close, you either do them or you don't.
# ---------------------------------------------------------------------------


def availability_frame(p: pd.DataFrame) -> pd.DataFrame:
    """Share of his team's games the player actually appeared in.

    The cheapest honest grit signal there is: the ability to stay on the
    field. Measured against his own club's game count rather than a fixed
    162 so that a player traded mid-season, or one whose team has played
    fewer games, is not penalised for the schedule.
    """
    if not {"batter", "game_pk"}.issubset(p.columns):
        return pd.DataFrame(columns=["batter", "availability", "availability__n"])

    batting_home = p["inning_topbot"].astype(str).str.startswith("Bot")
    team = np.where(batting_home, p["home_team"], p["away_team"])
    d = pd.DataFrame({
        "batter": p["batter"], "game_pk": p["game_pk"], "team": team,
    }).dropna(subset=["batter", "game_pk"])

    played = d.groupby("batter")["game_pk"].nunique().rename("_g")
    # Each player's own club, by where he appears most.
    club = d.groupby("batter")["team"].agg(lambda s: s.value_counts().idxmax())
    team_games = d.groupby("team")["game_pk"].nunique()

    out = played.to_frame().join(club.rename("team"))
    out["_team_g"] = out["team"].map(team_games)
    out["availability"] = (out["_g"] / out["_team_g"]).clip(upper=1.0)
    out["availability__n"] = out["_team_g"]
    return out.reset_index()[["batter", "availability", "availability__n"]]


def hbp_frame(p: pd.DataFrame) -> pd.DataFrame:
    """Hit-by-pitches above what his pitch locations would predict.

    Wearing one is the purest grit stat in the sport, but a raw HBP count
    mostly measures how often pitchers miss inside to you. So we build an
    empirical expectation: for every pitch he saw, how often does a pitch at
    that location, to a batter of that handedness, actually hit someone?
    The sum of those probabilities is his expected HBP, and the excess is
    the part attributable to not getting out of the way.
    """
    need = {"batter", "plate_x", "plate_z", "stand"}
    if not need.issubset(p.columns):
        return pd.DataFrame(columns=["batter", "hbp_above_expected",
                                     "hbp_above_expected__n"])

    d = pd.DataFrame({
        "batter": p["batter"],
        "px": pd.to_numeric(p["plate_x"], errors="coerce").to_numpy(
            dtype="float64", na_value=np.nan),
        "pz": pd.to_numeric(p["plate_z"], errors="coerce").to_numpy(
            dtype="float64", na_value=np.nan),
        "stand": p["stand"].astype(str).to_numpy(),
    })
    desc = p.get("description", pd.Series("", index=p.index)).astype(str)
    events = p.get("events", pd.Series("", index=p.index)).astype(str)
    d["hbp"] = (desc.str.contains("hit_by_pitch", na=False)
                | events.str.contains("hit_by_pitch", na=False)).to_numpy(float)
    d = d.dropna(subset=["px", "pz"])
    if d.empty or d["hbp"].sum() == 0:
        return pd.DataFrame(columns=["batter", "hbp_above_expected",
                                     "hbp_above_expected__n"])

    # Quarter-foot bins. Coarse on purpose: HBP is rare, and fine bins would
    # hand most batters an expectation built from a handful of pitches.
    d["bx"] = np.round(d["px"] * 4).astype(int)
    d["bz"] = np.round(d["pz"] * 4).astype(int)
    league = d.groupby(["stand", "bx", "bz"])["hbp"].mean().rename("p_hbp")
    d = d.join(league, on=["stand", "bx", "bz"])
    d["p_hbp"] = d["p_hbp"].fillna(d["hbp"].mean())

    g = d.groupby("batter").agg(
        actual=("hbp", "sum"), expected=("p_hbp", "sum"), n=("hbp", "size")
    ).reset_index()
    # Per 100 pitches seen, so it is a rate and not a playing-time proxy.
    g["hbp_above_expected"] = 100.0 * (g["actual"] - g["expected"]) / g["n"].clip(lower=1)
    return g.rename(columns={"n": "hbp_above_expected__n"})[
        ["batter", "hbp_above_expected", "hbp_above_expected__n"]
    ]


# Bases a runner is expected to take, learned from the league rather than
# assumed, keyed on (starting base, what the batter did).
_XBT_EVENTS = ("single", "double")


def xbt_frame(p: pd.DataFrame) -> pd.DataFrame:
    """Extra bases taken on hits, against the league's own baseline.

    Statcast's `on_1b`/`on_2b`/`on_3b` carry runner IDs, so consecutive
    plate appearances in a game reveal where every runner ended up without
    needing play-by-play from another source -- which would otherwise mean
    thousands of extra API calls and a much longer build.

    Restricted to singles and doubles: those are the classic first-to-third
    and scoring-from-first situations, and on a hit a vanished runner has
    almost always scored rather than been retired, which is what makes the
    inference safe. A runner lifted for a pinch runner mid-inning is
    misread, but that is rare and unbiased across players.
    """
    need = {"game_pk", "at_bat_number", "on_1b", "on_2b", "on_3b", "events"}
    if not need.issubset(p.columns):
        return pd.DataFrame(columns=["batter", "extra_bases_taken",
                                     "extra_bases_taken__n"])

    # Base state at the START of the plate appearance. Taken from the first
    # ROW rather than via agg("first"), which returns the first non-null
    # value per column and would report a runner who only reached second on
    # a steal mid-at-bat as having started there.
    order = ["game_pk", "at_bat_number", "pitch_number"]
    order = [c for c in order if c in p.columns]
    starts = (
        p.sort_values(order)
        .drop_duplicates(["game_pk", "at_bat_number"], keep="first")
        [["game_pk", "at_bat_number", "on_1b", "on_2b", "on_3b"]]
    )
    # `events` is null on every pitch that does not end the PA, so "last"
    # (last non-null) is genuinely what we want for that one.
    ends = (
        p.groupby(["game_pk", "at_bat_number"], sort=True)
        .agg(events=("events", "last")).reset_index()
    )
    pa = starts.merge(ends, on=["game_pk", "at_bat_number"], how="left") \
               .sort_values(["game_pk", "at_bat_number"])
    if pa.empty:
        return pd.DataFrame(columns=["batter", "extra_bases_taken",
                                     "extra_bases_taken__n"])

    # Everything below works in plain numpy. Statcast hands back `on_1b` and
    # friends as nullable Int64/Float64 carrying pd.NA, and comparing two of
    # those produces a nullable BooleanArray -- which np.where cannot consume
    # ("boolean value of NA is ambiguous"). Base state is null on most pitches
    # by definition, so this is the common path, not an edge case.
    def as_float(s: pd.Series) -> np.ndarray:
        return pd.to_numeric(s, errors="coerce").to_numpy(
            dtype="float64", na_value=np.nan)

    # Where every runner stands at the start of the NEXT plate appearance.
    nxt = {
        base: as_float(pa.groupby("game_pk")[f"on_{base}b"].shift(-1))
        for base in (1, 2, 3)
    }

    ev = pa["events"].astype(str).str.lower()
    keep = ev.isin(_XBT_EVENTS).fillna(False).to_numpy(dtype=bool)

    rows = []
    for base in (1, 2):  # a runner on third has no extra base to take
        rid = as_float(pa[f"on_{base}b"])
        sel = keep & ~np.isnan(rid)
        if not sel.any():
            continue
        # Which base is he standing on next time we look? None => scored.
        ended = np.full(len(pa), 4.0)
        for b in (1, 2, 3):
            same = (nxt[b] == rid) & ~np.isnan(rid)
            ended = np.where(same, float(b), ended)
        rows.append(pd.DataFrame({
            "batter": rid[sel].astype("int64"),
            "start": base,
            "event": ev.to_numpy()[sel],
            "advance": np.clip(ended[sel] - base, 0, 3),
        }))

    if not rows:
        return pd.DataFrame(columns=["batter", "extra_bases_taken",
                                     "extra_bases_taken__n"])
    d = pd.concat(rows, ignore_index=True)

    # Expectation learned from the league for the same situation, so this
    # measures aggression and reads, not how often he happened to be on base
    # when someone doubled.
    exp = d.groupby(["start", "event"])["advance"].transform("mean")
    d["excess"] = d["advance"] - exp

    g = d.groupby("batter").agg(
        extra_bases_taken=("excess", "mean"), n=("excess", "size")
    ).reset_index()
    return g.rename(columns={"n": "extra_bases_taken__n"})


def clutch_frame(p: pd.DataFrame) -> pd.DataFrame:
    """Per-hitter Clutch, from win expectancy already in the pitch feed.

    `delta_home_win_exp` is signed from the HOME team's point of view, so it
    is flipped for a batter hitting in the top half -- otherwise every road
    hitter would score as the mirror image of his real contribution.
    """
    need = {"batter", "game_pk", "at_bat_number", "delta_home_win_exp",
            "inning_topbot", "li"}
    if not need.issubset(p.columns):
        return pd.DataFrame(columns=["batter", "wpa_clutch_delta",
                                     "wpa_clutch_delta__n"])

    batting_home = p["inning_topbot"].astype(str).str.startswith("Bot")
    d = p.copy()
    d["_wpa"] = pd.to_numeric(d["delta_home_win_exp"], errors="coerce").to_numpy(
        dtype="float64", na_value=np.nan) * np.where(batting_home, 1.0, -1.0)

    pa = d.groupby(["batter", "game_pk", "at_bat_number"]).agg(
        wpa=("_wpa", "sum"), li=("li", "first")
    ).reset_index()
    return clutch(pa, "batter", "wpa", "li", min_n=100)
