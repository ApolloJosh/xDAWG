"""One award window in, a folder of ready-to-upload posts out.

This is the orchestration layer and nothing else. The three pieces it
strings together each own their own problem and none of them knows about
the others:

    xdawg.clips   an award row -> the MLB video of that moment
    xdawg.card    an award row -> the graphic
    xdawg.reel    graphic + clip -> the vertical video

Per winner it emits three files and a caption: a 4:5 still for the feed, a
1080x1920 reel, and the raw clip in case anything has to be recut by hand.
Nothing is uploaded anywhere -- posting is a later phase, and a human
approving a folder is the right amount of friction while the pipeline is
young.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import card as card_mod
from . import clips as clips_mod
from . import reel as reel_mod

# For captions. Abbreviations mean nothing to a reader scrolling past.
TEAM_NAMES = {
    "ARI": "Diamondbacks", "ATL": "Braves", "BAL": "Orioles", "BOS": "RedSox",
    "CHC": "Cubs", "CWS": "WhiteSox", "CIN": "Reds", "CLE": "Guardians",
    "COL": "Rockies", "DET": "Tigers", "HOU": "Astros", "KC": "Royals",
    "LAA": "Angels", "LAD": "Dodgers", "MIA": "Marlins", "MIL": "Brewers",
    "MIN": "Twins", "NYM": "Mets", "NYY": "Yankees", "ATH": "Athletics",
    "PHI": "Phillies", "PIT": "Pirates", "SD": "Padres", "SF": "Giants",
    "SEA": "Mariners", "STL": "Cardinals", "TB": "Rays", "TEX": "Rangers",
    "TOR": "BlueJays", "WSH": "Nationals",
}


def slug(*parts: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", " ".join(str(p) for p in parts))
    return s.strip("-").lower()


def caption(row: dict, window: str, key: str,
            awards: dict | None = None) -> str:
    """What goes in the post body.

    Built from the same helpers the card uses, and therefore worded the same
    way the site words it. The alternative -- a caption that phrases the
    moment its own way -- means the same play described three ways across
    the site, the graphic and the post, and a reader who notices that trusts
    all three slightly less.

    It says both halves of the score out loud on purpose. The whole claim of
    the metric is that it is not WPA with a haircut, and a caption printing
    only the total invites exactly the reading we are arguing against.
    """
    name = card_mod.proper_name(row.get("name", ""))
    team = str(row.get("team", "")).upper()
    club = TEAM_NAMES.get(team, team)
    rank = int(row.get("rank", 1) or 1)
    role = row.get("role", "hitter")
    label = card_mod.WINDOW_LABEL.get(window, "DAWG")
    head = label if rank == 1 else f"No. {rank} — {label}"
    when = card_mod.window_label(awards, window, key)

    stats = ", ".join(f"{v} {k}" for k, v in
                      card_mod.stat_cells(row.get("line"), role))

    bits = [f"{head} — {when}", "",
            f"{name} ({team}) — {card_mod.sgn(row.get('score', 0), 1)} "
            f"DAWG points."]
    if row.get("best"):
        bits.append(card_mod.why_line(row))
    if stats:
        bits.append(stats)
    bits += ["",
             f"{card_mod.sgn(row.get('wpa_pts', 0), 1)} win probability · "
             f"{card_mod.sgn(row.get('proc_pts', 0), 1)} process",
             "", f"#MLB #{club} #xDAWG"]
    return "\n".join(bits)


def alt_text(row: dict, window: str, key: str,
             awards: dict | None = None) -> str:
    """What the card says, for somebody who cannot see it.

    Not a caption -- a description. It reads the graphic top to bottom in
    the order a sighted reader takes it, because that is what alt text is
    for, and because the stat line and the credits are on the card and
    nowhere in the post text.
    """
    role = row.get("role", "hitter")
    stats = ", ".join(f"{v} {k}" for k, v in
                      card_mod.stat_cells(row.get("line"), role))
    credits = "; ".join(f"{n} {lbl}" for lbl, n, _ in
                        card_mod.credit_rows(row.get("credits"), 5))
    bits = [
        f"xDAWG card. {card_mod.WINDOW_LABEL.get(window, 'DAWG')}, "
        f"{card_mod.window_label(awards, window, key)}.",
        f"{card_mod.proper_name(row.get('name', ''))}, "
        f"{row.get('team', '')} {role}, "
        f"{card_mod.sgn(row.get('score', 0), 1)} DAWG points "
        f"({card_mod.sgn(row.get('wpa_pts', 0), 1)} win probability, "
        f"{card_mod.sgn(row.get('proc_pts', 0), 1)} process).",
    ]
    if stats:
        bits.append(stats + ".")
    if row.get("best"):
        bits.append(card_mod.why_line(row))
    if credits:
        bits.append("How he earned it: " + credits + ".")
    return " ".join(bits)


def bluesky_text(row: dict, window: str, key: str,
                 awards: dict | None = None) -> str:
    """The post body, inside Bluesky's 300.

    The full caption runs to 290 without trying and a long surname puts it
    over, so this is assembled from prioritised blocks and the cheap ones
    are dropped -- see bluesky.fit_text. Priorities, highest first: who and
    what he scored; what he did; the two halves of the score; the tags.

    The stat line is deliberately not here. It is on the card, in bigger
    type than a post body could give it, and spending forty of three
    hundred characters restating it costs the moment sentence.
    """
    from .bluesky import fit_text

    name = card_mod.proper_name(row.get("name", ""))
    team = str(row.get("team", "")).upper()
    club = TEAM_NAMES.get(team, team)
    rank = int(row.get("rank", 1) or 1)
    label = card_mod.WINDOW_LABEL.get(window, "DAWG")
    when = card_mod.window_label(awards, window, key)
    score = card_mod.sgn(row.get("score", 0), 1)

    if rank == 1:
        head = f"{label} — {when}"
        who = f"{name} ({team}) — {score} DAWG points."
    else:
        # A reply carries its own rank and its own window, because a reader
        # arriving from a quote or a notification never saw the root. The
        # phrasing is the card's own crown line -- not label.title(), which
        # renders "Dawg Of The Day".
        head = f"No. {rank} on the {window} — {when}"
        who = f"{name} ({team}) — {score}."

    blocks = [
        (9, head),                                     # required
        (9, who),                                      # required
        (5, card_mod.why_line(row) if row.get("best") else ""),
        (3, f"{card_mod.sgn(row.get('wpa_pts', 0), 1)} win probability · "
            f"{card_mod.sgn(row.get('proc_pts', 0), 1)} process"),
        (1, f"#MLB #{club} #xDAWG"),
    ]
    return fit_text([b for b in blocks if b[1]])


@dataclass
class Post:
    rank: int = 0
    name: str = ""
    team: str = ""
    role: str = ""
    score: float = 0.0
    caption: str = ""
    card: str = ""
    reel: str = ""
    clip: str = ""
    has_video: bool = False
    headshot: bool = False
    error: str = ""
    notes: list = field(default_factory=list)


def build_posts(awards_path: str | Path, *, window: str = "day",
                key: str | None = None, top: int = 5,
                out_dir: str | Path = "posts", handle: str = "@XDAWGMLB",
                logo: bytes | None = None,
                fetch_video: bool = True) -> list[Post]:
    awards = clips_mod.load_awards(awards_path)
    rows = clips_mod.winners(awards, window, key, top)
    key = key or awards.get("latest", {}).get(window) or ""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    posts: list[Post] = []
    for row in rows:
        rank = int(row.get("rank", 1) or 1)
        base = slug(f"{rank:02d}", key, row.get("team", ""),
                    card_mod.display_name(row.get("name", "")))
        p = Post(rank=rank, name=card_mod.proper_name(row.get("name", "")),
                 team=row.get("team", ""), role=row.get("role", ""),
                 score=float(row.get("score", 0) or 0),
                 caption=caption(row, window, key, awards))
        try:
            shot = card_mod.fetch_headshot(row["id"]) if row.get("id") else None
            p.headshot = bool(shot)

            still_png, _ = card_mod.render_card(
                row, out / f"{base}.png", window=window, key=key,
                layout="post", headshot=shot, logo=logo, handle=handle,
                awards=awards)
            p.card = str(still_png)

            reel_png, box = card_mod.render_card(
                row, out / f"{base}-reel-card.png", window=window, key=key,
                layout="reel", headshot=shot, logo=logo, handle=handle,
                awards=awards)

            # --no-video means no network at all, not "walk the chain and
            # then throw the answer away". It is the mode you want when the
            # only question is what the cards look like.
            res = (clips_mod.resolve(row, out_dir=out) if fetch_video
                   else clips_mod.Resolution(error="video not requested"))
            p.notes = list(res.notes)
            if res.ok:
                p.clip = res.path
                p.reel = str(reel_mod.compose(reel_png, box, [res.path],
                                              out / f"{base}.mp4"))
                p.has_video = True
            else:
                # No clip is not a failure. A still card, held for six
                # seconds, is a post; a missing post is not.
                p.error = res.error
                p.reel = str(reel_mod.still(reel_png, out / f"{base}.mp4"))
        except Exception as e:  # noqa: BLE001 -- one bad winner, not the night
            p.error = f"{type(e).__name__}: {e}"
        posts.append(p)

    (out / "posts.json").write_text(
        json.dumps({"window": window, "key": key,
                    "posts": [asdict(x) for x in posts]}, indent=2) + "\n")
    for p in posts:
        if p.card:
            Path(p.card).with_suffix(".txt").write_text(p.caption + "\n")
    return posts


def report(posts: list[Post]) -> str:
    head = ("| # | Player | Team | Score | Card | Video | Note |\n"
            "|---|--------|------|-------|------|-------|------|")
    lines = [head]
    for p in posts:
        lines.append(
            f"| {p.rank} | {p.name} | {p.team} | {p.score:.0f} | "
            f"{'yes' if p.card else '**no**'} | "
            f"{'clip' if p.has_video else 'still'} | {p.error or ''} |")
    vids = sum(1 for p in posts if p.has_video)
    n = len(posts)
    lines += ["", f"**{n} post{'' if n == 1 else 's'} built, "
                  f"{vids} with video.**"]
    if any(not p.headshot for p in posts):
        missing = ", ".join(p.name for p in posts if not p.headshot)
        lines.append(f"No headshot for: {missing}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Bluesky
# --------------------------------------------------------------------------

def thread_items(awards_path: str | Path, *, window: str = "day",
                 key: str | None = None, top: int = 5,
                 out_dir: str | Path = "posts", handle: str = "@XDAWGMLB",
                 logo: bytes | None = None,
                 video: bool = True) -> tuple[list, str]:
    """The window's winners as a thread: winner first, runners-up beneath.

    Each man gets a reel if Savant has video of his moment, and the still
    card if it does not. Never a six-second static video: a card that does
    not move is a picture, and posting it as a video buys nothing and costs
    the reader a tap.

    `video=False` skips the clip chain entirely -- no schedule lookups, no
    feeds, no downloads. That is the mode for looking at wording.
    """
    from .bluesky import Item

    awards = clips_mod.load_awards(awards_path)
    rows = clips_mod.winners(awards, window, key, top)
    key = key or awards.get("latest", {}).get(window) or ""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    items = []
    for row in rows:
        rank = int(row.get("rank", 1) or 1)
        base = slug(f"{rank:02d}", key, row.get("team", ""),
                    card_mod.display_name(row.get("name", "")))
        shot = card_mod.fetch_headshot(row["id"]) if row.get("id") else None
        text = bluesky_text(row, window, key, awards)
        alt = alt_text(row, window, key, awards)

        still_png, _ = card_mod.render_card(
            row, out / f"{base}.png", window=window, key=key, layout="post",
            headshot=shot, logo=logo, handle=handle, awards=awards)

        # Why this man ends up a card rather than a reel, in words. The
        # first version of this swallowed every failure into a bare except
        # and posted stills, which is indistinguishable from being asked
        # for stills -- and left nobody able to say which had happened.
        note, clip = "", None
        if not video:
            note = "video not requested"
        elif not reel_mod.have_ffmpeg():
            note = "ffmpeg is not installed on this machine"
        else:
            res = clips_mod.resolve(row, out_dir=out)
            if res.ok:
                clip = res.path
            else:
                note = res.error or "no clip resolved"

        if clip:
            reel_png, box = card_mod.render_card(
                row, out / f"{base}-reel-card.png", window=window, key=key,
                layout="reel", headshot=shot, logo=logo, handle=handle,
                awards=awards)
            try:
                mp4 = reel_mod.compose(reel_png, box, [clip],
                                       out / f"{base}.mp4")
                # The card rides along even when the reel exists. A video
                # upload can be refused for reasons that have nothing to do
                # with this winner, and losing his post over that is worse
                # than losing the motion.
                items.append(Item(text=text, alt=alt,
                                  video=Path(mp4).read_bytes(),
                                  image=Path(still_png).read_bytes(),
                                  aspect=card_mod.REEL,
                                  name=f"{base}.mp4"))
                continue
            except Exception as e:  # noqa: BLE001 -- a card still posts
                note = f"{type(e).__name__}: {e}"

        items.append(Item(text=text, image=Path(still_png).read_bytes(),
                          alt=alt, note=note))
    return items, key


def staleness(window: str, key: str, today=None) -> int:
    """How many days behind `today` this window's last day is.

    An award called "of the Day" that quietly goes out four days late is
    worse than one that does not go out: it reads as current, and nobody
    checks. The site already prints a banner for this; a channel has no
    banner, so the publisher refuses instead.
    """
    import datetime as dt

    today = today or dt.date.today()
    try:
        if window == "month":
            y, m = (int(x) for x in key.split("-")[:2])
            end = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1))
        else:
            start = dt.date.fromisoformat(key)
            end = start + dt.timedelta(days=6) if window == "week" else start
    except (ValueError, IndexError):
        return 0          # unparseable: not our call to make here
    return max((today - end).days, 0)
