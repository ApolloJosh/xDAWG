"""Incremental Statcast top-up.

The nightly job used `--refresh`, which ignores the cache and re-pulls the
entire season every night to acquire one day of new games. Correct, and forty
minutes of Savant traffic for a few hundred new pitches. `--topup` keeps the
cache and pulls only from the day before its last game forward.

The overlap day is the part worth testing. A build that ran while games were
still in progress cached a PARTIAL final day; re-pulling that day and letting
the fresh copy win is what repairs it. Get the dedupe backwards and the stale
partial row survives instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xdawg import ingest  # noqa: E402


def _pitches(dates, game_offset=0, tag="cached"):
    rows = []
    for d in dates:
        for ab in (1, 2):
            rows.append({
                "game_pk": 100 + game_offset + int(d[-2:]),
                "at_bat_number": ab, "pitch_number": 1,
                "game_date": d, "batter": 1, "pitcher": 2,
                "events": tag,
            })
    return pd.DataFrame(rows)


def _run_topup(monkeypatch, tmp_path, cached, fetched, capture):
    monkeypatch.setattr(ingest, "CACHE", tmp_path)
    monkeypatch.setattr(ingest, "season_dates",
                        lambda s: ("2026-03-15", "2026-08-27"))
    cached.to_parquet(tmp_path / "statcast_2026.parquet", index=False)

    import types
    fake = types.ModuleType("pybaseball")

    def statcast(start_dt=None, end_dt=None, verbose=False):
        capture["start"], capture["end"] = start_dt, end_dt
        return fetched

    fake.statcast = statcast
    monkeypatch.setitem(sys.modules, "pybaseball", fake)
    return ingest.load_statcast(2026, topup=True)


def test_topup_pulls_only_from_the_day_before_the_last_cached_game(monkeypatch, tmp_path):
    cached = _pitches(["2026-08-24", "2026-08-25"])
    fetched = _pitches(["2026-08-25", "2026-08-26"], tag="fresh")
    capture = {}
    out = _run_topup(monkeypatch, tmp_path, cached, fetched, capture)

    assert capture["start"] == "2026-08-24", (
        f"must re-pull the day before the last cached game, asked for {capture['start']}"
    )
    assert capture["end"] == "2026-08-27"
    assert set(pd.to_datetime(out["game_date"]).dt.strftime("%Y-%m-%d")) == {
        "2026-08-24", "2026-08-25", "2026-08-26"
    }


def test_the_fresh_copy_wins_on_the_overlap_day(monkeypatch, tmp_path):
    """A partial final day in the cache must be replaced, not preserved."""
    cached = _pitches(["2026-08-25"], tag="partial")
    fetched = _pitches(["2026-08-25"], tag="complete")
    out = _run_topup(monkeypatch, tmp_path, cached, fetched, {})

    assert len(out) == 2, f"the overlap must dedupe, not double: {len(out)} rows"
    assert set(out["events"]) == {"complete"}, (
        "the re-pulled copy must win; the stale partial day survived"
    )


def test_topup_writes_the_merged_frame_back_to_cache(monkeypatch, tmp_path):
    cached = _pitches(["2026-08-24"])
    fetched = _pitches(["2026-08-25"], tag="fresh")
    _run_topup(monkeypatch, tmp_path, cached, fetched, {})
    again = pd.read_parquet(tmp_path / "statcast_2026.parquet")
    assert len(again) == 4, "tomorrow's top-up must start from today's merge"


def test_topup_with_no_cache_behaves_like_a_normal_pull(monkeypatch, tmp_path):
    monkeypatch.setattr(ingest, "CACHE", tmp_path)
    monkeypatch.setattr(ingest, "season_dates",
                        lambda s: ("2026-03-15", "2026-08-27"))
    import types
    fake = types.ModuleType("pybaseball")
    seen = {}

    def statcast(start_dt=None, end_dt=None, verbose=False):
        seen["start"] = start_dt
        return _pitches(["2026-08-25"])

    fake.statcast = statcast
    monkeypatch.setitem(sys.modules, "pybaseball", fake)

    out = ingest.load_statcast(2026, topup=True)
    assert seen["start"] == "2026-03-15", "a cold cache must pull the whole season"
    assert len(out) == 2
