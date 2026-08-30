"""Command line entry point: `python -m xdawg ...`"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import SEASON_DEFAULT

SITE = Path(__file__).resolve().parents[1] / "site"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="xdawg", description="Calculate xDAWG.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="pull data, score everyone, write the site")
    b.add_argument("--season", type=int, default=SEASON_DEFAULT)
    b.add_argument("--refresh", action="store_true", help="ignore the local cache")
    b.add_argument(
        "--topup", action="store_true",
        help="keep the cache and pull only the days since its last game. "
             "This is what the nightly job wants: minutes instead of the "
             "forty --refresh costs to re-pull a season for one new day.")
    b.add_argument("--site", default=str(SITE))

    s = sub.add_parser(
        "smoke",
        help="short-window trial run to validate ingestion without a full pull",
    )
    s.add_argument("--season", type=int, default=SEASON_DEFAULT)
    s.add_argument("--days", type=int, default=10)

    h = sub.add_parser(
        "history",
        help="score several seasons and write the year-over-year view",
    )
    h.add_argument(
        "--seasons", type=int, nargs="+", default=None,
        help="seasons to score, e.g. --seasons 2023 2024 2025 2026. "
             "Each one is a full Savant pull the first time it is run.",
    )
    h.add_argument("--refresh", action="store_true", help="ignore the local cache")
    h.add_argument("--site", default=str(SITE))

    pr = sub.add_parser(
        "probe",
        help="report the real column names of every optional leaderboard",
    )
    pr.add_argument("--season", type=int, default=SEASON_DEFAULT)

    m = sub.add_parser("mock", help="regenerate placeholder data for the site")
    m.add_argument("--site", default=str(SITE))

    c = sub.add_parser(
        "clips",
        help="resolve an award window's winners to MLB video of their moment",
    )
    c.add_argument("--window", choices=("day", "week", "month"), default="day")
    c.add_argument("--key", default=None,
                   help="window key, e.g. 2026-08-28. Default: the latest one.")
    c.add_argument("--top", type=int, default=5)
    c.add_argument("--awards", default=str(SITE / "data" / "awards.js"))
    c.add_argument("--out", default="clips")
    c.add_argument("--summary", default=None,
                   help="also write the markdown report to this file")
    c.add_argument("--dry-run", action="store_true",
                   help="resolve playIds but download nothing")

    po = sub.add_parser(
        "posts",
        help="build a window's cards, reels and captions, ready to upload",
    )
    po.add_argument("--window", choices=("day", "week", "month"), default="day")
    po.add_argument("--key", default=None,
                    help="window key, e.g. 2026-08-28. Default: the latest one.")
    po.add_argument("--top", type=int, default=5)
    po.add_argument("--awards", default=str(SITE / "data" / "awards.js"))
    po.add_argument("--out", default="posts")
    po.add_argument("--handle", default="@XDAWGMLB")
    po.add_argument("--logo", default=None,
                    help="override the footer mark. Omitted uses "
                         "assets/logo/mark.png.")
    po.add_argument("--summary", default=None,
                    help="also write the markdown report to this file")
    po.add_argument("--no-video", action="store_true",
                    help="cards and captions only; do not fetch clips")

    pub = sub.add_parser(
        "publish",
        help="post a window's winners to Bluesky as one thread",
    )
    pub.add_argument("--window", choices=("day", "week", "month"), default="day")
    pub.add_argument("--key", default=None,
                     help="window key, e.g. 2026-08-28. Default: the latest one.")
    pub.add_argument("--top", type=int, default=5)
    pub.add_argument("--awards", default=str(SITE / "data" / "awards.js"))
    pub.add_argument("--out", default="posts")
    pub.add_argument("--handle", default="@XDAWGMLB",
                     help="the handle printed in the card footer")
    pub.add_argument("--logo", default=None,
                     help="override the footer mark. Omitted uses "
                          "assets/logo/mark.png.")
    pub.add_argument("--summary", default=None,
                     help="also write the markdown report to this file")
    # Posting is opt-in, every time. A flag that defaults to "send it" is a
    # flag somebody forgets is there, and this one is not undoable from a
    # follower's timeline.
    pub.add_argument("--live", action="store_true",
                     help="actually post. Without it this is a dry run and "
                          "nothing leaves the machine.")
    pub.add_argument("--max-age", type=int, default=2,
                     help="refuse to post a window whose last day is more "
                          "than this many days old (default 2). A stale "
                          "'DAWG of the Day' reads as current.")
    pub.add_argument("--allow-stale", action="store_true",
                     help="post an old window anyway. For backfilling.")
    pub.add_argument("--no-video", action="store_true",
                     help="cards only. Skips the clip chain entirely, so a "
                          "wording check costs no MLB traffic.")

    sub.add_parser("serve", help="serve the site on localhost:8000")

    a = ap.parse_args(argv)

    if a.cmd == "mock":
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import make_mock_data

        make_mock_data.main()
        return 0

    if a.cmd == "serve":
        import http.server
        import socketserver

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kw):
                super().__init__(*args, directory=str(SITE), **kw)

        with socketserver.TCPServer(("", 8000), Handler) as httpd:
            print("serving http://localhost:8000  (ctrl-c to stop)")
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                pass
        return 0

    if a.cmd == "history":
        from .history import (
            DEFAULT_WINDOW, build_history, summarize, write_history,
        )

        seasons = a.seasons or DEFAULT_WINDOW
        print(f"[xdawg] history for {seasons}")
        print("[xdawg] each uncached season is a full Savant pull; budget")
        print("[xdawg] roughly 40 minutes apiece on a cold cache\n")
        payload = build_history(seasons, refresh=a.refresh)
        print(summarize(payload))
        out = write_history(payload, a.site)
        print(f"\n[xdawg] wrote {out}")
        return 0

    if a.cmd == "publish":
        import os

        from . import bluesky
        from .posts import thread_items

        print(f"[xdawg] publish: {a.window} {a.key or '(latest)'} top {a.top}")
        logo = Path(a.logo).read_bytes() if a.logo else None
        items, key = thread_items(a.awards, window=a.window, key=a.key,
                                  top=a.top, out_dir=a.out, handle=a.handle,
                                  logo=logo, video=not a.no_video)
        if not items:
            print("[xdawg] that window has no board. Nothing to do.")
            return 1

        # Say what each man got and why, before anything is sent. A run that
        # silently posts five cards when it was asked for reels is the
        # failure this exists to make impossible to miss.
        vids = sum(1 for i in items if i.video)
        print(f"[xdawg] {vids}/{len(items)} have a reel")
        for n, i in enumerate(items, 1):
            what = f"{len(i.video)/1e6:.1f} MB reel" if i.video else "card"
            print(f"[xdawg]   {n}. {what}" + (f" — {i.note}" if i.note else ""))
        if not a.no_video and not vids:
            print("[xdawg] nothing resolved to video. The reasons are above; "
                  "if they all say ffmpeg, the machine needs it installed.")

        from .posts import staleness

        age = staleness(a.window, key)
        if age > a.max_age:
            note = (f"[xdawg] that {a.window} board is {age} days old "
                    f"(window {key}).")
            if a.live and not a.allow_stale:
                print(note)
                print("[xdawg] refusing to post it as current. Check whether "
                      "the nightly ran; --allow-stale overrides.")
                return 3
            print(note + "  (--allow-stale set)" if a.allow_stale else note)

        if not a.live:
            md = bluesky.report(bluesky.prepare(items), dry_run=True)
            print("\n" + md)
            if a.summary:
                Path(a.summary).write_text(md + "\n")
            print("\n[xdawg] dry run. Re-run with --live to post.")
            return 0

        # Credentials come from the environment, never from argv: an app
        # password on a command line lands in shell history and in the
        # process table where anyone on the box can read it.
        bhandle = os.environ.get("BSKY_HANDLE", "")
        bpass = os.environ.get("BSKY_APP_PASSWORD", "")
        if not bhandle or not bpass:
            print("[xdawg] set BSKY_HANDLE and BSKY_APP_PASSWORD to post.")
            return 2
        try:
            session = bluesky.login(bhandle, bpass)
        except bluesky.BlueskyError as e:
            print(f"[xdawg] login failed: {e}")
            return 2
        print(f"[xdawg] posting as {session.handle} ({session.did})")
        if any(i.video for i in items):
            # Both things that stop a video -- an unverified email and the
            # daily quota -- report here in a sentence, and otherwise
            # surface as an opaque failure after the file has been sent.
            try:
                lim = bluesky.upload_limits(session)
                print(f"[xdawg] video: canUpload={lim.get('canUpload')} "
                      f"remaining={lim.get('remainingDailyVideos')} videos / "
                      f"{(lim.get('remainingDailyBytes') or 0)/1e6:.0f} MB"
                      + (f" — {lim['message']}" if lim.get("message") else ""))
                if lim.get("canUpload") is False:
                    print("[xdawg] the account cannot upload video right now. "
                          "Verify the account email in Bluesky settings, or "
                          "wait for the daily quota. Re-run with --no-video "
                          "to post the cards instead.")
                    return 2
            except bluesky.BlueskyError as e:
                print(f"[xdawg] could not read the video limits: {e}")
        results = bluesky.publish_thread(session, items)
        md = bluesky.report(results, dry_run=False)
        print("\n" + md)
        if a.summary:
            Path(a.summary).write_text(md + "\n")
        return 0 if all(r.ok for r in results) else 2

    if a.cmd == "posts":
        from .posts import build_posts, report

        print(f"[xdawg] posts: {a.window} {a.key or '(latest)'} top {a.top}")
        logo = Path(a.logo).read_bytes() if a.logo else None
        results = build_posts(a.awards, window=a.window, key=a.key, top=a.top,
                              out_dir=a.out, handle=a.handle, logo=logo,
                              fetch_video=not a.no_video)
        if not results:
            print("[xdawg] that window has no board. Nothing to do.")
            return 1
        md = report(results)
        print("\n" + md)
        if a.summary:
            Path(a.summary).write_text(md + "\n")
        # A post with a still instead of a clip still counts. Only a run
        # that produced no card at all has actually failed.
        return 0 if any(r.card for r in results) else 2

    if a.cmd == "clips":
        from .clips import build_clips, report

        print(f"[xdawg] clips: {a.window} {a.key or '(latest)'} top {a.top}")
        results = build_clips(a.awards, window=a.window, key=a.key, top=a.top,
                              out_dir=a.out, fetch_video=not a.dry_run)
        if not results:
            print("[xdawg] that window has no board. Nothing to do.")
            return 1
        md = report(results)
        print("\n" + md)
        if a.summary:
            Path(a.summary).write_text(md + "\n")
        # Non-zero only if EVERY winner failed. A partial night is normal --
        # Savant does not have video for every play, and a run that returns
        # four of five clips is a success that should not page anybody.
        return 0 if any(r.play_id for r in results) else 2

    if a.cmd == "probe":
        from .ingest import probe

        print(f"[xdawg] probing optional leaderboards for {a.season}")
        print("[xdawg] these all degrade silently at runtime, so a rename")
        print("[xdawg] shows up as a missing pillar term rather than an error\n")
        dead = probe(a.season)
        print(f"\n[xdawg] {dead} source(s) unreachable")
        return 0

    if a.cmd == "smoke":
        import datetime as dt

        from .ingest import season_dates
        from .pipeline import run

        _, last = season_dates(a.season)
        end = dt.date.fromisoformat(last)
        start = end - dt.timedelta(days=a.days)
        print(f"[xdawg] smoke test: {start} to {end}")
        print("[xdawg] thresholds relaxed - a short window can't reach the real ones\n")

        # Deliberately does NOT write site data. This only answers "does the
        # ingestion path work end to end," so it can never clobber a good build.
        hit, pit = run(
            season=a.season, start=str(start), end=str(end), min_pa=15, min_bf=15
        )
        for label, df in (("HITTERS", hit), ("PITCHERS", pit)):
            print(f"\n  {label} - {len(df)} scored   (DAWG+ / wDAWG+)")
            for _, r in df.head(5).iterrows():
                print(f"    {r['name']:<24} {str(r['team']):<4} "
                      f"{r['DAWG+']:6.1f} {r['wDAWG+']:6.1f}")
        print("\n[xdawg] ingestion path works. Now run: python -m xdawg build")
        return 0

    from .export import build_payload, write_awards, write_site_data
    from .pipeline import run

    hit, pit = run(season=a.season, refresh=a.refresh, topup=a.topup)
    from .ingest import load_standings
    payload = build_payload(hit, pit, season=a.season, synthetic=False,
                            standings=load_standings(a.season))
    out = write_site_data(payload, a.site)
    print(f"[xdawg] wrote {out} ({len(payload['players'])} players)")

    # Awards ride along with the build rather than being their own command:
    # they need the same pitch frame, and a Day award that refreshes on a
    # different schedule from the leaderboard would drift out of agreement
    # with it. Failing here must not throw away the leaderboard that has
    # already been written.
    try:
        from .awards import build_awards
        from .ingest import load_statcast, player_names
        from .leverage import add_leverage
        from .ingest import normalize_team

        p = load_statcast(a.season)          # cached by the run above
        for col in ("home_team", "away_team"):
            if col in p.columns:
                p[col] = p[col].map(normalize_team)
        p = add_leverage(p)
        names = player_names(p)
        team_of = {(pl["id"], pl["role"]): pl["team"] for pl in payload["players"]}
        aw = build_awards(p, a.season, names, team_of)
        aout = write_awards(aw, a.site)
        print(f"[xdawg] wrote {aout} "
              f"({len(aw['boards']['day'])} days, {len(aw['boards']['week'])} weeks, "
              f"{len(aw['boards']['month'])} months)")
    except Exception as e:                                   # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"[xdawg] awards failed ({e}); the leaderboard above is unaffected")

    print("[xdawg] open site/index.html, or run: python -m xdawg serve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
