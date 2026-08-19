#!/usr/bin/env python3
"""Sanity-check a built leaderboard before it goes live.

These are the checks from the handoff's "verify these once real numbers
exist" list that a machine can make on its own. Face validity and the
WAR correlation still need a human squinting at the top of the board --
so this prints that board rather than pretending to judge it.

    python scripts/verify_build.py site/data/data.js

Exits non-zero on a hard failure, which is what stops a bad build from
being committed to main.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

PILLARS = ("BITE", "GRIT", "HUNT", "FIGHT")

MIN_PLAYERS = 100
MIN_PILLAR_SPREAD = 1e-6      # a pillar with no spread is a dead pillar
LOW_COVERAGE = 0.50           # component present for under half the players


def load(path: Path) -> dict:
    """data.js is a script assignment, not JSON. Unwrap it."""
    text = path.read_text(encoding="utf-8").strip()
    prefix = "window.XDAWG_DATA = "
    if not text.startswith(prefix):
        sys.exit(f"FAIL  {path} does not start with '{prefix}'")
    return json.loads(text[len(prefix):].rstrip().rstrip(";"))


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "site/data/data.js")
    allow_synthetic = "--allow-synthetic" in sys.argv
    if not path.exists():
        sys.exit(f"FAIL  {path} does not exist -- did the build actually run?")

    data = load(path)
    players = data.get("players", [])
    failures: list[str] = []
    warnings: list[str] = []

    print(f"## Leaderboard check -- {path}")
    print()
    print(f"    season      {data.get('season')}")
    print(f"    generated   {data.get('generated')}")
    print(f"    players     {len(players)}")
    print(f"    synthetic   {data.get('synthetic')}")
    print()

    # --- Hard check: this must be real data -----------------------------
    if data.get("synthetic") and not allow_synthetic:
        failures.append(
            "payload is flagged synthetic -- this is placeholder data, "
            "not a real build"
        )

    if len(players) < MIN_PLAYERS:
        failures.append(
            f"only {len(players)} players scored (expected >= {MIN_PLAYERS}); "
            "qualification thresholds or ingestion are wrong"
        )

    if not players:
        _report(failures, warnings)
        return 1

    # --- Hard check: no pillar is silently dead -------------------------
    print("### Pillar spread (a dead pillar means components dropped out)")
    print()
    for role in ("hitter", "pitcher"):
        rows = [p for p in players if p.get("role") == role]
        if not rows:
            warnings.append(f"no {role}s in the payload at all")
            continue
        print(f"    {role}s ({len(rows)})")
        for pillar in PILLARS:
            zs = [
                p["pillars"][pillar]["z"]
                for p in rows
                if p.get("pillars", {}).get(pillar, {}).get("z") is not None
            ]
            if not zs:
                failures.append(f"{role} {pillar}: no player has a value")
                print(f"      {pillar:<6} EMPTY")
                continue
            spread = statistics.pstdev(zs) if len(zs) > 1 else 0.0
            flag = ""
            if spread < MIN_PILLAR_SPREAD:
                failures.append(
                    f"{role} {pillar}: zero spread across {len(zs)} players "
                    "-- every component dropped out"
                )
                flag = "  <-- DEAD"
            print(
                f"      {pillar:<6} n={len(zs):<5} sd={spread:6.3f} "
                f"mean={statistics.fmean(zs):+6.3f}{flag}"
            )
        print()

    # --- Soft check: component coverage ---------------------------------
    print("### Component coverage (low coverage = a leaderboard we couldn't reach)")
    print()
    for role in ("hitter", "pitcher"):
        rows = [p for p in players if p.get("role") == role]
        if not rows:
            continue
        seen: dict[str, int] = defaultdict(int)
        for p in rows:
            for pillar in PILLARS:
                for comp in p.get("pillars", {}).get(pillar, {}).get("components", []):
                    if comp.get("z") is not None:
                        seen[f"{pillar}/{comp['key']}"] += 1
        print(f"    {role}s")
        if not seen:
            failures.append(f"{role}: no components present on any player")
            print("      none present")
        for key, n in sorted(seen.items(), key=lambda kv: kv[1]):
            cov = n / len(rows)
            mark = "  <-- low" if cov < LOW_COVERAGE else ""
            if cov < LOW_COVERAGE:
                warnings.append(f"{role} {key}: present for only {cov:.0%} of players")
            print(f"      {key:<34} {cov:5.0%}{mark}")
        print()

    # --- Soft check: the scale is where it should be --------------------
    print("### Scale")
    print()
    for key, label in (("dawg_plus", "DAWG+ "), ("wdawg_plus", "wDAWG+")):
        xs = [p[key] for p in players if p.get(key) is not None]
        if not xs:
            failures.append(f"{label.strip()} missing from every player")
            continue
        mean, sd = statistics.fmean(xs), statistics.pstdev(xs)
        print(f"    {label} mean {mean:5.1f} (target 100), sd {sd:5.1f} "
              f"(target ~25), range {min(xs):.1f} to {max(xs):.1f}")
        if abs(mean - 100) > 3:
            warnings.append(f"{label.strip()} mean is {mean:.1f}, not ~100")
        if not 15 <= sd <= 40:
            warnings.append(f"{label.strip()} sd is {sd:.1f}, off the ~25 target")
    print()

    # --- For the human: face validity ------------------------------------
    print("### Top 25 by DAWG+ -- eyeball this for face validity")
    print("    (columns: DAWG+ then wDAWG+)")
    print()
    print("    The design goal is grinders high and several inner-circle")
    print("    superstars near 100. A top 25 that reads like a WAR")
    print("    leaderboard means the self-referenced delta is not cancelling")
    print("    talent somewhere.")
    print()
    for p in players[:25]:
        print(
            f"    {p.get('rank', 0):>3}. {p.get('name', '?'):<26}"
            f"{p.get('team', ''):<5}{p.get('role', ''):<9}"
            f"{p.get('dawg_plus') or 0:7.1f}{p.get('wdawg_plus') or 0:8.1f}"
        )
    print()

    return _report(failures, warnings)


def _report(failures: list[str], warnings: list[str]) -> int:
    if warnings:
        print("### Warnings")
        print()
        for w in warnings:
            print(f"    WARN  {w}")
        print()
    if failures:
        print("### Failures")
        print()
        for f in failures:
            print(f"    FAIL  {f}")
        print()
        print(f"Verification failed with {len(failures)} error(s).")
        return 1
    print("Verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
