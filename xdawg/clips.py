"""Resolve a DAWG award's best moment to the MLB video of that play.

The chain, which is four hops and none of them documented:

    date            -> statsapi /schedule            -> gamePk
    gamePk          -> statsapi /game/{pk}/feed/live -> allPlays[]
    the right play  -> playEvents[].playId           -> a GUID
    the GUID        -> baseballsavant sporty-videos  -> an .mp4 on
                                                        sporty-clips.mlb.com

Everything that touches the network lives in a thin wrapper at the top and
everything that makes a decision is a pure function underneath it, because
the deciding is the part that can be wrong and the part that can be tested.
The container this is developed in cannot reach statsapi at all; GitHub
Actions can. So the rule here is that a bug must be reproducible from a
saved fixture, not only from a live game.

Note on the join. The obvious key would be Statcast's `at_bat_number`
against StatsAPI's `atBatIndex`, but those are two different systems'
numbering of the same list and the offset is an assumption, not a fact. We
key on things both sides observably agree about instead -- the inning, the
half, the player, and the event type -- and use the index only to break a
tie. Statcast's `events` and StatsAPI's `result.eventType` share a
vocabulary, which is what makes that work.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

STATSAPI = "https://statsapi.mlb.com"
SAVANT_VIDEO = "https://baseballsavant.mlb.com/sporty-videos?playId={}"

# The page embeds the clip in a <video><source src=...>. The filename is a
# long base64-ish blob, so the character class has to allow the padding.
MP4_RE = re.compile(r"https://sporty-clips\.mlb\.com/[A-Za-z0-9_=\-]+\.mp4")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Statcast's club abbreviations to MLB team ids. Ids because abbreviations
# are the one field the two systems have historically disagreed about --
# Oakland alone has been OAK, ATH and AL in different places in the same
# season -- and an id has never moved.
TEAM_IDS = {
    "LAA": 108, "ARI": 109, "BAL": 110, "BOS": 111, "CHC": 112, "CIN": 113,
    "CLE": 114, "COL": 115, "DET": 116, "KC": 118, "LAD": 119, "WSH": 120,
    "NYM": 121, "ATH": 133, "PIT": 134, "SD": 135, "SEA": 136, "SF": 137,
    "STL": 138, "TB": 139, "TEX": 140, "TOR": 141, "MIN": 142, "PHI": 143,
    "ATL": 144, "CWS": 145, "MIA": 146, "NYY": 147, "MIL": 158, "HOU": 117,
}
ID_TEAMS = {v: k for k, v in TEAM_IDS.items()}


# --------------------------------------------------------------------------
# network
# --------------------------------------------------------------------------

def _get(url: str, timeout: int = 30, retries: int = 3) -> bytes:
    """GET with a browser UA and a couple of retries.

    statsapi is generally reliable and Savant is generally not; both are
    free and neither owes us an SLA, so a transient 5xx is expected rather
    than exceptional and is worth one more try before it becomes an error.
    """
    last: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError) as e:  # noqa: PERF203
            last = e
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed: {last}")


def _get_json(url: str, **kw) -> dict:
    return json.loads(_get(url, **kw))


def schedule(date: str) -> list[dict]:
    """Every regular game on `date`, as {game_pk, away, home, away_id, home_id}."""
    url = f"{STATSAPI}/api/v1/schedule?sportId=1&date={date}"
    return parse_schedule(_get_json(url))


def live_feed(game_pk: int) -> dict:
    return _get_json(f"{STATSAPI}/api/v1.1/game/{int(game_pk)}/feed/live", timeout=60)


def mp4_url(play_id: str) -> str | None:
    """The clip URL behind a playId GUID, or None if Savant has no video."""
    try:
        html = _get(SAVANT_VIDEO.format(play_id)).decode("utf-8", "replace")
    except RuntimeError:
        return None
    return find_mp4(html)


def download(url: str, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = _get(url, timeout=120)
    dest.write_bytes(data)
    return len(data)


# --------------------------------------------------------------------------
# pure
# --------------------------------------------------------------------------

def find_mp4(html: str) -> str | None:
    m = MP4_RE.search(html or "")
    return m.group(0) if m else None


def parse_schedule(payload: dict) -> list[dict]:
    out = []
    for day in (payload or {}).get("dates", []):
        for g in day.get("games", []):
            t = g.get("teams", {})
            away = t.get("away", {}).get("team", {}) or {}
            home = t.get("home", {}).get("team", {}) or {}
            aid, hid = away.get("id"), home.get("id")
            out.append({
                "game_pk": g.get("gamePk"),
                "away_id": aid,
                "home_id": hid,
                # Prefer our own abbreviation for the id; fall back to
                # whatever the API called it, so an expansion club or a
                # rebrand degrades to "unmatched" rather than "wrong club".
                "away": ID_TEAMS.get(aid, away.get("abbreviation", "")),
                "home": ID_TEAMS.get(hid, home.get("abbreviation", "")),
                "state": (g.get("status", {}) or {}).get("abstractGameState", ""),
            })
    return out


def find_game(games: list[dict], team: str, opp: str) -> dict | None:
    """The game between these two clubs. None if it is not on this slate.

    Doubleheaders are the reason this returns the first match rather than
    asserting there is exactly one: two games between the same clubs on the
    same date is legal, and picking the wrong one produces a clip of the
    right players doing the wrong thing. `resolve` re-checks the play
    against the feed, so a doubleheader mismatch surfaces as "no play
    matched" and falls through to the second game.
    """
    for g in games:
        if {g["away"], g["home"]} == {team, opp}:
            return g
    return None


def feed_home(feed: dict) -> str:
    """Which club is the home side, straight from the game's own feed.

    Asked of the feed rather than the schedule because by the time we want
    it we are already holding the feed, and because a game_pk carried in
    from the award row may belong to a date whose schedule we never fetched.
    """
    t = ((feed or {}).get("gameData", {}).get("teams", {}) or {}).get("home", {}) or {}
    return ID_TEAMS.get(t.get("id"), t.get("abbreviation", "") or "")


def norm_event(s: str) -> str:
    """Statcast `events` and StatsAPI `eventType` into one spelling."""
    return re.sub(r"[^a-z]+", "_", str(s or "").strip().lower()).strip("_")


def is_top_inning(*, team: str, home: str) -> bool:
    """True when `team` is the club at the plate in the top half.

    The caller passes the batting club, so this is only the away/home test.
    For a pitcher, the batting club is his opponent.
    """
    return team != home


def match_play(feed: dict, *, pid: int, role: str, inning: int,
               top: bool, event: str = "", at_bat: int | None = None) -> dict | None:
    """The one play in this game that the award is about.

    Filters on the four things both systems observe the same way, then, if
    that still leaves more than one, prefers the one whose atBatIndex sits
    where Statcast's at_bat_number says it should -- accepting any of the
    plausible offsets rather than hard-coding one, since which offset is
    right is exactly the thing nobody has verified.
    """
    plays = ((feed or {}).get("liveData", {}).get("plays", {}) or {}).get("allPlays", [])
    who = "pitcher" if role == "pitcher" else "batter"
    want = norm_event(event)

    cands = []
    for p in plays:
        about = p.get("about", {}) or {}
        mu = p.get("matchup", {}) or {}
        if int(about.get("inning", -1)) != int(inning):
            continue
        if bool(about.get("isTopInning")) != bool(top):
            continue
        if int((mu.get(who, {}) or {}).get("id", -1)) != int(pid):
            continue
        cands.append(p)

    if not cands:
        return None
    if want:
        exact = [p for p in cands
                 if norm_event((p.get("result", {}) or {}).get("eventType")) == want]
        if exact:
            cands = exact
    if len(cands) > 1 and at_bat is not None:
        for off in (1, 0, 2):
            hit = [p for p in cands if int(p.get("atBatIndex", -99)) == int(at_bat) - off]
            if hit:
                return hit[0]
    return cands[0]


def play_id(play: dict) -> str | None:
    """The GUID of the pitch that ended the plate appearance.

    The last playId in the at-bat, not the first: a nine-pitch walk has nine
    of these and the one worth watching is the one where something happened.
    """
    for ev in reversed((play or {}).get("playEvents", []) or []):
        gid = ev.get("playId")
        if gid:
            return gid
    return None


def load_awards(path: str | Path) -> dict:
    """Parse site/data/awards.js, which is JSON wearing a `window.X =` hat."""
    text = Path(path).read_text()
    i = text.index("=")
    return json.loads(text[i + 1:].strip().rstrip(";"))


def winners(awards: dict, window: str, key: str | None = None,
            top: int = 5) -> list[dict]:
    """The top `top` rows of one window's board."""
    boards = awards.get("boards", {}).get(window, {})
    if not boards:
        return []
    key = key or awards.get("latest", {}).get(window) or max(boards)
    rows = boards.get(key) or []
    return [r for r in rows[:top] if r.get("best")]


# --------------------------------------------------------------------------
# the walk
# --------------------------------------------------------------------------

@dataclass
class Resolution:
    """What happened to one winner, at every hop, whether or not it worked."""
    name: str = ""
    team: str = ""
    role: str = ""
    date: str = ""
    inning: int | None = None
    event: str = ""
    game_pk: int | None = None
    at_bat_index: int | None = None
    at_bat_number: int | None = None
    play_id: str | None = None
    url: str | None = None
    path: str | None = None
    bytes: int = 0
    error: str = ""
    notes: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.path)


def resolve(row: dict, *, out_dir: Path | None = None,
            fetch_video: bool = True) -> Resolution:
    """Walk one winner from his award row to a downloaded clip."""
    best = row.get("best") or {}
    res = Resolution(
        name=row.get("name", ""), team=row.get("team", ""),
        role=row.get("role", ""), date=best.get("date", ""),
        inning=best.get("inning"), event=best.get("event", ""),
        game_pk=best.get("game_pk"), at_bat_number=best.get("at_bat_number"),
    )
    if not res.date or res.inning is None:
        res.error = "award row has no dated moment"
        return res

    try:
        if not res.game_pk:
            # Only needed for boards built before the clip pipeline existed,
            # which do not carry the game. Everything since does.
            games = [g for g in schedule(res.date) if g["game_pk"]]
            g = find_game(games, res.team, best.get("opp", ""))
            if g is None:
                res.error = f"no {res.team}-{best.get('opp','?')} game on {res.date}"
                return res
            res.game_pk = g["game_pk"]
            res.notes.append(f"game resolved from the schedule: {res.game_pk}")

        feed = live_feed(res.game_pk)
        home = feed_home(feed)
        if not home:
            res.error = f"feed for {res.game_pk} names no home club"
            return res

        # The award row names the winner's club and his opponent; the half
        # of the inning follows from which of them was batting.
        batting = best.get("opp", "") if res.role == "pitcher" else res.team
        top = is_top_inning(team=batting, home=home)

        play = match_play(feed, pid=int(row["id"]), role=res.role,
                          inning=int(res.inning), top=top, event=res.event,
                          at_bat=res.at_bat_number)
        if play is None:
            res.error = "no play in the feed matched the award"
            return res
        res.at_bat_index = play.get("atBatIndex")
        if res.at_bat_number is not None and res.at_bat_index is not None:
            res.notes.append(
                f"at_bat_number {res.at_bat_number} -> atBatIndex "
                f"{res.at_bat_index} (offset {res.at_bat_number - res.at_bat_index})")

        res.play_id = play_id(play)
        if not res.play_id:
            res.error = "play has no playId (feed predates video, or a no-pitch event)"
            return res
        if not fetch_video:
            return res

        res.url = mp4_url(res.play_id)
        if not res.url:
            res.error = "Savant has no clip for that playId"
            return res
        if out_dir is not None:
            slug = re.sub(r"[^A-Za-z0-9]+", "-", f"{res.date}-{res.team}-{res.name}").strip("-")
            dest = Path(out_dir) / f"{slug}.mp4"
            res.bytes = download(res.url, dest)
            res.path = str(dest)
    except Exception as e:  # noqa: BLE001 -- one bad winner must not kill the run
        res.error = f"{type(e).__name__}: {e}"
    return res


def build_clips(awards_path: str | Path, *, window: str = "day",
                key: str | None = None, top: int = 5,
                out_dir: str | Path = "clips",
                fetch_video: bool = True) -> list[Resolution]:
    awards = load_awards(awards_path)
    rows = winners(awards, window, key, top)
    out = Path(out_dir)
    return [resolve(r, out_dir=out if fetch_video else None,
                    fetch_video=fetch_video) for r in rows]


def report(results: list[Resolution]) -> str:
    """A markdown table, for the workflow's step summary."""
    head = ("| # | Player | Team | Moment | gamePk | playId | Clip |\n"
            "|---|--------|------|--------|--------|--------|------|")
    lines = [head]
    for i, r in enumerate(results, 1):
        clip = f"{r.bytes/1e6:.1f} MB" if r.ok else f"**{r.error or 'failed'}**"
        gid = (r.play_id or "")[:8]
        lines.append(
            f"| {i} | {r.name} | {r.team} | {r.event or '?'} "
            f"inn {r.inning} {r.date} | {r.game_pk or '-'} | {gid or '-'} | {clip} |")
    ok = sum(1 for r in results if r.ok)
    lines.append("")
    lines.append(f"**{ok}/{len(results)} clips resolved.**")
    notes = [n for r in results for n in r.notes]
    if notes:
        lines.append("")
        lines.append("Index offsets observed: " + "; ".join(sorted(set(notes))))
    return "\n".join(lines)
