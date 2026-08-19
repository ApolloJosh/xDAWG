"""
Every tunable knob in xDAWG lives here.

Nothing else in the package hardcodes a weight, a coefficient, or a
stabilization constant. If you want to argue about what "dawg" means,
this is the file you argue in.
"""

SEASON_DEFAULT = 2026

# ---------------------------------------------------------------------------
# Pillar weights. These are a VALUES JUDGMENT, not an empirical finding.
# There is no ground-truth dawg dataset to regress against, so these encode
# a stated preference about what matters. Change them and rerun.
# ---------------------------------------------------------------------------

# Hitter HUNT is cut to 0.10 because it rests on OAA -- a season total, only
# coarsely situational, and the one component not self-referenced against the
# player's own baseline. The other three pillars are all built from
# leverage-weighted deltas, so they carry the metric.
#
# Pitcher weights are unchanged: pitcher HUNT is a different construction
# entirely (risp_stuff_delta / putaway_lev / jam_escape_process), all three
# leverage-weighted per pitch, so none of the reasons for cutting the hitter
# side apply to it.
PILLAR_WEIGHTS = {
    "hitter": {"bite": 0.30, "grit": 0.30, "hunt": 0.10, "fight": 0.30},
    "pitcher": {"bite": 0.30, "grit": 0.25, "hunt": 0.25, "fight": 0.20},
}

# ---------------------------------------------------------------------------
# Components within each pillar.
#
#   weight  - relative weight inside the pillar (renormalized if a component
#             is unavailable, so a missing leaderboard degrades gracefully)
#   invert  - True if a LOWER raw value is more dawg (whiffs, walks)
#   k       - shrinkage constant, in that component's own opportunity units.
#             z_shrunk = z_raw * n / (n + k). Roughly "the sample size at
#             which we trust the signal half as much as we'd like to."
# ---------------------------------------------------------------------------

COMPONENTS = {
    "hitter": {
        "bite": {
            # The extended at-bat, not the disciplined one. Whiff is primary;
            # chasing and surviving it is a POSITIVE.
            "whiff_delta":        {"weight": 0.45, "invert": True,  "k": 150},
            # chase_contact correlates 0.738 with whiff_delta on real data --
            # both are whiff-avoidance under leverage -- so at 0.22 the two
            # of them held 52% of BITE between them. Weight moved to the
            # terms that measure something else: grinding the count and
            # refusing to be put away.
            "chase_contact":      {"weight": 0.15, "invert": False, "k": 100},
            # Continuous exit velocity, not a binary >=95mph flag: no cliff
            # at 94.9, and it uses the whole distribution. Replaces
            # hard_hit_delta rather than joining it -- two measures of the
            # same thing splitting weight is how whiff_delta and
            # chase_contact ended up correlated at 0.738.
            "ev_situational":     {"weight": 0.28, "invert": False, "k": 200},
            "post_k_bounceback":  {"weight": 0.12, "invert": False, "k": 80},
        },
        # Grinding out an at-bat is effort, not approach, so the two terms
        # that measure refusing to give one away live here rather than in
        # BITE. It also stops any single measurement dominating: hustle alone
        # used to be 35% of GRIT and, when GRIT was a one-component pillar,
        # 30% of a hitter's entire score.
        "grit": {
            "hustle_ratio":       {"weight": 0.22, "invert": False, "k": 60},
            "pitches_per_pa_delta": {"weight": 0.20, "invert": False, "k": 90},
            "hbp_above_expected": {"weight": 0.16, "invert": False, "k": 400},
            "two_strike_foul_delta": {"weight": 0.15, "invert": False, "k": 120},
            "extra_bases_taken":  {"weight": 0.15, "invert": False, "k": 50},
            "availability":       {"weight": 0.12, "invert": False, "k": 30},
        },
        # HUNT is built on OAA, which Savant publishes only as a season total
        # per fielder -- there are no per-play rows to attach leverage to.
        # `oaa_situational` carries most of the weight because it is the one
        # tilted toward the games that mattered; `oaa_rate` keeps a smaller
        # share so a fielder whose batted balls we could not attribute still
        # scores on something. The two are correlated by construction, which
        # is why the split is uneven rather than 50/50.
        #
        # Unlike every other component, this one is NOT self-referenced
        # against the player's own baseline -- OAA is a talent measure. If
        # the leaderboard starts tracking WAR, look here first.
        #
        # `assists_blocks_lev` and `baserunning_lev` are specced but nothing
        # computes them yet; they are kept at zero rather than deleted so the
        # intended shape of the pillar stays visible.
        "hunt": {
            # oaa_rate is kept at zero weight, not deleted: oaa_situational
            # IS oaa_rate times the context index, and on the first real
            # build the two correlated at r = 0.994 -- one number drawing
            # two weights. The situational version carries the pillar
            # because when the plays happened is the whole point.
            "oaa_situational":    {"weight": 0.65, "invert": False, "k": 40},
            "oaa_rate":           {"weight": 0.00, "invert": False, "k": 40},
            # Win probability added against the player's own context-neutral
            # rate -- FanGraphs' Clutch, computed in-house. This is what
            # makes hitter HUNT mean "the hit that wins it" and not just
            # "the catch that saves it"; before it, hitter HUNT was purely
            # fielding while pitcher HUNT was situational performance.
            #
            # k is deliberately huge. Clutch has notoriously low year-over-
            # year signal, and one of xDAWG's pass conditions is beating raw
            # clutch metrics on stability -- so this needs a full season of
            # evidence before it moves anybody much.
            "wpa_clutch_delta":   {"weight": 0.35, "invert": False, "k": 350},
            "assists_blocks_lev": {"weight": 0.00, "invert": False, "k": 30},
            "baserunning_lev":    {"weight": 0.00, "invert": False, "k": 50},
        },
        # Two separate readings rather than one blended weight: beating the
        # best team in the league and beating the club you see nineteen times
        # are different kinds of dawg, and blending them made the breakdown
        # panel unreadable.
        "fight": {
            "contender_rv_delta": {"weight": 0.40, "invert": False, "k": 250},
            "division_rv_delta":  {"weight": 0.35, "invert": False, "k": 250},
            "fight_process_delta": {"weight": 0.25, "invert": False, "k": 180},
        },
    },
    "pitcher": {
        "bite": {
            "two_strike_stuff_delta": {"weight": 0.30, "invert": False, "k": 200},
            "attack_delta":       {"weight": 0.30, "invert": False, "k": 150},
            "bb_delta_lev":       {"weight": 0.22, "invert": True,  "k": 120},
            "post_hr_bounceback": {"weight": 0.18, "invert": False, "k": 40},
        },
        # Every durability term here except the two workhorse rates is
        # CONDITIONED ON SURVIVING: `stuff_after_75` only exists for a
        # pitcher who threw a 76th pitch, `third_time_through` only for one
        # who faced the order three times. A starter who keeps getting
        # knocked out early is therefore graded only on the nights he
        # lasted, and his disasters never appear anywhere. `long_start_rate`
        # and `blowup_rate` are defined over ALL of his starts, which is
        # what closes that hole -- so they take weight from the two
        # survivorship terms rather than being bolted on beside them.
        "grit": {
            "stuff_after_75":     {"weight": 0.20, "invert": False, "k": 300},
            "third_time_through": {"weight": 0.15, "invert": False, "k": 150},
            "long_start_rate":    {"weight": 0.18, "invert": False, "k": 8},
            "blowup_rate":        {"weight": 0.15, "invert": True,  "k": 8},
            "inherited_runners":  {"weight": 0.14, "invert": False, "k": 35},
            "workload":           {"weight": 0.09, "invert": False, "k": 25},
            "pitching_inside":    {"weight": 0.09, "invert": False, "k": 250},
        },
        # Escaping jams now carries 45% of HUNT between its process and
        # outcome readings, up from 25% for process alone. k on the outcome
        # term is in JAMS, not pitches -- a starter sees somewhere around
        # forty a season, so k=25 means a full year is trusted at roughly
        # 60% and a half season at 45%. Noisier than the process terms by
        # construction, which is exactly why it is shrunk harder relative to
        # its own sample size.
        "hunt": {
            "risp_stuff_delta":   {"weight": 0.25, "invert": False, "k": 180},
            "putaway_lev":        {"weight": 0.30, "invert": False, "k": 120},
            "jam_escape_runs":    {"weight": 0.30, "invert": False, "k": 25},
            "jam_escape_process": {"weight": 0.15, "invert": False, "k": 100},
        },
        "fight": {
            "contender_rv_delta": {"weight": 0.40, "invert": False, "k": 300},
            "division_rv_delta":  {"weight": 0.35, "invert": False, "k": 300},
            "fight_process_delta": {"weight": 0.25, "invert": False, "k": 220},
        },
    },
}

# ---------------------------------------------------------------------------
# The in-house "stuff" proxy, z-scored against each pitcher's OWN baseline
# for each pitch type. Asks "is his slider still his slider in the 7th",
# never "is his slider good", which keeps it a pure process measure.
#
# Spin was pulled from Statcast but unused until it was added here.
# ---------------------------------------------------------------------------

STUFF_WEIGHTS = {
    "_velo": 0.40,
    "_move": 0.35,
    "_spin": 0.15,
    "_ext":  0.10,
}

# ---------------------------------------------------------------------------
# FIGHT — opponent quality weighting.
#   PT = (1 + A*Q_opp) * (1 + B*DIV) * (1 + C*LATE*STAKES)
# ---------------------------------------------------------------------------

FIGHT = {
    "opponent_quality_coef": 0.30,   # A - and NOT clamped at zero, so feasting
                                     #     on a bad team scores BELOW neutral
    "division_coef": 0.25,           # B
    "late_stakes_coef": 0.30,        # C
    "late_season_start_pct": 0.75,   # LATE ramps 0->1 over the final quarter
    "pythag_exponent": 1.83,
    "stakes_games_back": 6.0,        # "in striking distance" threshold
    "min_weight": 0.15,              # floor so no game weighs literally nothing
}

# ---------------------------------------------------------------------------
# Leverage. We compute an EMPIRICAL leverage index directly from the data
# rather than shipping a static table: for each game state, LI is the mean
# absolute win-expectancy swing, normalized to the league mean of 1.0.
# ---------------------------------------------------------------------------

LEVERAGE = {
    "score_diff_clip": 6,      # states beyond +/- 6 runs collapse into one bucket
    "min_state_sample": 25,    # below this, fall back to the coarser bucket
    "max_li": 6.0,             # clip pathological states
}

# ---------------------------------------------------------------------------
# Final scale.
#   xDAWG+ = 100 + SCALE * z_total          (rate stat, 100 = league average)
#   DAWG   = z_total * (opportunities / league average opportunities)
#
# SCALE=25 is chosen so the spread READS like OPS+: ~125 is a very good
# season, ~160 is an MVP-tier outlier. Recalibrate once real distributions
# are in hand.
# ---------------------------------------------------------------------------

SCALE = 25.0
QUALIFY = {
    # Loosened from 250/150. At 250 PA only 271 hitters qualified against 383
    # pitchers -- roughly a third of position players in the league, so
    # part-timers and platoon bats were missing from the board entirely.
    #
    # Letting them in is safe because shrinkage already does this job
    # properly: a component's z is scaled by n/(n+k), so a player with 160 PA
    # is regressed hard toward average rather than spiking on a small sample.
    # A hard cutoff on top of that was doing the same work twice, and doing
    # it more bluntly.
    "hitter_min_pa": 150,
    "pitcher_min_bf": 100,
}

# Teams -> (league, division). Used for the DIV term and the site's filters.
TEAMS = {
    "BAL": ("AL", "AL East"), "BOS": ("AL", "AL East"), "NYY": ("AL", "AL East"),
    "TB":  ("AL", "AL East"), "TOR": ("AL", "AL East"),
    "CWS": ("AL", "AL Central"), "CLE": ("AL", "AL Central"), "DET": ("AL", "AL Central"),
    "KC":  ("AL", "AL Central"), "MIN": ("AL", "AL Central"),
    "ATH": ("AL", "AL West"), "HOU": ("AL", "AL West"), "LAA": ("AL", "AL West"),
    "SEA": ("AL", "AL West"), "TEX": ("AL", "AL West"),
    "ATL": ("NL", "NL East"), "MIA": ("NL", "NL East"), "NYM": ("NL", "NL East"),
    "PHI": ("NL", "NL East"), "WSH": ("NL", "NL East"),
    "CHC": ("NL", "NL Central"), "CIN": ("NL", "NL Central"), "MIL": ("NL", "NL Central"),
    "PIT": ("NL", "NL Central"), "STL": ("NL", "NL Central"),
    "ARI": ("NL", "NL West"), "COL": ("NL", "NL West"), "LAD": ("NL", "NL West"),
    "SD":  ("NL", "NL West"), "SF":  ("NL", "NL West"),
}
