"""Write the scored leaderboard out as the static site's data file."""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .config import COMPONENTS, PILLAR_WEIGHTS, SEASON_DEFAULT, TEAMS

PILLARS = ("BITE", "GRIT", "HUNT", "FIGHT")

# Human-readable labels for the per-player breakdown panel.
LABELS = {
    "whiff_delta": "Whiff rate under pressure",
    "chase_contact": "Contact on chases",
    "pitches_per_pa_delta": "Pitches seen per PA",
    "two_strike_foul_delta": "Two-strike foul-offs",
    "ev_situational": "Exit velocity when it mattered",
    "wpa_clutch_delta": "Win probability added, clutch",
    "post_k_bounceback": "Bounce-back after a K",
    "hustle_ratio": "Hustle down the line",
    "hbp_above_expected": "HBP above expected",
    "extra_bases_taken": "Extra bases taken",
    "availability": "Availability",
    "oaa_situational": "Defense when it mattered",
    "oaa_rate": "Outs above average, per chance",
    "assists_blocks_lev": "Assists and blocks",
    "baserunning_lev": "Baserunning in big spots",
    "contender_rv_delta": "Production vs. contenders",
    "division_rv_delta": "Production vs. division rivals",
    "fight_process_delta": "Approach vs. contenders",
    "two_strike_stuff_delta": "Two-strike stuff",
    "attack_delta": "Attacking with runners on",
    "bb_delta_lev": "Walks in big spots",
    "post_hr_bounceback": "Bounce-back after a HR",
    "stuff_after_75": "Stuff after pitch 75",
    "third_time_through": "Third time through order",
    "inherited_runners": "Inherited runners",
    "long_start_rate": "Starts of 5+ innings",
    "blowup_rate": "Knocked out early",
    "workload": "Workload willingness",
    "pitching_inside": "Pitching inside",
    "risp_stuff_delta": "Stuff with RISP",
    "putaway_lev": "Put-away rate",
    "jam_escape_process": "Escaping jams, process",
    "jam_escape_runs": "Escaping jams, damage",
}


def _clean(v) -> float | None:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return None
    try:
        f = float(v)
        return round(f, 4) if np.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def build_payload(
    hitters: pd.DataFrame,
    pitchers: pd.DataFrame,
    season: int = SEASON_DEFAULT,
    synthetic: bool = False,
    standings: pd.DataFrame | None = None,
) -> dict:
    players = []
    for role, df in (("hitter", hitters), ("pitcher", pitchers)):
        if df is None or df.empty:
            continue
        comp_names = [c for p in COMPONENTS[role].values() for c in p]
        for _, r in df.iterrows():
            team = str(r.get("team", ""))
            lg, div = TEAMS.get(team, ("", ""))
            breakdown = {}
            for p in PILLARS:
                key = p.lower()
                breakdown[p] = {
                    "z": _clean(r.get(p)),
                    "z_w": _clean(r.get(f"{p}_w")),
                    "weight": PILLAR_WEIGHTS[role][key],
                    # Zero-weight components are omitted: they are kept in
                    # config to document the intended shape of a pillar, but
                    # showing one in the breakdown panel implies it moved the
                    # score when it contributed exactly nothing.
                    "components": [
                        {"key": c, "label": LABELS.get(c, c), "z": _clean(r.get(f"_c_{c}"))}
                        for c, cfg in COMPONENTS[role][key].items()
                        if cfg["weight"] > 0 and _clean(r.get(f"_c_{c}")) is not None
                    ],
                }
            players.append({
                "id": int(r.get("player_id", 0)),
                "name": str(r.get("name", "Unknown")),
                "team": team,
                "league": lg,
                "division": div,
                "role": role,
                "pos": str(r.get("pos", "")),
                # Four numbers now. DAWG+/DAWG compare to the LEAGUE, so
                # talent stays in; wDAWG+/wDAWG compare each player to his
                # own norms, so only change-under-pressure survives.
                "dawg_plus": _clean(r.get("DAWG+")),
                "dawg": _clean(r.get("DAWG")),
                "wdawg_plus": _clean(r.get("wDAWG+")),
                "wdawg": _clean(r.get("wDAWG")),
                "opportunities": _clean(r.get("opportunities")),
                "pillars": breakdown,
            })

    players.sort(key=lambda p: (p["dawg_plus"] is None, -(p["dawg_plus"] or 0)))
    for i, p in enumerate(players, 1):
        p["rank"] = i

    return {
        "season": season,
        "synthetic": synthetic,
        "generated": pd.Timestamp.now("UTC").strftime("%Y-%m-%d %H:%M UTC"),
        "pillars": list(PILLARS),
        "teams": sorted(TEAMS),
        "players": players,
        "team_table": build_teams(players, standings),
    }


def build_teams(players: list[dict], standings: pd.DataFrame | None) -> list[dict]:
    """Team totals, for comparing the DAWG stats against actual records.

    The headline is the SUM of each club's cumulative scores, so depth and
    playing time count -- a team of eight good regulars should outrank one
    with three stars and five passengers. The roster's mean rate sits beside
    it, because the sum alone rewards simply having more qualified players.

    Records come from StatsAPI. Note FIGHT uses Pythagorean expectation from
    runs rather than actual W-L, so wins here are genuinely independent of
    anything that fed the player scores -- which is what makes the comparison
    worth looking at rather than circular.
    """
    rec = {}
    if standings is not None and not standings.empty:
        for _, r in standings.iterrows():
            rec[str(r.get("team", ""))] = {
                "wins": _clean(r.get("wins")),
                "losses": _clean(r.get("losses")),
                "rs": _clean(r.get("rs")),
                "ra": _clean(r.get("ra")),
            }

    by_team: dict[str, list[dict]] = {}
    for p in players:
        if p.get("team"):
            by_team.setdefault(p["team"], []).append(p)

    rows = []
    for team, roster in by_team.items():
        def total(key):
            vals = [p[key] for p in roster if p.get(key) is not None]
            return round(sum(vals), 2) if vals else None

        def mean(key):
            vals = [p[key] for p in roster if p.get(key) is not None]
            return round(sum(vals) / len(vals), 1) if vals else None

        r = rec.get(team, {})
        w, l = r.get("wins"), r.get("losses")
        lg, div = TEAMS.get(team, ("", ""))
        rows.append({
            "team": team, "league": lg, "division": div,
            "players": len(roster),
            "hitters": sum(1 for p in roster if p["role"] == "hitter"),
            "pitchers": sum(1 for p in roster if p["role"] == "pitcher"),
            "dawg": total("dawg"), "dawg_plus": mean("dawg_plus"),
            "wdawg": total("wdawg"), "wdawg_plus": mean("wdawg_plus"),
            "wins": w, "losses": l,
            "win_pct": round(w / (w + l), 3) if w is not None and l and (w + l) else None,
            "run_diff": (round(r["rs"] - r["ra"]) if r.get("rs") is not None
                         and r.get("ra") is not None else None),
        })

    rows.sort(key=lambda x: (x["dawg"] is None, -(x["dawg"] or 0)))
    for i, x in enumerate(rows, 1):
        x["rank"] = i
    return rows


def write_site_data(payload: dict, site_dir: str | Path) -> Path:
    """Emit `data.js` as a plain script assignment.

    Deliberately not JSON+fetch: a script tag works when the page is opened
    straight off the filesystem, with no server and no CORS argument. Very
    2005, and it means the site just opens.
    """
    site_dir = Path(site_dir)
    (site_dir / "data").mkdir(parents=True, exist_ok=True)
    out = site_dir / "data" / "data.js"
    out.write_text(
        "window.XDAWG_DATA = " + json.dumps(payload, separators=(",", ":")) + ";\n",
        encoding="utf-8",
    )
    _stamp_cache_buster(site_dir, payload.get("generated", ""))
    return out


def _stamp_cache_buster(
    site_dir: Path,
    generated: str,
    page: str = "index.html",
    data: str = "data/data.js",
) -> None:
    """Version the data script tag so browsers pick up a new build.

    `index.html` is a static file at a stable URL, so a browser that has
    already cached `data/data.js` will keep serving the old numbers after a
    deploy -- the page looks stale even though the file on the server is
    correct. This bit us once: a successful build sat live for an hour
    looking like it had never deployed.

    Rewriting the tag to `data/data.js?v=<build timestamp>` gives each build
    a distinct URL, so the fetch misses cache exactly when it should and
    keeps hitting it the rest of the time.

    Parameterized over page and data file because the history view has
    exactly the same problem and would have hit it exactly the same way.
    """
    target = site_dir / page
    if not target.exists():
        return
    version = "".join(ch for ch in str(generated) if ch.isdigit()) or "0"
    pattern = re.compile(r'src="' + re.escape(data) + r'(?:\?v=[^"]*)?"')
    html = target.read_text(encoding="utf-8")
    stamped, n = pattern.subn(f'src="{data}?v={version}"', html)
    if n and stamped != html:
        target.write_text(stamped, encoding="utf-8")
