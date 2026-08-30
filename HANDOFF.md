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
- **HUNT** — makes the play that wins it. Built on OAA, scaled by the
  leverage and opponent quality the fielder was actually exposed to.
  Savant publishes OAA only as a season total per player, so no individual
  play can be weighted; `pipeline._fielding_context` instead attributes
  batted balls to fielders via `fielder_2`..`fielder_9` and measures the
  situations each man played in. **This is the one component not
  self-referenced against a player's own baseline** — OAA is a talent
  measure. If the board starts tracking WAR, look here first.
- **FIGHT** — shows up against teams that matter. Opponent quality × division
  × late-season stakes, multiplicative.

## The four numbers

Two baselines, each with a rate and a cumulative version. The "x" was
dropped from all of them, reserved for a future expected-value variant.

| | Rate (100 = avg, ~25/SD) | Cumulative (0 = avg) | Baseline |
| --- | --- | --- | --- |
| League-relative | **DAWG+** | **DAWG** | the league |
| Self-referenced | **wDAWG+** | **wDAWG** | the player's own norms |

`DAWG+` is the site default. It keeps raw ability in the number, so it reads
closer to a conventional leaderboard. `wDAWG+` is the original construction:
each player against himself, so talent cancels algebraically and only
change-under-pressure survives.

Both come from ONE pass. Every delta component computes its league-baselined
twin alongside the self-referenced one (`<component>__lg`), because they are
the same arithmetic with a different subtrahend --
`league_delta = delta + (player's level - league level)`. Scoring runs twice
over frames that are already built; Savant is hit once. On synthetic data the
two correlate at about +0.32, so they are genuinely different measurements
rather than a rescaling.

Components measured on absolute level (hustle, availability, OAA) have no
separate league version and pass through unchanged -- z-scoring across
players IS a league comparison, so they read the same in both stats.

## Where things stand

**Real 2026 data is live** (commit `30b3a0b`, 18 Aug 2026). 592,197 pitches,
271 hitters and 383 pitchers scored, every component at 100% coverage, no
schema drift and no leaderboard fallbacks. The synthetic banner is gone.

The distribution came out clean: max +2.86 SD (171.5), min 14.9, both roles
standardized to 100/25. The fat tail seen in the 10-day smoke (+5.0 SD) was
sparse component coverage and resolved on its own at full-season length, so
the `score_pillar` renormalization flaw below was never worth fixing.

Face validity holds. The four pillars are near-independent for hitters (all
|r| <= 0.09), xDAWG barely tracks playing time (r = +0.12), and 12 of 22
inner-circle stars land within +/-0.6 SD of average — Witt 102.0, Harper
101.3, Carroll 102.1, Bregman 106.2 — while the top of the board is
grinders: Giménez 168.2, Murakami 166.0, Cowser 151.5, Varsho 143.8,
Rafaela 141.9. That separation was the entire design goal.

### Done since the first real build

- **GRIT now has four components, FIGHT two.** `availability` (games played
  as a share of his own club's games), `hbp_above_expected` (HBP against an
  empirical expectation built from the pitch locations he actually saw) and
  `extra_bases_taken` all compute from the pitch feed. `fight_process_delta`
  runs the FIGHT weighting over contact-on-swing instead of run value.
- **`extra_bases_taken` needs no new data source.** `on_1b`/`on_2b`/`on_3b`
  carry runner IDs, so comparing consecutive plate appearances within a game
  recovers where each runner ended up. Restricted to singles and doubles,
  where a vanished runner has almost certainly scored rather than been
  retired. This replaced a planned StatsAPI play-by-play loader that would
  have meant ~2,400 extra game calls per build.
- **HUNT deduplicated.** `oaa_rate` dropped to zero weight (r = 0.994 with
  `oaa_situational`); zero-weight components are now hidden from the site's
  breakdown panel rather than displayed as if they counted.
- **BITE rebalanced.** `chase_contact` 0.22 -> 0.12, with the weight moved to
  `pitches_per_pa_delta` and `two_strike_foul_delta`, which measure something
  whiff-avoidance does not.
- **data.js is cache-busted.** `write_site_data` stamps the script tag in
  `index.html` with the build timestamp, and the build workflow now commits
  `index.html` alongside `data.js`.

- **Clutch, computed in-house.** `leverage.clutch` implements FanGraphs'
  `WPA/pLI - WPA/LI` from `delta_home_win_exp` and our own empirical leverage
  index. It feeds `wpa_clutch_delta` in hitter HUNT, which is what makes that
  pillar mean "the hit that wins it" rather than only "the catch that saves
  it" -- before it, hitter HUNT was pure fielding while pitcher HUNT was
  situational performance. Weight 0.35 with k=350: Clutch has notoriously
  low year-over-year signal, so it needs a full season before it moves
  anyone. Note this DIVIDES by leverage rather than multiplying, because WPA
  already has leverage baked in; multiplying would count it twice.
- **Exit velocity replaced the hard-hit flag.** `ev_situational` is a
  leverage-weighted delta on continuous exit velocity, so there is no cliff
  at 94.9 mph and squaring one up for an out still scores as good process.
- **Spin joined the stuff proxy.** `release_spin_rate` was pulled from
  Statcast from day one and used by nothing. Stuff weights now live in
  `config.STUFF_WEIGHTS` (velo .40 / move .35 / spin .15 / extension .10)
  rather than being hardcoded in the pillar module.
- Two of the four metrics proposed in that round already existed: pitches
  per AB is `pitches_per_pa_delta`, and two-strike velocity is
  `two_strike_stuff_delta`.

- **`groupby().first()` is not the first row.** It returns the first NON-NULL
  value in each column independently. `inherited_runners` used it to read the
  base state when a pitcher entered, so a starter who began an inning with
  bases empty picked up whichever runners appeared later in his outing and
  was credited with inheriting them — Michael Wacha, who has started every
  game, was showing +0.55 on a reliever-only component. Fixed with
  `drop_duplicates(keep="first")`, which takes the actual row. The same trap
  was reading `xbt_frame`'s per-PA base state and is fixed the same way.
- **FIGHT is two categories now.** `contender_rv_delta` weights only by
  opponent quality, `division_rv_delta` only by divisional rivalry, each
  still riding the late-season ramp. `fight.fight_weight` takes a `terms`
  argument selecting which multiplicands apply. Verified they are independent:
  contender weight tracks quality and is blind to rivalry, division weight
  the reverse.
- **Team is now the most RECENT club, not the most frequent.** A player
  traded in July kept more games with his old team, so he stayed listed
  there. Note the ceiling: this is the last club he actually appeared for in
  the pitch data, so a player released and not since signed still shows his
  last team. Current-roster truth would need a StatsAPI roster call.
- **Qualification loosened to 150 PA / 100 BF** (from 250/150). At 250 only
  271 hitters made the board against 383 pitchers. Shrinkage already
  regresses small samples toward average, so a hard cutoff on top was doing
  the same job twice and more bluntly.
- **Grinding moved from BITE to GRIT.** `pitches_per_pa_delta` and
  `two_strike_foul_delta` are effort, not approach. Also drops `hustle_ratio`
  from 35% to 22% of GRIT.

- **Team totals table.** `export.build_teams` sums each club's cumulative
  DAWG (so depth and playing time count) and shows the roster's mean rate
  beside it, against actual W-L, win% and run differential from StatsAPI.
  Worth noting the comparison is not circular: FIGHT uses Pythagorean
  expectation from runs, never actual wins, so the record column is
  independent of everything feeding the player scores.
- **Site defaults to DAWG+**, with all four numbers in the table and the
  podium reading down each column (1-5 left, 6-10 right) via
  `grid-auto-flow: column`. Verified by rendering the page headlessly and
  checking the DOM order stays 1-10 for screen readers while the visual
  order is column-major.

- **Two-way players were always scored twice; the UI hid one.** Ohtani has
  a hitter row AND a pitcher row in the payload (2026: pitcher DAWG+ 167.5 at
  rank 5, hitter 122.7 at rank 150) because the pipeline scores the roles
  independently and he clears both thresholds. Both rows carried the same
  MLBAM id, so the site's `data-id` lookup collided and always opened the
  first one. Rows are now keyed `id + "-" + role`, and the breakdown panel
  shows a Hitter/Pitcher toggle when more than one entry shares an id.
  Nothing about the scoring changed.
- **A cached frame missing a newly-added column is now treated as a miss.**
  The cache is keyed on season alone, so the standings parquet written before
  `wins`/`losses` existed kept being served and team W-L stayed empty on all
  30 clubs while run differential filled in fine (rs/ra were always cached).
  `ingest._read_cache(name, required)` checks columns and refetches, so
  adding a field to a loader is self-healing instead of needing a manual
  cache purge. Use it for any loader that gains a column.

### Still open

1. **Hitter GRIT and FIGHT each rest on a single component** — `hustle_ratio`
   and `fight_rv_delta` — yet carry 30% of the metric apiece. So 60% of every
   hitter's score is two numbers. Aaron Judge's GRIT of -2.45 *is* his hustle
   figure and nothing else. `hbp_above_expected`, `extra_bases_taken`,
   `availability` and `fight_process_delta` are specced in config but no
   loader computes them; `pipeline.run` passes `None` for all three GRIT
   extras. Building those is the highest-value work left.
2. **HUNT's two components correlate at r = 0.994.** `oaa_situational` is
   `oaa_rate` times the context index, so they are one number wearing two
   hats while drawing two weights. Either drop `oaa_rate` or give the pillar
   a genuinely independent second term.
3. **`whiff_delta` and `chase_contact` correlate at r = 0.738** and together
   hold 52% of BITE, so whiff avoidance is substantially double-counted. Note
   this rose when `chase_contact` was converted from a raw level to a
   leverage-weighted delta — the conversion was right on its own terms, but
   it made the two terms more alike.
2. **WAR correlation is still unmeasured.** It needs a source this
   environment cannot reach. It is the one pass condition from the original
   spec that has not been checked.
3. **FanGraphs cannot be pulled from CI at all** -- it 403s datacentre IPs,
   which is what killed the standings loader. Anything wanted from there has
   to be computed in-house (as Clutch now is) or fetched from a machine on a
   residential connection.
4. **Pitchers have no clutch term.** `leverage.clutch` is role-agnostic and
   would work on `pitcher` unchanged; it was left to hitters because pitcher
   HUNT already carries three components and adding a noisy fourth there is
   a harder case to make.

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

## Awards — Day, Week, Month

`xdawg/awards.py`, written by the ordinary `build` (not a separate command,
because they need the same pitch frame and a Day award refreshing on a
different schedule from the leaderboard would drift out of agreement with
it). Output is `site/data/awards.js`, page is `site/awards.html`.

Weeks are Monday–Sunday. Months are calendar months.

**They are not decided by DAWG+.** A day is about four plate appearances and
every pillar is a rate against a baseline; at four trips those rates are
noise, and a confident DAWG+ for one day would be a lie with a straight face.
The score is two halves, in DAWG points (1 pt = 1% win probability):

    score = OUTCOME (100 x WPA x FIGHT) + PROCESS (credits x leverage x FIGHT)

PROCESS is the DAWG half and the reason this is not a win-probability
leaderboard: the eight-pitch walk in the ninth, the two-strike foul-off,
chasing and not whiffing, wearing a pitch; the closer pumping strikes with men
on, working inside to a same-handed hitter, escaping a jam scoreless. Debits
for taking strike three and for the free pass in a tight spot. Values live in
`config.AWARD_CREDITS`. Every credit is a COUNTABLE EVENT, which is what makes
it usable in a one-day window where a rate would be shrunk to nothing.

Three corrections make the halves comparable, each found by watching the first
cut get it wrong:

- **Centered.** WPA is zero-sum; credits only accumulate. Raw, the day award
  went to a pitcher with +0.039 WPA and a score of 64 — a pitch count, not an
  award. Every plate appearance is now charged the credits an average player
  would have earned at that leverage against that opponent.
- **Damped.** Leverage runs to 6 and multiplies every credit, so a six-point
  jam escape at 6x was worth 68 points, more than a walk-off. The multiplier
  is square-rooted and capped at 2x.
- **Capped.** Leverage and opponent weight compound, so no single plate
  appearance may earn more process than the 99.5th percentile of real WPA
  swings.

The balance is **calibrated, not guessed**: `calibrate_process` matches the
process bucket's spread to WPA's at the player-day level, so the AWARD_CREDITS
numbers only decide what a jam escape is worth relative to a two-strike
foul-off. `AWARD_PROCESS_BALANCE` is the dial (1.0 = even).

Worth knowing: process accumulates steadily while WPA is noisy, so **the
longer the window the more process decides it** — roughly even on a day,
heavily process by a month. Arguably right for a dawg metric, but a design
property rather than an accident.

`inside_sign` learns which sign of `plate_x` means inside from the mean
plate_x of hit batsmen per stance, rather than hardcoding a convention that
would silently reward pitching AWAY if Savant flipped the axis.

WPA is already leverage-aware by construction, the FIGHT weight asks who it
was against, and it is summed rather than averaged so four good trips beat
one. Eligibility floors — 2 / 8 / 30 trips for a COMPLETE window — stop a one-swing
cameo winning. They are **prorated by how much of the window has actually been
played**, never below a hard minimum of 2. This was not a nicety: on the
Tuesday of a week, holding a two-day week to the eight-batter bar meant for a
finished week threw out every reliever in the league and handed the award to
whichever starter happened to have made a start. The live site crowned one at
a score of **minus 0.668**, unopposed, with an empty runners-up list.

**Traditional lines.** Every kept row carries a `line`: PA / HR / BB / OPS for
hitters, IP / K / BB / RA9 for pitchers, computed from Statcast events by the
rule book (a walk is a plate appearance but not an at-bat; a sacrifice fly
leaves the on-base denominator alone but not the slugging one). Clicking any
archive row opens that window's podium with each man's line and the moment
that decided his score.

It is **RA9, not ERA**, and the name is load-bearing: Statcast publishes runs
but not the earned/unearned split, and recovering it properly means replaying
each inning as if the errors had not happened — the official reconstructed-
inning rule. RA9 runs a few tenths above ERA. If real ERA is ever wanted it has
to come from StatsAPI's `byDateRange` splits, which is one call per window per
group. Runs are also charged to whoever was on the mound when they crossed,
so a reliever wears an inherited runner here where the official book charges
the man who put him on.

Payload is ~1.0 MB raw for a full season, ~107 KB gzipped — Pages serves it
compressed, so the raw figure is not what anyone downloads. Lines have to stay
on every kept row rather than just the podium, because the team filter can
surface a club's best man from well down the overall board.

The page also refuses to crown a negative score at all. On a narrow filter —
one club, one role, a window two days old — the whole pool can be under water,
and the least-bad man is not a dawg. It renders "No dawg" with the closest
three instead.

The four pillars are still computed over the window and shown underneath,
using `score_pillar(..., raw=True)`. That flag skips the final z-score, which
otherwise re-standardizes a pillar to sd 1 no matter how little evidence built
it — so a single day would print bars as confident as a full season's. Raw,
they collapse to ~0.01 for a day and wake up to ~0.25 by a month, and the page
says why. The season leaderboard never asks for raw and is unchanged.

Payload size is the one thing to watch. A full ranked board for all 150 days
came to 2.7 MB; only the CURRENT window keeps every player, and past windows
keep each club's best plus the overall top five — enough for the team and
league filters to work all the way back, at 680 KB.

**Freshness.** `.github/workflows/nightly.yml` rebuilds and deploys at 11:17
UTC daily with `--topup`, which keeps the Statcast cache and pulls only the
days since its last game, re-pulling one overlap day so a build that ran while
games were in progress gets its partial final day repaired. It replaced
`--refresh`, which reached the same games by re-pulling the whole season --
forty minutes of Savant traffic every night to acquire one day.

The cron sits at :17 rather than :00 deliberately. GitHub runs scheduled
workflows on a best-effort queue and warns they may be delayed or dropped
under load, and the top of the hour is where nearly everybody points their
cron. If a nightly appears not to have run, check the Actions tab first: a run
that never fired and a run that failed look identical from the site, and only
one of them is a bug in this repo. The page also computes its own staleness from
`through` and prints a banner when the data is two or more days old — an award
called "of the Day" that is quietly a week old is worse than no award.

## Social — video clips (Phase 0)

The plan is channels that post the top five DAWGs of the Day and the MLB top
five DAWGs of the Week, each with video of the winner's biggest moment. Josh
is building the channels and the logos. **Josh's call on rights: assume
native reposting and build for it** — clips get downloaded and uploaded to
each platform rather than embedded from MLB. That is a deliberate accepted
risk taken with open eyes; MLB enforces its video rights vigorously and a
takedown or a channel strike is a live possibility, not a theoretical one.
It is written here so that if it happens, nobody has to reconstruct whether
it was a decision or an oversight. It was a decision.

`python -m xdawg clips --window day --top 5`, or **Actions tab → "Clips"**.
Nothing posts anywhere yet. The job downloads the clips and leaves them as a
run artifact with a table of what resolved at each hop.

The chain is four undocumented hops:

    date              -> statsapi /schedule             -> gamePk
    gamePk            -> statsapi /game/{pk}/feed/live  -> allPlays[]
    the right play    -> playEvents[].playId            -> a GUID
    the GUID          -> savant sporty-videos           -> an .mp4

**None of it is reachable from the dev sandbox.** The container's egress
proxy 403s both statsapi and Savant, which is why `clips.yml` exists and why
`xdawg/clips.py` is split hard down the middle: four thin urlopen wrappers on
top, every decision a pure function underneath, all of them tested from
fixtures in `tests/test_clips.py`. A bug in the join has to be reproducible
without a live game or it cannot be worked on here at all.

The join deserves a note. The tempting key is Statcast's `at_bat_number`
against StatsAPI's `atBatIndex` — but those are two systems numbering the
same list, and the offset between them is an assumption, not a fact. The
match is on what both sides observe identically instead: inning, half,
player id, and event type (Statcast's `events` and StatsAPI's
`result.eventType` share a vocabulary). `at_bat_number` only breaks a tie,
and even then it tries several offsets and *reports which one worked* rather
than trusting one. Once real runs confirm the offset is always 1, that can
be tightened.

`best` in the awards payload now carries `game_pk` and `at_bat_number`.
Boards built before this change do not have them, so `resolve` falls back to
finding the game from the date and the two club abbreviations — which is
wrong on a doubleheader, and is exactly why the fields were added. Clips for
old windows deserve more suspicion than clips for new ones.

Two things to watch in the first real runs: whether `playId` is present on
every play worth posting, and whether Savant serves a clip for every playId.
Partial success is the expected steady state — the CLI exits non-zero only
when *every* winner fails.

## Social — the card and the reel (Phase 1)

`python -m xdawg posts --window day --top 5`, or **Actions tab → "Posts"**.
Per winner it writes a 4:5 card, a 1080×1920 vertical reel, the raw clip,
and a caption. Nothing is uploaded — posting is a later phase, and a folder
a human glances at is the right amount of friction while this is young.

Three modules, one job each, and none of them knows about the others:
`xdawg/card.py` (award row → graphic), `xdawg/reel.py` (graphic + clip →
vertical video), `xdawg/posts.py` (orchestration and captions).

**The look is the site's, deliberately and in detail.** Same palette, same
type, same words. A channel whose posts look like the site is one thing
with two front doors; a channel with its own visual language is a second
brand nobody asked for, and the mismatch is the first thing a reader
notices when they click through. Concretely, from `site/awards.html`:

| `#E63600` primary | the score |
| `#00A69B` secondary | the process half, debits, the PIT tag |
| `#FFD300` accent | the HIT tag |
| `#000` ink | rules, the kind bar, body type |
| `#5a5a5a` / `#d5d5d5` | captions and meta / light rules |

No club colours. The site does not use them, and thirty accent schemes
against one leaderboard would read as thirty products.

The card also borrows the site's **words**, not just its colours: the
thirteen credit labels and their order, the "His biggest moment was a
double play in the 5th against BOS — worth +12.9 points of win probability
at 2.58x leverage" sentence, the "NYY · 24 trips across 1 game" meta line,
the signed-three-decimal number format. `tests/test_card.py` reads
`site/awards.html` and asserts they still agree — two lists of thirteen
phrases drift the moment somebody edits one of them, and that test is the
thing that notices. Where the awards payload already holds a value, such as
the window's label, the card uses it rather than re-deriving it.

**Fonts.** Gobold is the site's display face, is not redistributable, and is
not in this repo — the site currently falls back to Arial Narrow. Drop
`Gobold.ttf` / `Gobold-Bold.ttf` into `assets/fonts/` (or `site/assets/
fonts/`, where the site already looks) and the cards pick them up
automatically. Until then **Anton** stands in, which is the closest free
face in the same heavy-condensed genre. Body text is **Arimo**, which is
metrically Arial — what the site's `--body` actually resolves to. Both are
inlined as base64, so a runner without them renders the same card.

**The video window is a real hole.** The reel card is 1080×1920 RGBA with a
transparent rectangle; ffmpeg lays the clip *underneath* and flattens them,
so the card frames the clip and the clip is never composited over artwork.
Two things follow, both with tests that fail loudly rather than produce a
wrong-looking video:

- Nothing paints the `.card` background. The paper lives on the `.top` and
  `.bot` panels, because a background on `.card` fills the hole with white
  and the clip becomes invisible.
- The rectangle is **measured after layout**, not assumed. It moves whenever
  a block above it changes height. `render_card` returns it; `reel.compose`
  takes it. A hard-coded y here and a CSS edit there is a clip half behind
  the credit list, and nobody notices until they watch the file.

**The card fixes its own overflow.** Once the fonts have loaded the page
squeezes an over-long surname, then drops credit rows from the bottom until
the footer fits. If it still does not fit, `render_card` raises `Overflow`
rather than writing a PNG with a footer sliced in half — the card is
published, so a clipped one is not degraded, it is wrong. Tuning pixel
constants until today's five winners happen to fit is not a layout, it is a
coincidence; this holds for whoever wins tomorrow.

Gotchas already paid for:

- **Font-dependent fixups must wait for the fonts.** Run at parse time they
  measure a fallback face and squeeze names that fit. They now run inside
  `document.fonts.ready` and set `__cardReady`, which `render_html` waits
  on — a fixed sleep is a race that loses on a cold runner.
- **The video frame is `box-shadow`, not `border`.** A border is inside the
  box, so the measured rectangle would include it and the clip would cover
  the frame. `.video` also needs a `z-index` or the shadow paints behind
  its positioned siblings and the rules vanish.
- **The reel fades to white, and its canvas is white.** A dip to black at
  each end of a card printed on paper reads as a fault in the file.
- **Don't `str.title()` anything.** `card.proper_name` exists because
  `.title()` renders DeJong as Dejong; captions are built from fields
  rather than by re-casing the card's shouty version.

**The logo lives in `assets/logo/`,** in four cuts, and which one you reach
for is decided by the background:

| `mark.png` | badge only, dog knocked out (transparent) — for white |
| `mark-solid.png` | dog filled white, padded — safe on any ground |
| `lockup.png` | badge plus wordmark, transparent — for white |
| `lockup-white.png` | the same on an opaque white square — avatars, decks |

The card footer takes `mark.png` automatically; `--logo` only overrides it.
The badge alone, not a lockup: at 92px tall a stacked wordmark is
illegible, and `@XDAWGMLB` beside it already says the name. On the black
kind bar the knockout cut would read as a black dog on black — that is what
`mark-solid.png` is for. The drawn XDAWG wordmark in the CSS survives only
as the fallback for a checkout without the assets.

The logo's three colours are `#E63600`, `#00A69B` and `#FFD300` — the site's
`--primary`, `--secondary` and `--accent` exactly, which is a useful
confirmation that the palette is settled rather than a coincidence worth
relying on.

**The credit list is capped by measurement, not by a constant.** The card
asks for seven rows and the page drops whatever does not fit, which lands
on four for a post and five for a reel today and will land somewhere else
the day the moment sentence runs to three lines. Do not replace that with a
number.

Playwright is deliberately **not** in `requirements.txt`. The nightly build
has no use for a browser and installing Chromium there would add minutes to
a job that already takes forty. `posts.yml` installs it itself.

## Social — Bluesky (Phase 2)

`python -m xdawg publish --window day --top 5`, or **Actions tab →
"Publish to Bluesky"**. The account is
[@xdawgmlb.bsky.social](https://bsky.app/profile/xdawgmlb.bsky.social)
(`did:plc:kw3ukp37mdhf7iby2srs3vtu`).

**Posting is opt-in on every run.** Without `--live` (or the workflow's
`live` box) it is a dry run that prints exactly what would go out, built by
the same code path that posts — `bluesky.prepare` produces the same records
`publish_thread` sends, so a preview written by different code can never
disagree with the real thing.

**Shape: one thread.** Winner as the root, ranks 2–5 chained beneath as
replies, each carrying its own card. Josh's call. Five separate posts would
be five feed items from an account with no followers, and the newest-first
ordering would put the winner last.

Auth is an **app password** — Settings → App Passwords on bsky.app — held in
the repository secrets `BSKY_HANDLE` and `BSKY_APP_PASSWORD`. Never the
account password: an app password cannot change the account's email or
password, and revoking one disturbs nothing else. `bluesky.login` refuses
anything that is not in `xxxx-xxxx-xxxx-xxxx` shape, because an account
password would authenticate perfectly well and that is precisely the
problem. Credentials reach the process as environment variables, never on a
command line where they would sit in the process table; `_call` never echoes
a request body into an error, because on `createSession` that body is the
password.

Three things about this API bite, and all three have tests:

- **300 graphemes, and our captions already run to 290.** So a post is
  assembled from prioritised blocks — headline and name are required, the
  moment sentence is worth 5, the split line 3, the tags 1 — and the cheap
  ones are dropped until it fits. Truncation is the backstop, not the
  mechanism. The stat line is deliberately absent from the post text: it is
  on the card in bigger type, and restating it would cost the moment
  sentence.
- **Facets carry UTF-8 byte offsets, not character offsets.** The captions
  contain `·` (2 bytes) and `—` (3 bytes), so every tag sits at a byte index
  past its character index. Get it wrong and the tag highlights the wrong
  slice of the sentence — silently, since nothing raises. I got the
  arithmetic wrong in the *test* first; the round-trip assertion (slice the
  encoded text by the facet's own offsets and check it spells the tag) is
  what caught it, and is the one to keep.
- **Nothing is auto-linked.** `#MLB` with no facet is three inert
  characters.

Also worth knowing: `aspectRatio` on an image embed is not decoration —
without it the client guesses and crops a 4:5 card to a letterbox with the
footer cut off. Alt text is generated from the row and describes the card
top to bottom, because the stat line and the credit list are on the graphic
and nowhere in the post text.

**Staleness guard.** `--live` refuses a window whose last day is more than
`--max-age` (default 2) days old. An award called "of the Day" that goes out
four days late reads as current and nobody checks; the site prints a banner
for this, and a timeline has no banner. `--allow-stale` overrides, for
backfilling. Blob limit is 1,000,000 bytes and the budget here is 950,000 —
a card that squeaks in at 999,000 is a card that fails the day a name needs
one more glyph. Cards land at about 130 KB, so the JPEG ladder in
`fit_image` is a fallback that currently never fires.

**Video.** Built. A winner gets his reel when Savant has a clip of the
moment and the still card when it does not — never a six-second static
video, which is a picture that costs the reader a tap. A thread mixes the
two freely; the image replies still hang off the video root.

The path is nothing like the image one, and the differences are all
security-shaped:

- Video does not go to the PDS. It goes to `video.bsky.app`, which is a
  different party, so the session's own JWT is not accepted. Each call
  needs a **service auth** token from `com.atproto.server.getServiceAuth`,
  scoped to one audience and one method.
- For the *upload* the audience is **this account's PDS DID** and the lxm
  is **`com.atproto.repo.uploadBlob`**, not `uploadVideo` — what is being
  authorised is the video service writing a blob into the account's repo.
  For `getJobStatus` the audience is `did:web:video.bsky.app` instead.
- The PDS is read from the session's own DID document rather than
  hardcoded. This account currently lives on
  `poisonpie.us-west.host.bsky.network`; Bluesky spreads accounts across a
  fleet and an account can be migrated.
- Upload returns a **job, not a blob**. Transcoding is somebody else's
  queue, so `await_video` polls until `JOB_STATE_COMPLETED`, raises with
  the service's own words on failure, and times out rather than hanging the
  run behind an unbounded loop. A job that completes with no blob is an
  error, not a silent post.

**Video requires a verified account email.** That and the daily quota are
the two things that stop an upload, and both report through
`app.bsky.video.getUploadLimits` as `canUpload:false` with a sentence a
human can act on. `publish` asks *before* sending anything, so the failure
arrives in one line instead of after a 10 MB upload.

Limits: 100 MB and mp4 per `app.bsky.embed.video`; three minutes; a daily
video count the API will tell you rather than one worth hardcoding. Reels
run 0.3–2 MB for six to eleven seconds, so the size cap is not a
constraint we will meet. `alt` is capped at 1000 graphemes by the lexicon
and is truncated there. Captions are supported by the lexicon (up to 20
WebVTT tracks) and are not generated — there is no transcript of broadcast
audio to make one from.

A post carries images **or** a video, never both: `embed` is one field, and
sending both would drop one depending on dict ordering. `post_record`
raises rather than picking.

## Past seasons

`python -m xdawg history --seasons 2023 2024 2025 2026`, or **Actions tab →
"Build history"**. Everything downstream of `load_statcast` was already
parameterized by season, so this needed no new scoring code — only the
cross-year assembly in `xdawg/history.py` and a page to show it
(`site/history.html`, fed by `site/data/history.js`).

What it costs: **each uncached season is its own full Savant pull**, roughly
40 minutes. Three years is a two-hour job, four is three. The workflow keeps
one shared cache across every season under the `statcast-history-` key, so
adding a year later only pays for the year added. A hosted runner is capped
at six hours, which is why the input defaults to three seasons.

Two limits worth knowing before promising anything:

- **2015 is the floor.** Every pillar is built from pitch-level tracking
  fields and seasons before 2015 do not have them. The CLI refuses those
  outright rather than producing a board of nulls.
- **2020 is a trap.** Sixty games, no crowds, a runner on second in extras.
  It will compute, and its leverage index is derived from its own data so it
  is internally consistent, but setting it beside a 162-game season as though
  they were the same thing is not defensible.

Each season is scored **independently** and then compared. That is what makes
the comparison mean anything: DAWG+ is z-scored within its own season against
that season's empirically-derived leverage index, so 112 in 2023 and 112 in
2026 both mean "this far above his league, that year." Pooling the seasons
into one scoring pass would quietly let a high-offense year read as a league
full of dawgs.

The page reports the two numbers worth arguing about: the year-over-year
correlation of DAWG+ with itself (the stability test — the one raw clutch
metrics fail, and the bar this metric claims to clear), and the pooled
correlation of team DAWG against win% and against how far the club got in
October. Postseason results come from StatsAPI via `ingest.load_postseason`,
keyed off MLB's own game-type codes rather than dates, because the bracket has
been reshaped three times in a decade.

## Shipping a change

    ./scripts/ship.sh "what changed"

Stages everything, commits, rebases on origin, pushes. Use it rather than
hand-written git commands.

That is not a style preference. The hand-written version failed twice the
same way: the `git add` list was typed out fresh each round and both times
it was missing a path. Nobody noticed, because a commit that omits a
directory looks exactly like one that does not. `assets/` — the fonts and
the logo the cards are built from — sat untracked through two rounds, and
`.github/workflows/posts.yml` never reached the repo at all. CI would have
rendered cards in whatever sans the runner happened to have, with the CSS
wordmark instead of the mark, because `_font_faces()` and `default_logo()`
both skip files that are not there rather than failing loudly. `git add -A`
against a correct `.gitignore` cannot omit a path.

**There is no auto-push, and there never was.** Files written into the
working folder over the file bridge are *in the folder*; they are not in
the repo until somebody commits and pushes them. Two things stand in the
way of automating it, and both are real:

- The sandbox's git proxy refuses `apollojosh/xDAWG` — it is not in the
  session's authorised repository set, so no credential is injected and the
  push 403s.
- The device bridge runs in its own Linux VM with no GitHub credentials of
  its own. It can *read* the repo (public repos need no auth for that,
  which is why `git ls-remote` works and misleadingly suggests push might),
  but `git push` there dies on "could not read Username".

So: the assistant can stage and commit on the device, and often will. The
push is a human's.

The bridge can write into `.git/` but is not permitted to unlink, so every
git command it runs leaves an `index.lock` behind that will block the
*next* one. `ship.sh` clears those first, along with the `tmp_obj_*` litter
`git fetch` strews through `.git/objects/`.

## Before pushing anything

`python -m pytest tests/ -q` runs the whole scoring path offline against a
synthetic Statcast frame with the real feed's columns, dtypes and nulls
(`tests/test_pipeline_smoke.py`). Both smoke and build now run it *before*
the Savant pull, so a crash costs five seconds rather than forty-five
minutes. Every crash so far has been a TypeError or KeyError from missing
data, not a wrong number — which is exactly what this catches.

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

- **Pillar weights**: hitters 30/30/10/30 (BITE/GRIT/HUNT/FIGHT), pitchers
  30/25/25/20. Hitter HUNT is deliberately small — it rests on OAA, a season
  total that is only coarsely situational. Pitcher HUNT keeps its 25% because
  it's a different construction entirely, leverage-weighted per pitch. These
  are a values judgment, not an empirical finding — there's no ground-truth
  dawg dataset to fit against.
- **FIGHT playoff-team definition**: standings at time of game (specced,
  truer to the concept) vs. final standings (simpler, but hindsight).
- Positional adjustment for HUNT — a catcher can't make a 5-star catch.
- **Per-player weight renormalization in `aggregate.score_pillar`.** It
  divides by the total weight of every component present in the *frame*, not
  the weight present for that *player*. So someone missing four of six
  components has his survivors divided by the full denominator and collapses
  toward 0, while fully-covered players keep full magnitude — and `zscore`
  then stretches those few into a long tail. The docstring's promise that
  weights "are renormalized" holds frame-wide only, never per row.

  Measured on synthetic data at n=311: with every player fully covered the
  max is +3.4 SD and kurtosis 0.9; at 13% coverage it is +5.8 SD and
  kurtosis 12.2. The 2026 10-day smoke landed at +3.8 SD (hitters) and
  +5.0 SD (pitchers), which is the sparse-coverage regime — a full season
  should be far milder, since most players will clear most `min_n`
  thresholds. Decide after looking at a real full-season board: the fix is a
  values judgment, not just a bug fix, because scoring a sparse player on
  only what he has will move him up relative to a fully-measured one.

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
  whether you have a network route before debugging the code. It IS reachable
  from a GitHub runner — a full season pulled 592k pitches clean.
- FanGraphs 403s datacentre IPs, so `pybaseball.team_batting` fails on CI and
  FIGHT silently collapses to flat weights. Standings now come from MLB's own
  StatsAPI, which isn't IP-blocked; FanGraphs is only a local fallback.
- Statcast spells Arizona `AZ`; `config.TEAMS` says `ARI`. Those raw codes fed
  the FIGHT opponent lookup and the site's division filter, both of which
  return nothing on a miss — so Arizona was losing its opponent-quality weight
  with no error anywhere. `pipeline.run` now normalizes team codes once at
  load via `ingest.normalize_team`, and warns on any code it doesn't know.
- The catch-probability leaderboard is outfielders only and pre-bucketed by
  star difficulty, with no per-play rows and no total opportunity column. Its
  `n_opp_5stars` column will happily answer to a loose match for
  "opportunities" and inflate a rate sevenfold. Sum the buckets.
- `chase_contact` and both bounce-back terms were plain season means, not
  leverage-weighted — so surviving a 1-2 slider in a 2nd-inning blowout scored
  identically to the same pitch with two on in the 9th. `chase_contact` was
  22% of hitter BITE and the only BITE term measured on absolute level, which
  also leaked raw contact ability into a pillar built from deltas. All three
  are `weighted_delta` now. When adding a component, check it goes through
  `weighted_delta` unless it is deliberately absolute (all of GRIT is, by
  design — see the docstring).
- The all-position OAA leaderboard publishes **no opportunity count**. `n` is
  the shrinkage denominator (`z * n / (n + k)`), so leaving it at zero shrinks
  every HUNT z to exactly 0.0 — a silently dead pillar, not an error, and
  `verify_build` would hard-fail the run after the full 45-minute pull.
  `hunt()` now resolves opportunities in four tiers: a real column, summed
  star buckets, batted balls attributed from the pitch data (the normal path),
  then an estimate backed out of `diff_success_rate_formatted` (OAA ~ diff x
  chances). Last resort is a flat nominal 200 — deliberately not zero. The
  tier actually used is printed each run as `hunt: opportunities from ...`.
- Statcast arrives in pandas **nullable extension dtypes** — `plate_x` as
  Float64, `stand` as string — which carry `pd.NA`, not `NaN`. So
  `pd.to_numeric(...)` stays nullable, `px < -0.55` returns `pd.NA`,
  `np.where` propagates it into an object array, and `.astype(float)` dies
  with "float() argument must be a real number, not 'NAType'". Force to plain
  numpy before comparing: `.to_numpy(dtype="float64", na_value=np.nan)` and
  `.fillna(False).to_numpy(dtype=bool)`. `tests/test_pipeline_smoke.py`
  reproduces this offline in two seconds; it is why that file uses extension
  dtypes deliberately. The second form of the same problem is
  `TypeError: boolean value of NA is ambiguous`, raised when a nullable
  BooleanArray reaches `np.where` -- comparing two nullable columns produces
  one, which is how `xbt_frame` died on `on_1b == rid`.

  The fixture originally converted only the columns involved in whichever
  bug was being chased, and the very next nullable column to be touched
  escaped again and cost another build. It now converts EVERY column that
  can carry a null in the real feed (`_make_nullable`). When adding a
  column to `STATCAST_COLS`, add it there too.
- xDAWG is standardized by construction (`compute` z-scores the composite,
  then 100 + 25z), so mean and SD are ALWAYS exactly 100 and 25 no matter how
  broken the inputs are. Never read those two numbers as a health check —
  read the max, the min and the kurtosis instead. For n~300 a clean metric
  tops out near +2.9 SD, i.e. about 172; anything past ~190 means a tail that
  should not exist.
- `load_oaa` returns a SEASON TOTAL. In a short smoke it gets divided by only
  that window's attributed chances, inflating `oaa_rate` roughly 35x. Z-scoring
  cancels it so rankings are unaffected, but the raw component values in the
  site's breakdown panel are meaningless in a smoke run. Full builds line up.
- A deployed site can look un-deployed. `index.html` is a static file at a
  stable URL, so a browser holding a cached `data/data.js` keeps showing the
  previous build's numbers even when the file on the server is correct. Check
  `https://apollojosh.github.io/xDAWG/data/data.js` directly for the
  `synthetic` and `generated` fields before concluding a deploy failed — and
  note that fetching `index.html` proves nothing about the placeholder
  banner, which is rendered by JavaScript from data.js.
- **Pandas major versions differ across the three places this runs.** The
  dev container is on 3.0.2, the Mac on 2.3.3, and the CI runner takes
  whatever `pandas>=2.0` resolves to. They disagree on nullable handling:
  pandas 3 quietly turns pd.NA into nan on `.astype(float)` for a nullable
  boolean, pandas 2 is not guaranteed to. Anywhere that matters now converts
  explicitly via `.astype("Float64").to_numpy(dtype="float64", na_value=nan)`
  rather than relying on the version's default. A green test run in one
  environment is not proof for the others.
- **Four separate "the stats are broken" reports were all browser cache.**
  Every one: the numbers were correct on the server and the browser was
  showing a previous build. The fix that finally stuck is structural rather
  than "remember to hard-refresh":
    * `index.html` sends `no-cache` meta headers. It is the file carrying the
      `?v=` stamp that points at the current `data.js`, so a stale copy of it
      pins the browser to an old payload. `data.js` stays cacheable precisely
      because the stamp changes each build.
    * The build timestamp is in the MASTHEAD, not just the footer. Comparing
      it against the newest run in the Actions tab answers "am I looking at
      the current data" in one glance, with no tooling.
  When a number looks wrong, check that stamp before anything else.
- **A stale data.js looks exactly like broken math.** A page newer than its
  payload renders em-dashes in every column the old format lacks, plus an
  empty team table -- and `dawg` is the ONE key name shared by the old and
  new formats, so it stays populated while everything beside it is blank.
  That signature means "the build ran with only some of the new files", not
  "the stats stopped calculating". `site/index.html` now checks the payload
  for `dawg_plus`/`wdawg_plus`/`wdawg`/`team_table` on load and says so in
  the notice banner instead of showing dashes.
- Optional leaderboards degrade silently by design. That's right for a build
  but means a rename shows up as a quietly emptier pillar, so run
  `python -m xdawg probe` (or the Probe workflow) when something looks thin.

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
