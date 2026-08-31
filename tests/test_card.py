"""The card's decisions, and the geometry the video depends on.

Two things are being defended here. The first is that the card says what
the site says -- same numbers, same words, same order -- because the whole
point of the design is that a reader who clicks through from a post finds
one thing rather than two. The second is the reel's geometry, which is the
only part that can be wrong in a way nobody sees until they watch the
finished video.

The render tests need Chromium and skip where it is absent, but in CI they
are not optional: the video window has to be a real hole in the PNG, the
measured rectangle has to be where the hole is, and nothing may run off
the bottom edge.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from xdawg import card, posts

ROW = {
    "rank": 1, "id": 693645, "name": "Schlittler, Cam", "role": "pitcher",
    "team": "NYY", "league": "AL", "score": 95.95, "wpa_pts": 74.8,
    "proc_pts": 21.14, "wpa": 0.329, "n": 24, "games": 1,
    "line": {"IP": "5.2", "outs": 17, "K": 8, "BB": 5, "RA9": 0.0,
             "BF": 24, "R": 0},
    "credits": {"zone_with_traffic": 22, "first_pitch_strike": 13,
                "inside_same_hand": 11, "putaway": 8, "jam_escaped": 2,
                "walk_allowed": 5},
    "best": {"date": "2026-08-28", "inning": 5, "event": "double play",
             "li": 2.58, "wpa": 0.129, "opp": "BOS"},
}
HITTER = {
    "rank": 3, "id": 518692, "name": "Freddie Freeman", "role": "hitter",
    "team": "LAD", "score": 76.49, "wpa_pts": 39.84, "proc_pts": 36.65,
    "wpa": 0.229, "n": 5, "games": 1,
    "line": {"PA": 5, "HR": 0, "BB": 1, "OPS": 1.1, "AVG": 0.5, "H": 2},
    "credits": {"extra_pitch": 7, "two_strike_foul": 6, "chase_contact": 5},
    "best": {"date": "2026-08-28", "inning": 8, "event": "single",
             "li": 1.01, "wpa": 0.233, "opp": "DET"},
}

SITE = Path(__file__).resolve().parents[1] / "site" / "awards.html"


# --------------------------------------------------------------------------
# the card and the site must agree
# --------------------------------------------------------------------------

def _site_source() -> str:
    return SITE.read_text() if SITE.exists() else ""


needs_site = pytest.mark.skipif(not SITE.exists(), reason="no site/awards.html")


@needs_site
def test_the_palette_is_the_site_s_palette():
    src = _site_source()
    for name, value in (("primary", card.PRIMARY), ("secondary", card.SECONDARY),
                        ("accent", card.ACCENT), ("muted", card.MUTED),
                        ("hair", card.HAIR), ("alt", card.ALT)):
        assert f"--{name}:{value}" in src.replace("#000;", "#000000;"), name


def test_the_extra_pitch_label_says_pitch_not_inning():
    # "pitches past the 5th" reads as the fifth inning. It means the fifth
    # pitch of the plate appearance.
    assert card.CREDIT_LABELS["extra_pitch"] == "pitches after pitch 5"


@needs_site
def test_every_credit_is_worded_the_way_the_site_words_it():
    # Two lists of thirteen phrases will drift the moment somebody edits one
    # of them. This is the thing that notices.
    src = _site_source()
    for key, label in card.CREDIT_LABELS.items():
        assert re.search(rf'{key}\s*:\s*"{re.escape(label)}"', src), key


@needs_site
def test_the_credits_read_in_the_site_s_order():
    src = _site_source()
    block = src[src.index("var CREDIT_LABELS"):src.index("var DEBITS")]
    order = re.findall(r"^\s*(\w+)\s*:", block, re.M)
    assert order == list(card.CREDIT_LABELS)


@needs_site
def test_the_same_credits_count_against_a_man_in_both_places():
    src = _site_source()
    site_debits = set(re.findall(r"(\w+): 1", src[src.index("var DEBITS"):][:120]))
    assert site_debits == card.DEBITS


@needs_site
def test_the_stat_columns_are_the_site_s_columns():
    src = _site_source()
    for role in ("hitter", "pitcher"):
        block = re.search(rf"{role}:\s*(\[\[.*?\]\])", src, re.S).group(1)
        assert re.findall(r'\["(\w+)"', block) == card.STAT_COLS[role]


# --------------------------------------------------------------------------
# numbers
# --------------------------------------------------------------------------

def test_numbers_are_always_signed_like_the_site_s():
    assert card.sgn(96.0, 1) == "+96.0"
    assert card.sgn(-0.1, 2) == "-0.10"
    assert card.sgn(0.329) == "+0.329"     # three decimals is the default
    assert card.sgn(None) == "—"
    assert card.sgn("nope") == "—"


def test_the_score_reads_as_a_signed_one_decimal_figure():
    assert "+96.0" in card.card_html(ROW)


# --------------------------------------------------------------------------
# names
# --------------------------------------------------------------------------

def test_both_spellings_of_a_name_come_out_the_same():
    assert card.display_name("Schlittler, Cam") == "CAM SCHLITTLER"
    assert card.display_name("Freddie Freeman") == "FREDDIE FREEMAN"


def test_the_surname_carries_the_weight():
    assert card.split_name("Freddie Freeman") == ("FREDDIE", "FREEMAN")
    assert card.split_name("Tatis Jr., Fernando") == ("FERNANDO", "TATIS JR.")


def test_a_one_word_name_is_all_surname():
    assert card.split_name("Ichiro") == ("", "ICHIRO")
    assert card.split_name("") == ("", "")


def test_a_name_keeps_its_own_capitals_in_prose():
    assert card.proper_name("DeJong, Paul") == "Paul DeJong"
    assert card.proper_name("Tatis Jr., Fernando") == "Fernando Tatis Jr."


# --------------------------------------------------------------------------
# the sentences
# --------------------------------------------------------------------------

def test_the_why_line_is_the_site_s_sentence_word_for_word():
    assert card.why_line(ROW) == (
        "His biggest moment was a double play in the 5th against BOS — worth "
        "+12.9 points of win probability at 2.58x leverage.")


def test_a_hitter_swings_and_a_pitcher_does_not():
    assert card.why_line(HITTER).startswith("His biggest swing was a single")
    assert card.why_line(ROW).startswith("His biggest moment was")


def test_a_moment_with_no_event_still_forms_a_sentence():
    row = dict(ROW, best={"inning": 9, "opp": "BOS"})
    assert card.why_line(row) == "His biggest moment came in the 9th against BOS."


def test_the_meta_line_is_the_site_s_meta_line():
    assert card.meta_line(ROW) == \
        "NYY · 24 trips across 1 game · +0.329 win probability added"


def test_the_meta_line_counts_in_singulars_when_it_should():
    row = dict(ROW, n=1, games=1)
    assert "1 trip across 1 game" in card.meta_line(row)


@pytest.mark.parametrize("n,want", [(1, "1st"), (2, "2nd"), (3, "3rd"),
                                    (4, "4th"), (11, "11th"), (21, "21th")])
def test_innings_use_the_site_s_ordinal_rule(n, want):
    # 21th is wrong English and right here: the site's rule is a flat lookup
    # on the whole number, and a card that says 21st beside a page that says
    # 21th is a worse bug than the one it fixes. Fix both or neither.
    assert card.ordinal_suffix(n) == want


# --------------------------------------------------------------------------
# the numbers under the name
# --------------------------------------------------------------------------

def test_a_pitcher_gets_pitching_numbers_and_a_hitter_hitting_ones():
    assert [k for k, _ in card.stat_cells(ROW["line"], "pitcher")] == \
        ["IP", "K", "BB", "RA9"]
    assert [k for k, _ in card.stat_cells(HITTER["line"], "hitter")] == \
        ["PA", "HR", "BB", "OPS"]


def test_rate_stats_are_formatted_like_a_box_score():
    assert dict(card.stat_cells(HITTER["line"], "hitter"))["OPS"] == "1.100"
    assert dict(card.stat_cells({"OPS": 0.812}, "hitter"))["OPS"] == ".812"
    assert dict(card.stat_cells(ROW["line"], "pitcher"))["RA9"] == "0.00"


def test_innings_keep_their_box_score_notation():
    # .2 is two outs, not two tenths. Josh's call; a float here would
    # silently turn 5.2 into 5.7 the first time something rounded.
    assert dict(card.stat_cells(ROW["line"], "pitcher"))["IP"] == "5.2"


def test_a_missing_stat_is_an_em_dash_not_a_blank_or_a_zero():
    assert dict(card.stat_cells({"PA": 4}, "hitter"))["HR"] == "—"


# --------------------------------------------------------------------------
# how he earned it
# --------------------------------------------------------------------------

def test_credits_read_in_the_site_s_order_not_by_size():
    rows = card.credit_rows(ROW["credits"])
    assert [r[0] for r in rows][:3] == \
        ["jams escaped", "strikes with men on", "pitches inside, same hand"]


def test_a_debit_is_marked_and_signed():
    rows = {r[0]: r for r in card.credit_rows({"walk_allowed": 5})}
    label, n, pts, debit = rows["walks allowed"]
    assert debit is True and n == "−5"


def test_a_credit_is_neither_marked_nor_signed():
    _, n, pts, debit = card.credit_rows({"putaway": 8})[0]
    assert debit is False and n == "8"


def test_a_credit_carries_the_points_it_earned():
    # Counts alone do not tell a reader whether 22 first-pitch strikes
    # mattered more than 2 jams escaped. The points do.
    rows = card.credit_rows({"jam_escaped": 2, "putaway": 8},
                            points={"jam_escaped": 12.4, "putaway": 3.1})
    by_label = {r[0]: r for r in rows}
    assert by_label["jams escaped"][2] == "+12.4"
    assert by_label["put-away strikeouts"][2] == "+3.1"


def test_a_debit_s_points_are_shown_as_the_negative_they_are():
    rows = card.credit_rows({"walk_allowed": 3}, points={"walk_allowed": -6.2})
    assert rows[0][2] == "-6.2"


def test_a_credit_with_no_points_still_renders():
    # An older board has counts and no credit_pts. The row must survive.
    label, n, pts, debit = card.credit_rows({"putaway": 8})[0]
    assert pts == "" and n == "8"


def test_the_points_reach_the_markup():
    row = dict(ROW, credit_pts={"jam_escaped": 12.4})
    assert "+12.4" in card.card_html(row)


def test_unknown_and_zero_credits_are_dropped():
    assert card.credit_rows({"vibes": 9, "putaway": 0}) == []
    assert card.credit_rows(None) == []


def test_the_list_can_be_capped_for_a_reel():
    assert len(card.credit_rows(ROW["credits"], 3)) == 3


# --------------------------------------------------------------------------
# window labels
# --------------------------------------------------------------------------

def test_the_window_label_comes_from_the_payload_when_it_is_there():
    aw = {"labels": {"week": {"2026-08-24": "August 24–30, 2026"}}}
    assert card.window_label(aw, "week", "2026-08-24") == "August 24–30, 2026"


def test_the_window_label_falls_back_to_a_derived_date():
    assert card.window_label(None, "day", "2026-08-28") == "August 28, 2026"
    assert card.window_label({}, "month", "2026-08") == "August 2026"


def test_an_unparseable_key_is_shown_rather_than_crashed_on():
    assert card.window_label(None, "day", "later") == "later"


# --------------------------------------------------------------------------
# the markup
# --------------------------------------------------------------------------

def test_only_the_reel_has_a_video_window():
    assert 'class="video"' in card.card_html(ROW, layout="reel")
    assert 'class="video"' not in card.card_html(ROW, layout="post")


def test_a_runner_up_does_not_claim_to_be_the_winner():
    assert "DAWG OF THE DAY" in card.card_html(ROW, window="day")
    assert "NO. 3 ON THE DAY" in card.card_html(HITTER, window="day")


def test_the_role_tag_uses_the_site_s_two_colours():
    assert f"background:{card.ACCENT}" in card.card_html(HITTER)      # HIT
    assert f"background:{card.SECONDARY}" in card.card_html(ROW)      # PIT


def test_a_name_with_markup_in_it_is_escaped():
    row = dict(ROW, name="<script>alert(1)</script>, Cam")
    assert "<script>alert(1)</script>" not in card.card_html(row)


def test_the_fonts_are_embedded_rather_than_linked():
    markup = card.card_html(ROW)
    assert "@font-face" in markup and "base64," in markup
    assert "http" not in markup.split("</style>")[0]


def test_no_club_colours_leak_in():
    # The site uses three colours for thirty clubs. A card that tinted
    # itself per team would look like a different product on every post.
    a = card.card_html(ROW)                       # NYY
    b = card.card_html(dict(ROW, team="BOS"))
    assert a.split("</style>")[0] == b.split("</style>")[0]


# --------------------------------------------------------------------------
# rendering, and the three facts the video depends on
# --------------------------------------------------------------------------

def _chromium() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as pw:
            pw.chromium.launch().close()
        return True
    except Exception:
        return False


needs_chromium = pytest.mark.skipif(not _chromium(), reason="no Chromium")


@needs_chromium
def test_a_post_renders_at_its_stated_size_and_has_no_window(tmp_path):
    from PIL import Image
    p, box = card.render_card(ROW, tmp_path / "p.png", layout="post")
    assert box is None
    assert Image.open(p).size == card.POST


@needs_chromium
def test_the_video_window_is_a_real_hole_in_the_reel(tmp_path):
    # If this fails the clip is invisible: ffmpeg lays it underneath, and
    # underneath an opaque card is nowhere.
    from PIL import Image
    p, box = card.render_card(ROW, tmp_path / "r.png", layout="reel")
    im = Image.open(p).convert("RGBA")
    assert im.size == card.REEL
    x, y, w, h = box
    assert im.getpixel((x + w // 2, y + h // 2))[3] == 0


@needs_chromium
def test_the_measured_window_matches_the_hole_on_every_edge(tmp_path):
    from PIL import Image
    p, box = card.render_card(ROW, tmp_path / "r.png", layout="reel")
    im = Image.open(p).convert("RGBA")
    x, y, w, h = box
    mid = x + w // 2
    assert im.getpixel((mid, y + 2))[3] == 0          # just inside the top
    assert im.getpixel((mid, y + h - 3))[3] == 0      # just inside the bottom
    assert im.getpixel((mid, y - 10))[3] == 255       # just outside
    assert im.getpixel((mid, y + h + 10))[3] == 255


@needs_chromium
def test_a_very_long_surname_does_not_move_the_window(tmp_path):
    # The window's y is what ffmpeg targets. A name long enough to wrap
    # would push it down and the clip would land over the credit list.
    _, a = card.render_card(ROW, tmp_path / "a.png", layout="reel")
    long_row = dict(ROW, name="Featherstonehaugh-Rodriguez, Bartholomew")
    _, b = card.render_card(long_row, tmp_path / "b.png", layout="reel")
    assert a == b


@needs_chromium
@pytest.mark.parametrize("layout", ["post", "reel"])
@pytest.mark.parametrize("row", [ROW, HITTER], ids=["pitcher", "hitter"])
def test_nothing_runs_off_the_bottom_edge(tmp_path, row, layout):
    # The card is overflow:hidden, so an overrun is not an error at render
    # time -- it is a footer sliced in half in a published post.
    card.render_card(row, tmp_path / "c.png", layout=layout)


@needs_chromium
def test_a_card_that_cannot_fit_drops_credits_rather_than_the_footer(tmp_path):
    # Everything at once: a long name, a full credit list, a month's worth
    # of counts. It has to come out whole.
    row = dict(ROW, name="Featherstonehaugh-Rodriguez, Bartholomew",
               score=380.1, wpa_pts=133.3, proc_pts=246.7, n=105, games=25,
               credits={k: 40 for k in card.CREDIT_LABELS})
    card.render_card(row, tmp_path / "c.png", layout="post")
    card.render_card(row, tmp_path / "r.png", layout="reel")


# --------------------------------------------------------------------------
# captions
# --------------------------------------------------------------------------

def test_a_caption_uses_the_site_s_sentence_verbatim():
    text = posts.caption(ROW, "day", "2026-08-28")
    assert card.why_line(ROW) in text


def test_a_caption_names_both_halves_of_the_score():
    text = posts.caption(ROW, "day", "2026-08-28")
    assert "+74.8 win probability · +21.1 process" in text


def test_a_caption_leads_with_the_award_and_ends_with_the_club():
    text = posts.caption(ROW, "day", "2026-08-28")
    assert text.startswith("DAWG OF THE DAY — August 28, 2026")
    assert text.rstrip().endswith("#MLB #Yankees #xDAWG")
    assert "Cam Schlittler (NYY) — +96.0 DAWG points." in text


def test_a_caption_prefers_the_payload_s_own_window_label():
    aw = {"labels": {"week": {"2026-08-24": "August 24–30, 2026"}}}
    assert "August 24–30, 2026" in posts.caption(ROW, "week", "2026-08-24", aw)


def test_a_runner_up_caption_says_which_rank():
    assert posts.caption(HITTER, "day", "2026-08-28").startswith("No. 3 —")


def test_a_caption_and_the_card_agree_about_innings_pitched():
    # Both go through stat_cells, so 5.2 can never become 5.7 in one and not
    # the other.
    assert "5.2 IP" in posts.caption(ROW, "day", "2026-08-28")


def test_a_caption_survives_a_row_with_almost_nothing_in_it():
    text = posts.caption({"name": "X Y", "team": "NYY"}, "day", "2026-08-28")
    assert "+0.0 DAWG points" in text


def test_slugs_are_safe_to_use_as_filenames():
    assert posts.slug("01", "2026-08-28", "NYY", "CAM SCHLITTLER") == \
        "01-2026-08-28-nyy-cam-schlittler"
    assert "/" not in posts.slug("a/b", "c d")


# --------------------------------------------------------------------------
# alt text
# --------------------------------------------------------------------------
# This section exists because it did not. alt_text was the one consumer of
# credit_rows with no test, so when that list grew a fourth field the unpack
# here kept saying three and nobody found out until it raised on CI in the
# middle of a live publish. Every branch of it is now walked.

def test_alt_text_reads_the_card_top_to_bottom():
    text = posts.alt_text(ROW, "day", "2026-08-29")
    assert text.startswith("xDAWG card. DAWG OF THE DAY, August 29, 2026.")
    assert "Cam Schlittler, NYY pitcher" in text
    assert "+96.0 DAWG points (+74.8 win probability, +21.1 process)" in text


def test_alt_text_carries_the_stat_line_and_the_credits():
    # Both are on the card and in no other part of the post, so a reader
    # using alt text gets them here or not at all.
    text = posts.alt_text(ROW, "day", "2026-08-29")
    assert "5.2 IP" in text and "8 K" in text
    assert "How he earned it: " in text
    assert "2 jams escaped" in text


def test_alt_text_describes_the_best_moment_in_the_site_s_words():
    assert card.why_line(ROW) in posts.alt_text(ROW, "day", "2026-08-29")


def test_alt_text_works_for_a_hitter_too():
    text = posts.alt_text(HITTER, "day", "2026-08-29")
    assert "Freddie Freeman, LAD hitter" in text
    assert "5 PA" in text


def test_alt_text_survives_a_row_with_almost_nothing_in_it():
    # No line, no credits, no best moment: three branches skipped at once.
    text = posts.alt_text({"name": "X Y", "team": "NYY"}, "day", "2026-08-29")
    assert text.startswith("xDAWG card.")
    assert "How he earned it" not in text


def test_alt_text_takes_the_payload_s_own_window_label():
    aw = {"labels": {"week": {"2026-08-24": "August 24–30, 2026"}}}
    assert "August 24–30, 2026" in posts.alt_text(ROW, "week", "2026-08-24", aw)


def test_credit_rows_are_read_by_name_not_by_position():
    # The durable half of the fix: the row is a NamedTuple, so a consumer
    # that asks for .label keeps working when a field is added, and a stray
    # `for a, b, c in` is a test failure here rather than a live one.
    row = card.credit_rows({"jam_escaped": 2}, points={"jam_escaped": 7.5})[0]
    assert (row.label, row.count, row.points, row.debit) == tuple(row)
    assert row.label == "jams escaped"


# --------------------------------------------------------------------------
# the board's vintage
# --------------------------------------------------------------------------
# A payload written before a field existed renders every card fine, just
# with that column blank. Nothing raises, nothing logs, and the detector is
# a human squinting at a finished graphic. So it is checked out loud.

def test_a_board_without_per_credit_points_says_so():
    rows = [dict(ROW), dict(HITTER)]          # counts, no credit_pts
    gaps = posts.board_gaps(rows, "2026-08-30 12:47 UTC")
    assert len(gaps) == 1
    assert "blank points column" in gaps[0]
    assert "2026-08-30 12:47 UTC" in gaps[0]
    assert "nightly refresh" in gaps[0]


def test_a_current_board_is_quiet():
    rows = [dict(ROW, credit_pts={"jam_escaped": 12.4}), dict(HITTER)]
    assert posts.board_gaps(rows) == []


def test_a_board_with_no_credits_at_all_is_not_a_gap():
    # Nothing to be missing. A complaint here would be noise on every
    # window that happens to have thin rows.
    assert posts.board_gaps([{"name": "X Y"}]) == []
    assert posts.board_gaps([]) == []
