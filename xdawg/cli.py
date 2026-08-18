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

    from .export import build_payload, write_site_data
    from .pipeline import run

    hit, pit = run(season=a.season, refresh=a.refresh)
    payload = build_payload(hit, pit, season=a.season, synthetic=False)
    out = write_site_data(payload, a.site)
    print(f"[xdawg] wrote {out} ({len(payload['players'])} players)")
    print("[xdawg] open site/index.html, or run: python -m xdawg serve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
