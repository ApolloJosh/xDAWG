"""The clip pipeline's decisions, tested without a network.

Everything here exercises the pure half of `xdawg.clips`: which game, which
play inside that game, which pitch inside that play, and which URL out of a
page of HTML. The network half is four one-line wrappers around urlopen and
is validated by the workflow running against live games, because a fixture
of statsapi's response proves only that we can read our own fixture.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xdawg import clips


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

SCHEDULE = {
    "dates": [{
        "games": [
            {"gamePk": 824638,
             "status": {"abstractGameState": "Final"},
             "teams": {"away": {"team": {"id": 111, "abbreviation": "BOS"}},
                       "home": {"team": {"id": 147, "abbreviation": "NYY"}}}},
            {"gamePk": 824640,
             "status": {"abstractGameState": "Final"},
             "teams": {"away": {"team": {"id": 134, "abbreviation": "PIT"}},
                       "home": {"team": {"id": 138, "abbreviation": "STL"}}}},
        ]
    }]
}


def _play(idx, inning, top, batter, pitcher, event_type, play_ids):
    return {
        "atBatIndex": idx,
        "about": {"inning": inning, "isTopInning": top},
        "matchup": {"batter": {"id": batter}, "pitcher": {"id": pitcher}},
        "result": {"eventType": event_type},
        "playEvents": [{"playId": g} if g else {"isPitch": False}
                       for g in play_ids],
    }


FEED = {"gameData": {"teams": {"home": {"id": 147, "abbreviation": "NYY"},
                               "away": {"id": 111, "abbreviation": "BOS"}}},
        "liveData": {"plays": {"allPlays": [
    _play(0, 1, True, 1001, 693645, "strikeout", ["aaa", "bbb"]),
    _play(1, 1, True, 1002, 693645, "walk", ["ccc"]),
    _play(16, 5, True, 1003, 693645, "field_out", ["ddd"]),
    _play(17, 5, True, 1004, 693645, "double_play", [None, "eee", "fff"]),
    _play(18, 5, False, 518692, 2002, "single", ["ggg"]),
]}}}

AMBIGUOUS = {"liveData": {"plays": {"allPlays": [
    _play(16, 5, True, 1003, 693645, "field_out", ["ddd"]),
    _play(19, 5, True, 1009, 693645, "field_out", ["hhh"]),
]}}}


# --------------------------------------------------------------------------
# schedule
# --------------------------------------------------------------------------

def test_schedule_prefers_our_own_abbreviations():
    games = clips.parse_schedule(SCHEDULE)
    assert [g["game_pk"] for g in games] == [824638, 824640]
    assert games[0]["away"] == "BOS" and games[0]["home"] == "NYY"


def test_unknown_team_id_falls_back_to_the_api_abbreviation():
    payload = {"dates": [{"games": [{
        "gamePk": 1, "teams": {
            "away": {"team": {"id": 999, "abbreviation": "XXX"}},
            "home": {"team": {"id": 147, "abbreviation": "NYY"}}}}]}]}
    g = clips.parse_schedule(payload)[0]
    assert g["away"] == "XXX"          # not silently mapped to a real club
    assert g["home"] == "NYY"


def test_find_game_is_order_independent():
    games = clips.parse_schedule(SCHEDULE)
    assert clips.find_game(games, "NYY", "BOS")["game_pk"] == 824638
    assert clips.find_game(games, "BOS", "NYY")["game_pk"] == 824638
    assert clips.find_game(games, "NYY", "STL") is None


def test_every_club_has_a_team_id():
    from xdawg.config import TEAMS
    assert set(clips.TEAM_IDS) == set(TEAMS)
    assert len(set(clips.TEAM_IDS.values())) == 30


# --------------------------------------------------------------------------
# which half
# --------------------------------------------------------------------------

def test_home_club_bats_in_the_bottom():
    assert clips.is_top_inning(team="BOS", home="NYY") is True
    assert clips.is_top_inning(team="NYY", home="NYY") is False


def test_home_club_comes_from_the_feed_by_id():
    assert clips.feed_home(FEED) == "NYY"


def test_feed_home_falls_back_to_the_abbreviation_then_to_empty():
    odd = {"gameData": {"teams": {"home": {"id": 999, "abbreviation": "XXX"}}}}
    assert clips.feed_home(odd) == "XXX"
    assert clips.feed_home({}) == ""


# --------------------------------------------------------------------------
# which play
# --------------------------------------------------------------------------

def test_matches_a_hitter_by_id_inning_and_half():
    p = clips.match_play(FEED, pid=518692, role="hitter", inning=5, top=False,
                         event="single")
    assert p["atBatIndex"] == 18


def test_matches_a_pitcher_on_the_event_when_the_inning_has_several():
    # 693645 faced two hitters in the top of the 5th. Only the event
    # separates them, and the award spells it with a space.
    p = clips.match_play(FEED, pid=693645, role="pitcher", inning=5, top=True,
                         event="double play")
    assert p["atBatIndex"] == 17


def test_wrong_half_matches_nothing():
    assert clips.match_play(FEED, pid=518692, role="hitter", inning=5,
                            top=True, event="single") is None


def test_wrong_player_matches_nothing():
    assert clips.match_play(FEED, pid=99999, role="hitter", inning=5,
                            top=False, event="single") is None


def test_an_unknown_event_does_not_throw_the_match_away():
    # Statcast and StatsAPI mostly agree on event names, but "mostly" is the
    # operative word. A disagreement should cost us precision, not the play.
    p = clips.match_play(FEED, pid=518692, role="hitter", inning=5, top=False,
                         event="sacrifice_fly_double_play_but_spelled_oddly")
    assert p["atBatIndex"] == 18


def test_at_bat_number_breaks_a_tie_without_assuming_the_offset():
    # Statcast counts from 1, StatsAPI from 0, so at_bat_number 20 is the
    # play at atBatIndex 19 -- and we get there without hard-coding that.
    p = clips.match_play(AMBIGUOUS, pid=693645, role="pitcher", inning=5,
                         top=True, event="field_out", at_bat=20)
    assert p["atBatIndex"] == 19


def test_normalises_both_spellings_of_an_event():
    assert clips.norm_event("double play") == "double_play"
    assert clips.norm_event("Grounded Into DP") == "grounded_into_dp"
    assert clips.norm_event(None) == ""


# --------------------------------------------------------------------------
# which pitch
# --------------------------------------------------------------------------

def test_play_id_is_the_last_pitch_of_the_at_bat():
    # The at-bat ends on the pitch worth watching. An eight-pitch walk has
    # eight GUIDs and seven of them are a man taking a ball.
    p = FEED["liveData"]["plays"]["allPlays"][3]
    assert clips.play_id(p) == "fff"


def test_play_id_is_none_when_no_pitch_carries_one():
    assert clips.play_id({"playEvents": [{"isPitch": False}]}) is None
    assert clips.play_id({}) is None


# --------------------------------------------------------------------------
# which URL
# --------------------------------------------------------------------------

def test_finds_the_clip_in_savants_page():
    html = ('<video id="sporty" controls><source src="https://sporty-clips.'
            'mlb.com/akR3UHhfWGw0TUFRPT1fQXdSVVhRSUJVMWNBREZZR0FnQUhVZzRDQUFN'
            'Q0J3QUFCbEpVQ1ZBR1VnUlZWQUZX.mp4" type="video/mp4"></video>')
    url = clips.find_mp4(html)
    assert url.startswith("https://sporty-clips.mlb.com/")
    assert url.endswith(".mp4")


def test_a_page_without_a_clip_returns_none():
    assert clips.find_mp4("<html><body>no video here</body></html>") is None
    assert clips.find_mp4("") is None


# --------------------------------------------------------------------------
# reading the board
# --------------------------------------------------------------------------

def _awards_file(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "awards.js"
    p.write_text("window.XDAWG_AWARDS = " + json.dumps(payload) + ";\n")
    return p


AWARDS = {
    "latest": {"day": "2026-08-28"},
    "boards": {"day": {
        "2026-08-27": [{"rank": 1, "id": 1, "name": "Old, Guy", "team": "NYY",
                        "role": "hitter", "best": {"date": "2026-08-27"}}],
        "2026-08-28": [
            {"rank": i, "id": i, "name": f"P{i}", "team": "NYY",
             "role": "hitter", "best": {"date": "2026-08-28", "inning": 9}}
            for i in range(1, 8)
        ],
    }},
}


def test_load_awards_unwraps_the_javascript(tmp_path):
    got = clips.load_awards(_awards_file(tmp_path, AWARDS))
    assert got["latest"]["day"] == "2026-08-28"


def test_winners_defaults_to_the_latest_window_and_honours_top():
    rows = clips.winners(AWARDS, "day", None, 5)
    assert len(rows) == 5
    assert all(r["best"]["date"] == "2026-08-28" for r in rows)


def test_winners_can_be_pointed_at_an_older_window():
    rows = clips.winners(AWARDS, "day", "2026-08-27", 5)
    assert [r["name"] for r in rows] == ["Old, Guy"]


def test_winners_skips_rows_with_no_moment():
    board = {"boards": {"day": {"k": [{"id": 1, "name": "No Moment"}]}},
             "latest": {"day": "k"}}
    assert clips.winners(board, "day") == []


def test_an_absent_window_is_empty_not_an_error():
    assert clips.winners({"boards": {}}, "week") == []


# --------------------------------------------------------------------------
# the walk, with the network stubbed out
# --------------------------------------------------------------------------

def _stub_network(monkeypatch, *, url=lambda g: f"https://sporty-clips.mlb.com/{g}.mp4"):
    def sched(_):
        raise AssertionError("the schedule should not be needed when game_pk is known")
    monkeypatch.setattr(clips, "schedule", sched)
    monkeypatch.setattr(clips, "live_feed", lambda pk: FEED)
    monkeypatch.setattr(clips, "mp4_url", url)
    monkeypatch.setattr(clips, "download",
                        lambda u, dest: (dest.parent.mkdir(parents=True, exist_ok=True),
                                         dest.write_bytes(b"x" * 42))[1])


HOME_HITTER = {"id": 518692, "name": "Freddie Freeman", "team": "NYY",
               "role": "hitter",
               "best": {"date": "2026-08-28", "inning": 5, "event": "single",
                        "opp": "BOS", "game_pk": 824638, "at_bat_number": 19}}


def test_resolve_walks_the_whole_chain(monkeypatch, tmp_path):
    _stub_network(monkeypatch)
    r = clips.resolve(HOME_HITTER, out_dir=tmp_path)
    assert r.error == ""
    assert r.play_id == "ggg"
    assert r.ok and Path(r.path).exists()
    assert any("offset 1" in n for n in r.notes)


def test_a_carried_game_pk_skips_the_schedule_entirely(monkeypatch, tmp_path):
    # _stub_network's schedule raises. Reaching the clip proves we never
    # asked -- which is what makes a doubleheader safe.
    _stub_network(monkeypatch)
    assert clips.resolve(HOME_HITTER, out_dir=tmp_path).ok


def test_a_pitcher_is_placed_in_the_other_half(monkeypatch):
    # The Yankee pitcher works the top of the inning; the Yankee hitter the
    # bottom. Same club, same game, opposite halves.
    _stub_network(monkeypatch)
    row = {"id": 693645, "name": "Cam Schlittler", "team": "NYY",
           "role": "pitcher",
           "best": {"date": "2026-08-28", "inning": 5, "event": "double play",
                    "opp": "BOS", "game_pk": 824638}}
    r = clips.resolve(row, fetch_video=False)
    assert r.error == "" and r.at_bat_index == 17


def test_resolve_reports_a_missing_clip_rather_than_raising(monkeypatch):
    _stub_network(monkeypatch, url=lambda g: None)
    r = clips.resolve(HOME_HITTER)
    assert not r.ok
    assert "Savant" in r.error


def test_resolve_falls_back_to_the_schedule_for_an_older_board(monkeypatch, tmp_path):
    monkeypatch.setattr(clips, "schedule", lambda d: clips.parse_schedule(SCHEDULE))
    monkeypatch.setattr(clips, "live_feed", lambda pk: FEED)
    monkeypatch.setattr(clips, "mp4_url", lambda g: f"https://sporty-clips.mlb.com/{g}.mp4")
    monkeypatch.setattr(clips, "download",
                        lambda u, dest: (dest.parent.mkdir(parents=True, exist_ok=True),
                                         dest.write_bytes(b"x" * 9))[1])
    row = dict(HOME_HITTER, best=dict(HOME_HITTER["best"]))
    row["best"].pop("game_pk")
    r = clips.resolve(row, out_dir=tmp_path)
    assert r.game_pk == 824638 and r.ok


def test_one_winners_network_failure_does_not_take_down_the_run(monkeypatch):
    def boom(_):
        raise RuntimeError("statsapi said 503")
    monkeypatch.setattr(clips, "schedule", boom)
    row = {"id": 1, "name": "X", "team": "NYY", "role": "hitter",
           "best": {"date": "2026-08-28", "inning": 1, "opp": "BOS"}}
    r = clips.resolve(row)
    assert not r.ok and "503" in r.error


def test_a_row_without_a_moment_says_so():
    r = clips.resolve({"id": 1, "name": "X", "role": "hitter", "best": {}})
    assert "no dated moment" in r.error


def test_report_counts_what_actually_landed():
    good = clips.Resolution(name="A", team="NYY", path="/tmp/a.mp4", bytes=2_000_000)
    bad = clips.Resolution(name="B", team="BOS", error="no play matched")
    md = clips.report([good, bad])
    assert "1/2 clips resolved" in md
    assert "no play matched" in md
    assert "2.0 MB" in md
