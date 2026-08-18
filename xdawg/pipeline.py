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
from .aggregate import compute
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
    tmp = pd.DataFrame({who: p[who], "team": team}).dropna()
    return tmp.groupby(who)["team"].agg(lambda s: s.value_counts().idxmax())


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

    d["fight_w"] = fight_mod.fight_weight(
        pd.Series(d["_opp"].values, index=d.index),
        pd.Series(d["_own"].values, index=d.index),
        d["_pct"],
        quality,
    )

    pa = d.groupby([who, "game_pk", "at_bat_number"]).agg(
        rv=("delta_run_exp", "sum"), fight_w=("fight_w", "first")
    ).reset_index()
    pa["rv"] = pa["rv"] * sign

    out = fight_mod.fight_delta(pa, who, "rv", "fight_w", min_n=60)
    return out.rename(columns={"delta": "fight_rv_delta", "n": "fight_rv_delta__n"})


def run(season: int = SEASON_DEFAULT, refresh: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    print(f"[xdawg] loading statcast {season} (first run is slow, then cached)")
    p = ingest.load_statcast(season, refresh=refresh)
    print(f"[xdawg] {len(p):,} pitches")

    print("[xdawg] computing empirical leverage index")
    p = add_leverage(p)

    names = ingest.player_names(p)

    # ---------------- hitters ----------------
    print("[xdawg] hitter pillars")
    h_frames = [
        H.bite(p),
        H.post_k_bounceback(p),
        H.grit(ingest.load_sprint_speed(season), None, None, None),
        H.hunt(ingest.load_catch_probability(season), p),
        _attach_fight(p, "batter", season),
    ]
    hit = _merge_all(h_frames, "batter").rename(columns={"batter": "player_id"})

    pa_counts = p.groupby("batter")["at_bat_number"].nunique().rename("opportunities")
    hit = hit.merge(pa_counts.reset_index().rename(columns={"batter": "player_id"}),
                    on="player_id", how="left")
    hit = hit[hit["opportunities"].fillna(0) >= QUALIFY["hitter_min_pa"]]
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

    bf = p.groupby("pitcher")["at_bat_number"].nunique().rename("opportunities")
    pit = pit.merge(bf.reset_index().rename(columns={"pitcher": "player_id"}),
                    on="player_id", how="left")
    pit = pit[pit["opportunities"].fillna(0) >= QUALIFY["pitcher_min_bf"]]
    pit["team"] = pit["player_id"].map(_team_of(p, "pitcher"))
    pit["name"] = pit["player_id"].map(lambda i: names.get(int(i), str(i)))
    pit["pos"] = ""

    print(f"[xdawg] scoring {len(hit)} hitters, {len(pit)} pitchers")
    return compute(hit, "hitter"), compute(pit, "pitcher")
