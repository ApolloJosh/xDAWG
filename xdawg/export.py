"""Write the scored leaderboard out as the static site's data file."""

from __future__ import annotations

import json
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
    "hard_hit_delta": "Hard-hit rate",
    "post_k_bounceback": "Bounce-back after a K",
    "hustle_ratio": "Hustle down the line",
    "hbp_above_expected": "HBP above expected",
    "extra_bases_taken": "Extra bases taken",
    "availability": "Availability",
    "star_catch_lev": "Star catches (leveraged)",
    "attempt_rate": "Attempts on tough balls",
    "assists_blocks_lev": "Assists and blocks",
    "baserunning_lev": "Baserunning in big spots",
    "fight_rv_delta": "Production vs. contenders",
    "fight_process_delta": "Approach vs. contenders",
    "two_strike_stuff_delta": "Two-strike stuff",
    "attack_delta": "Attacking with runners on",
    "bb_delta_lev": "Walks in big spots",
    "post_hr_bounceback": "Bounce-back after a HR",
    "stuff_after_75": "Stuff after pitch 75",
    "third_time_through": "Third time through order",
    "inherited_runners": "Inherited runners",
    "workload": "Workload willingness",
    "pitching_inside": "Pitching inside",
    "risp_stuff_delta": "Stuff with RISP",
    "putaway_lev": "Put-away rate",
    "jam_escape_process": "Escaping jams",
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
                    "weight": PILLAR_WEIGHTS[role][key],
                    "components": [
                        {"key": c, "label": LABELS.get(c, c), "z": _clean(r.get(f"_c_{c}"))}
                        for c in COMPONENTS[role][key]
                        if _clean(r.get(f"_c_{c}")) is not None
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
                "xdawg": _clean(r.get("xDAWG")),
                "dawg": _clean(r.get("DAWG")),
                "opportunities": _clean(r.get("opportunities")),
                "pillars": breakdown,
            })

    players.sort(key=lambda p: (p["xdawg"] is None, -(p["xdawg"] or 0)))
    for i, p in enumerate(players, 1):
        p["rank"] = i

    return {
        "season": season,
        "synthetic": synthetic,
        "generated": pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "pillars": list(PILLARS),
        "teams": sorted(TEAMS),
        "players": players,
    }


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
    return out
