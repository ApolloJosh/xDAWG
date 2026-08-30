"""The graphic that goes with a clip.

The look is the site's, deliberately and in detail. Same palette, same
type, same words. A channel whose posts look like the site is one thing
with two front doors; a channel with its own visual language is a second
brand nobody asked for, and the moment somebody clicks through from a post
to xdawg.github.io the mismatch is the first thing they notice.

Concretely, from site/awards.html:

    #E63600  primary     the score, the positive half of a meter
    #00A69B  secondary   the process half, debits, the PIT tag
    #FFD300  accent      the HIT tag
    #000     ink         rules, the kind bar, body type
    #5a5a5a  muted       captions and meta
    #d5d5d5  hair        the light rules inside a card
    #f4f4f2  alt         banded rows

No club colours. The site does not use them and a card that does would put
thirty accent schemes against one leaderboard.

The credit labels, the "his biggest moment was..." sentence and the meta
line are lifted from awards.html rather than reinvented, so a reader who
sees both never finds the same fact worded two ways. Where a value already
exists in the awards payload -- the window's label, for instance -- the
card uses it instead of re-deriving it.

Cards are authored in HTML and photographed by a headless Chromium rather
than drawn pixel by pixel with an imaging library. PIL would save the
browser dependency, but every layout change would become arithmetic, and
the look is going to change a hundred times before anybody likes it.

Two layouts share one template:

  post  1080x1350   a standalone graphic, the 4:5 that every feed treats best
  reel  1080x1920   the same card with a hole cut in it for video, which
                    ffmpeg fills from underneath (see xdawg.reel)

The hole is genuinely transparent, so the card can frame the clip and the
clip is never composited over artwork we care about.

Nothing here reaches the network. The headshot is passed in as bytes or not
at all, because the sandbox this is developed in cannot fetch mlbstatic and
a card that only renders on CI is a card nobody can iterate on.
"""

from __future__ import annotations

import base64
import html
import re
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "assets"
FONTS = ASSETS / "fonts"
LOGOS = ASSETS / "logo"
SITE_FONTS = Path(__file__).resolve().parents[1] / "site" / "assets" / "fonts"

POST = (1080, 1350)
REEL = (1080, 1920)

# The video window's height inside a reel. 16:9 at full bleed, which is what
# Savant serves. Its *position* is never assumed -- render_card measures it.
VIDEO_H = 608

# site/awards.html :root, verbatim.
PRIMARY = "#E63600"
SECONDARY = "#00A69B"
ACCENT = "#FFD300"
INK = "#000000"
MUTED = "#5a5a5a"
HAIR = "#d5d5d5"
ALT = "#f4f4f2"
PAPER = "#FFFFFF"
PAD = 44

WINDOW_LABEL = {"day": "DAWG OF THE DAY", "week": "DAWG OF THE WEEK",
                "month": "DAWG OF THE MONTH"}

# Plain-English names for the process credits, and the order they read in.
# Copied from CREDIT_LABELS in site/awards.html. If that list changes, this
# one has to change with it -- tests/test_card.py asserts they agree.
CREDIT_LABELS = {
    "survived_two_strikes": "reached after two strikes",
    "extra_pitch": "pitches past the 5th",
    "two_strike_foul": "two-strike foul-offs",
    "chase_contact": "chased and made contact",
    "hard_hit": "hard-hit balls",
    "hbp": "wore a pitch",
    "called_strike_three": "took strike three",
    "jam_escaped": "jams escaped",
    "zone_with_traffic": "strikes with men on",
    "inside_same_hand": "pitches inside, same hand",
    "first_pitch_strike": "first-pitch strikes",
    "putaway": "put-away strikeouts",
    "walk_allowed": "walks allowed",
}
DEBITS = {"called_strike_three", "walk_allowed"}

# COLS in site/awards.html. Josh's call: PA/HR/BB/OPS and IP/K/BB/ERA, with
# RA9 standing in for ERA because Statcast publishes no earned/unearned
# split. Labelling it ERA would claim a number we cannot compute.
STAT_COLS = {"hitter": ["PA", "HR", "BB", "OPS"],
             "pitcher": ["IP", "K", "BB", "RA9"]}

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def sgn(v, dp: int = 3) -> str:
    """The site's number format: always signed, fixed decimals."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{f:+.{dp}f}"


def display_name(name: str) -> str:
    """"Schlittler, Cam" and "Freddie Freeman" both become "CAM SCHLITTLER".

    Statcast gives last-comma-first, our own roster join gives first-last,
    and both end up in the same board. The card cannot show both spellings.

    Note the site does *not* do this -- its table prints "Schlittler, Cam"
    because a sortable column wants surname first. A poster wants the name
    the way a broadcaster would say it.
    """
    return re.sub(r"\s+", " ", proper_name(name)).strip().upper()


def proper_name(name: str) -> str:
    """The same flip, keeping the man's own capitals.

    Captions are prose, so they want "Cam Schlittler". They emphatically do
    not want str.title(), which renders DeJong as Dejong and Jr. as JR.
    """
    n = (name or "").strip()
    if "," in n:
        last, _, first = n.partition(",")
        n = f"{first.strip()} {last.strip()}"
    return re.sub(r"\s+", " ", n).strip()


def split_name(name: str) -> tuple[str, str]:
    """First name and the rest, so the surname can carry the visual weight."""
    parts = display_name(name).split(" ")
    if len(parts) < 2:
        return "", parts[0] if parts else ""
    return parts[0], " ".join(parts[1:])


def ordinal_suffix(n) -> str:
    """The site's rule, which is the simple one: 1st, 2nd, 3rd, else th."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n, 'th') }".replace(" ", "")


def window_date(window: str, key: str) -> str:
    """The window as a human would say it.

    Only a fallback. The awards payload carries `labels`, which is what the
    site prints, and `window_label` prefers it -- two places deriving the
    same date string is two places to disagree about a Sunday.
    """
    import datetime as dt

    key = (key or "").strip()
    try:
        if window == "month":
            y, m = key.split("-")[:2]
            return f"{MONTHS[int(m) - 1]} {y}"
        d = dt.date.fromisoformat(key)
        if window == "week":
            end = d + dt.timedelta(days=6)
            if end.month == d.month:
                return f"{MONTHS[d.month - 1]} {d.day}–{end.day}, {d.year}"
            return (f"{MONTHS[d.month - 1]} {d.day}–"
                    f"{MONTHS[end.month - 1]} {end.day}, {d.year}")
        return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"
    except (ValueError, IndexError):
        return key


def window_label(awards: dict | None, window: str, key: str) -> str:
    """What the site calls this window, straight from the payload."""
    lab = ((awards or {}).get("labels", {}) or {}).get(window, {}) or {}
    return lab.get(key) or window_date(window, key)


def why_line(row: dict) -> str:
    """"His biggest moment was a double play in the 5th against BOS — ..."

    whyLine() from site/awards.html, word for word. A pitcher does not take
    a swing; he induces the outcome.
    """
    b = row.get("best") or {}
    role = row.get("role", "hitter")
    noun = "His biggest moment" if role == "pitcher" else "His biggest swing"
    ev = str(b.get("event") or "").strip()
    bits = [f"{noun} was a {ev}"] if ev else [f"{noun} came"]
    if b.get("inning"):
        bits.append("in the " + ordinal_suffix(b["inning"]))
    if b.get("opp"):
        bits.append("against " + str(b["opp"]))
    s = " ".join(bits)
    if b.get("wpa") is not None:
        s += f" — worth {sgn(b['wpa'] * 100, 1)} points of win probability"
        if b.get("li"):
            s += f" at {float(b['li']):.2f}x leverage"
    return s + "."


def meta_line(row: dict) -> str:
    """"NYY · 24 trips across 1 game · +0.329 win probability added"."""
    n = int(row.get("n", 0) or 0)
    g = int(row.get("games", 0) or 0)
    bits = [str(row.get("team", ""))]
    if n:
        bits.append(f"{n} trip{'' if n == 1 else 's'} across "
                    f"{g} game{'' if g == 1 else 's'}")
    if row.get("wpa") is not None:
        bits.append(f"{sgn(row['wpa'])} win probability added")
    return " · ".join(b for b in bits if b)


def stat_cells(line: dict | None, role: str) -> list[tuple[str, str]]:
    """The four numbers, formatted exactly as statLine() formats them."""
    out = []
    line = line or {}
    for key in STAT_COLS.get(role, []):
        v = line.get(key)
        if v is None:
            out.append((key, "—"))
        elif key == "OPS":
            out.append((key, f"{float(v):.3f}".lstrip("0") or "0"))
        elif key == "RA9":
            out.append((key, f"{float(v):.2f}"))
        elif key == "IP":
            # Already box-score notation: 5.2 is five and two thirds. Running
            # it through a decimal formatter mangles the first one that needs
            # rounding.
            out.append((key, str(v)))
        else:
            out.append((key, str(int(v))))
    return out


def credit_rows(credits: dict | None, limit: int | None = None
                ) -> list[tuple[str, str, bool]]:
    """"How he earned it", in the site's order and the site's words."""
    c = credits or {}
    rows = []
    for key, label in CREDIT_LABELS.items():
        n = c.get(key)
        if not n:
            continue
        debit = key in DEBITS
        rows.append((label, f"{'−' if debit else ''}{abs(int(n))}", debit))
        if limit and len(rows) >= limit:
            break
    return rows


def _font_faces() -> str:
    """Every font inlined, so a runner without them renders the same card.

    Gobold is the site's display face and is not redistributable, so it is
    not in this repo. Drop the files into assets/fonts/ (or site/assets/
    fonts/, where the site already looks for them) and the cards pick them
    up; until then Anton stands in, which is the closest free face in the
    same heavy-condensed genre. Arimo is metrically Arial, which is what the
    site's body text actually resolves to.
    """
    faces: list[tuple[str, Path, int]] = []
    for stem, weight in (("Gobold", 400), ("Gobold-Bold", 700)):
        for root in (FONTS, SITE_FONTS):
            p = root / f"{stem}.ttf"
            if p.exists():
                faces.append(("XD Display", p, weight))
                break
    if not faces:
        faces.append(("XD Display", FONTS / "Anton-Regular.ttf", 400))
        faces.append(("XD Display", FONTS / "Anton-Regular.ttf", 700))
    faces.append(("XD Text", FONTS / "Arimo-Variable.ttf", 400))

    css = []
    for family, path, weight in faces:
        if not path.exists():
            continue
        b64 = base64.b64encode(path.read_bytes()).decode()
        rng = "100 900" if path.name.endswith("Variable.ttf") else str(weight)
        css.append(f"@font-face{{font-family:'{family}';font-weight:{rng};"
                   f"font-style:normal;font-display:block;"
                   f"src:url(data:font/ttf;base64,{b64}) format('truetype')}}")
    return "\n".join(css)


def default_logo() -> bytes | None:
    """The mark, for the footer.

    `mark.png` is the knockout cut: the dog is transparent rather than
    white, so it reads as the paper it sits on. That is right for the card
    footer and wrong on anything dark -- `mark-solid.png` is the version
    with the dog filled in, for a black bar or a coloured ground. The
    lockups carry the wordmark too and are for places with room to read it,
    which a 74px-tall footer is not.
    """
    p = LOGOS / "mark.png"
    return p.read_bytes() if p.exists() else None


def _data_uri(img: bytes | None) -> str:
    if not img:
        return ""
    kind = "png" if img[:8] == b"\x89PNG\r\n\x1a\n" else "jpeg"
    return f"data:image/{kind};base64,{base64.b64encode(img).decode()}"


# --------------------------------------------------------------------------
# the template
# --------------------------------------------------------------------------

def card_html(row: dict, *, window: str = "day", key: str = "",
              layout: str = "post", headshot: bytes | None = None,
              handle: str = "@XDAWGMLB", logo: bytes | None = None,
              awards: dict | None = None) -> str:
    """One card's markup. Pure -- no network, no files beyond the fonts."""
    e = html.escape
    reel = layout == "reel"
    w, h = REEL if reel else POST

    first, last = split_name(row.get("name", ""))
    rank = int(row.get("rank", 1) or 1)
    role = row.get("role", "hitter")
    hitter = role != "pitcher"
    cells = stat_cells(row.get("line"), role)
    # Ask for more than will fit and let the page drop what does not. A
    # hard cap here would be a guess at how much room a two-line moment
    # sentence leaves; the page can simply measure.
    credits = credit_rows(row.get("credits"), 7)

    label = WINDOW_LABEL.get(window, "DAWG")
    crown = label if rank == 1 else f"NO. {rank} {label.replace('DAWG OF THE', 'ON THE')}"
    when = window_label(awards, window, key)

    shot = _data_uri(headshot)
    mark = _data_uri(logo if logo is not None else default_logo())

    cells_html = "".join(
        f'<div><div class="k">{e(k)}</div><div class="v">{e(v)}</div></div>'
        for k, v in cells)
    def _credit(lbl: str, n: str, debit: bool) -> str:
        open_tag = '<div class="debit">' if debit else "<div>"
        return f"{open_tag}<span>{e(lbl)}</span><span>{e(n)}</span></div>"

    credits_html = "".join(_credit(*c) for c in credits)

    return f"""<!doctype html><meta charset="utf-8"><style>
{_font_faces()}
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:{w}px;height:{h}px;background:transparent}}
body{{font-family:'XD Text',Arial,Helvetica,sans-serif;color:{INK};
  -webkit-font-smoothing:antialiased}}

/* The card paints nothing itself. In a reel the video window has to be a
   real hole in the PNG -- ffmpeg lays the clip underneath it -- and a
   background here would fill that hole with white. The paper lives on the
   panels instead, and the gap between them is genuinely transparent. */
.card{{position:relative;width:{w}px;height:{h}px;display:flex;
  flex-direction:column;background:transparent;overflow:hidden}}
.panel{{background:{PAPER};position:relative;display:flex;flex-direction:column;
  border-left:6px solid {INK};border-right:6px solid {INK}}}
.top{{border-top:6px solid {INK};flex:0 0 auto}}
.bot{{border-bottom:6px solid {INK};flex:1 1 auto;justify-content:flex-start}}

/* .kind -- the black bar, straight off the site's award card. */
.kind{{background:{INK};color:{PAPER};display:flex;justify-content:space-between;
  align-items:baseline;gap:16px;padding:14px {PAD}px 11px;flex:0 0 auto}}
.kind b{{font-family:'XD Display';font-weight:400;font-size:{50 if reel else 54}px;
  letter-spacing:.06em;line-height:1;text-transform:uppercase}}
.kind em{{font-style:normal;font-size:26px;color:#cfcfcb;letter-spacing:.02em;
  white-space:nowrap}}

.who{{padding:{24 if reel else 30}px {PAD}px 0;flex:0 0 auto;position:relative}}
.first{{font-family:'XD Display';font-size:{44 if reel else 48}px;line-height:1;
  letter-spacing:.05em;color:{MUTED};text-transform:uppercase}}
.last{{font-family:'XD Display';font-size:{106 if reel else 118}px;line-height:.94;
  letter-spacing:.015em;text-transform:uppercase;margin-top:4px;
  transform-origin:left center;white-space:nowrap}}
.tag{{display:inline-block;vertical-align:middle;margin-left:18px;
  font-family:'XD Text';font-size:24px;font-weight:700;letter-spacing:.06em;
  text-transform:uppercase;padding:4px 12px;border:2px solid {INK};
  background:{ACCENT if hitter else SECONDARY};color:{INK if hitter else PAPER}}}
.meta{{margin-top:12px;font-size:27px;color:{MUTED};line-height:1.3}}

/* The photo bleeds off the right of the name block and fades before it can
   reach the type. Absent on a sandbox that cannot reach mlbstatic, and the
   layout is built so its absence costs nothing. */
.shot{{position:absolute;right:0;top:{-10 if reel else -14}px;width:{300 if reel else 330}px;
  z-index:0;object-fit:contain;object-position:top right;
  -webkit-mask-image:linear-gradient(180deg,#000 62%,transparent 97%),
                     linear-gradient(90deg,transparent 0%,#000 30%);
  -webkit-mask-composite:source-in;mask-composite:intersect}}
.who>div{{position:relative;z-index:1}}

.score{{font-family:'XD Display';font-size:{136 if reel else 148}px;line-height:.9;
  color:{PRIMARY};padding:{14 if reel else 20}px {PAD}px 0;flex:0 0 auto}}
.scorelab{{font-size:22px;text-transform:uppercase;letter-spacing:.04em;
  color:{MUTED};padding:7px {PAD}px 0;line-height:1.28;flex:0 0 auto}}

/* .split -- two bordered cells, as on the site. Win probability and
   process, named the way the site names them, because the whole claim of
   the metric is that the second column is not decoration. */
.split{{display:flex;margin:{22 if reel else 28}px {PAD}px 0;font-size:25px;
  text-transform:uppercase;letter-spacing:.05em;color:{MUTED};flex:0 0 auto}}
.split span{{flex:1;text-align:center;padding:11px 0 9px;border:2px solid {HAIR}}}
.split span+span{{border-left:0}}
.split b{{font-weight:700;color:{INK}}}

.statline{{display:flex;border-top:2px solid {HAIR};border-bottom:2px solid {HAIR};
  margin:{22 if reel else 28}px {PAD}px 0;flex:0 0 auto}}
.statline>div{{flex:1;padding:16px 4px 14px;text-align:center}}
.statline>div+div{{border-left:2px solid {HAIR}}}
.statline .k{{font-size:21px;text-transform:uppercase;letter-spacing:.09em;color:{MUTED}}}
.statline .v{{font-family:'XD Display';font-size:{56 if reel else 62}px;line-height:1.05;
  margin-top:5px}}

.credits{{margin:{20 if reel else 24}px {PAD}px {18 if reel else 22}px;
  font-size:26px;flex:0 0 auto}}
.credits>b{{display:block;font-family:'XD Display';font-weight:400;font-size:27px;
  letter-spacing:.05em;text-transform:uppercase;color:{MUTED};margin-bottom:7px}}
.credits>div{{display:flex;justify-content:space-between;gap:24px;
  border-bottom:2px dotted {HAIR};padding:5px 0 4px}}
.credits>div.debit{{color:{SECONDARY}}}

.why{{margin:{20 if reel else 24}px {PAD}px 0;padding-top:{16 if reel else 20}px;
  border-top:2px solid {HAIR};font-size:28px;line-height:1.32;flex:0 0 auto}}

/* The hole. box-shadow rather than a border: a border is inside the box, so
   the measured rectangle would include it and the clip would be laid over
   the frame we just drew. z-index so the rules paint above the panels. */
.video{{position:relative;z-index:3;width:{w}px;height:{VIDEO_H}px;flex:0 0 auto;
  background:transparent;box-shadow:0 -6px 0 {INK},0 6px 0 {INK}}}

footer{{margin-top:auto;padding:{22 if reel else 26}px {PAD}px {22 if reel else 26}px;
  display:flex;align-items:center;gap:20px;border-top:6px solid {INK};flex:0 0 auto}}
.wordmark{{font-family:'XD Display';font-size:46px;letter-spacing:.02em;
  text-transform:uppercase;line-height:1}}
.wordmark i{{font-style:normal;color:{PRIMARY}}}
.logo{{height:{78 if reel else 92}px;width:auto}}
.handle{{font-size:26px;color:{MUTED};letter-spacing:.03em}}
.slogan{{margin-left:auto;font-size:24px;color:{MUTED};text-align:right;
  text-transform:uppercase;letter-spacing:.05em}}
</style>
<div class="card">
  <div class="panel top">
    <div class="kind"><b>{e(crown)}</b><em>{e(when)}</em></div>
    <div class="who">
      {f'<img class="shot" src="{shot}">' if shot else ''}
      <div class="first">{e(first)}</div>
      <div class="last" id="last">{e(last)}<span class="tag"
        >{'HIT' if hitter else 'PIT'}</span></div>
      <div class="meta">{e(meta_line(row))}</div>
    </div>
    <div class="score">{sgn(row.get('score', 0), 1)}</div>
    <div class="scorelab">DAWG points &mdash; win probability plus process,
      both weighted by leverage and opponent</div>
    <div class="split">
      <span><b>{sgn(row.get('wpa_pts', 0), 1)}</b> win probability</span>
      <span><b>{sgn(row.get('proc_pts', 0), 1)}</b> process</span>
    </div>
    <div class="statline">{cells_html}</div>
    {'' if reel else f'<div class="why">{e(why_line(row))}</div>'}
    <div style="height:{16 if reel else 12}px"></div>
  </div>

  {f'<div class="video"></div>' if reel else ''}

  <div class="panel bot">
    {f'<div class="why">{e(why_line(row))}</div>' if reel else ''}
    {f'<div class="credits"><b>How he earned it</b>{credits_html}</div>'
     if credits_html else ''}
    <footer>
      {f'<img class="logo" src="{mark}">'
       if mark else '<div class="wordmark"><i>X</i>DAWG</div>'}
      <div class="handle">{e(handle)}</div>
      <div class="slogan">Does he have that<br>dawg in him</div>
    </footer>
  </div>
</div>
<script>
// The surname squeeze measures text, so it has to wait for the real font.
// Run at parse time it measures a fallback face and shrinks names that fit
// perfectly well. render_html waits on __cardReady rather than sleeping,
// because a fixed sleep is a race that loses on a cold runner.
window.__cardReady = false;
document.fonts.ready.then(function () {{
  // Squeeze an over-long surname rather than let it wrap. CABRERA fits;
  // HERNANDEZ-PERALTA does not, and the layout below him is not negotiable
  // -- in a reel the video window has to land on an exact pixel.
  var el = document.getElementById('last');
  if (el) {{
    var room = {w} - {PAD * 2} - 24;
    if (el.scrollWidth > room) {{
      el.style.transform = 'scaleX(' + (room / el.scrollWidth) + ')';
    }}
  }}
  // If the card is still too tall -- a long name that wrapped the meta
  // line, a stat that ran wide -- give the space back by dropping the
  // quietest credits rather than by slicing the footer in half. Tuning
  // pixel constants until today's five winners happen to fit is not a
  // layout, it is a coincidence; this holds for whoever wins tomorrow.
  var card = document.querySelector('.card');
  var foot = document.querySelector('.bot footer');
  var rows = document.querySelectorAll('.credits > div');
  var i = rows.length;
  while (foot && i > 2 &&
         foot.getBoundingClientRect().bottom > card.clientHeight) {{
    rows[--i].remove();
  }}
  window.__cardReady = true;
}});
</script>"""


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

class Overflow(RuntimeError):
    """The card's content did not fit the canvas.

    Raised rather than warned because the card is published: a clipped
    footer is not a degraded card, it is a wrong one, and the only place to
    catch it is before the file exists.
    """


def render_html(markup: str, out: str | Path,
                size: tuple[int, int]) -> tuple[Path, tuple[int, int, int, int] | None]:
    """Photograph the markup, and report where the video window landed.

    The window is *measured*, not assumed. It sits in normal flow, so its y
    moves whenever a block above it changes height -- a two-line meta, a
    longer club name, a headshot that loaded. Hard-coding the rectangle here
    and editing the CSS there is how a clip ends up half behind the credit
    list, and the failure is invisible until someone watches the video.
    """
    from playwright.sync_api import sync_playwright

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--force-color-profile=srgb",
                                     "--font-render-hinting=none"])
        page = b.new_page(viewport={"width": w, "height": h},
                          device_scale_factor=1)
        page.set_content(markup, wait_until="load")
        page.wait_for_function("() => window.__cardReady === true", timeout=15000)
        box = page.evaluate("""() => {
            const el = document.querySelector('.video');
            if (!el) return null;
            const r = el.getBoundingClientRect();
            return [Math.round(r.x), Math.round(r.y),
                    Math.round(r.width), Math.round(r.height)];
        }""")
        # How far past the bottom edge the content ran. The card has
        # overflow:hidden, so an overrun is not an error at render time --
        # it is a footer quietly sliced in half in a published post. Ask
        # once, here, where it is cheap to notice.
        over = page.evaluate("""(h) => {
            const last = document.querySelector('.bot footer');
            if (!last) return 0;
            return Math.max(0, Math.round(last.getBoundingClientRect().bottom - h));
        }""", h)
        page.screenshot(path=str(out), omit_background=True, type="png")
        b.close()
    if over:
        raise Overflow(f"{out.name}: content runs {over}px past the bottom "
                       f"edge; the footer would be cut off")
    return out, (tuple(box) if box else None)


def render_card(row: dict, out: str | Path, *, window: str = "day",
                key: str = "", layout: str = "post",
                headshot: bytes | None = None, logo: bytes | None = None,
                handle: str = "@XDAWGMLB", awards: dict | None = None
                ) -> tuple[Path, tuple[int, int, int, int] | None]:
    """Render one card. Returns its path and the video window, if any."""
    markup = card_html(row, window=window, key=key, layout=layout,
                       headshot=headshot, logo=logo, handle=handle,
                       awards=awards)
    return render_html(markup, out, REEL if layout == "reel" else POST)


def headshot_url(player_id: int, width: int = 426) -> str:
    """MLB's own headshot for a player. Unreachable from the dev sandbox."""
    return (f"https://img.mlbstatic.com/mlb-photos/image/upload/"
            f"w_{width},q_auto:best/v1/people/{int(player_id)}/headshot/67/current")


def fetch_headshot(player_id: int) -> bytes | None:
    """Best effort. A card without a face is a card; a crash is not."""
    from .clips import _get
    try:
        return _get(headshot_url(player_id), timeout=20, retries=2)
    except Exception:
        return None
