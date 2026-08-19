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
        "generated": pd.Timestamp.now("UTC").strftime("%Y-%m-%d %H:%M UTC"),
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
    _stamp_cache_buster(site_dir, payload.get("generated", ""))
    return out


# Matches `src="data/data.js"` with or without an existing ?v= stamp.
_DATA_SRC = re.compile(r'src="data/data\.js(?:\?v=[^"]*)?"')


def _stamp_cache_buster(site_dir: Path, generated: str) -> None:
    """Version the data.js script tag so browsers pick up a new build.

    `index.html` is a static file at a stable URL, so a browser that has
    already cached `data/data.js` will keep serving the old numbers after a
    deploy -- the page looks stale even though the file on the server is
    correct. This bit us once: a successful build sat live for an hour
    looking like it had never deployed.

    Rewriting the tag to `data/data.js?v=<build timestamp>` gives each build
    a distinct URL, so the fetch misses cache exactly when it should and
    keeps hitting it the rest of the time.
    """
    index = site_dir / "index.html"
    if not index.exists():
        return
    version = "".join(ch for ch in str(generated) if ch.isdigit()) or "0"
    html = index.read_text(encoding="utf-8")
    stamped, n = _DATA_SRC.subn(f'src="data/data.js?v={version}"', html)
    if n and stamped != html:
        index.write_text(stamped, encoding="utf-8")
