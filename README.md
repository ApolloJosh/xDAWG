# xDAWG

A baseball metric for whether a player has that dawg in him.

Not a talent metric. xDAWG measures how a player's performance **changes when
the moment gets bigger**, plus how hard he plays when nobody is forcing him to.
A .240 hitter can post a 150. If this leaderboard ends up looking like the WAR
leaderboard, the metric has failed.

Full design rationale is in [`docs/SPEC.md`](docs/SPEC.md).

## The four pillars

| | Question | How it's measured |
|---|---|---|
| **BITE** | Does he hold his approach when it's tight? | Whiff rate, contact on chases, pitches per PA, two-strike foul-offs — all leverage-weighted against his own baseline |
| **GRIT** | Does he play hard and stay available? | Hustle down the line, HBP above expected, extra bases taken, availability |
| **HUNT** | Does he make the play that wins it? | Star catches weighted by leverage, attempt rate on low-probability balls |
| **FIGHT** | Does he show up against the teams that matter? | Opponent quality × division × late-season stakes |

Pitchers use the same four pillars with different instruments — stuff retention
after pitch 75, attacking with runners on, two-strike stuff, inherited runners.

## Why it isn't just "clutch"

Clutch *outcomes* famously don't persist year to year. The average hitter gets
60–80 high-leverage plate appearances a season and OPS needs ~500 to stabilize,
so a naive high-leverage-OPS-minus-normal-OPS stat is a random number generator.

xDAWG dodges this by measuring **process, not outcomes**. Swing decisions
stabilize in 50–100 PA, and those same 80 high-leverage PA contain ~300 pitches.
We don't ask whether he got the hit. We ask whether he stayed himself.

The organizing idea in BITE is the *extended* at-bat, not the disciplined one.
Chasing isn't the sin — whiffing is. A hitter who chases a 1-2 slider and fouls
it off has won something: he's alive, and the pitcher has to throw another one.

## Install

```bash
git clone <your-remote> xdawg && cd xdawg
pip install -r requirements.txt
```

## Use

```bash
python -m xdawg mock                    # placeholder data, renders instantly
python -m xdawg build --season 2026     # the real thing (slow first run)
python -m xdawg serve                   # http://localhost:8000
```

`site/index.html` also opens straight off the filesystem — data is emitted as a
`data.js` script assignment rather than JSON, specifically so there's no server
or CORS argument between you and the leaderboard.

**The first `build` takes a while.** A season is ~700k pitches and Savant
rate-limits, so pybaseball chunks it by date. Everything caches to
`~/.xdawg_cache` as Parquet; subsequent runs are instant. Override with
`XDAWG_CACHE`.

## Output scales

- **xDAWG** — the rate stat. 100 is league average, each 25 points is roughly
  one standard deviation. Calibrated so the spread reads like OPS+.
- **DAWG** — the cumulative version, scaled by playing time. Zero is league
  average here, *not* replacement level, so negative means actively not-a-dawg.

## Tuning

Every weight, coefficient, and shrinkage constant lives in
[`xdawg/config.py`](xdawg/config.py). Nothing else hardcodes them.

The pillar weights are a **values judgment, not an empirical finding** — there's
no ground-truth dawg dataset to regress against. Change them and rerun.

## Tests

```bash
python tests/test_core.py
```

The important one is `test_weighted_delta_is_zero_under_uniform_weights`: if
leverage is flat, every delta must be exactly zero. A non-zero result there
would mean the metric is picking up raw talent instead of change-under-pressure,
which is the exact failure the design exists to prevent.

## The site

Leaderboard with MLB/AL/NL tabs, team and role filters, name search, sortable
columns, and an inline breakdown panel (never a modal) showing how each pillar
contributes to the total.

**Fonts.** The display face is Gobold. Drop the files into
`site/assets/fonts/` as `Gobold.woff2` / `Gobold-Bold.woff2` (`.woff` and
`.ttf` also wired up) and they pick up automatically; until then it falls back
to a condensed grotesque. Gobold is free for personal use — commercial use
needs a license from the foundry.

**Colors.** `#E63600` primary, `#00A69B` secondary, `#FFD300` accent, on white
with black text. The accent is chrome only (active tab, open row) and never a
data fill — it fails contrast at 1.4:1 on white. The primary/secondary pair was
validated as a diverging scale at ΔE 15.3 under deuteranopia.

## Data notes

- Full metric requires the Statcast era: **2016 and later**.
- Optional leaderboards degrade gracefully. If Savant catch probability is
  unreachable, HUNT's star-catch term drops out and the remaining pillar
  weights renormalize rather than failing the run.
- `python -m xdawg mock` fabricates every number. Player names are real; the
  stats are random. The site shows a visible notice whenever it's displaying
  placeholder data.

## Status

The math is tested end to end against synthetic data. The **live ingestion path
has not been run against Savant yet** — it was written in an environment with no
route to the data hosts. Expect to shake out a schema detail or two on the first
real `build`.
