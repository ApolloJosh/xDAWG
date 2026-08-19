"""Multi-season DAWG: year-over-year player consistency and team October.

Two questions this answers that a single-season leaderboard cannot:

  1. Is a player's DAWG number a TRAIT or a coincidence? A metric that says
     something real about a player should say roughly the same thing about
     him next year. Clutch metrics famously do not, and one of xDAWG's stated
     pass conditions is beating raw clutch on stability -- which is not
     checkable at all with one season on the board.

  2. Does team DAWG have anything to do with winning in October? The team
     board already compares DAWG against regular-season record, but the whole
     folk claim being formalized here is about the postseason.

Every season is scored INDEPENDENTLY and then compared. That matters: DAWG+
is z-scored within its own season against its own empirically-derived
leverage index, so 112 in 2023 and 112 in 2026 mean the same thing -- "this
far above his league, that year" -- even though the run environments differ.
Pooling the seasons into one scoring pass instead would have quietly let a
high-offense year masquerade as a league full of dawgs.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import ingest
from .config import SEASON_DEFAULT
from .export import build_payload

# Statcast's pitch-tracking era. Earlier seasons exist but lack the
# pitch-level fields every pillar is built from, so a request for them would
# produce a leaderboard of nulls rather than an error.
FIRST_TRACKED_SEASON = 2015

_SLIM = ("id", "name", "role", "team", "dawg_plus", "dawg",
         "wdawg_plus", "wdawg", "opportunities")
_TEAM_SLIM = ("team", "dawg", "dawg_plus", "wdawg", "wdawg_plus",
              "wins", "losses", "win_pct", "run_diff", "players")


def score_season(season: int, refresh: bool = False) -> dict:
    """Run the full pipeline for one season and keep only the headline numbers.

    The breakdown panels are dropped here on purpose. A season's worth of
    per-component z-scores is most of the payload's weight, and the history
    view never reads them -- keeping them would put a multi-megabyte file in
    front of every page load to show a sparkline.
    """
    from .pipeline import run

    hit, pit = run(season=season, refresh=refresh)
    standings = ingest.load_standings(season)
    payload = build_payload(hit, pit, season=season, synthetic=False,
                            standings=standings)

    post = ingest.load_postseason(season)
    rounds = {}
    if post is not None and not post.empty:
        rounds = {
            str(r["team"]): {"round": int(r["round"]),
                             "round_label": str(r.get("round_label", "")),
                             "ps_wins": int(r.get("ps_wins", 0))}
            for _, r in post.iterrows()
        }

    teams = []
    for t in payload["team_table"]:
        row = {k: t.get(k) for k in _TEAM_SLIM}
        row.update(rounds.get(t["team"], {"round": 0, "round_label": "Missed",
                                          "ps_wins": 0}))
        teams.append(row)

    return {
        "season": season,
        "players": [{k: p.get(k) for k in _SLIM} for p in payload["players"]],
        "teams": teams,
    }


def _trend(seasons: list[int], values: list[float]) -> float | None:
    """Least-squares slope in DAWG+ per year. Needs at least two seasons."""
    if len(seasons) < 2:
        return None
    return round(float(np.polyfit(seasons, values, 1)[0]), 2)


def _consistency(values: list[float]) -> float | None:
    """Spread of a player's seasons, as a plain standard deviation.

    Reported rather than inverted into a 0-100 "consistency score" because
    the units are the ones already on the board: a standard deviation of 8
    DAWG+ points is directly comparable to the 25-point scale everything else
    is quoted on, and a manufactured index would not be.
    """
    if len(values) < 2:
        return None
    return round(float(np.std(values, ddof=1)), 1)


def _corr(x: list[float], y: list[float]) -> float | None:
    if len(x) < 3 or np.std(x) == 0 or np.std(y) == 0:
        return None
    return round(float(np.corrcoef(x, y)[0, 1]), 3)


def build_history(seasons: list[int], refresh: bool = False) -> dict:
    """Score every requested season and assemble the cross-year view."""
    seasons = sorted({int(s) for s in seasons})
    too_old = [s for s in seasons if s < FIRST_TRACKED_SEASON]
    if too_old:
        raise SystemExit(
            f"[xdawg] {too_old} predate pitch tracking ({FIRST_TRACKED_SEASON}+); "
            "every pillar is built from pitch-level fields those seasons lack"
        )

    slices = []
    for s in seasons:
        print(f"\n[xdawg] ===== season {s} =====")
        slices.append(score_season(s, refresh=refresh))

    # ---------------- players ----------------
    by_player: dict[tuple[int, str], dict] = {}
    for sl in slices:
        for p in sl["players"]:
            key = (p["id"], p["role"])
            rec = by_player.setdefault(key, {
                "id": p["id"], "role": p["role"], "name": p["name"],
                "seasons": {},
            })
            # Most recent name and club win: a player who changed either
            # should read as who he is now, not who he was in 2019.
            rec["name"] = p["name"]
            rec["team"] = p["team"]
            rec["seasons"][str(sl["season"])] = {
                k: p.get(k) for k in
                ("team", "dawg_plus", "dawg", "wdawg_plus", "wdawg",
                 "opportunities")
            }

    players = []
    for rec in by_player.values():
        yrs = sorted(int(y) for y in rec["seasons"])
        vals = [rec["seasons"][str(y)]["dawg_plus"] for y in yrs]
        pairs = [(y, v) for y, v in zip(yrs, vals) if v is not None]
        ys, vs = [p[0] for p in pairs], [p[1] for p in pairs]
        rec["n_seasons"] = len(pairs)
        rec["mean_dawg_plus"] = round(float(np.mean(vs)), 1) if vs else None
        rec["sd_dawg_plus"] = _consistency(vs)
        rec["best"] = max(vs) if vs else None
        rec["worst"] = min(vs) if vs else None
        rec["trend"] = _trend(ys, vs)
        players.append(rec)

    players.sort(key=lambda r: (r["n_seasons"] < 2,
                                -(r["mean_dawg_plus"] or -999)))

    # Year-over-year stability, the headline number for question 1: correlate
    # each player's DAWG+ with his own DAWG+ the following season, pooled
    # across every consecutive pair in the window. This is the same
    # construction used to report that a metric "stabilizes" -- and it is the
    # honest test, because a metric can look stable simply by being dominated
    # by playing time.
    stability = {}
    for a, b in zip(seasons, seasons[1:]):
        pairs = [
            (r["seasons"][str(a)]["dawg_plus"], r["seasons"][str(b)]["dawg_plus"])
            for r in players
            if str(a) in r["seasons"] and str(b) in r["seasons"]
            and r["seasons"][str(a)]["dawg_plus"] is not None
            and r["seasons"][str(b)]["dawg_plus"] is not None
        ]
        stability[f"{a}->{b}"] = {
            "n": len(pairs),
            "r": _corr([p[0] for p in pairs], [p[1] for p in pairs]),
        }

    # ---------------- teams ----------------
    by_team: dict[str, dict] = {}
    for sl in slices:
        for t in sl["teams"]:
            rec = by_team.setdefault(t["team"], {"team": t["team"], "seasons": {}})
            rec["seasons"][str(sl["season"])] = {
                k: t.get(k) for k in
                ("dawg", "dawg_plus", "wdawg", "wdawg_plus", "wins", "losses",
                 "win_pct", "run_diff", "round", "round_label", "ps_wins")
            }

    teams = sorted(by_team.values(), key=lambda r: r["team"])

    flat = [t for rec in by_team.values() for t in rec["seasons"].values()]
    def pull(a, b):
        pairs = [(t[a], t[b]) for t in flat
                 if t.get(a) is not None and t.get(b) is not None]
        return _corr([p[0] for p in pairs], [p[1] for p in pairs]), len(pairs)

    corr = {}
    for name, (a, b) in {
        "dawg_vs_win_pct": ("dawg", "win_pct"),
        "dawg_plus_vs_win_pct": ("dawg_plus", "win_pct"),
        "dawg_vs_postseason_round": ("dawg", "round"),
        "dawg_vs_postseason_wins": ("dawg", "ps_wins"),
        "wdawg_vs_postseason_round": ("wdawg", "round"),
    }.items():
        r, n = pull(a, b)
        corr[name] = {"r": r, "n": n}

    return {
        "generated": pd.Timestamp.now("UTC").strftime("%Y-%m-%d %H:%M UTC"),
        "seasons": seasons,
        "players": players,
        "teams": teams,
        "stability": stability,
        "correlations": corr,
    }


def write_history(payload: dict, site_dir: str | Path) -> Path:
    """Write history.js beside data.js, in the same `window.X = {...}` shape."""
    site_dir = Path(site_dir)
    out = site_dir / "data" / "history.js"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "window.XDAWG_HISTORY = "
        + json.dumps(payload, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )
    # Same cache stamp as data.js, for the same reason: history.html sits at
    # a stable URL, so without a versioned query string a browser that has
    # seen one build keeps serving it after the next one deploys. That
    # failure mode already cost four rounds of "the stats are broken" on the
    # main page; it is not worth rediscovering here.
    from .export import _stamp_cache_buster

    _stamp_cache_buster(site_dir, payload.get("generated", ""),
                        page="history.html", data="data/history.js")
    return out


def summarize(payload: dict) -> str:
    """A console readout, so a 4-hour CI run reports something legible."""
    lines = [f"\n[xdawg] history: {payload['seasons']}"]
    lines.append(f"[xdawg] {len(payload['players'])} player-careers, "
                 f"{len(payload['teams'])} clubs")

    lines.append("\n  YEAR-OVER-YEAR STABILITY OF DAWG+")
    for span, s in payload["stability"].items():
        r = "n/a" if s["r"] is None else f"{s['r']:+.3f}"
        lines.append(f"    {span}   r = {r}   (n = {s['n']})")

    lines.append("\n  TEAM DAWG vs WHAT HAPPENED")
    for name, c in payload["correlations"].items():
        r = "n/a" if c["r"] is None else f"{c['r']:+.3f}"
        lines.append(f"    {name:<30} r = {r}   (n = {c['n']})")

    steady = [p for p in payload["players"]
              if p["n_seasons"] >= 3 and p["sd_dawg_plus"] is not None]
    steady.sort(key=lambda p: -(p["mean_dawg_plus"] or 0))
    if steady:
        lines.append("\n  MOST DAWG, 3+ SEASONS  (mean DAWG+ / sd)")
        for p in steady[:10]:
            lines.append(f"    {p['name']:<26} {p['team']:<4} "
                         f"{p['mean_dawg_plus']:6.1f}  +/- {p['sd_dawg_plus']:.1f}"
                         f"   ({p['n_seasons']} yrs)")
    return "\n".join(lines)


DEFAULT_WINDOW = [SEASON_DEFAULT - 2, SEASON_DEFAULT - 1, SEASON_DEFAULT]
