"""
End-to-end run: Statcast -> leverage -> pillars -> xDAWG+ -> site data.

Each stage is defensive. A missing optional leaderboard removes its
components and renormalizes the remaining pillar weights rather than
failing the run, so a partial data environment still produces a
leaderboard -- just a slightly coarser one.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from . import fight as fight_mod
from . import ingest
from .aggregate import compute, league_view as aggregate_league_view
from .config import QUALIFY, SEASON_DEFAULT, TEAMS
from .leverage import add_leverage
from .pillars import hitters as H
from .pillars import pitchers as P


def _merge_all(frames: list[pd.DataFrame], key: str) -> pd.DataFrame:
    frames = [f for f in frames if f is not None and not f.empty and key in f.columns]
    if not frames:
        return pd.DataFrame(columns=[key])
    out = frames[0]
    for f in frames[1:]:
        dupes = [c for c in f.columns if c in out.columns and c != key]
        out = out.merge(f.drop(columns=dupes), on=key, how="outer")
    return out


def _team_of(p: pd.DataFrame, who: str) -> pd.Series:
    """Most frequent team, derived from which half-inning the player appears in."""
    batting_home = p["inning_topbot"].astype(str).str.startswith("Bot")
    if who == "batter":
        team = np.where(batting_home, p["home_team"], p["away_team"])
    else:
        team = np.where(batting_home, p["away_team"], p["home_team"])
    tmp = pd.DataFrame({
        who: p[who], "team": team, "_d": pd.to_datetime(p["game_date"], errors="coerce")
    }).dropna(subset=[who, "team"])
    # Most RECENT club, not the most frequent one. A player traded in July
    # still has more games with his old team, so "most frequent" left him
    # listed there for the rest of the season -- which reads as stale data
    # even though the scoring is correct.
    tmp = tmp.sort_values("_d")
    return tmp.groupby(who)["team"].last()


def _attach_fight(p: pd.DataFrame, who: str, season: int) -> pd.DataFrame:
    """Compute the FIGHT-weighted run value delta for hitters or pitchers."""
    standings = ingest.load_standings(season)
    if standings is None or standings.empty:
        return pd.DataFrame(columns=[who])

    quality = fight_mod.opponent_quality(standings)
    batting_home = p["inning_topbot"].astype(str).str.startswith("Bot")

    if who == "batter":
        own = np.where(batting_home, p["home_team"], p["away_team"])
        opp = np.where(batting_home, p["away_team"], p["home_team"])
        sign = 1.0
    else:
        own = np.where(batting_home, p["away_team"], p["home_team"])
        opp = np.where(batting_home, p["home_team"], p["away_team"])
        sign = -1.0  # run value is from the batter's view

    d = p.copy()
    d["_own"], d["_opp"] = own, opp
    dates = pd.to_datetime(d["game_date"], errors="coerce")
    span = (dates.max() - dates.min()).days or 1
    d["_pct"] = (dates - dates.min()).dt.days / span

    def weight_for(terms):
        return fight_mod.fight_weight(
            pd.Series(d["_opp"].values, index=d.index),
            pd.Series(d["_own"].values, index=d.index),
            d["_pct"], quality, terms=terms,
        )

    # Two independent readings of "showed up when it mattered", rather than
    # one blended number nobody can decompose on the site.
    d["w_contender"] = weight_for(("quality", "late"))
    d["w_division"] = weight_for(("division", "late"))
    d["fight_w"] = weight_for(fight_mod.ALL_TERMS)

    # Process alongside outcome. `fight_rv_delta` is run value, which FIGHT
    # is allowed to use because divisional and quality-opponent samples are
    # four to five times a leverage sample -- but run value still carries
    # every bit of batted-ball luck in it. Contact-on-swing is the same
    # question asked of the approach instead of the result, and it stabilizes
    # far faster. Without it FIGHT was a single component carrying 30% of a
    # hitter's score.
    from .pillars.hitters import tag_pitch_events

    d = tag_pitch_events(d)
    # Higher is better for both roles: the hitter wants contact, the pitcher
    # wants the miss.
    d["_proc"] = d["is_whiff"].astype(float)
    if who == "batter":
        d["_proc"] = 1.0 - d["_proc"]
    swings = d[d["is_swing"]]

    pa = d.groupby([who, "game_pk", "at_bat_number"]).agg(
        rv=("delta_run_exp", "sum"),
        w_contender=("w_contender", "first"),
        w_division=("w_division", "first"),
    ).reset_index()
    pa["rv"] = pa["rv"] * sign

    out = None
    for wcol, name in (("w_contender", "contender_rv_delta"),
                       ("w_division", "division_rv_delta")):
        part = fight_mod.fight_delta(pa, who, "rv", wcol, min_n=60)
        part = part.rename(columns={
            "delta": name, "league_delta": f"{name}__lg", "n": f"{name}__n"})
        out = part if out is None else out.merge(part, on=who, how="outer")

    if not swings.empty:
        proc = fight_mod.fight_delta(swings, who, "_proc", "w_contender", min_n=80)
        proc = proc.rename(columns={
            "delta": "fight_process_delta",
            "league_delta": "fight_process_delta__lg",
            "n": "fight_process_delta__n"})
        out = out.merge(proc, on=who, how="outer")
    return out if out is not None else pd.DataFrame(columns=[who])


def _fielding_context(p: pd.DataFrame, season: int) -> pd.DataFrame:
    """Per-fielder situational exposure, for weighting a season-total OAA.

    OAA arrives as one number per player for the whole year, so individual
    plays cannot be weighted. What we can do is measure the situations that
    player actually fielded in: `fielder_2`..`fielder_9` name the man at each
    position on every pitch, so any batted ball can be attributed to the
    fielder who handled it, and read for leverage and FIGHT weight.

    Returns one row per player with `context`, the mean of leverage x FIGHT
    weight over his batted balls, normalized so the league mean is 1.0. A
    fielder who spent the year in tie games against contenders scores above
    1; one who mopped up blowouts scores below.
    """
    loc_col = "hit_location"
    fielder_cols = [c for c in ingest.FIELDER_COLS if c.startswith("fielder_")]
    have = [c for c in fielder_cols if c in p.columns]
    if loc_col not in p.columns or not have:
        warnings.warn(
            "no fielder attribution columns; HUNT uses unweighted OAA"
        )
        return pd.DataFrame(columns=["player_id", "context", "context__n"])

    # Only balls actually fielded by someone carry a hit_location.
    bip = p[pd.to_numeric(p[loc_col], errors="coerce").between(2, 9)].copy()
    if bip.empty:
        return pd.DataFrame(columns=["player_id", "context", "context__n"])

    standings = ingest.load_standings(season)
    quality = (
        fight_mod.opponent_quality(standings)
        if standings is not None and not standings.empty
        else pd.Series(dtype=float)
    )

    batting_home = bip["inning_topbot"].astype(str).str.startswith("Bot")
    # The fielding side is the one NOT batting.
    own = np.where(batting_home, bip["away_team"], bip["home_team"])
    opp = np.where(batting_home, bip["home_team"], bip["away_team"])

    dates = pd.to_datetime(bip["game_date"], errors="coerce")
    span = (dates.max() - dates.min()).days or 1
    pct = (dates - dates.min()).dt.days / span

    fight_w = fight_mod.fight_weight(
        pd.Series(opp, index=bip.index),
        pd.Series(own, index=bip.index),
        pct,
        quality,
    )
    lev = pd.to_numeric(bip.get("li", pd.Series(1.0, index=bip.index)),
                        errors="coerce").fillna(1.0)

    # Pick out the fielder at the position that handled the ball.
    pos = pd.to_numeric(bip[loc_col], errors="coerce").astype("Int64")
    who = pd.Series(pd.NA, index=bip.index, dtype="Float64")
    for n in range(2, 10):
        col = f"fielder_{n}"
        if col in bip.columns:
            who = who.mask(pos == n, pd.to_numeric(bip[col], errors="coerce"))

    d = pd.DataFrame({
        "player_id": who,
        "_w": lev.to_numpy() * fight_w.to_numpy(),
    }).dropna(subset=["player_id"])
    if d.empty:
        return pd.DataFrame(columns=["player_id", "context", "context__n"])

    g = d.groupby("player_id").agg(
        context=("_w", "mean"), context__n=("_w", "size")
    ).reset_index()
    g["player_id"] = g["player_id"].astype("int64")

    mean_w = d["_w"].mean()
    g["context"] = g["context"] / mean_w if mean_w else 1.0
    print(
        f"[xdawg] fielding context for {len(g):,} fielders "
        f"({len(d):,} batted balls attributed)"
    )
    return g


def run(
    season: int = SEASON_DEFAULT,
    refresh: bool = False,
    start: str | None = None,
    end: str | None = None,
    min_pa: int | None = None,
    min_bf: int | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Score a season. Pass start/end plus low min_pa/min_bf to smoke test."""
    min_pa = QUALIFY["hitter_min_pa"] if min_pa is None else min_pa
    min_bf = QUALIFY["pitcher_min_bf"] if min_bf is None else min_bf

    print(f"[xdawg] loading statcast {season} (first run is slow, then cached)")
    p = ingest.load_statcast(season, refresh=refresh, start=start, end=end)
    print(f"[xdawg] {len(p):,} pitches")
    if p.empty:
        raise SystemExit("[xdawg] no pitches returned - check the season/date range")

    # Statcast spells some clubs differently from config.TEAMS (Arizona is
    # AZ there, ARI here). These raw codes feed the FIGHT opponent lookup and
    # the site's league/division filters, both of which silently return
    # nothing on a miss -- so an unnormalized club loses its opponent-quality
    # weight without any error. Normalize once, here, and everything
    # downstream keys on the same spelling.
    for col in ("home_team", "away_team"):
        if col in p.columns:
            p[col] = p[col].map(ingest.normalize_team)
    seen = set(p["home_team"].dropna()) | set(p["away_team"].dropna())
    unknown = sorted(seen - set(TEAMS))
    if unknown:
        warnings.warn(
            f"team codes not in config.TEAMS: {unknown} -- these clubs get no "
            "division or opponent-quality weight; add them to "
            "ingest.TEAM_ALIASES or config.TEAMS"
        )

    print("[xdawg] computing empirical leverage index")
    p = add_leverage(p)

    names = ingest.player_names(p)

    # ---------------- hitters ----------------
    print("[xdawg] hitter pillars")
    h_frames = [
        H.bite(p),
        H.post_k_bounceback(p),
        H.grit(ingest.load_sprint_speed(season), H.hbp_frame(p),
               H.xbt_frame(p), H.availability_frame(p)),
        H.hunt(ingest.load_oaa(season), _fielding_context(p, season)),
        H.clutch_frame(p),
        _attach_fight(p, "batter", season),
    ]
    hit = _merge_all(h_frames, "batter").rename(columns={"batter": "player_id"})

    # at_bat_number restarts at 1 every game, so nunique() would cap every
    # player around 80. Count distinct (game, at-bat) pairs instead.
    pa_counts = (
        p.drop_duplicates(["batter", "game_pk", "at_bat_number"])
        .groupby("batter").size().rename("opportunities")
    )
    hit = hit.merge(pa_counts.reset_index().rename(columns={"batter": "player_id"}),
                    on="player_id", how="left")
    hit = hit[hit["opportunities"].fillna(0) >= min_pa]
    hit["team"] = hit["player_id"].map(_team_of(p, "batter"))
    hit["name"] = hit["player_id"].map(lambda i: names.get(int(i), str(i)))
    hit["pos"] = ""

    # ---------------- pitchers ----------------
    print("[xdawg] pitcher pillars")
    p_frames = [
        P.bite(p),
        P.post_hr_bounceback(p),
        P.grit(p),
        P.inherited_runners(p),
        P.hunt(p),
        _attach_fight(p, "pitcher", season),
    ]
    pit = _merge_all(p_frames, "pitcher").rename(columns={"pitcher": "player_id"})

    bf = (
        p.drop_duplicates(["pitcher", "game_pk", "at_bat_number"])
        .groupby("pitcher").size().rename("opportunities")
    )
    pit = pit.merge(bf.reset_index().rename(columns={"pitcher": "player_id"}),
                    on="player_id", how="left")
    pit = pit[pit["opportunities"].fillna(0) >= min_bf]
    pit["team"] = pit["player_id"].map(_team_of(p, "pitcher"))
    pit["name"] = pit["player_id"].map(lambda i: names.get(int(i), str(i)))
    pit["pos"] = ""

    print(f"[xdawg] scoring {len(hit)} hitters, {len(pit)} pitchers")
    return _score_both(hit, "hitter"), _score_both(pit, "pitcher")


# Pillar z-scores differ between the two variants, so each needs its own
# breakdown for the site. The self-referenced set is suffixed to keep both.
_PILLAR_COLS = ("BITE", "GRIT", "HUNT", "FIGHT")


def _score_both(df: pd.DataFrame, role: str) -> pd.DataFrame:
    """Score a role twice: league-baselined and self-referenced.

    DAWG+  / DAWG   compare each player to the LEAGUE. Talent stays in, so
                    this reads closer to a conventional leaderboard.
    wDAWG+ / wDAWG  compare each player to HIS OWN norms. Talent cancels
                    algebraically and only change-under-pressure survives --
                    the original xDAWG construction.

    Both come from one pass over the data: every delta component already
    carries its league-baselined twin, so this is two cheap rescorings of
    frames that are already built, not two trips to Savant.
    """
    league = compute(aggregate_league_view(df, role), role,
                     rate_name="DAWG+", count_name="DAWG")
    selfref = compute(df, role, rate_name="wDAWG+", count_name="wDAWG")

    keep = ["player_id", "wDAWG+", "wDAWG"] + [f"{c}_w" for c in _PILLAR_COLS]
    selfref = selfref.rename(columns={c: f"{c}_w" for c in _PILLAR_COLS})
    selfref = selfref[[c for c in keep if c in selfref.columns]]

    out = league.merge(selfref, on="player_id", how="left")
    return out.sort_values("DAWG+", ascending=False).reset_index(drop=True)
