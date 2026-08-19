"""
Data ingestion with an aggressive local cache.

A full season of Statcast is roughly 700k pitches and takes a long while to
pull. We cache to Parquet on first fetch and never ask twice. Every optional
leaderboard degrades gracefully -- if Savant is unreachable or a schema has
drifted, the affected components drop out and their pillar weights
renormalize rather than crashing the run.
"""

from __future__ import annotations

import datetime as _dt
import os
import warnings
from pathlib import Path

import pandas as pd

CACHE = Path(os.environ.get("XDAWG_CACHE", Path.home() / ".xdawg_cache"))
CACHE.mkdir(parents=True, exist_ok=True)

STATCAST_COLS = [
    "game_pk", "game_date", "at_bat_number", "pitch_number", "pitch_type",
    "batter", "pitcher", "player_name", "events", "description", "type", "zone",
    "plate_x", "plate_z", "balls", "strikes", "stand", "p_throws",
    "launch_speed", "launch_angle", "release_speed", "pfx_x", "pfx_z",
    "release_spin_rate", "release_extension", "delta_home_win_exp",
    "delta_run_exp", "inning", "inning_topbot", "outs_when_up",
    "on_1b", "on_2b", "on_3b", "bat_score", "fld_score",
    "home_team", "away_team",
]

# Needed to attribute a batted ball to the individual fielder who handled it,
# which is what lets a season-total OAA be weighted by the leverage and
# opponent quality that player was actually exposed to. Kept separate from
# STATCAST_COLS so their absence is a degraded HUNT rather than a "schema
# drift" alarm about the core feed.
FIELDER_COLS = ["hit_location"] + [f"fielder_{i}" for i in range(2, 10)]


def _cache_path(name: str) -> Path:
    return CACHE / f"{name}.parquet"


def _read_cache(name: str, required: tuple[str, ...] = ()) -> pd.DataFrame | None:
    """Read a cached frame, but only if it still has the columns we need.

    The cache is keyed on season alone, so a parquet written before a loader
    learned to fetch a new field keeps being served forever and the new field
    silently never appears. That is exactly what happened to team W-L: runs
    scored and allowed came back fine because they were always cached, while
    `wins` stayed empty on every team because the cached standings predated
    it. Nothing errored; the column was just quietly absent.

    Treating a schema-short cache as a miss means adding a column to a loader
    is self-healing on the next run rather than needing a manual cache purge.
    """
    path = _cache_path(name)
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    missing = [c for c in required if c not in df.columns]
    if missing:
        print(f"[xdawg] cache {name} predates {missing}; refetching")
        return None
    return df


def pick_col(df: pd.DataFrame, *candidates: str) -> str | None:
    """Find the first candidate column actually present, loosely matched.

    Savant's leaderboard CSVs rename columns without warning, and the names
    drift in predictable, cosmetic ways -- `catch_probability` becomes
    `catch_prob`, spaces become underscores, casing flips. Matching loosely
    on a list of candidates means a rename costs us a warning rather than a
    KeyError forty minutes into a build.

    Returns the real column name, or None if nothing matched.
    """
    def norm(s: str) -> str:
        return "".join(ch for ch in str(s).lower() if ch.isalnum())

    lookup = {norm(c): c for c in df.columns}
    for cand in candidates:
        hit = lookup.get(norm(cand))
        if hit is not None:
            return hit
    # Fall back to containment in EITHER direction, so `catch_probability`
    # finds both `five_star_catch_probability` (longer) and `catch_prob`
    # (shorter). Require 4+ characters so short names like `n` or `rs` can't
    # match half the frame by accident.
    for cand in candidates:
        n = norm(cand)
        if len(n) < 4:
            continue
        for key, real in lookup.items():
            if len(key) >= 4 and (n in key or key in n):
                return real
    return None


def season_dates(season: int) -> tuple[str, str]:
    """Regular season window, clipped to today for an in-progress season."""
    start, end = f"{season}-03-15", f"{season}-10-05"
    today = _dt.date.today().isoformat()
    return start, min(end, today)


def load_statcast(
    season: int,
    refresh: bool = False,
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """Pull pitch-level Statcast, cached to Parquet.

    The first full-season run is slow -- pybaseball chunks the request by
    date and Savant rate-limits. Subsequent runs read from cache instantly.

    Pass `start`/`end` to pull a narrow window instead; that variant caches
    under its own key so a smoke test never clobbers the full-season pull.
    """
    default_start, default_end = season_dates(season)
    start = start or default_start
    end = end or default_end

    tag = (f"statcast_{season}" if (start, end) == (default_start, default_end)
           else f"statcast_{season}_{start}_{end}")
    path = _cache_path(tag)
    if path.exists() and not refresh:
        return pd.read_parquet(path)

    from pybaseball import statcast  # imported lazily: heavy, and optional

    df = statcast(start_dt=start, end_dt=end, verbose=True)

    keep = [c for c in STATCAST_COLS if c in df.columns]
    missing = set(STATCAST_COLS) - set(keep)
    if missing:
        warnings.warn(f"Statcast schema drift, missing columns: {sorted(missing)}")

    fielders = [c for c in FIELDER_COLS if c in df.columns]
    if len(fielders) < len(FIELDER_COLS):
        warnings.warn(
            "fielder columns missing: "
            f"{sorted(set(FIELDER_COLS) - set(fielders))}; HUNT falls back to "
            "unweighted OAA"
        )
    keep = keep + fielders

    df = df[keep].copy()
    df.to_parquet(path, index=False)
    return df


def load_sprint_speed(season: int) -> pd.DataFrame | None:
    """Sprint speed and home-to-first, for the GRIT hustle ratio."""
    path = _cache_path(f"sprint_{season}")
    if path.exists():
        return pd.read_parquet(path)
    try:
        from pybaseball import statcast_sprint_speed

        df = statcast_sprint_speed(season, min_opp=5)
        df = df.rename(columns={"player_id": "batter"})
        df.to_parquet(path, index=False)
        return df
    except Exception as e:  # noqa: BLE001 - any failure means "degrade"
        warnings.warn(f"sprint speed unavailable ({e}); GRIT hustle term dropped")
        return None


def load_catch_probability(season: int, refresh: bool = False) -> pd.DataFrame | None:
    """Outfield catch probability, for HUNT star catches."""
    path = _cache_path(f"catch_{season}")
    if path.exists() and not refresh:
        return pd.read_parquet(path)
    try:
        import io
        import requests

        url = (
            "https://baseballsavant.mlb.com/leaderboard/catch_probability"
            f"?type=player&min=10&year={season}&total=5&csv=true"
        )
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        # Print rather than warn: this is the one leaderboard whose schema has
        # actually bitten us, and the column list is what you need to see when
        # it does. A warning would be filtered out of a long build log.
        print(f"[xdawg] catch_probability columns: {list(df.columns)}")
        df.to_parquet(path, index=False)
        return df
    except Exception as e:  # noqa: BLE001
        warnings.warn(f"catch probability unavailable ({e}); HUNT star term dropped")
        return None


def load_oaa(season: int, refresh: bool = False) -> pd.DataFrame | None:
    """Outs Above Average for every fielder, not just outfielders.

    The catch-probability leaderboard is the outfield slice only (249 players
    in 2026). This endpoint covers all positions, which is what HUNT needs if
    infielders and catchers are to be scored at all.

    Savant has served this leaderboard under more than one query shape, so we
    try each and keep the first that returns a usable frame. The one that
    worked is printed, because that is the thing you need to know when it
    stops working.
    """
    path = _cache_path(f"oaa_{season}")
    if path.exists() and not refresh:
        return pd.read_parquet(path)

    import io
    import requests

    base = "https://baseballsavant.mlb.com/leaderboard/outs_above_average"
    attempts = [
        {"type": "Fielder", "startYear": season, "endYear": season,
         "split": "no", "team": "", "range": "year", "min": "10",
         "pos": "", "roles": "", "viz": "show", "csv": "true"},
        {"type": "Fielder", "year": season, "min": "10", "pos": "",
         "team": "", "csv": "true"},
    ]

    errors = []
    for i, params in enumerate(attempts, 1):
        try:
            r = requests.get(base, params=params, timeout=60)
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text))
            if df.empty:
                raise ValueError("empty frame")
            if pick_col(df, "outs_above_average", "oaa") is None:
                raise ValueError(f"no OAA column in {list(df.columns)}")
            print(f"[xdawg] oaa: endpoint shape {i} worked, {len(df)} fielders")
            print(f"[xdawg] oaa columns: {list(df.columns)}")
            df.to_parquet(path, index=False)
            return df
        except Exception as e:  # noqa: BLE001
            errors.append(f"shape {i}: {e}")

    # Outfield-only fallback: better a partial HUNT than a dead pillar.
    catches = load_catch_probability(season, refresh=refresh)
    if catches is not None and pick_col(catches, "oaa") is not None:
        warnings.warn(
            "all-position OAA unavailable (" + "; ".join(errors)
            + "); falling back to the outfield-only leaderboard"
        )
        return catches

    warnings.warn(
        "OAA unavailable (" + "; ".join(errors) + "); HUNT term dropped"
    )
    return None


OPTIONAL_LEADERBOARDS = ("sprint_speed", "catch_probability", "oaa", "standings")


def probe(season: int) -> int:
    """Fetch each optional leaderboard and report its real column names.

    Every one of these degrades gracefully at runtime, which is right for a
    build but means a rename shows up as a quietly missing pillar term. This
    asks each source what it actually returns, so a mapping can be written
    against the truth instead of against last season's column names.

    Returns the number of sources that could not be reached at all.
    """
    loaders = {
        "sprint_speed": lambda: load_sprint_speed(season),
        "catch_probability": lambda: load_catch_probability(season, refresh=True),
        "oaa": lambda: load_oaa(season, refresh=True),
        "standings": lambda: load_standings(season, refresh=True),
    }
    # What each pillar term needs from that source.
    needed = {
        "sprint_speed": ("batter", "sprint_speed", "hp_to_1b"),
        "catch_probability": ("player_id", "oaa"),
        "oaa": ("player_id", "outs_above_average", "n_fielding_opportunities",
                "primary_pos_formatted"),
        "standings": ("team", "rs", "ra"),
    }

    dead = 0
    for name, load in loaders.items():
        print(f"\n=== {name} ===")
        try:
            df = load()
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR {e}")
            dead += 1
            continue
        if df is None or df.empty:
            print("  unreachable or empty -- its pillar term will drop out")
            dead += 1
            continue

        print(f"  {len(df)} rows, {len(df.columns)} columns")
        print(f"  columns: {list(df.columns)}")
        print("  what the pillars look for:")
        for want in needed[name]:
            got = pick_col(df, want)
            mark = f"-> {got}" if got else "-> MISSING"
            print(f"    {want:<20} {mark}")
        with pd.option_context("display.max_columns", None, "display.width", 200):
            print(f"  first row:\n{df.head(1).to_string()}")
    return dead


# Statcast, StatsAPI and config.TEAMS do not agree on every abbreviation.
# Everything is normalized to the config.TEAMS spelling, which is what the
# site and the FIGHT opponent lookup key on.
TEAM_ALIASES = {
    "AZ": "ARI", "ARZ": "ARI",
    "CHW": "CWS", "CHA": "CWS", "CHN": "CHC",
    "OAK": "ATH",
    "WAS": "WSH", "WSN": "WSH",
    "SDP": "SD", "SFG": "SF", "TBR": "TB", "KCR": "KC",
    "NYA": "NYY", "NYN": "NYM", "LAN": "LAD", "SLN": "STL",
}


def normalize_team(code: str) -> str:
    """Map any known abbreviation spelling onto the config.TEAMS one."""
    c = str(code).strip().upper()
    return TEAM_ALIASES.get(c, c)


def _standings_from_statsapi(season: int) -> pd.DataFrame:
    """Team runs scored / allowed from MLB's own API.

    pybaseball routes this through FanGraphs, which 403s CI runners and any
    other datacentre IP. StatsAPI is MLB's public endpoint, needs no key,
    and is not IP-blocked -- so FIGHT keeps its opponent-quality term on CI
    instead of silently collapsing to flat weights.
    """
    import requests

    from .config import TEAMS

    teams = requests.get(
        "https://statsapi.mlb.com/api/v1/teams",
        params={"sportId": 1, "season": season},
        timeout=60,
    )
    teams.raise_for_status()
    abbr = {
        t["id"]: normalize_team(t.get("abbreviation", ""))
        for t in teams.json().get("teams", [])
    }

    standings = requests.get(
        "https://statsapi.mlb.com/api/v1/standings",
        params={
            "leagueId": "103,104",
            "season": season,
            "standingsTypes": "regularSeason",
        },
        timeout=60,
    )
    standings.raise_for_status()

    rows = []
    for record in standings.json().get("records", []):
        for tr in record.get("teamRecords", []):
            tid = tr.get("team", {}).get("id")
            rows.append({
                "team": abbr.get(tid, ""),
                "rs": float(tr.get("runsScored") or 0),
                "ra": float(tr.get("runsAllowed") or 0),
                # Actual record, for the team leaderboard. FIGHT deliberately
                # uses Pythagorean expectation from rs/ra rather than these,
                # because real W-L carries bullpen luck and one-run-game noise
                # -- but the whole point of the team board is comparing the
                # DAWG stats against what actually happened.
                "wins": float(tr.get("wins") or 0),
                "losses": float(tr.get("losses") or 0),
            })

    df = pd.DataFrame(rows)
    df = df[(df["team"] != "") & (df["rs"] > 0) & (df["ra"] > 0)]
    if df.empty:
        raise ValueError("StatsAPI returned no usable team records")

    unknown = sorted(set(df["team"]) - set(TEAMS))
    if unknown:
        warnings.warn(
            f"standings: team codes not in config.TEAMS: {unknown} -- "
            "these teams get no opponent-quality weight; add them to "
            "TEAM_ALIASES or TEAMS"
        )
    missing = sorted(set(TEAMS) - set(df["team"]))
    if missing:
        warnings.warn(f"standings: no runs data for {missing}")

    print(f"[xdawg] standings: {len(df)} teams from StatsAPI")
    return df.reset_index(drop=True)


def load_standings(season: int, refresh: bool = False) -> pd.DataFrame | None:
    """Team runs scored / allowed, for the FIGHT opponent-quality term."""
    path = _cache_path(f"standings_{season}")
    if not refresh:
        cached = _read_cache(f"standings_{season}", ("team", "rs", "ra", "wins", "losses"))
        if cached is not None:
            return cached

    errors = []
    try:
        df = _standings_from_statsapi(season)
        df.to_parquet(path, index=False)
        return df
    except Exception as e:  # noqa: BLE001
        errors.append(f"StatsAPI: {e}")

    try:  # FanGraphs, via pybaseball -- works locally, 403s on CI
        from pybaseball import team_batting, team_pitching

        tb = team_batting(season)[["Team", "R"]].rename(columns={"R": "rs"})
        tp = team_pitching(season)[["Team", "R"]].rename(columns={"R": "ra"})
        df = tb.merge(tp, on="Team").rename(columns={"Team": "team"})
        df["team"] = df["team"].map(normalize_team)
        # The FanGraphs fallback carries no record, so the team board simply
        # shows no W-L rather than the column vanishing.
        for col in ("wins", "losses"):
            if col not in df.columns:
                df[col] = float("nan")
        df.to_parquet(path, index=False)
        return df
    except Exception as e:  # noqa: BLE001
        errors.append(f"FanGraphs: {e}")

    warnings.warn(
        "standings unavailable (" + "; ".join(errors)
        + "); FIGHT falls back to flat weights"
    )
    return None


def player_names(pitches: pd.DataFrame) -> dict[int, str]:
    """Statcast's `player_name` is the PITCHER, so batters need a lookup."""
    names: dict[int, str] = {}
    if "player_name" in pitches.columns:
        for pid, nm in pitches.dropna(subset=["player_name"]).groupby("pitcher")[
            "player_name"
        ].first().items():
            names[int(pid)] = str(nm)
    try:
        from pybaseball import playerid_reverse_lookup

        need = sorted(set(pitches["batter"].dropna().astype(int)) - set(names))
        if need:
            lk = playerid_reverse_lookup(need, key_type="mlbam")
            for _, r in lk.iterrows():
                names[int(r["key_mlbam"])] = (
                    f"{r['name_first']} {r['name_last']}".title()
                )
    except Exception as e:  # noqa: BLE001
        warnings.warn(f"name lookup unavailable ({e}); showing raw MLBAM ids")
    return names
