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

    from .export import build_payload, write_site_data
    from .pipeline import run

    hit, pit = run(season=a.season, refresh=a.refresh)
    from .ingest import load_standings
    payload = build_payload(hit, pit, season=a.season, synthetic=False,
                            standings=load_standings(a.season))
    out = write_site_data(payload, a.site)
    print(f"[xdawg] wrote {out} ({len(payload['players'])} players)")
    print("[xdawg] open site/index.html, or run: python -m xdawg serve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
