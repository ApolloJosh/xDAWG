"""DAWG of the Day, Week and Month.

A season leaderboard answers "who is like this." These answer "who was like
this on Tuesday," and that is a different measurement problem: one day is
about four plate appearances, and every pillar in xDAWG is a rate compared
against a baseline. At four trips those rates are noise. Anything that
printed a confident DAWG+ for a single day would be lying with a straight
face.

So the award is decided by CONTRIBUTION, not by the pillars:

    score = sum over the window of ( WPA x FIGHT weight )

WPA is how far the player moved his own team's win probability, which is
already leverage-aware by construction -- a two-out single in a tie game in
the ninth moves it enormously and the same single in a 9-0 game moves it
almost not at all. The FIGHT weight then asks who it was against, using the
same opponent-quality engine the season metric uses. Summed rather than
averaged, because "of the Day" should reward the man who did it four times
over the man who did it once.

The four pillars are still computed over the window and shown underneath,
with the ordinary shrinkage applied. That shrinkage is the honesty: at a
day's sample every pillar collapses to nearly zero on its own, which is the
correct statement rather than a hidden one. By a month they carry real
weight. The reader can watch them wake up as the window grows.

Weeks run Monday to Sunday. Months are calendar months.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from . import fight as fight_mod
from . import ingest
from .config import PILLAR_WEIGHTS

# Windows below this many plate appearances are not eligible to win. Without
# it a September call-up with one pinch-hit homer in his only trip wins DAWG
# of the Day over a man who did it four times, and the award stops meaning
# anything the second anyone looks at it.
MIN_PA = {"day": 2, "week": 8, "month": 30}


def _plate_appearances(p: pd.DataFrame, season: int) -> pd.DataFrame:
    """One row per (player, role, plate appearance) with WPA and opponent weight.

    Both roles come out of one pass because a plate appearance IS the unit
    for both: the batter's gain in win probability is exactly the pitcher's
    loss, so the pitcher rows are the batter rows with the sign flipped.
    """
    need = {"game_pk", "at_bat_number", "game_date", "batter", "pitcher",
            "delta_home_win_exp", "inning_topbot", "home_team", "away_team"}
    missing = need - set(p.columns)
    if missing:
        raise SystemExit(f"[xdawg] awards need columns absent from the feed: {sorted(missing)}")

    d = p.sort_values(["game_pk", "at_bat_number", "pitch_number"]) \
         if "pitch_number" in p.columns else p.sort_values(["game_pk", "at_bat_number"])

    agg = {
        "wpa_home": ("delta_home_win_exp", "sum"),
        "batter": ("batter", "first"),
        "pitcher": ("pitcher", "first"),
        "game_date": ("game_date", "first"),
        "inning_topbot": ("inning_topbot", "first"),
        "home_team": ("home_team", "first"),
        "away_team": ("away_team", "first"),
    }
    for opt, how in (("inning", "first"), ("li", "max"), ("delta_run_exp", "sum"),
                     ("events", "last"), ("bat_score", "first"),
                     ("fld_score", "first")):
        if opt in d.columns:
            agg[opt] = (opt, how)

    pa = d.groupby(["game_pk", "at_bat_number"], sort=False).agg(**agg).reset_index()
    pa["game_date"] = pd.to_datetime(pa["game_date"], errors="coerce")
    pa = pa.dropna(subset=["game_date"])

    batting_home = pa["inning_topbot"].astype(str).str.startswith("Bot")
    # Win probability from the BATTING team's side. delta_home_win_exp is
    # signed for the home club, so a road hitter's contribution is its
    # negative -- get this backwards and every away player becomes the
    # mirror image of his real day.
    wpa_bat = np.where(batting_home, pa["wpa_home"], -pa["wpa_home"])

    bat_team = np.where(batting_home, pa["home_team"], pa["away_team"])
    fld_team = np.where(batting_home, pa["away_team"], pa["home_team"])

    standings = ingest.load_standings(season)
    quality = (fight_mod.opponent_quality(standings)
               if standings is not None and not standings.empty
               else pd.Series(dtype=float))

    span_days = (pa["game_date"].max() - pa["game_date"].min()).days or 1
    pct = (pa["game_date"] - pa["game_date"].min()).dt.days / span_days

    def weights(own, opp):
        return fight_mod.fight_weight(
            pd.Series(opp, index=pa.index), pd.Series(own, index=pa.index),
            pct, quality,
        ).to_numpy()

    rv = pd.to_numeric(pa.get("delta_run_exp", pd.Series(np.nan, index=pa.index)),
                       errors="coerce").to_numpy(dtype="float64", na_value=np.nan)

    common = {
        "game_pk": pa["game_pk"], "at_bat_number": pa["at_bat_number"],
        "game_date": pa["game_date"],
        "inning": pa.get("inning", pd.Series(np.nan, index=pa.index)),
        "li": pd.to_numeric(pa.get("li", pd.Series(1.0, index=pa.index)),
                            errors="coerce").fillna(1.0),
        "events": pa.get("events", pd.Series("", index=pa.index)).astype(str),
    }

    hit = pd.DataFrame(dict(
        common, player_id=pa["batter"], role="hitter", team=bat_team,
        opp=fld_team, wpa=wpa_bat, rv=rv, fight_w=weights(bat_team, fld_team),
    ))
    pit = pd.DataFrame(dict(
        common, player_id=pa["pitcher"], role="pitcher", team=fld_team,
        opp=bat_team, wpa=-wpa_bat, rv=-rv, fight_w=weights(fld_team, bat_team),
    ))
    out = pd.concat([hit, pit], ignore_index=True).dropna(subset=["player_id"])
    out["player_id"] = out["player_id"].astype("int64")
    out["score"] = out["wpa"] * out["fight_w"]
    return out


def window_key(d: pd.Timestamp, kind: str) -> str:
    if kind == "day":
        return d.strftime("%Y-%m-%d")
    if kind == "week":
        # ISO weeks run Monday to Sunday, which is what was asked for, so the
        # key is the Monday's date rather than a week number nobody can read.
        return (d - dt.timedelta(days=int(d.weekday()))).strftime("%Y-%m-%d")
    return d.strftime("%Y-%m")          # calendar month, not a trailing 30 days


def window_label(key: str, kind: str) -> str:
    if kind == "day":
        return pd.Timestamp(key).strftime("%B %-d, %Y")
    if kind == "week":
        a = pd.Timestamp(key)
        b = a + dt.timedelta(days=6)
        same = a.month == b.month
        return (f"{a.strftime('%B %-d')}–{b.strftime('%-d' if same else '%B %-d')}"
                f", {b.year}")
    return pd.Timestamp(key + "-01").strftime("%B %Y")


def _leaders(pa: pd.DataFrame, kind: str, names: dict, teams: dict,
             full_key: str | None = None) -> dict:
    """Rank every player inside every window of this kind.

    Returns {window_key: [ranked rows]}.

    Only the CURRENT window keeps every eligible player. Past windows keep
    each club's best man and the overall top five, which is what the team
    and league filters need and nothing more -- a full board for all 150
    days of a season came to 2.7 MB, and a page that takes three seconds to
    load so the reader can filter to rows he will never open is a bad trade.
    Trimmed rows also drop the "why he won" detail, which only the featured
    award displays.
    """
    from .config import TEAMS

    d = pa.copy()
    d["_w"] = d["game_date"].map(lambda x: window_key(x, kind))

    g = d.groupby(["_w", "player_id", "role"]).agg(
        score=("score", "sum"),
        wpa=("wpa", "sum"),
        rv=("rv", "sum"),
        n=("score", "size"),
        games=("game_pk", "nunique"),
        team=("team", "last"),
        opp_w=("fight_w", "mean"),
        top_li=("li", "max"),
    ).reset_index()

    # The single biggest swing in the window, for the "why he won" line.
    best_idx = d.groupby(["_w", "player_id", "role"])["score"].idxmax()
    best = d.loc[best_idx, ["_w", "player_id", "role", "game_date", "inning",
                            "events", "li", "wpa", "opp"]]
    best = best.rename(columns={c: f"best_{c}" for c in
                                ("game_date", "inning", "events", "li", "wpa", "opp")})
    g = g.merge(best, on=["_w", "player_id", "role"], how="left")

    g = g[g["n"] >= MIN_PA[kind]]
    g = g.sort_values(["_w", "score"], ascending=[True, False])

    out: dict[str, list] = {}
    for key, chunk in g.groupby("_w"):
        full = (key == full_key)
        if not full:
            # Each club's best, plus the overall top five so the MLB view has
            # a podium rather than only a winner.
            best_per_team = chunk.drop_duplicates("team", keep="first")
            chunk = pd.concat([chunk.head(5), best_per_team]) \
                      .drop_duplicates(["player_id", "role"]) \
                      .sort_values("score", ascending=False)
        rows = []
        for rank, (_, r) in enumerate(chunk.iterrows(), 1):
            pid = int(r["player_id"])
            team = str(teams.get((pid, r["role"]), r["team"]) or r["team"])
            lg, div = TEAMS.get(team, ("", ""))
            row = {
                "rank": rank,
                "id": pid,
                "name": names.get(pid, str(pid)),
                "role": r["role"],
                "team": team,
                "league": lg,
                "score": round(float(r["score"]), 4),
                "wpa": round(float(r["wpa"]), 4),
                "n": int(r["n"]),
                "games": int(r["games"]),
            }
            if full:
                ev = str(r.get("best_events", "") or "").replace("_", " ")
                row.update({
                    "division": div,
                    "rv": None if pd.isna(r["rv"]) else round(float(r["rv"]), 3),
                    "opp_w": round(float(r["opp_w"]), 3),
                    "best": {
                        "date": (None if pd.isna(r.get("best_game_date")) else
                                 pd.Timestamp(r["best_game_date"]).strftime("%Y-%m-%d")),
                        "inning": (None if pd.isna(r.get("best_inning"))
                                   else int(r["best_inning"])),
                        "event": ev if ev and ev.lower() != "nan" else "",
                        "li": (None if pd.isna(r.get("best_li"))
                               else round(float(r["best_li"]), 2)),
                        "wpa": (None if pd.isna(r.get("best_wpa"))
                                else round(float(r["best_wpa"]), 4)),
                        "opp": str(r.get("best_opp", "") or ""),
                    },
                })
            rows.append(row)
        out[key] = rows
    return out


def _window_pillars(p: pd.DataFrame, season: int, start, end) -> dict:
    """The four pillars, computed over one window only.

    Deliberately runs the ordinary scoring path with ordinary shrinkage. A
    window of a few days has a handful of opportunities against stabilization
    constants in the hundreds, so `z * n/(n+k)` drives every pillar to nearly
    zero and the panel says, truthfully, that a few days cannot tell you
    about a player's approach. Nothing here special-cases the small sample --
    the existing machinery already handles it correctly, and suppressing it
    would have meant inventing a second, less honest scoring path.
    """
    from .pipeline import run_frames

    sl = p[(p["game_date"] >= start) & (p["game_date"] <= end)]
    if sl.empty:
        return {}
    try:
        # names={} skips the reverse-lookup network call: the ids are already
        # known here and only the numbers are wanted.
        hit, pit = run_frames(sl, season, min_pa=1, min_bf=1, names={}, quiet=True)
    except Exception as e:                                    # noqa: BLE001
        import warnings
        warnings.warn(
            f"window pillars unavailable ({e}); awards show contribution only"
        )
        return {}

    from .aggregate import score_pillar

    out = {}
    for role, df in (("hitter", hit), ("pitcher", pit)):
        if df is None or df.empty:
            continue
        # raw=True, NOT the scored BITE/GRIT/... columns already on `df`.
        # Those went through a final z-score that re-standardizes a pillar to
        # sd 1 however little evidence built it -- so a single day would print
        # pillar bars every bit as confident as a full season's, which is the
        # exact lie this module's docstring promises not to tell. The raw
        # total keeps the shrinkage visible.
        raws = {pil: score_pillar(df, role, pil.lower(), raw=True)[0]
                for pil in ("BITE", "GRIT", "HUNT", "FIGHT")}
        for i, pid in df["player_id"].items():
            out[(int(pid), role)] = {
                pil: (None if pd.isna(s.get(i)) else round(float(s[i]), 3))
                for pil, s in raws.items()
            }
    return out


def build_awards(p: pd.DataFrame, season: int, names: dict,
                 teams: dict | None = None) -> dict:
    """Day / week / month winners for the whole season, newest first."""
    pa = _plate_appearances(p, season)
    teams = teams or {}

    # Which window is "current" has to be decided before ranking, because it
    # decides which board keeps its full roster.
    latest = {kind: max(pa["game_date"].map(lambda x: window_key(x, kind)))
              for kind in ("day", "week", "month")}
    boards = {kind: _leaders(pa, kind, names, teams, full_key=latest[kind])
              for kind in ("day", "week", "month")}
    latest = {kind: (k if k in boards[kind] else (max(boards[kind]) if boards[kind] else None))
              for kind, k in latest.items()}

    # Pillars only for the three CURRENT windows. Computing them for every
    # historical week and month would mean thirty-odd full pillar passes for
    # rows nobody opens, and the pillar panel is context for the featured
    # award rather than something the archive rows show.
    pillars = {}
    p = p.copy()
    p["game_date"] = pd.to_datetime(p["game_date"], errors="coerce")
    for kind, key in latest.items():
        if key is None:
            continue
        if kind == "day":
            start = end = pd.Timestamp(key)
        elif kind == "week":
            start = pd.Timestamp(key)
            end = start + dt.timedelta(days=6)
        else:
            start = pd.Timestamp(key + "-01")
            end = start + pd.offsets.MonthEnd(1)
        print(f"[xdawg] window pillars for the current {kind} "
              f"({start.date()} to {end.date()})")
        got = _window_pillars(p, season, start, end)
        pillars[kind] = {f"{pid}|{role}": v for (pid, role), v in got.items()}

    return {
        "season": season,
        "generated": pd.Timestamp.now("UTC").strftime("%Y-%m-%d %H:%M UTC"),
        "through": pa["game_date"].max().strftime("%Y-%m-%d"),
        "min_pa": MIN_PA,
        "pillar_weights": PILLAR_WEIGHTS,
        "latest": latest,
        "labels": {kind: {k: window_label(k, kind) for k in b}
                   for kind, b in boards.items()},
        "boards": boards,
        "pillars": pillars,
    }
