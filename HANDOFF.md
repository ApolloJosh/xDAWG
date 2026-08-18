# xDAWG — handoff

Working folder: `Documents/Claude/Projects/xdawg`
Repo: `github.com/apollojosh/xDAWG` (default branch `main`) · Live: https://apollojosh.github.io/xDAWG/

## What this is

xDAWG is a baseball metric for whether a player "has that dawg in him" —
clutch, plays hard, shows up when it matters. Deliberately **not** a talent
metric: if the leaderboard correlates tightly with WAR, the design has failed.
Full rationale in `docs/SPEC.md`; read that first.

Four pillars, for both hitters and pitchers:

- **BITE** — holds his approach under pressure. Built on *whiff*, not chase.
  Chasing isn't the sin; whiffing is. A hitter who chases a 1-2 slider and
  fouls it off has won something. Rewards extending the at-bat.
- **GRIT** — plays hard, stays available. Hustle down the line, HBP above
  expected, extra bases taken. For pitchers: stuff retention after pitch 75,
  third time through the order, inherited runners.
- **HUNT** — makes the play that wins it. Star catches weighted by leverage.
- **FIGHT** — shows up against teams that matter. Opponent quality × division
  × late-season stakes, multiplicative.

Output is `xDAWG` (rate, 100 = average, ~25 per SD) and `DAWG` (cumulative,
0 = average rather than replacement level).

## Where things stand

Working: the full Python package, the site, GitHub Pages deploy via Actions,
8/8 math tests passing. The site is live and rendering.

**The site is still showing placeholder data.** Names are real players, every
number is randomly generated. A yellow banner says so, driven by a `synthetic`
flag in the payload — it clears itself once real data is built.

**The live ingestion path has still never been run against Baseball Savant.**
This remains the immediate next task and the most likely source of bugs.

## Next step — run the real build, on CI

Baseball Savant is not reachable from sandboxed dev environments (the
proxy 403s on CONNECT), which is why this has stayed unrun. It *is*
reachable from a GitHub Actions runner, so both commands now exist as
manually-triggered workflows.

**Actions tab → "Smoke test ingestion" → Run workflow.** ~2 min. Pulls ten
days with relaxed qualification thresholds, runs the whole pipeline, prints
a top five, writes nothing. Caches under its own key, so it cannot clobber a
good full-season build.

Read the run summary, not just the green check. It pulls out:

- `Statcast schema drift, missing columns: [...]` — Savant renamed something.
  Fix `STATCAST_COLS` in `xdawg/ingest.py` (and whichever pillar module uses
  the column), rerun smoke, repeat.
- `... unavailable (...)` — an optional leaderboard fell back and its pillar
  term was dropped. Sprint speed, catch probability and standings each
  degrade gracefully rather than crashing, so this is easy to miss.

Once smoke is clean: **Actions tab → "Build real data" → Run workflow.**
30–60 min, ~500k pitches. It runs `scripts/verify_build.py`, and only if
that passes does it commit `site/data/data.js` to `main` — which fires the
Pages deploy on its own. Untick **commit** for a dry run that produces the
artifact without touching `main`. Tick **refresh** to bypass the Statcast
cache; otherwise a rerun after a code fix takes minutes, not another hour.

Running locally still works if you're on a machine with a normal network
route — `python -m xdawg smoke --season 2026`, then `build`. Same order.

## Verify these once real numbers exist

`scripts/verify_build.py` runs inside the build workflow and hard-fails on
1 and 2 below. 3 and 4 are yours.

1. **No pillar is all zeros.** Zero spread across a pillar means every one of
   its components silently dropped out. Also flags any component present for
   under half the players.
2. **Payload is not still flagged synthetic**, and enough players scored.
3. **Face validity.** Pedroia/Judge/Altuve/Arozarena types high; several
   inner-circle superstars near 100. That separation is the whole point. The
   verifier prints the top 25 into the run summary for exactly this.
4. **Low correlation with WAR.** This is a pass condition, not a failure. A
   leaderboard of the ten best players in baseball means a self-referenced
   delta isn't cancelling talent properly somewhere.
5. **Year-over-year stability** of xDAWG should beat raw clutch metrics.

## Design constraints for the site (locked, from Josh)

Gobold font family. No emojis, no gradients, no shiny buttons, no popup
modals — the player breakdown expands inline in the table. Must scale with
device size. Retro 2005-2015 flat aesthetic.

Palette: `#E63600` primary, `#00A69B` secondary, `#FFD300` accent, white
background, black text. **Yellow is chrome only, never a data fill** — it
measures 1.4:1 against white and fails contrast. The primary/secondary pair
was validated as a diverging scale at ΔE 15.3 under deuteranopia; keep those
two as the above/below-average colors.

Gobold files go in `site/assets/fonts/` (gitignored). Until they're added it
falls back to a condensed grotesque.

## Open questions Josh hasn't settled

- **Pillar weights** (currently 30/25/20/25 hitters, 30/25/25/20 pitchers).
  These are a values judgment, not an empirical finding — there's no
  ground-truth dawg dataset to fit against. His call.
- **FIGHT playoff-team definition**: standings at time of game (specced,
  truer to the concept) vs. final standings (simpler, but hindsight).
- Positional adjustment for HUNT — a catcher can't make a 5-star catch.

## Gotchas already hit

- Pages only serves from repo root or `/docs`. The site lives in `site/`, so
  deployment goes through `.github/workflows/pages.yml` publishing it as the
  Pages artifact. Source must be set to "GitHub Actions", not a branch.
- `cancel-in-progress` must be `false` in that workflow. True leaves the Pages
  deployment stuck server-side and blocks the next deploy with a 400.
- `at_bat_number` restarts every game — never `nunique()` it. Count distinct
  (game_pk, at_bat_number) pairs. This bug capped every player near 80
  opportunities and would have emptied the leaderboard.
- The local working copy was `git init`'d fresh at some point and lost its
  remote, so it sat on a `master` branch with a history unrelated to
  `origin/main`. If `git log` and the GitHub repo ever disagree again, check
  `git remote -v` before assuming either side is wrong. Content-wise the two
  never actually diverged beyond work-in-progress.
- Savant being unreachable looks like a bug in `ingest.py` but isn't. Check
  whether you have a network route before debugging the code.

## Tuning

Every weight, coefficient, and shrinkage constant is in `xdawg/config.py`.
Nothing else hardcodes them.

The most important test is
`test_weighted_delta_is_zero_under_uniform_weights`: under flat leverage every
delta must be *exactly* zero. If that ever fails, xDAWG has started measuring
raw talent instead of change-under-pressure — the precise failure the whole
design exists to prevent.

`test_weighted_delta_detects_a_real_clutch_signal` used to be a coin flip —
n=400 against a bare `> 0.15` floor, when the analytic answer is 0.2, so it
failed on roughly one seed in ten. It now uses its own generator (the shared
module-level `RNG` made every test's draws depend on run order) and asserts a
band. If you add tests here, give them their own generator.
