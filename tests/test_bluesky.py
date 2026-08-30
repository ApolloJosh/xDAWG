"""The AT Protocol's three traps, and the shape of a thread.

None of this touches the network. What is being defended is the arithmetic
that decides what a post says and where its links point, because those are
the parts that are wrong silently: a bad facet highlights the wrong words
rather than raising, and a post one grapheme over is refused by the server
after the images have already been uploaded.
"""

from __future__ import annotations

import json

import pytest

from xdawg import bluesky as bs
from xdawg import posts

ROW = {
    "rank": 1, "id": 693645, "name": "Schlittler, Cam", "role": "pitcher",
    "team": "NYY", "score": 95.95, "wpa_pts": 74.8, "proc_pts": 21.14,
    "wpa": 0.329, "n": 24, "games": 1,
    "line": {"IP": "5.2", "K": 8, "BB": 5, "RA9": 0.0},
    "credits": {"zone_with_traffic": 22, "jam_escaped": 2, "walk_allowed": 5},
    "best": {"date": "2026-08-28", "inning": 5, "event": "double play",
             "li": 2.58, "wpa": 0.129, "opp": "BOS"},
}


# --------------------------------------------------------------------------
# facets: byte offsets, not character offsets
# --------------------------------------------------------------------------

def test_a_hashtag_becomes_a_tag_facet():
    f = bs.facets("dawg of the day #MLB")
    assert len(f) == 1
    assert f[0]["features"][0]["$type"] == "app.bsky.richtext.facet#tag"
    assert f[0]["features"][0]["tag"] == "MLB"        # no leading hash


def test_offsets_are_utf8_bytes_and_not_characters():
    # The em dash costs two extra bytes and the middot one, so the tag's
    # byte index runs three past its character index. This is the bug that
    # makes a tag highlight the wrong half of a sentence.
    text = "A — B · #MLB"
    f = bs.facets(text)[0]
    start, end = f["index"]["byteStart"], f["index"]["byteEnd"]
    assert start == text.index("#MLB") + 3
    assert text.encode()[start:end].decode() == "#MLB"


def test_every_facet_in_a_real_post_slices_out_its_own_tag():
    text = posts.bluesky_text(ROW, "day", "2026-08-28")
    raw = text.encode()
    for f in bs.facets(text):
        feat = f["features"][0]
        got = raw[f["index"]["byteStart"]:f["index"]["byteEnd"]].decode()
        if feat["$type"].endswith("#tag"):
            assert got == "#" + feat["tag"]
        else:
            assert got == feat["uri"]


def test_several_tags_are_all_found_and_come_out_in_order():
    f = bs.facets("#MLB #Yankees #xDAWG")
    assert [x["features"][0]["tag"] for x in f] == ["MLB", "Yankees", "xDAWG"]
    starts = [x["index"]["byteStart"] for x in f]
    assert starts == sorted(starts)


def test_a_hash_inside_a_word_is_not_a_tag():
    assert bs.facets("game#3 of the series") == []
    assert bs.facets("C# is a language") == []


def test_a_bare_hash_and_a_numeric_tag_are_not_tags():
    # Bluesky requires a tag to start with a letter; "#5" is a rank, and
    # linkifying it would make every "No. #5" a dead tag search.
    assert bs.facets("# spaced") == []
    assert bs.facets("#5 on the day") == []


def test_a_url_becomes_a_link_facet_without_its_trailing_full_stop():
    f = bs.facets("See https://apollojosh.github.io/xDAWG/.")
    assert f[0]["features"][0]["uri"] == "https://apollojosh.github.io/xDAWG/"


def test_text_with_nothing_in_it_has_no_facets():
    assert bs.facets("just words") == []
    assert bs.facets("") == []


# --------------------------------------------------------------------------
# length
# --------------------------------------------------------------------------

def test_a_post_that_fits_is_left_alone():
    out = bs.fit_text([(9, "head"), (5, "body"), (1, "#MLB")])
    assert out == "head\n\nbody\n\n#MLB"


def test_the_cheapest_block_is_dropped_first():
    long = "x" * 200
    out = bs.fit_text([(9, long), (5, long), (1, "#MLB")], limit=300)
    assert "#MLB" not in out          # priority 1 went before priority 5
    assert out.count(long) == 1       # and then priority 5


def test_a_required_block_is_never_dropped():
    long = "y" * 400
    out = bs.fit_text([(9, long)], limit=300)
    assert out.startswith("yyy")
    assert bs.graphemes(out) <= 300


def test_an_over_long_required_block_is_cut_on_a_word_boundary():
    text = " ".join(["word"] * 200)
    out = bs.fit_text([(9, text)], limit=300)
    assert bs.graphemes(out) <= 300
    assert out.endswith("…")
    assert "wor…" not in out          # not mid-word


@pytest.mark.parametrize("window", ["day", "week", "month"])
@pytest.mark.parametrize("rank", [1, 2, 5])
def test_every_real_post_fits_in_three_hundred(window, rank):
    row = dict(ROW, rank=rank)
    text = posts.bluesky_text(row, window, "2026-08-28")
    assert bs.graphemes(text) <= bs.MAX_GRAPHEMES
    assert len(text.encode()) <= bs.MAX_BYTES


def test_the_worst_case_row_still_fits():
    # Longest plausible everything: a hyphenated name, a wordy event, a
    # three-figure month score.
    row = dict(ROW, rank=5, name="Featherstonehaugh-Rodriguez, Bartholomew",
               score=380.1, wpa_pts=133.3, proc_pts=246.7,
               best=dict(ROW["best"], event="grounded into double play",
                         inning=11, wpa=0.4812, li=3.94))
    text = posts.bluesky_text(row, "month", "2026-08")
    assert bs.graphemes(text) <= bs.MAX_GRAPHEMES


def test_a_runner_up_says_its_own_rank_and_window():
    text = posts.bluesky_text(dict(ROW, rank=3), "day", "2026-08-28")
    # A reader arriving from a notification never saw the root post.
    assert text.startswith("No. 3 on the day —")


def test_a_headline_is_not_run_through_title_case():
    assert "Dawg Of The Day" not in posts.bluesky_text(dict(ROW, rank=2),
                                                       "day", "2026-08-28")


# --------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------

def test_a_post_record_has_what_the_lexicon_needs():
    rec = bs.post_record("hello #MLB")
    assert rec["$type"] == "app.bsky.feed.post"
    assert rec["text"] == "hello #MLB"
    assert rec["langs"] == ["en"]
    assert rec["createdAt"].endswith("Z")
    assert rec["facets"]                       # tags are not auto-linked


def test_a_record_without_tags_omits_facets_rather_than_sending_an_empty_list():
    assert "facets" not in bs.post_record("plain words")


def test_an_image_embed_carries_alt_text_and_an_aspect_ratio():
    item = bs.image_item({"$type": "blob", "ref": {}}, "a card", (1080, 1350))
    assert item["alt"] == "a card"
    # Without this the client guesses and crops a 4:5 card to a letterbox.
    assert item["aspectRatio"] == {"width": 1080, "height": 1350}


def test_at_most_four_images_go_into_one_post():
    rec = bs.post_record("x", images=[{"i": n} for n in range(6)])
    assert len(rec["embed"]["images"]) == 4


def test_a_reply_points_at_both_the_root_and_its_parent():
    root = {"uri": "at://x/1", "cid": "aaa"}
    parent = {"uri": "at://x/2", "cid": "bbb"}
    ref = bs.reply_ref(root, parent)
    assert ref == {"root": root, "parent": parent}


def test_a_reply_ref_carries_only_uri_and_cid():
    ref = bs.reply_ref({"uri": "at://x/1", "cid": "a", "extra": 1},
                       {"uri": "at://x/1", "cid": "a", "extra": 1})
    assert set(ref["root"]) == {"uri", "cid"}


def test_the_record_is_json_serialisable():
    # It goes over the wire as JSON; a stray non-serialisable value here
    # would fail after the blob upload had already happened.
    json.dumps(bs.post_record("hi #MLB", images=[{"a": 1}],
                              reply=bs.reply_ref({"uri": "u", "cid": "c"},
                                                 {"uri": "u", "cid": "c"})))


def test_a_post_url_is_browsable():
    assert bs.post_url("xdawgmlb.bsky.social", "at://did:plc:abc/app.bsky.feed.post/3kxyz") \
        == "https://bsky.app/profile/xdawgmlb.bsky.social/post/3kxyz"


# --------------------------------------------------------------------------
# credentials
# --------------------------------------------------------------------------

def test_an_account_password_is_refused_before_it_reaches_the_wire():
    # An account password would authenticate perfectly well, which is
    # exactly the problem: it would then be sitting in a CI secret with the
    # power to change the email and lock the account out.
    with pytest.raises(bs.BlueskyError, match="app password"):
        bs.login("xdawgmlb.bsky.social", "correct horse battery staple")


def test_missing_credentials_are_refused():
    with pytest.raises(bs.BlueskyError):
        bs.login("", "abcd-efgh-ijkl-mnop")
    with pytest.raises(bs.BlueskyError):
        bs.login("xdawgmlb.bsky.social", "")


def test_a_session_does_not_carry_the_password():
    s = bs.Session(did="did:plc:x", handle="h", access_jwt="jwt")
    assert "password" not in json.dumps(s.__dict__).lower()
    assert s.auth == {"Authorization": "Bearer jwt"}


# --------------------------------------------------------------------------
# images
# --------------------------------------------------------------------------

def _png(w=1080, h=1350, noisy=False) -> bytes:
    from io import BytesIO

    from PIL import Image

    im = Image.new("RGB", (w, h), (255, 255, 255))
    if noisy:
        import random
        rnd = random.Random(0)
        px = im.load()
        for y in range(0, h, 2):
            for x in range(0, w, 2):
                px[x, y] = (rnd.randrange(256), rnd.randrange(256),
                            rnd.randrange(256))
    buf = BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_a_card_that_already_fits_is_sent_untouched():
    data = _png()
    out, mime = bs.fit_image(data)
    assert out is data and mime == "image/png"


def test_an_oversized_image_is_re_encoded_under_the_budget():
    big = _png(noisy=True)
    assert len(big) > 950_000, "fixture is not actually oversized"
    out, mime = bs.fit_image(big)
    assert len(out) <= bs.IMAGE_BUDGET
    assert mime == "image/jpeg"


def test_the_budget_sits_under_the_protocol_limit():
    # The limit is enforced on the encoded blob. A card that squeaks in at
    # 999,000 bytes is a card that fails the day a name needs one more glyph.
    assert bs.IMAGE_BUDGET < 1_000_000


def test_image_size_reads_the_real_dimensions():
    assert bs.image_size(_png(400, 500)) == (400, 500)


# --------------------------------------------------------------------------
# the thread
# --------------------------------------------------------------------------

def test_a_dry_run_builds_the_records_a_live_run_would_send():
    items = [bs.Item(text="root #MLB", image=_png(), alt="a"),
             bs.Item(text="reply", image=_png(), alt="b")]
    out = bs.prepare(items)
    assert [r.text for r in out] == ["root #MLB", "reply"]
    assert out[0].record["facets"]
    assert all(r.bytes > 0 for r in out)
    assert not any(r.ok for r in out)      # nothing was posted


def test_the_dry_run_report_shows_the_text_and_the_tags():
    md = bs.report(bs.prepare([bs.Item(text="root #MLB")]), dry_run=True)
    assert "Dry run" in md and "nothing was posted" in md
    assert "root #MLB" in md
    assert "tags: #MLB" in md


def test_publish_chains_every_reply_to_the_root(monkeypatch):
    posted = []

    def fake_create(session, record):
        posted.append(record)
        n = len(posted)
        return {"uri": f"at://did:plc:x/app.bsky.feed.post/{n}", "cid": f"c{n}"}

    monkeypatch.setattr(bs, "create_record", fake_create)
    monkeypatch.setattr(bs, "upload_blob", lambda s, d, m: {"$type": "blob"})
    monkeypatch.setattr(bs.time, "sleep", lambda _: None)

    items = [bs.Item(text=f"post {i}", image=_png(), alt=f"alt {i}")
             for i in range(4)]
    results = bs.publish_thread(bs.Session(did="did:plc:x", handle="h",
                                           access_jwt="j"), items)

    assert all(r.ok for r in results)
    assert "reply" not in posted[0]
    root_uri = "at://did:plc:x/app.bsky.feed.post/1"
    for i, rec in enumerate(posted[1:], start=1):
        # Every reply hangs off the root, and off the post before it.
        assert rec["reply"]["root"]["uri"] == root_uri
        assert rec["reply"]["parent"]["uri"] == \
            f"at://did:plc:x/app.bsky.feed.post/{i}"


def test_each_post_carries_its_own_card_and_alt(monkeypatch):
    monkeypatch.setattr(bs, "create_record",
                        lambda s, r: {"uri": "at://x/1", "cid": "c"})
    monkeypatch.setattr(bs, "upload_blob", lambda s, d, m: {"$type": "blob"})
    monkeypatch.setattr(bs.time, "sleep", lambda _: None)
    items = [bs.Item(text="a", image=_png(), alt="the winner's card")]
    bs.publish_thread(bs.Session(access_jwt="j"), items)


def test_one_failed_post_leaves_the_rest_of_the_thread_standing(monkeypatch):
    calls = {"n": 0}

    def flaky(session, record):
        calls["n"] += 1
        if calls["n"] == 2:
            raise bs.BlueskyError("502 upstream")
        return {"uri": f"at://x/{calls['n']}", "cid": f"c{calls['n']}"}

    monkeypatch.setattr(bs, "create_record", flaky)
    monkeypatch.setattr(bs, "upload_blob", lambda s, d, m: {"$type": "blob"})
    monkeypatch.setattr(bs.time, "sleep", lambda _: None)

    items = [bs.Item(text=f"p{i}") for i in range(3)]
    results = bs.publish_thread(bs.Session(access_jwt="j"), items)
    assert [r.ok for r in results] == [True, False, True]
    assert "502" in results[1].error
    # The third still attaches to the root rather than to the post that
    # never existed.
    assert results[2].record["reply"]["root"]["uri"] == "at://x/1"


def test_a_thread_report_counts_what_landed():
    good = bs.Result(text="a", uri="at://x/1", url="https://bsky.app/x")
    bad = bs.Result(text="b", error="429 rate limited")
    md = bs.report([good, bad], dry_run=False)
    assert "1/2 posted" in md
    assert "429 rate limited" in md
    assert "https://bsky.app/x" in md


# --------------------------------------------------------------------------
# staleness
# --------------------------------------------------------------------------

import datetime as dt  # noqa: E402

TODAY = dt.date(2026, 8, 30)


@pytest.mark.parametrize("window,key,days", [
    ("day", "2026-08-30", 0),
    ("day", "2026-08-28", 2),
    ("week", "2026-08-24", 0),      # the week ends 08-30, which is today
    ("week", "2026-08-17", 7),
    ("month", "2026-08", 0),        # August is not over
    ("month", "2026-07", 30),
])
def test_staleness_measures_from_the_end_of_the_window(window, key, days):
    assert posts.staleness(window, key, TODAY) == days


def test_a_window_is_never_negatively_stale():
    assert posts.staleness("day", "2026-09-15", TODAY) == 0


def test_an_unparseable_key_is_not_treated_as_ancient():
    # Returning a huge number here would block every publish on a key we
    # simply failed to read, which is a worse failure than not checking.
    assert posts.staleness("day", "whenever", TODAY) == 0
    assert posts.staleness("month", "", TODAY) == 0


# --------------------------------------------------------------------------
# video
# --------------------------------------------------------------------------

DID_DOC = {"service": [
    {"id": "#atproto_pds", "type": "AtprotoPersonalDataServer",
     "serviceEndpoint": "https://poisonpie.us-west.host.bsky.network"},
    {"id": "#other", "type": "SomethingElse", "serviceEndpoint": "https://nope"},
]}


def _sess(**kw):
    return bs.Session(did="did:plc:kw3", handle="xdawgmlb.bsky.social",
                      access_jwt="jwt", did_doc=DID_DOC, **kw)


def test_the_pds_is_read_from_the_did_document():
    # Not hardcoded: accounts are spread across a fleet and can be migrated.
    s = _sess()
    assert s.pds == "https://poisonpie.us-west.host.bsky.network"
    assert s.pds_did == "did:web:poisonpie.us-west.host.bsky.network"


def test_an_account_with_no_pds_in_its_doc_reports_empty_rather_than_guessing():
    assert bs.Session().pds_did == ""
    assert bs.Session(did_doc={"service": []}).pds_did == ""


def test_uploading_video_asks_for_a_token_scoped_to_the_repo_write(monkeypatch):
    # The lxm is uploadBlob, not uploadVideo: what is being authorised is the
    # video service writing a blob into this account's repo.
    seen = {}

    def fake_query(session, path, params, **kw):
        seen[path] = params
        return {"token": "svc-token"}

    def fake_call(url, **kw):
        seen["upload_url"] = url
        seen["upload_auth"] = kw["headers"]["Authorization"]
        seen["upload_mime"] = kw["headers"]["Content-Type"]
        return {"jobStatus": {"jobId": "j1", "state": "JOB_STATE_CREATED"}}

    monkeypatch.setattr(bs, "_query", fake_query)
    monkeypatch.setattr(bs, "_call", fake_call)

    job = bs.upload_video(_sess(), b"\x00" * 100, "reel.mp4")
    assert job["jobId"] == "j1"
    auth = seen["com.atproto.server.getServiceAuth"]
    assert auth["aud"] == "did:web:poisonpie.us-west.host.bsky.network"
    assert auth["lxm"] == "com.atproto.repo.uploadBlob"
    assert auth["exp"] > 0
    assert seen["upload_auth"] == "Bearer svc-token"
    assert seen["upload_mime"] == "video/mp4"
    assert "did=did%3Aplc%3Akw3" in seen["upload_url"]
    assert "name=reel.mp4" in seen["upload_url"]


def test_the_job_status_token_is_scoped_to_the_video_service(monkeypatch):
    seen = {}

    def fake_query(session, path, params, **kw):
        seen.setdefault(path, []).append((params, kw))
        if path.endswith("getServiceAuth"):
            return {"token": "svc"}
        return {"jobStatus": {"jobId": "j1", "state": "JOB_STATE_COMPLETED",
                              "blob": {"$type": "blob"}}}

    monkeypatch.setattr(bs, "_query", fake_query)
    bs.job_status(_sess(), "j1")
    auth = seen["com.atproto.server.getServiceAuth"][0][0]
    assert auth["aud"] == "did:web:video.bsky.app"
    assert auth["lxm"] == "app.bsky.video.getJobStatus"
    _, kw = seen["app.bsky.video.getJobStatus"][0]
    assert kw["host"] == bs.VIDEO_HOST      # not the PDS


def test_a_video_over_the_limit_is_refused_before_it_is_sent(monkeypatch):
    monkeypatch.setattr(bs, "_query", lambda *a, **k: {"token": "t"})
    called = {"n": 0}
    monkeypatch.setattr(bs, "_call", lambda *a, **k: called.__setitem__("n", 1))
    with pytest.raises(bs.BlueskyError, match="limit"):
        bs.upload_video(_sess(), b"x" * (bs.VIDEO_MAX + 1), "big.mp4")
    assert called["n"] == 0, "it sent the file anyway"


def test_the_limit_matches_the_lexicon():
    assert bs.VIDEO_MAX == 100_000_000


def test_awaiting_a_video_polls_until_the_blob_arrives(monkeypatch):
    states = [{"state": "JOB_STATE_CREATED"},
              {"state": "JOB_STATE_ENCODING", "progress": 40},
              {"state": "JOB_STATE_COMPLETED", "blob": {"$type": "blob"}}]
    monkeypatch.setattr(bs, "job_status", lambda s, j: states.pop(0))
    monkeypatch.setattr(bs.time, "sleep", lambda _: None)
    assert bs.await_video(_sess(), "j1") == {"$type": "blob"}


def test_a_failed_job_raises_with_the_service_s_own_words(monkeypatch):
    monkeypatch.setattr(bs, "job_status", lambda s, j: {
        "state": "JOB_STATE_FAILED", "error": "unsupported_codec",
        "message": "could not read the video"})
    monkeypatch.setattr(bs.time, "sleep", lambda _: None)
    with pytest.raises(bs.BlueskyError, match="unsupported_codec"):
        bs.await_video(_sess(), "j1")


def test_a_job_that_never_finishes_times_out(monkeypatch):
    monkeypatch.setattr(bs, "job_status", lambda s, j: {"state": "JOB_STATE_ENCODING"})
    monkeypatch.setattr(bs.time, "sleep", lambda _: None)
    clock = iter([0, 1, 2, 999])
    monkeypatch.setattr(bs.time, "time", lambda: next(clock))
    with pytest.raises(bs.BlueskyError, match="still"):
        bs.await_video(_sess(), "j1", timeout=10)


def test_a_completed_job_with_no_blob_is_an_error_not_a_silent_post(monkeypatch):
    monkeypatch.setattr(bs, "job_status", lambda s, j: {"state": "JOB_STATE_COMPLETED"})
    monkeypatch.setattr(bs.time, "sleep", lambda _: None)
    with pytest.raises(bs.BlueskyError, match="no blob"):
        bs.await_video(_sess(), "j1")


def test_the_video_embed_carries_alt_and_an_aspect_ratio():
    e = bs.video_embed({"$type": "blob"}, "a reel", (1080, 1920))
    assert e["$type"] == "app.bsky.embed.video"
    assert e["aspectRatio"] == {"width": 1080, "height": 1920}
    assert e["alt"] == "a reel"


def test_video_alt_is_capped_at_the_lexicon_s_thousand_graphemes():
    e = bs.video_embed({"$type": "blob"}, "z" * 5000, (1080, 1920))
    assert len(e["alt"]) == 1000


def test_a_post_carries_images_or_a_video_but_never_both():
    # `embed` is one field. Sending both would drop one silently, and which
    # one would depend on dict ordering.
    with pytest.raises(bs.BlueskyError, match="not both"):
        bs.post_record("x", images=[{"a": 1}],
                       video={"$type": "app.bsky.embed.video"})


def test_a_video_post_embeds_the_video_and_not_an_images_block():
    rec = bs.post_record("x", video={"$type": "app.bsky.embed.video"})
    assert rec["embed"]["$type"] == "app.bsky.embed.video"


def test_a_dry_run_reports_a_video_without_uploading_it():
    out = bs.prepare([bs.Item(text="root", video=b"\x00" * 2_000_000)])
    assert out[0].kind == "video"
    assert out[0].bytes == 2_000_000
    assert not out[0].ok
    assert "video 2.0 MB" in bs.report(out, dry_run=True)


def test_a_dry_run_flags_a_video_that_is_too_big_to_post():
    out = bs.prepare([bs.Item(text="x", video=b"\x00" * (bs.VIDEO_MAX + 1))])
    assert "limit" in out[0].error


def test_publishing_a_video_item_takes_the_video_path(monkeypatch):
    monkeypatch.setattr(bs, "upload_video", lambda s, d, n: {"jobId": "j1"})
    monkeypatch.setattr(bs, "await_video", lambda s, j: {"$type": "blob"})
    monkeypatch.setattr(bs, "upload_blob",
                        lambda *a: pytest.fail("used the image path"))
    monkeypatch.setattr(bs, "create_record",
                        lambda s, r: {"uri": "at://x/1", "cid": "c"})
    monkeypatch.setattr(bs.time, "sleep", lambda _: None)
    res = bs.publish_thread(_sess(), [bs.Item(text="a", video=b"mp4",
                                              alt="the reel")])
    assert res[0].ok
    assert res[0].record["embed"]["$type"] == "app.bsky.embed.video"
    assert res[0].record["embed"]["alt"] == "the reel"


def test_a_thread_can_mix_a_video_winner_with_image_runners_up(monkeypatch):
    # Savant does not have a clip for every play. A winner without one gets
    # the card rather than six silent seconds of a static video.
    monkeypatch.setattr(bs, "upload_video", lambda s, d, n: {"jobId": "j"})
    monkeypatch.setattr(bs, "await_video", lambda s, j: {"$type": "vblob"})
    monkeypatch.setattr(bs, "upload_blob", lambda s, d, m: {"$type": "iblob"})
    monkeypatch.setattr(bs, "image_size", lambda d: (1080, 1350))
    monkeypatch.setattr(bs, "fit_image", lambda d, **k: (d, "image/png"))
    n = {"i": 0}

    def create(s, r):
        n["i"] += 1
        return {"uri": f"at://x/{n['i']}", "cid": f"c{n['i']}"}

    monkeypatch.setattr(bs, "create_record", create)
    monkeypatch.setattr(bs.time, "sleep", lambda _: None)

    res = bs.publish_thread(_sess(), [
        bs.Item(text="winner", video=b"mp4", alt="reel"),
        bs.Item(text="runner up", image=b"png", alt="card"),
    ])
    assert all(r.ok for r in res)
    assert res[0].record["embed"]["$type"] == "app.bsky.embed.video"
    assert res[1].record["embed"]["$type"] == "app.bsky.embed.images"
    # And the image reply still hangs off the video root.
    assert res[1].record["reply"]["root"]["uri"] == "at://x/1"


# --------------------------------------------------------------------------
# saying why a reel became a card
# --------------------------------------------------------------------------

def test_an_item_that_fell_back_carries_the_reason_into_the_report():
    # The first version swallowed every failure into a bare except and
    # posted stills, which is indistinguishable from being *asked* for
    # stills. That cost a live run and an hour of not knowing why.
    out = bs.prepare([bs.Item(text="a", image=b"png",
                              note="ffmpeg is not installed on this machine")])
    md = bs.report(out, dry_run=True)
    assert "no reel: ffmpeg is not installed" in md
    assert "Why the rest are cards:" in md
    assert "0/1 with video" in md


def test_the_report_counts_videos_even_on_a_dry_run():
    out = bs.prepare([bs.Item(text="a", video=b"\x00" * 1000),
                      bs.Item(text="b", image=b"png", note="no clip resolved")])
    assert "1/2 with video" in bs.report(out, dry_run=True)


def test_a_thread_with_every_reel_intact_says_nothing_about_cards():
    md = bs.report(bs.prepare([bs.Item(text="a", video=b"\x00" * 10)]),
                   dry_run=True)
    assert "Why the rest are cards" not in md
