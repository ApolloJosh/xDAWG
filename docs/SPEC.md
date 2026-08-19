# xDAWG — Specification v0.2

*A metric for measuring whether a player has that dawg in him.*

---

## 1. What xDAWG measures — and what it deliberately doesn't

xDAWG measures **how a player's performance changes when the moment gets bigger**, plus **how hard he plays when nobody's forcing him to**.

It is explicitly **not** a talent metric. The design goal is that a .240 hitter can post a 150 xDAWG+ while an MVP-caliber bat posts a 98. If the leaderboard ends up correlating tightly with WAR, the stat has failed — we'd have just rebuilt WAR with a funnier name.

This falls out of the math rather than being bolted on. Three of the four pillars are **self-referenced deltas**: a player is measured against *his own* baseline, not the league's. That means park effects, era, home ballpark, and raw talent all cancel out algebraically. What survives the subtraction is the thing we actually care about.

---

## 2. The central problem, and the design that dodges it

The naive version of this stat — high-leverage OPS minus normal OPS — doesn't work. Twenty years of sabermetric research says clutch *outcomes* essentially don't persist year to year. The reason is sample size: the average hitter gets **60–80 high-leverage plate appearances per season**. You need ~500 PA for OPS to stabilize. Build xDAWG on that foundation and last year's leaderboard will have near-zero overlap with this year's.

Four design principles keep us out of that hole:

**P1 — Process over outcomes.** Outcomes need ~500 PA to stabilize; swing decisions need ~50–100. Those same 80 high-leverage PA contain roughly 300 pitches, and chase rate, contact rate, and exit velocity *are* measurable off that. So we don't ask "did he get the hit." We ask **"did he stay himself."** Did he expand the zone, did his swing get long, did his stuff back up. That's a real signal in the sample we actually have — and it maps cleanly onto the intuition, because the dawg is the guy who doesn't shrink.

**P2 — Continuous leverage weighting.** No `LI > 1.5` cutoff. Every plate appearance is weighted by its own Leverage Index. Keeps all the data, kills an arbitrary threshold.

**P3 — Self-referenced deltas.** Every pillar except GRIT is `(weighted performance) − (that player's own unweighted baseline)`.

**P4 — Shrinkage, always.** Each component is regressed toward league mean by its own opportunity count: `z_shrunk = z_raw × n / (n + k)`, where `k` is that metric's stabilization constant. Without this, a guy with 12 high-leverage PA tops every leaderboard.

---

## 3. The four pillars

| Pillar | Question it answers | Nature |
|---|---|---|
| **BITE** | Does he hold his approach when it's tight? | Self-referenced delta, process |
| **GRIT** | Does he play hard and stay available? | Absolute level, effort |
| **HUNT** | Does he make the play that wins it? | Self-referenced delta, big moments |
| **FIGHT** | Does he show up against the teams that matter? | Self-referenced delta, opponent-weighted |

---

### 3.1 Hitters

**BITE — process under pressure** *(weight 30%)*

Leverage-weighted minus unweighted, per player.

**The organizing idea is the extended at-bat, not the disciplined one.** Chasing is not itself an anti-dawg act. A hitter who chases a 1-2 slider and fouls it off has *won something* — he's still alive, and he just made the pitcher throw another one. What actually kills you is the swing and miss. So BITE is built around **whiff, not chase**, and it explicitly rewards making the pitcher work.

- **Whiff rate delta** (inverted) — swings-and-misses per swing under leverage vs. baseline. **The primary component.** Does he still make contact when it's tight?
- **Chase-contact rate** — of the pitches he *does* chase, how many does he put in play or foul off? This is scored as a *positive*, which is the whole point of the change: expanding the zone and surviving it is a skill, and it's a dawg skill.
- **Pitches per PA delta** — grinding the count in big spots. Making a starter throw 9 pitches in the 6th with a runner on is a real contribution that shows up nowhere in a box score.
- **Two-strike foul rate delta** — fouling off pitches with two strikes is the single most literal measurement of refusing to be put away.
- **Hard-hit % delta** — does the swing hold up, or does he get tentative?
- **Bounce-back** — performance in the PA immediately following a strikeout. Short memory is a dawg trait and it's directly computable from pitch-level data.

Chase rate survives only as a small *context* term — a hitter who both chases constantly and whiffs on those chases is a different (and worse) player than one who chases and fouls them off. Whiff and chase-contact do the real work.

**GRIT — plays hard, always** *(weight 25%)*

The stickiest pillar year-to-year, and the most literally "dawg." This is the one pillar measured on absolute level, not delta.

- **Hustle ratio** — home-to-first time on routine ground balls as a fraction of the player's *own* max sprint speed. This is a direct, physical measurement of running it out. Statcast gives us both numbers.
- **HBP rate above expected** — wearing one on purpose, controlling for how often he's actually pitched inside.
- **Extra bases taken %** — first-to-third on singles, aggression on the bases.
- **Availability** — games played as a share of team games, with credit for returning quickly from injury.

**HUNT — the play that wins it** *(weight 20%)*

Statcast buckets every batted ball by catch probability, with 5-star being 0–25% likely to be caught. This is your 3–5 star catch idea, made computable.

- **Leverage-weighted star catches** — a 5-star grab in a tie game in the 9th counts enormously; the same catch in an 11-run blowout counts for almost nothing.
- **Attempt rate on low-probability balls** — credit for *committing to the route*, not just for conversion. Selling out for a ball you probably won't get is the dawg part, and conversion is noisier than attempt.
- **Outfield assists and catcher blocks**, leverage-weighted.
- **Baserunning run value in high leverage** — the tag-up, the extra base in a one-run game.

**FIGHT — against the teams that matter** *(weight 25%)*

See §4 for the opponent-weighting math.

---

### 3.2 Pitchers

Same four pillars, different instruments. Note on "stuff": FanGraphs' Stuff+ isn't available split by count or pitch number, so we build a **lightweight in-house stuff proxy** from raw Statcast physical components — velocity, induced movement (`pfx_x`/`pfx_z`), spin, and release extension — measured against *each pitcher's own* season baseline. This is a pure process metric, which is exactly what P1 asks for.

**BITE — execution under pressure** *(weight 30%)*

- **Two-strike stuff delta** — does the put-away pitch keep its shape, or does he lose the finish? *(your call, and it's the right one — it mirrors the hitter's two-strike battle from the other side)*
- **Zone% and first-pitch-strike% with runners on vs. bases empty** — attacking versus nibbling. This is *the* classic pitcher-dawg trait and it's very sticky year to year.
- **Walk rate delta under leverage** (inverted) — the free pass in a tight spot is the anti-dawg outcome.
- **Bounce-back after damage** — performance against the next batter after allowing a home run, or after an error behind him. Short memory again.

**GRIT — durability and dirty work** *(weight 25%)*

- **Stuff retention after pitch 75** — velocity and movement in pitches 76+ measured against his own pitches 1–25. *(your idea, and it's the cleanest possible operationalization of "he's still got it in the 7th.")*
- **Third-time-through-the-order performance** — holding up when the lineup has seen him twice.
- **Starts of 5+ innings** and **knocked out early** (failed to record nine outs, inverted) — the innings eater who saves a bullpen, and the starter who does not. These exist because everything above them is *conditioned on surviving*: stuff-after-75 only exists for a pitcher who threw a 76th pitch, third-time-through only for one who faced the order again. A starter who keeps getting blown out early contributed to neither, so he was graded only on the nights he lasted and his disasters appeared nowhere in the metric. That is a selection bias large enough to invert a pitcher's score, and it is exactly what the eye test caught.
- **Inherited runners** — relievers cleaning up someone else's mess. Dirty work nobody gets credit for.
- **Workload willingness** — days rest, back-to-back appearances, multi-inning relief outings.
- **Pitching inside** — willingness to work the inner half against same-handed hitters and wear the consequences.

**HUNT — the kill shot** *(weight 25%)*

- **Stuff with RISP and in high leverage** — same delta structure, aimed at the moment.
- **Put-away rate** in two-strike counts, leverage-weighted.
- **Escaping self-created jams, process** — chase rate with runners on, immune to what the defense did behind him.
- **Escaping self-created jams, damage** — run value from the first jam of an inning through the end of that inning, against the run expectancy of the state he was in. Process alone was the original design, on the argument that strand rate is too noisy to grade. That argument is right about *strand rate* and wrong as a reason to ignore outcomes: a run-expectancy-relative measure is far steadier than LOB%, and without it a pitcher who blows up every time he gets in trouble still graded out fine on the strength of his swing-and-miss. Charged through the end of the half inning even if he is pulled — the runners were his. The reliever is separately credited under **inherited runners**, so it is one event on two ledgers, not double counting.

**FIGHT** *(weight 20%)*

---

### A known asymmetry in the two stat families

Every leverage delta ships two readings — one against the player's own flat
mean (feeds wDAWG+), one against the league's (feeds DAWG+). But the
components measured on **absolute level** have no self-referenced version,
because there is nothing to self-reference: hustle, HBP above expected, extra
bases taken, availability, and season-total OAA are single numbers, not
splits. They pass into BOTH stats unchanged.

That is **26.0% of a hitter's score and 25.2% of a pitcher's** identical
across the two families. It matters most for exactly the players the two
numbers disagree about: a hitter whose self-referenced deltas sit near zero —
which is what a player with no real clutch *change* looks like, however great
he is — has his wDAWG+ decided almost entirely by that quarter. For an elite
slugger who is a station-to-station baserunner and a poor fielder, wDAWG+
stops being "how much he rises to the moment" and becomes a baserunning and
availability score.

Worth knowing what is NOT the cause. Simulation of the null (no clutch skill,
600 PA) shows `weighted_delta` is unbiased at every talent level — mean delta
is zero at a .55 true rate and at a .93 one. Its noise actually *falls* as the
baseline rises (sd .0136 → .0070), so a real +5-point clutch effect is easier
to detect in an elite hitter, not harder: signal-to-noise goes 1.24 → 2.97. A
high baseline does not make a good wDAWG+ harder to earn. The absolute-
component block does.

The fix, if it is wanted, is to drop the absolute components from the wDAWG
pass and renormalize — `score_pillar` already renormalizes around missing
components, so this is a flag, not a rewrite. It would make wDAWG+ mean
strictly what it claims to. It would also stop wDAWG+ from penalizing a DH for
being a DH, which is either the point or a loss depending on what "dawg" is
supposed to include.

---

## 4. FIGHT — the opponent-quality engine

Every plate appearance (or batter faced) gets a **fight weight** alongside its leverage weight.

**Step 1 — opponent quality.** Use Pythagorean win% rather than actual win%, because it's meaningfully more stable:

```
PyW%_t = RS^1.83 / (RS^1.83 + RA^1.83)
Q_t    = (PyW%_t − 0.500) / SD(PyW%)        # z-score, roughly −2.5 to +2.5
```

**Step 2 — the weight.** Multiplicative, so the factors stack:

```
PT_i = (1 + 0.30·Q_opp) × (1 + 0.25·DIV_i) × (1 + 0.30·LATE_i·STAKES_i)
```

- `Q_opp` — opponent quality. Note this is *not* clamped at zero: feasting on a 100-loss team actively counts **less** than a neutral game, which is what you asked for.
- `DIV_i` — 1 if divisional opponent, else 0. Division games decide the standings directly.
- `LATE_i` — ramps from 0 to 1 over the final six weeks of the season.
- `STAKES_i` — 1 if either club is within striking distance of a playoff spot at the time of the game.

**What this produces:** a September division game against a 100-win team carries roughly **6.5× the weight** of an April game against a tanking non-division club (≈2.6 vs ≈0.4). That ratio feels about right, and every coefficient is tunable.

**Step 3 — the score.** Primetime-weighted run value per opportunity, minus the player's unweighted baseline.

**One honest note:** FIGHT is the only pillar where I'm comfortable using outcomes rather than process, and the reason is sample size. High-leverage PA are ~80 a season. Divisional and quality-opponent PA are **300–400** — four to five times the sample, which lands close enough to stabilization that run-value outcomes carry real signal.

**One caution:** a high-leverage PA against a playoff team in September gets counted in both BITE and FIGHT. Some overlap is desirable — those moments *should* count most — but we should check inter-pillar correlation once we have real data and orthogonalize if it's severe.

---

## 5. Aggregation and scale

You asked whether cumulative or index-based makes more sense. **The answer is both**, and they're nearly free to produce from the same core — this mirrors how wRC+ and Batting Runs are the same underlying number expressed two ways.

```
Z_total = Σ (pillar weight × shrunk pillar z-score)     # re-standardized so SD = 1
```

**xDAWG+ — the rate stat, and the headline number.**

```
xDAWG+ = 100 + 25 × Z_total
```

100 = league average. 125 ≈ 1 SD (top ~16%). 150 ≈ 2 SD (top ~2.5%). 175 ≈ 3 SD (a handful of players per decade).

One honesty note: because the composite is built from z-scores, 120 means "1 SD more dawg," not "20% more dawg" — z-scores are interval, not ratio. In practice this is the same fudge OPS+ makes, and the `25` multiplier is chosen specifically so the spread *reads* like OPS+, where 120 is a good regular and 160 is an MVP season. We calibrate that constant empirically once we see real distributions.

**DAWG — the counting stat.**

```
DAWG = Z_total × (opportunities / league-average opportunities)
```

Units are "dawgs." Zero is league-average — note this is different from WAR, which uses replacement level. Negative values mean actively not-a-dawg, which is both useful and funny. A full season at 2 SD = 2.0 dawgs; the same rate in half a season = 1.0.

**On the weights:** there's no ground-truth dawg dataset to regress against, so the pillar weights are a stated *values judgment*, not an empirical result. I'd rather be upfront about that than dress it up. We should run sensitivity analysis to see which conclusions survive reweighting.

---

## 6. DAWG vs. xDAWG — descriptive and predictive

Worth producing both, because the gap between them is the most interesting output:

- **DAWG** — what actually happened. Includes the walk-offs, the raw clutch outcomes. Noisy, fun, settles arguments.
- **xDAWG** — built only from the stable, repeatable inputs. What his dawg *should* look like next year.
- **DAWG − xDAWG** — the money column. Large positive = overperforming his dawg, regression candidate. Large negative = a dawg the results haven't caught up to yet, buy low.

---

## 7. Validation plan

Since there's no ground truth, **face validity is the real test** — and the test is specifically that the stat must separate *great* from *dawg*.

- **Should score high:** Pedroia, Utley, Jeter, Judge, Altuve, Arozarena, Seager. Pitchers: Scherzer, Verlander, Eovaldi, Charlie Morton.
- **The critical test:** several inner-circle superstars should land near 100. If every great player scores high, the stat is measuring talent and we've failed.
- **Stability check:** year-over-year correlation of xDAWG should be meaningfully positive (target r > 0.4). DAWG will be much lower — that's expected and fine.
- **Independence check:** correlation with WAR should be *low*. That's a pass condition, not a failure.

---

## 8. Data sources and feasibility

| Input | Source | Notes |
|---|---|---|
| Pitch-level everything | Statcast via `pybaseball` | Includes `delta_home_win_exp`, `delta_run_exp` |
| Leverage Index | Derived from base-out-inning-score state | Lookup table, or from FanGraphs directly |
| Catch probability / star ratings | Baseball Savant | 2016+ |
| Sprint speed, home-to-first | Baseball Savant | 2015+ |
| Stuff components | Statcast raw (velo, movement, spin, extension) | Build our own proxy |
| Extra bases taken % | Baseball-Reference | |
| Team Pythagorean W% | Any standings source | For FIGHT |

**Era limit:** the full version works for **2016–present** (Statcast era). Earlier seasons could support a reduced version — Retrosheet play-by-play gives leverage, HBP, and baserunning, but no stuff or catch probability.

**Environment constraint:** this cloud sandbox's network is proxied — FanGraphs, Savant, and Baseball-Reference are all unreachable from here. `pybaseball` installs fine but every fetch dies at the proxy. The pipeline runs on your machine, or from CSV exports. GitHub's API *is* reachable, so the repo itself is no problem.

---

## 9. Open calibration questions

1. **Pillar weights** — the 30/25/20/25 split is a starting guess, not a finding.
2. **Playoff-team definition for FIGHT** — final standings (stable, but hindsight) or standings at time of game (truer to "primetime," more work)? Currently specced as `STAKES` at time of game.
3. **Positional adjustment for HUNT** — a catcher can't make a 5-star catch. Either compare within position or drop the outfield-specific components for non-outfielders.
4. **Minimum playing time** to qualify for xDAWG+.
