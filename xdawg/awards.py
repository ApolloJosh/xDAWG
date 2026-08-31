"""DAWG of the Day, Week and Month.

A season leaderboard answers "who is like this." These answer "who was like
this on Tuesday," and that is a different measurement problem: one day is
about four plate appearances, and every pillar in xDAWG is a rate compared
against a baseline. At four trips those rates are noise. Anything that
printed a confident DAWG+ for a single day would be lying with a straight
face.

So the award is decided by two halves, both countable per event:

    score = OUTCOME + PROCESS      in DAWG points, 1 pt = 1% win probability

OUTCOME is 100 x WPA x FIGHT weight. WPA is already leverage-aware by
construction -- a two-out single in a tie game in the ninth moves win
probability enormously and the same single in a 9-0 game barely at all -- and
the FIGHT weight asks who it was against. Summed rather than averaged, because
"of the Day" should reward the man who did it four times over the man who did
it once.

PROCESS is the DAWG half, and it is why this is not just a win-probability
leaderboard. An eight-pitch walk in the ninth, a two-strike foul-off, chasing
and not whiffing, wearing a pitch; a closer pumping strikes with men on,
working inside to a same-handed hitter, escaping a jam scoreless. Each is a
COUNTABLE EVENT, weighted by the leverage it happened in and the opponent it
happened against. See `config.AWARD_CREDITS`.

Three things make the two halves comparable, and all three were found by
watching the first cut get them wrong:

  centered   WPA is zero-sum -- what the hitter gains the pitcher loses, and
             an average night is zero. Credits only accumulate, so raw they
             measure playing time first and character second. Every plate
             appearance is charged the credits an average player would have
             earned in the same leverage and against the same opponent.
  damped     Leverage runs to 6 and multiplies every credit, so a six-point
             jam escape at 6x leverage was worth 68 points -- more than a
             walk-off. The multiplier is square-rooted and capped at 2x.
  capped     Leverage and opponent weight compound, so no single plate
             appearance may earn more process than the 99.5th percentile of
             win-probability swings. Measured off the real distribution
             rather than picked.

The balance between the halves is calibrated, not guessed: `calibrate_process`
matches the process bucket's spread to WPA's at the player-day level, so the
numbers in AWARD_CREDITS decide only what a jam escape is worth RELATIVE to a
two-strike foul. One property worth knowing -- process accumulates steadily
while WPA is noisy, so the longer the window the more process decides it. Over
a month that is arguably correct for a dawg metric, but it is a design choice,
and `AWARD_PROCESS_BALANCE` is the dial.

The four pillars are still computed over the window and shown underneath,
with the ordinary shrinkage applied. That shrinkage is the honesty: at a
day's sample every pillar collapses to nearly zero on its own, which is the
correct statement rather than a hidden one. By a month they carry real
weight. The reader can watch them wake up as the window grows.

Weeks run Monday to Sunday. Months are calendar months.
"""

from __future__ import annotations

import datetime as dt
import warnings

import numpy as np
import pandas as pd

from . import fight as fight_mod
from . import ingest
from .config import AWARD_CREDITS, AWARD_PROCESS_BALANCE, PILLAR_WEIGHTS

# Every credit name, both roles, for carrying per-credit counts through to the
# site so a reader can see WHY the process half scored what it did.
CREDIT_NAMES = sorted({c for role in AWARD_CREDITS.values() for c in role})

# Column prefixes: CW is a credit's leverage-weighted count, CP the points
# that count is worth once the credit's value, the opponent weight and the
# season's process scale are applied.
CW = "cw_"
CP = "cp_"

# Trips needed to be eligible, for a COMPLETE window. Without a floor a
# September call-up with one pinch-hit homer in his only trip wins DAWG of the
# Day over a man who did it four times.
#
# These are prorated by how much of the window has actually been played -- see
# `eligibility_floor`. A week that is two days old is not a week, and holding
# a two-day week to a full week's bar threw out every reliever in the league
# and handed the award to whichever starter happened to have made a start,
# score irrelevant.
MIN_PA = {"day": 2, "week": 8, "month": 30}

# The floor never prorates below this. Two trips is the point of having a
# floor at all: one swing is an anecdote.
MIN_FLOOR = 2


def window_span(key: str, kind: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """First and last calendar day of a window, inclusive."""
    if kind == "day":
        d = pd.Timestamp(key)
        return d, d
    if kind == "week":
        a = pd.Timestamp(key)
        return a, a + dt.timedelta(days=6)
    a = pd.Timestamp(key + "-01")
    return a, a + pd.offsets.MonthEnd(1)


def eligibility_floor(kind: str, key: str, through: pd.Timestamp) -> int:
    """Trips required to win THIS window, scaled to how much of it has happened.

    A window still in progress has had less baseball played in it, so holding
    it to the completed-window bar excludes exactly the players whose usage is
    spread thin -- relievers, platoon bats -- and leaves whoever front-loaded
    their workload. On the second day of a week that meant one starter versus
    nobody, and the award went to him at a score of MINUS 0.668.
    """
    start, end = window_span(key, kind)
    total = (end - start).days + 1
    played = min(max((through - start).days + 1, 1), total)
    scaled = int(round(MIN_PA[kind] * played / total))
    return max(MIN_FLOOR, scaled)


def inside_sign(p: pd.DataFrame) -> dict:
    """Which sign of `plate_x` means INSIDE, learned from hit batsmen.

    Statcast's plate_x convention is stated from the catcher's point of view,
    which means the sign that counts as "in on his hands" flips with the
    batter's handedness -- and getting it backwards would score a pitcher for
    living on the outside corner and call it courage.

    Rather than hardcode the convention and hope it never changes, read it off
    the data: a hit-by-pitch is unambiguously inside. The mean plate_x of
    HBPs for each stance gives the sign directly, and it is self-correcting if
    Savant ever flips the axis.
    """
    if not {"plate_x", "stand", "events"}.issubset(p.columns):
        return {}
    hbp = p[p["events"].astype(str).eq("hit_by_pitch")]
    out = {}
    for stance in ("L", "R"):
        sel = hbp[hbp["stand"].astype(str).eq(stance)]
        x = pd.to_numeric(sel.get("plate_x"), errors="coerce").dropna()
        if len(x) >= 20:
            out[stance] = 1.0 if x.mean() > 0 else -1.0
    return out


def _process_points(p: pd.DataFrame) -> pd.DataFrame:
    """Per-plate-appearance DAWG process credits, for both roles.

    Returns one row per (game_pk, at_bat_number) with a `proc_hitter` and
    `proc_pitcher` total plus a count column per credit, all already weighted
    by the leverage of the moment. The FIGHT opponent weight is applied later,
    at the plate-appearance level, because it is constant across a PA.
    """
    from .config import AWARD_CREDITS, INSIDE_FT
    from .pillars.hitters import tag_pitch_events

    d = tag_pitch_events(p).sort_values(
        ["game_pk", "at_bat_number", "pitch_number"])
    key = ["game_pk", "at_bat_number"]

    li = pd.to_numeric(d.get("li", pd.Series(1.0, index=d.index)),
                       errors="coerce").fillna(1.0).to_numpy(dtype="float64")
    desc = d["description"].astype(str)
    ev_pa = d.groupby(key)["events"].transform("last").astype(str)
    pnum = pd.to_numeric(d["pitch_number"], errors="coerce").fillna(1).to_numpy()
    strikes = pd.to_numeric(d["strikes"], errors="coerce").fillna(0).to_numpy()

    def arr(x):
        return pd.Series(x).fillna(False).to_numpy(dtype=bool)

    two_strike = arr(d["two_strike"])
    flags = {}

    # ---- hitter ----
    flags["extra_pitch"] = (pnum >= 6).astype(float)
    flags["two_strike_foul"] = (arr(d["is_foul"]) & two_strike).astype(float)
    flags["chase_contact"] = arr(d["chase_contact"]).astype(float)
    flags["hard_hit"] = arr(d["hard_hit"]).astype(float)
    # A called strike with two already on him IS strike three looking.
    flags["called_strike_three"] = (
        desc.eq("called_strike").to_numpy() & (strikes == 2)).astype(float)
    # PA-level: credit once, on the final pitch, so it is not multiplied by
    # the number of pitches it took.
    last_pitch = ~d.duplicated(key, keep="last")
    reached = ev_pa.isin(_HITS | _WALKS | _HBP).to_numpy()
    saw_two = d.groupby(key)["two_strike"].transform("max").fillna(False).to_numpy(dtype=bool)
    flags["survived_two_strikes"] = (
        last_pitch.to_numpy() & reached & saw_two).astype(float)
    flags["hbp"] = (last_pitch.to_numpy()
                    & ev_pa.isin(_HBP).to_numpy()).astype(float)

    # ---- pitcher ----
    runners = d[["on_1b", "on_2b", "on_3b"]].notna().any(axis=1).to_numpy()
    in_zone = ~arr(d["out_of_zone"])
    flags["zone_with_traffic"] = (in_zone & runners).astype(float)

    signs = inside_sign(p)
    if signs and {"plate_x", "stand", "p_throws"}.issubset(d.columns):
        px = pd.to_numeric(d["plate_x"], errors="coerce").to_numpy(
            dtype="float64", na_value=np.nan)
        stance = d["stand"].astype(str).to_numpy()
        sgn = np.where(stance == "L", signs.get("L", 0.0), signs.get("R", 0.0))
        same_hand = (d["p_throws"].astype(str).to_numpy() == stance)
        with np.errstate(invalid="ignore"):
            flags["inside_same_hand"] = (
                same_hand & (px * sgn > INSIDE_FT)).astype(float)
    else:
        warnings.warn("no hit-by-pitch sample to orient plate_x; the "
                      "pitching-inside credit is dropped rather than guessed")
        flags["inside_same_hand"] = np.zeros(len(d))

    strike_desc = {"called_strike", "swinging_strike", "swinging_strike_blocked",
                   "foul", "foul_tip", "hit_into_play"}
    flags["first_pitch_strike"] = (
        (pnum == 1) & desc.isin(strike_desc).to_numpy()).astype(float)
    flags["putaway"] = (last_pitch.to_numpy()
                        & ev_pa.isin(_K).to_numpy()).astype(float)
    flags["walk_allowed"] = (last_pitch.to_numpy()
                             & ev_pa.isin(_WALKS).to_numpy()).astype(float)

    # Jam escaped: the first genuine jam of a half inning, credited only if
    # nothing scored from there to the end of it.
    flags["jam_escaped"] = _jam_escapes(p, d, key)

    # Leverage rides every credit, DAMPED. The empirical leverage index runs
    # to 6, and multiplying a six-point jam escape by six put 68 points on a
    # single plate appearance -- more than a walk-off homer is worth in win
    # probability. The credit is for the ACT; leverage should modulate it, not
    # decide it by itself. Square-rooting keeps the ordering (a 4x spot still
    # counts double a neutral one) while capping the tail at 2x.
    lev = np.clip(np.sqrt(np.maximum(li, 0.0)), 0.25, 2.0)

    out = pd.DataFrame({c: d[c].to_numpy() for c in key})
    for name, f in flags.items():
        out[name] = f                            # raw counts, for display
        out[f"{CW}{name}"] = f * lev             # weighted, for scoring
    out = out.groupby(key, as_index=False).sum()

    for role in ("hitter", "pitcher"):
        vals = AWARD_CREDITS[role]
        out[f"proc_{role}"] = sum(
            out[f"{CW}{c}"] * v for c, v in vals.items()
            if f"{CW}{c}" in out.columns)
    # The weighted sums are KEPT, not dropped. They are what lets the card
    # say what each credit was worth in points rather than only how many
    # times it fired -- "22 strikes with men on" is a count; "+8.8" is the
    # thing the reader actually wants to weigh against the win probability.
    return out


def _jam_escapes(p: pd.DataFrame, d: pd.DataFrame, key: list) -> np.ndarray:
    """1.0 on the pitch that opened a jam the pitcher then escaped scoreless."""
    try:
        from .pillars.pitchers import _is_jam, half_inning_pas

        hip = half_inning_pas(p)
        jam = _is_jam(hip)
        hip = hip.assign(_jam=jam)
        # Runs from each plate appearance to the end of its half inning.
        rest = hip.groupby("_half")["runs"].transform(
            lambda s: s[::-1].cumsum()[::-1])
        first = hip[hip["_jam"]].drop_duplicates("_half", keep="first")
        escaped = first[rest.loc[first.index].fillna(0) <= 0][key]
        if escaped.empty:
            return np.zeros(len(d))
        escaped = escaped.assign(_e=1.0)
        marked = d[key].merge(escaped, on=key, how="left")["_e"].fillna(0.0)
        # Once per plate appearance, not once per pitch in it.
        return (marked.to_numpy() * (~d.duplicated(key, keep="last")).to_numpy()
                ).astype(float)
    except Exception as e:                                   # noqa: BLE001
        warnings.warn(f"jam-escape credit unavailable ({e})")
        return np.zeros(len(d))


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

    # Outs recorded and runs allowed, for the pitchers' innings and RA9. Both
    # are reconstructed from half-inning bookkeeping rather than shipped by
    # the feed, so they come from the one place that already does it
    # carefully instead of being re-derived here.
    try:
        from .pillars.pitchers import half_inning_pas

        hip = half_inning_pas(p)[["game_pk", "at_bat_number",
                                  "outs_recorded", "runs"]]
        pa = pa.merge(hip, on=["game_pk", "at_bat_number"], how="left")
    except Exception as e:                                   # noqa: BLE001
        warnings.warn(f"innings and runs unavailable ({e}); pitcher lines "
                      "will show strikeouts and walks only")
        pa["outs_recorded"] = np.nan
        pa["runs"] = np.nan

    # Process credits: the DAWG half of the award, countable per event so it
    # survives a one-day window where every rate would be shrunk to nothing.
    try:
        proc = _process_points(p)
        pa = pa.merge(proc, on=["game_pk", "at_bat_number"], how="left")
    except Exception as e:                                   # noqa: BLE001
        warnings.warn(f"process credits unavailable ({e}); awards fall back "
                      "to win probability alone")
        pa["proc_hitter"] = 0.0
        pa["proc_pitcher"] = 0.0

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
        "outs_recorded": pd.to_numeric(
            pa.get("outs_recorded", pd.Series(np.nan, index=pa.index)),
            errors="coerce").fillna(0.0),
        "runs": pd.to_numeric(
            pa.get("runs", pd.Series(np.nan, index=pa.index)),
            errors="coerce").fillna(0.0),
        "inning": pa.get("inning", pd.Series(np.nan, index=pa.index)),
        "li": pd.to_numeric(pa.get("li", pd.Series(1.0, index=pa.index)),
                            errors="coerce").fillna(1.0),
        "events": pa.get("events", pd.Series("", index=pa.index)).astype(str),
    }

    def proc_of(role):
        col = f"proc_{role}"
        return pd.to_numeric(pa.get(col, pd.Series(0.0, index=pa.index)),
                             errors="coerce").fillna(0.0).to_numpy()

    credit_cols = [c for c in pa.columns
                   if c in set(CREDIT_NAMES) and c not in ("proc_hitter", "proc_pitcher")]

    def frame(role, pid, team, opp, wpa_signed, rv_signed, own, against):
        w = weights(own, against)
        f = pd.DataFrame(dict(
            common, player_id=pid, role=role, team=team, opp=opp,
            wpa=wpa_signed, rv=rv_signed, fight_w=w,
            proc=proc_of(role) * w,
        ))
        for c in credit_cols:
            f[c] = pd.to_numeric(pa[c], errors="coerce").fillna(0.0).to_numpy()
        # And what each of those credits was worth, in the same units the
        # score is in. The opponent weight multiplies every credit exactly
        # as it multiplies the total, so it belongs here rather than being
        # applied to a sum later.
        for c, v in AWARD_CREDITS[role].items():
            col = f"{CW}{c}"
            if col in pa.columns:
                f[f"{CP}{c}"] = (pd.to_numeric(pa[col], errors="coerce")
                                 .fillna(0.0).to_numpy() * v * w)
        return f

    hit = frame("hitter", pa["batter"], bat_team, fld_team, wpa_bat, rv,
                bat_team, fld_team)
    pit = frame("pitcher", pa["pitcher"], fld_team, bat_team, -wpa_bat, -rv,
                fld_team, bat_team)
    out = pd.concat([hit, pit], ignore_index=True).dropna(subset=["player_id"])
    out["player_id"] = out["player_id"].astype("int64")

    # DAWG points: 1 point = 1% of win probability. The process bucket is
    # rescaled to match on the caller's side, once, for the whole season.
    out["wpa_pts"] = 100.0 * out["wpa"] * out["fight_w"]

    # Process must be centered before it can be added to win probability.
    # WPA is zero-sum by construction -- what the hitter gains the pitcher
    # loses, and an average night is zero. Process credits only accumulate, so
    # raw they measure playing time first and character second: on the first
    # cut the day award went to a pitcher with +0.039 WPA and a score of 64,
    # which is not an award, it is a pitch count.
    #
    # So each plate appearance is charged the credits an AVERAGE player would
    # have earned in the same situation. The expectation scales with leverage
    # and opponent quality, because those multiply every credit: a closer in a
    # one-run game against a rival is held to a correspondingly higher bar,
    # and a man mopping up a blowout is not credited for being there.
    for role in ("hitter", "pitcher"):
        m = out["role"].eq(role).to_numpy()
        if not m.any():
            continue
        ctx = (out.loc[m, "li"] * out.loc[m, "fight_w"]).to_numpy(dtype="float64")
        mean_ctx = ctx.mean() or 1.0
        expected = out.loc[m, "proc"].mean() * (ctx / mean_ctx)
        out.loc[m, "proc"] = out.loc[m, "proc"].to_numpy() - expected
    return out


def innings_notation(outs) -> str | None:
    """Innings pitched the way a box score writes them.

    6.1 is six innings and ONE THIRD, not six and one tenth. The decimal here
    is a count of outs, not a fraction, which is why 19 outs is "6.1" and
    never "6.3" -- and why this cannot be left to a decimal formatter.

    Returned as a string on purpose. The true value is `outs`, which is what
    RA9 divides by; anything that needs arithmetic must use that rather than
    parsing this back, because "6.1" and "6.2" are not comparable as numbers.
    """
    if outs is None or (isinstance(outs, float) and not np.isfinite(outs)):
        return None
    o = int(round(float(outs)))
    return f"{o // 3}.{o % 3}"


# Statcast `events` vocabulary, grouped the way the rule book groups it.
_HITS = {"single", "double", "triple", "home_run"}
_TB = {"single": 1, "double": 2, "triple": 3, "home_run": 4}
_WALKS = {"walk", "intent_walk", "intentional_walk"}
_HBP = {"hit_by_pitch"}
_SF = {"sac_fly", "sac_fly_double_play"}
_SH = {"sac_bunt", "sac_bunt_double_play"}
_CI = {"catcher_interf"}
_K = {"strikeout", "strikeout_double_play"}


def _stat_lines(pa: pd.DataFrame, key_col: str) -> dict:
    """Traditional stat lines per (player, role, window).

    Hitters get PA / HR / BB / OPS, pitchers IP / K / BB / RA9.

    RA9, not ERA, and the distinction is not pedantry: Statcast publishes runs
    but not the earned/unearned split, and recovering it properly means
    replaying each inning as if the errors had not happened -- the official
    "reconstructed inning" rule. So this is every run that scored while he was
    on the mound. It runs a few tenths above ERA and it is labelled RA9
    everywhere it appears rather than being passed off as ERA.

    Runs are charged to whoever was pitching when they crossed, which also
    differs from the official book: a reliever who lets an inherited runner
    score has that run charged to the pitcher who put him on. Here the
    reliever wears it. `inherited_runners` in the season metric measures that
    job properly; this is a box score, not a ledger.
    """
    d = pa.copy()
    ev = d["events"].astype(str).str.lower()
    d["_h"] = ev.isin(_HITS).astype(float)
    d["_tb"] = ev.map(_TB).fillna(0.0).astype(float)
    d["_hr"] = ev.eq("home_run").astype(float)
    d["_bb"] = ev.isin(_WALKS).astype(float)
    d["_hbp"] = ev.isin(_HBP).astype(float)
    d["_sf"] = ev.isin(_SF).astype(float)
    d["_sh"] = ev.isin(_SH).astype(float)
    d["_ci"] = ev.isin(_CI).astype(float)
    d["_k"] = ev.isin(_K).astype(float)
    # A plate appearance only counts once, on the pitch that ended it. Every
    # row here is already one PA, but `events` is null on a PA that a caught
    # stealing ended, so count rows rather than non-null events.
    d["_pa"] = 1.0

    g = d.groupby([key_col, "player_id", "role"]).agg(
        pa=("_pa", "sum"), h=("_h", "sum"), tb=("_tb", "sum"), hr=("_hr", "sum"),
        bb=("_bb", "sum"), hbp=("_hbp", "sum"), sf=("_sf", "sum"),
        sh=("_sh", "sum"), ci=("_ci", "sum"), k=("_k", "sum"),
        outs=("outs_recorded", "sum"), runs=("runs", "sum"),
    ).reset_index()

    ab = g["pa"] - g["bb"] - g["hbp"] - g["sf"] - g["sh"] - g["ci"]
    on_base = g["pa"] - g["sh"] - g["ci"]          # the OBP denominator
    obp = ((g["h"] + g["bb"] + g["hbp"]) / on_base.where(on_base > 0))
    slg = (g["tb"] / ab.where(ab > 0))
    ip = g["outs"] / 3.0
    ra9 = (g["runs"] * 9.0 / ip.where(ip > 0))

    out = {}
    for i, r in g.iterrows():
        if r["role"] == "hitter":
            line = {
                "PA": int(r["pa"]), "HR": int(r["hr"]), "BB": int(r["bb"]),
                "OPS": (None if pd.isna(obp[i]) or pd.isna(slg[i])
                        else round(float(obp[i] + slg[i]), 3)),
                "AVG": (None if pd.isna(r["h"]) or ab[i] <= 0
                        else round(float(r["h"] / ab[i]), 3)),
                "H": int(r["h"]),
            }
        else:
            line = {
                # IP is a STRING in box-score notation; `outs` is the number
                # anything arithmetic should use. RA9 below already divides
                # by true innings (outs/3), not by this.
                "IP": innings_notation(r["outs"]),
                "outs": int(r["outs"]) if pd.notna(r["outs"]) else 0,
                "K": int(r["k"]), "BB": int(r["bb"]),
                "RA9": (None if pd.isna(ra9[i]) else round(float(ra9[i]), 2)),
                "BF": int(r["pa"]), "R": int(r["runs"]),
            }
        out[(r[key_col], int(r["player_id"]), r["role"])] = line
    return out


def calibrate_process(pa: pd.DataFrame) -> float:
    """Scale the process bucket so it decides the award about as often as WPA.

    "Even split" is enforced empirically rather than by picking constants:
    both halves are summed to the finest award unit there is -- one player,
    one day -- and the process bucket is multiplied by whatever makes its
    spread across those player-days equal to win probability's.

    Doing it this way means the numbers in `AWARD_CREDITS` only ever decide
    what a jam escape is worth RELATIVE to a two-strike foul. How much process
    counts overall is measured, not guessed, and it re-measures itself every
    build -- which matters because the right constant depends on the run
    environment and would drift every season.

    One scalar for the whole season, not one per window, so a Tuesday in April
    and a Tuesday in September are on the same scale.
    """
    day = pa.assign(_d=pa["game_date"].dt.strftime("%Y-%m-%d")).groupby(
        ["_d", "player_id", "role"])[["wpa_pts", "proc"]].sum()
    # Standard deviation, matched at the player-day level.
    #
    # A 95th percentile was tried here and is worse. Process is mostly-zero
    # with occasional spikes, so its p95 is small while its max is not;
    # matching p95 therefore hands the bucket a large multiplier and the rare
    # big inning explodes -- one plate appearance came out worth 70 points,
    # more than a walk-off homer. Matching sd uses the whole distribution,
    # which is what actually needs to line up. The tail is controlled at
    # source instead, by damping the leverage multiplier on each credit.
    sd_w = float(day["wpa_pts"].std())
    sd_p = float(day["proc"].std())
    if not np.isfinite(sd_w) or not np.isfinite(sd_p) or sd_p <= 0:
        warnings.warn("cannot calibrate the process bucket; using 1.0")
        return 1.0
    scale = (sd_w / sd_p) * AWARD_PROCESS_BALANCE
    print(f"[xdawg] process scale {scale:.3f} "
          f"(player-day spread: WPA {sd_w:.2f} pts, process {sd_p:.2f} raw)")
    return scale


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
             full_key: str | None = None,
             through: pd.Timestamp | None = None) -> tuple[dict, dict]:
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

    agg_credits = {c: (c, "sum") for c in CREDIT_NAMES if c in d.columns}
    agg_credits.update({f"{CP}{c}": (f"{CP}{c}", "sum") for c in CREDIT_NAMES
                        if f"{CP}{c}" in d.columns})
    g = d.groupby(["_w", "player_id", "role"]).agg(
        score=("score", "sum"),
        wpa_pts=("wpa_pts", "sum"),
        proc_pts=("proc_pts", "sum"),
        wpa=("wpa", "sum"),
        rv=("rv", "sum"),
        n=("score", "size"),
        games=("game_pk", "nunique"),
        team=("team", "last"),
        opp_w=("fight_w", "mean"),
        top_li=("li", "max"),
        **agg_credits,
    ).reset_index()

    # The single biggest swing in the window, for the "why he won" line.
    best_idx = d.groupby(["_w", "player_id", "role"])["score"].idxmax()
    # game_pk and at_bat_number ride along for the video pipeline, which has
    # to find this exact play in MLB's own feed. Without them the clip job
    # re-derives the game from the date and the two club abbreviations,
    # which is one more thing that can be wrong -- and is wrong outright on
    # a doubleheader.
    _bcols = [c for c in ("game_date", "inning", "events", "li", "wpa", "opp",
                          "game_pk", "at_bat_number") if c in d.columns]
    best = d.loc[best_idx, ["_w", "player_id", "role", *_bcols]]
    best = best.rename(columns={c: f"best_{c}" for c in _bcols})
    g = g.merge(best, on=["_w", "player_id", "role"], how="left")

    lines = _stat_lines(d, "_w")
    through = through if through is not None else pa["game_date"].max()
    floors = {k: eligibility_floor(kind, k, through) for k in g["_w"].unique()}
    g = g[g["n"] >= g["_w"].map(floors)]
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
                "score": round(float(r["score"]), 2),
                # The two halves, so the reader can see which one won it and
                # so the balance between them is auditable rather than buried.
                "wpa_pts": round(float(r["wpa_pts"]), 2),
                "proc_pts": round(float(r["proc_pts"]), 2),
                "wpa": round(float(r["wpa"]), 4),
                "n": int(r["n"]),
                "games": int(r["games"]),
            }
            row["line"] = lines.get((key, pid, r["role"]))
            # Which credits he actually earned. Only the ones that fired, and
            # only for his own role -- a hitter has no jam escapes and listing
            # a zero would imply he might have.
            got = {c: int(round(float(r[c])))
                   for c in AWARD_CREDITS[r["role"]]
                   if c in g.columns and abs(float(r[c])) >= 0.5}
            if got:
                row["credits"] = got
                # What each of those was worth. GROSS: the process total
                # beside it is net of the average-day baseline every plate
                # appearance is charged, and is capped per plate appearance
                # besides, so these deliberately do not sum to it. Showing a
                # reconciling figure would mean apportioning a cap across
                # credits, which is arithmetic nobody could defend.
                row["credit_pts"] = {
                    c: round(float(r[f"{CP}{c}"]), 1) for c in got
                    if f"{CP}{c}" in g.columns}
            # `best` is kept for the podium of EVERY window, not just the
            # featured one: the archive popup explains the moment for a week
            # three weeks back, and without this it would have nothing to
            # explain. Rows past the third stay slim.
            if full or rank <= 3:
                ev = str(r.get("best_events", "") or "").replace("_", " ")
                row.update({
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
                        # Not for the reader -- for the clip job. See above.
                        "game_pk": (None if pd.isna(r.get("best_game_pk"))
                                    else int(r["best_game_pk"])),
                        "at_bat_number": (None if pd.isna(r.get("best_at_bat_number"))
                                          else int(r["best_at_bat_number"])),
                    },
                })
            rows.append(row)
        out[key] = rows
    return out, floors


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

    # DAWG points: win probability in hundredths, plus the process bucket
    # rescaled to match its spread.
    proc_scale = calibrate_process(pa)
    pa["proc_pts"] = pa["proc"] * proc_scale
    # The per-credit points ride the same scale, so a row on the card is in
    # the same units as the total beside it.
    for c in [c for c in pa.columns if c.startswith(CP)]:
        pa[c] = pa[c] * proc_scale

    # No single plate appearance may earn more process than the biggest swing
    # win probability ever produces. Leverage and the FIGHT weight both
    # multiply every credit, so they compound: a jam escaped plus a put-away
    # at high leverage against a good club came out at 68 points against a
    # WPA maximum of 29, which would mean the process half could outbid a
    # walk-off. The cap is measured off the real WPA distribution rather than
    # picked, and it bites on a handful of plate appearances a season.
    cap = float(pa["wpa_pts"].abs().quantile(0.995))
    if np.isfinite(cap) and cap > 0:
        clipped = int((pa["proc_pts"].abs() > cap).sum())
        pa["proc_pts"] = pa["proc_pts"].clip(-cap, cap)
        print(f"[xdawg] process capped at +/-{cap:.1f} pts per plate "
              f"appearance ({clipped} of {len(pa):,} clipped)")
    pa["score"] = pa["wpa_pts"] + pa["proc_pts"]

    # Which window is "current" has to be decided before ranking, because it
    # decides which board keeps its full roster.
    through_ts = pa["game_date"].max()
    latest = {kind: max(pa["game_date"].map(lambda x: window_key(x, kind)))
              for kind in ("day", "week", "month")}
    built = {kind: _leaders(pa, kind, names, teams, full_key=latest[kind],
                            through=through_ts)
             for kind in ("day", "week", "month")}
    boards = {kind: b for kind, (b, _) in built.items()}
    floors = {kind: f for kind, (_, f) in built.items()}
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
        "process_scale": round(float(proc_scale), 4),
        "credit_values": AWARD_CREDITS,
        # The floor ACTUALLY applied to each window, after prorating for how
        # much of it had been played. The page quotes this rather than MIN_PA,
        # because on an in-progress window they differ and the reader deserves
        # the number that decided who was on the ballot.
        "floors": floors,
        "pillar_weights": PILLAR_WEIGHTS,
        "latest": latest,
        "labels": {kind: {k: window_label(k, kind) for k in b}
                   for kind, b in boards.items()},
        "boards": boards,
        "pillars": pillars,
    }
