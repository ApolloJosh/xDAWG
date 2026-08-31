"""Posting to Bluesky, over the AT Protocol's XRPC endpoints directly.

No SDK. The whole surface we need is four HTTP calls -- create a session,
upload a blob, create a record, and repeat the last one with a reply ref --
and a dependency that wraps them would still leave us writing the facet
arithmetic and the length budget ourselves, which is where the actual bugs
live.

Authentication is an **app password**, never the account password. Bluesky
issues them per-application at Settings -> App Passwords, they cannot change
the account's own password or email, and revoking one does not disturb
anything else. It belongs in a secret store; nothing here ever logs it.

Three things about this API bite, and all three are tested:

  Length is 300 *graphemes*, not characters and not bytes. Our captions run
  to 290 without trying, so a post is assembled from prioritised blocks and
  the low-priority ones are dropped until it fits, rather than being written
  hopefully and truncated mid-word.

  Facets carry UTF-8 **byte** offsets, not character offsets. The captions
  contain "·" (2 bytes) and "—" (3 bytes), so every hashtag after the first
  em dash sits at a byte index well past its character index. Get this wrong
  and the tag highlights the wrong slice of the sentence.

  A hashtag is not a link unless you say so. Bluesky does no auto-linking at
  all: "#MLB" with no facet is three inert characters.

Nothing here is reachable from the development sandbox -- the egress proxy
refuses bsky.social along with everything else -- so the pure half is
generous and the network half is four thin wrappers.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

HOST = "https://bsky.social"
COLLECTION = "app.bsky.feed.post"

# Video does not go to the PDS like an image does. It goes to a separate
# service that transcodes it and hands back a blob ref, and reaching that
# service needs a *service auth* token rather than the session's own -- a
# short-lived JWT scoped to one audience and one method. See upload_video.
VIDEO_HOST = "https://video.bsky.app"
VIDEO_DID = "did:web:video.bsky.app"
VIDEO_MAX = 100_000_000      # app.bsky.embed.video, video.maxSize
VIDEO_MAX_SECONDS = 180

# app.bsky.embed.images caps a blob at 1,000,000 bytes. The budget here is
# under it on purpose: the limit is enforced on the encoded blob and a card
# that squeaks in at 999,000 is a card that fails the day somebody's name
# needs one more glyph.
IMAGE_BUDGET = 950_000
MAX_IMAGES = 4
MAX_GRAPHEMES = 300
MAX_BYTES = 3000


class BlueskyError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# text: length, facets
# --------------------------------------------------------------------------

def graphemes(text: str) -> int:
    """How Bluesky counts a post's length.

    True grapheme segmentation needs a Unicode library. This counts code
    points, which is the same number for everything we post -- ASCII plus a
    handful of punctuation -- and is an *over*count for emoji sequences,
    which fails safe: a post is rejected as too long rather than accepted
    and truncated by the server.
    """
    return len(text)


TAG_RE = re.compile(r"(?:^|(?<=\s))#([A-Za-z][A-Za-z0-9_]{0,63})")
URL_RE = re.compile(r"https?://[^\s\]<>]+")


def facets(text: str) -> list[dict]:
    """Rich-text ranges for the hashtags and links in `text`.

    byteStart/byteEnd are indices into the UTF-8 encoding of the whole post,
    which is why every offset here is measured by encoding the prefix rather
    than by counting characters. The captions carry multi-byte punctuation,
    so the two numbers diverge in the first line.
    """
    out = []
    for m in TAG_RE.finditer(text):
        out.append({
            "index": {"byteStart": len(text[:m.start()].encode()),
                      "byteEnd": len(text[:m.end()].encode())},
            "features": [{"$type": "app.bsky.richtext.facet#tag",
                          "tag": m.group(1)}],
        })
    for m in URL_RE.finditer(text):
        # Trailing punctuation belongs to the sentence, not the URL.
        uri = m.group(0).rstrip(".,;:!?)")
        end = m.start() + len(uri)
        out.append({
            "index": {"byteStart": len(text[:m.start()].encode()),
                      "byteEnd": len(text[:end].encode())},
            "features": [{"$type": "app.bsky.richtext.facet#link",
                          "uri": uri}],
        })
    return sorted(out, key=lambda f: f["index"]["byteStart"])


def fit_text(blocks: list[tuple[int, str]], limit: int = MAX_GRAPHEMES) -> str:
    """Assemble a post from prioritised blocks, dropping the cheap ones.

    `blocks` is [(priority, text)] in reading order, lower priority dropped
    first. Blocks are joined with a blank line. A post that will not fit
    even with everything optional gone is truncated on a word boundary --
    but that is the backstop, not the mechanism.
    """
    kept = list(blocks)
    while kept:
        text = "\n\n".join(t for _, t in kept if t).strip()
        if graphemes(text) <= limit and len(text.encode()) <= MAX_BYTES:
            return text
        worst = max(range(len(kept)), key=lambda i: (-kept[i][0], i))
        if kept[worst][0] >= 9:          # required; nothing left to drop
            break
        kept.pop(worst)

    text = "\n\n".join(t for _, t in kept if t).strip()
    if graphemes(text) <= limit:
        return text
    cut = text[:limit - 1]
    if " " in cut[limit // 2:]:
        cut = cut[:cut.rstrip().rfind(" ")]
    return cut.rstrip() + "…"


# --------------------------------------------------------------------------
# images
# --------------------------------------------------------------------------

def fit_image(data: bytes, budget: int = IMAGE_BUDGET) -> tuple[bytes, str]:
    """Get a card under the blob limit, changing it as little as possible.

    A card is flat colour over white and PNG-compresses to about 140 KB, so
    the common case returns the bytes untouched. The ladder below only
    matters for a card carrying a photograph, and it steps down quality
    before it steps down resolution -- a soft card the right size beats a
    crisp card scaled to 640px.
    """
    if len(data) <= budget:
        return data, "image/png"

    from io import BytesIO

    from PIL import Image

    im = Image.open(BytesIO(data))
    if im.mode in ("RGBA", "LA", "P"):
        flat = Image.new("RGB", im.size, (255, 255, 255))
        im = im.convert("RGBA")
        flat.paste(im, mask=im.split()[-1])
        im = flat
    for quality in (92, 86, 80, 72, 64):
        buf = BytesIO()
        im.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
        if buf.tell() <= budget:
            return buf.getvalue(), "image/jpeg"
    for scale in (0.85, 0.7, 0.55):
        small = im.resize((int(im.width * scale), int(im.height * scale)),
                          Image.LANCZOS)
        buf = BytesIO()
        small.save(buf, "JPEG", quality=80, optimize=True, progressive=True)
        if buf.tell() <= budget:
            return buf.getvalue(), "image/jpeg"
    raise BlueskyError(f"cannot get this image under {budget} bytes")


def image_size(data: bytes) -> tuple[int, int]:
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(data)) as im:
        return im.size


# --------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------

def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def post_record(text: str, *, images: list[dict] | None = None,
                video: dict | None = None, reply: dict | None = None,
                langs: tuple[str, ...] = ("en",),
                created_at: str | None = None) -> dict:
    """The app.bsky.feed.post record. Pure -- builds it, sends nothing."""
    if images and video:
        # `embed` is one field, not a list of them. Sending both would
        # silently drop one, and which one would depend on dict ordering.
        raise BlueskyError("a post carries images or a video, not both")
    rec: dict = {
        "$type": COLLECTION,
        "text": text,
        "createdAt": created_at or now_iso(),
        "langs": list(langs),
    }
    f = facets(text)
    if f:
        rec["facets"] = f
    if video:
        rec["embed"] = video
    elif images:
        rec["embed"] = {"$type": "app.bsky.embed.images",
                        "images": images[:MAX_IMAGES]}
    if reply:
        rec["reply"] = reply
    return rec


def image_item(blob: dict, alt: str, size: tuple[int, int]) -> dict:
    """One entry in an images embed.

    aspectRatio is not decoration: without it the client guesses, and a 4:5
    card gets cropped to a letterbox in the feed with the footer cut off.
    """
    w, h = size
    return {"alt": alt[:1000], "image": blob,
            "aspectRatio": {"width": int(w), "height": int(h)}}


def reply_ref(root: dict, parent: dict) -> dict:
    """A reply points at both its parent and the thread root, always."""
    keys = ("uri", "cid")
    return {"root": {k: root[k] for k in keys},
            "parent": {k: parent[k] for k in keys}}


# --------------------------------------------------------------------------
# network
# --------------------------------------------------------------------------

@dataclass
class Session:
    did: str = ""
    handle: str = ""
    access_jwt: str = ""
    host: str = HOST
    did_doc: dict = field(default_factory=dict)

    @property
    def auth(self) -> dict:
        return {"Authorization": f"Bearer {self.access_jwt}"}

    @property
    def pds(self) -> str:
        """This account's PDS host, from its own DID document.

        Read rather than assumed: Bluesky spreads accounts across a fleet
        (this one is on poisonpie.us-west) and an account can be migrated.
        A hardcoded host works right up until it does not.
        """
        for svc in (self.did_doc or {}).get("service", []) or []:
            if svc.get("type") == "AtprotoPersonalDataServer":
                return str(svc.get("serviceEndpoint", ""))
        return ""

    @property
    def pds_did(self) -> str:
        host = urllib.parse.urlparse(self.pds).netloc
        return f"did:web:{host}" if host else ""


def _call(url: str, *, data: bytes | None = None, headers: dict | None = None,
          method: str = "POST", timeout: int = 60) -> dict:
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        # Never echo the request body: on createSession it is the password.
        raise BlueskyError(f"{method} {urllib.parse.urlparse(url).path} "
                           f"-> {e.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise BlueskyError(f"{urllib.parse.urlparse(url).path}: {e}") from None
    return json.loads(body) if body else {}


def _query(session: Session, path: str, params: dict, *,
           host: str | None = None, token: str | None = None) -> dict:
    url = f"{host or session.host}/xrpc/{path}?{urllib.parse.urlencode(params)}"
    headers = {"Authorization": f"Bearer {token}"} if token else dict(session.auth)
    return _call(url, headers=headers, method="GET")


def _json_call(session_or_host, path: str, payload: dict,
               headers: dict | None = None) -> dict:
    host = (session_or_host.host if isinstance(session_or_host, Session)
            else session_or_host)
    h = {"Content-Type": "application/json"}
    if isinstance(session_or_host, Session):
        h.update(session_or_host.auth)
    h.update(headers or {})
    return _call(f"{host}/xrpc/{path}",
                 data=json.dumps(payload).encode(), headers=h)


def login(handle: str, app_password: str, host: str = HOST) -> Session:
    """Open a session with an app password.

    The password is not stored on the Session and not returned in any error
    -- see _call, which deliberately never echoes a request body.
    """
    if not handle or not app_password:
        raise BlueskyError("both a handle and an app password are required")
    if app_password.count("-") != 3:
        # App passwords are xxxx-xxxx-xxxx-xxxx. An account password here
        # would authenticate perfectly well and be a bad thing to have put
        # in a CI secret, so say so rather than quietly accepting it.
        raise BlueskyError("that does not look like an app password "
                           "(xxxx-xxxx-xxxx-xxxx). Create one at Settings -> "
                           "App Passwords; do not use the account password.")
    out = _json_call(host, "com.atproto.server.createSession",
                     {"identifier": handle, "password": app_password})
    return Session(did=out["did"], handle=out.get("handle", handle),
                   access_jwt=out["accessJwt"], host=host,
                   did_doc=out.get("didDoc") or {})


def upload_blob(session: Session, data: bytes, mime: str) -> dict:
    h = dict(session.auth)
    h["Content-Type"] = mime
    out = _call(f"{session.host}/xrpc/com.atproto.repo.uploadBlob",
                data=data, headers=h, timeout=180)
    return out["blob"]


def create_record(session: Session, record: dict) -> dict:
    out = _json_call(session, "com.atproto.repo.createRecord",
                     {"repo": session.did, "collection": COLLECTION,
                      "record": record})
    return {"uri": out["uri"], "cid": out["cid"]}


def recent_texts(session: Session, limit: int = 100) -> list[str]:
    """The text of this account's own recent posts, newest first.

    Read from the repo rather than from a feed view: `listRecords` returns
    what we wrote, without threading, reposts or any of the algorithmic
    shaping a feed applies. The question here is "did we already say this",
    and the repo is the only place that answers it exactly.
    """
    out = _query(session, "com.atproto.repo.listRecords",
                 {"repo": session.did, "collection": COLLECTION,
                  "limit": min(int(limit), 100)})
    return [str((r.get("value") or {}).get("text", ""))
            for r in out.get("records", [])]


def already_posted(session: Session, headline: str,
                   limit: int = 100) -> bool:
    """Has a post starting with this headline already gone out?

    The alternative is a ledger file, which is one more thing to commit,
    to race, and to be wrong. The timeline is the ledger. A day's headline
    is unique per window -- "DAWG OF THE DAY — August 29, 2026" -- so an
    exact prefix match answers it without heuristics.
    """
    head = (headline or "").strip()
    if not head:
        return False
    return any(t.strip().startswith(head) for t in recent_texts(session, limit))


def headline_of(text: str) -> str:
    """The first line of a post, which is the window it belongs to."""
    return (text or "").strip().split("\n", 1)[0].strip()


def post_url(handle: str, uri: str) -> str:
    """A browsable link for an at:// record URI."""
    return f"https://bsky.app/profile/{handle}/post/{uri.rsplit('/', 1)[-1]}"


# --------------------------------------------------------------------------
# threads
# --------------------------------------------------------------------------

@dataclass
class Item:
    """One post to be made, and whatever goes with it.

    The card is always here. The reel is here too when Savant had a clip.
    A post carries one embed, so publish_thread prefers the reel and falls
    back to the card -- which matters because a video upload can fail for
    reasons that have nothing to do with this winner (an unconfirmed email,
    the daily quota), and losing his post over that is worse than losing
    the motion.
    """
    text: str
    image: bytes | None = None
    alt: str = ""
    video: bytes | None = None
    aspect: tuple[int, int] = (1080, 1920)
    name: str = "reel.mp4"
    # Why this is a card rather than a reel. Carried so the report can say
    # so: a thread that quietly posts stills looks identical to a thread
    # that was asked for stills, and the difference is the whole question.
    note: str = ""


@dataclass
class Result:
    text: str = ""
    uri: str = ""
    cid: str = ""
    url: str = ""
    bytes: int = 0
    mime: str = ""
    kind: str = "text"          # text | image | video
    note: str = ""
    error: str = ""
    record: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.uri)


def prepare(items: list[Item]) -> list[Result]:
    """Everything a thread needs except the network.

    Separate from `publish` so a dry run produces the exact records that a
    real run would send -- same text, same facets, same alt, same encoded
    image size. A preview built by different code proves nothing.
    """
    out = []
    for it in items:
        r = Result(text=it.text, note=it.note)
        r.record = post_record(it.text)
        if it.video:
            r.kind, r.bytes, r.mime = "video", len(it.video), "video/mp4"
            if it.image:
                r.note = r.note or "card ready as a fallback"
            if len(it.video) > VIDEO_MAX:
                r.error = (f"video is {len(it.video)/1e6:.1f} MB; the limit "
                           f"is {VIDEO_MAX/1e6:.0f} MB")
        elif it.image:
            r.kind = "image"
            try:
                data, mime = fit_image(it.image)
                r.bytes, r.mime = len(data), mime
            except Exception as e:  # noqa: BLE001
                r.error = f"{type(e).__name__}: {e}"
        out.append(r)
    return out


def publish_thread(session: Session, items: list[Item], *,
                   pause: float = 0.6) -> list[Result]:
    """Post the first item, then chain the rest beneath it as replies.

    A reply that fails leaves the thread short, which is survivable: there
    is no transaction here, and deleting three good posts because the
    fourth failed would be worse.

    A *root* that fails is different, and stops everything. The first
    version carried on, so when the winner's video upload was refused his
    post never existed and "No. 2 on the day" went up as a standalone
    top-level post with nothing above it -- a runner-up presented as the
    day's item. A short thread is a smaller problem than a wrong one.
    """
    results = prepare(items)
    root = parent = None
    for n, (it, r) in enumerate(zip(items, results)):
        if r.error:
            continue
        try:
            r.uri, r.cid = _post_one(session, it, r, root, parent)
            r.url = post_url(session.handle, r.uri)
            ref = {"uri": r.uri, "cid": r.cid}
            if root is None:
                root = ref
            parent = ref
        except Exception as e:  # noqa: BLE001 -- one post, not the thread
            r.error = f"{type(e).__name__}: {e}"
            if n == 0:
                for rest in results[1:]:
                    rest.error = ("not attempted: the root post failed, and a "
                                  "runner-up alone reads as the day's winner")
                break
        time.sleep(pause)
    return results


def _post_one(session: Session, it: Item, r: Result,
              root: dict | None, parent: dict | None) -> tuple[str, str]:
    """Send one post, degrading from reel to card rather than losing it."""
    images, video = [], None
    if it.video:
        try:
            job = upload_video(session, it.video, it.name)
            blob = await_video(session, job["jobId"])
            video = video_embed(blob, it.alt, it.aspect)
        except BlueskyError as e:
            if not it.image:
                raise
            # The reel is gone; the post is not. Say which, and why.
            r.note = f"video upload failed, posted the card: {e}"
            r.kind = "image"
    if video is None and it.image:
        data, mime = fit_image(it.image)
        r.bytes, r.mime, r.kind = len(data), mime, "image"
        blob = upload_blob(session, data, mime)
        images.append(image_item(blob, it.alt, image_size(it.image)))
    rec = post_record(it.text, images=images or None, video=video,
                      reply=reply_ref(root, parent) if root else None)
    r.record = rec
    ref = create_record(session, rec)
    return ref["uri"], ref["cid"]


def report(results: list[Result], *, dry_run: bool) -> str:
    """What went out, or what would have."""
    head = "## Dry run — nothing was posted" if dry_run else "## Posted"
    lines = [head, ""]
    for i, r in enumerate(results):
        who = "root" if i == 0 else f"reply {i}"
        size = ""
        if r.bytes:
            size = (f", {r.kind} {r.bytes/1e6:.1f} MB" if r.kind == "video"
                    else f", {r.kind} {r.bytes/1000:.0f} KB {r.mime}")
        lines.append(f"### {who} — {graphemes(r.text)}/300 graphemes{size}")
        if r.note:
            lines.append("")
            lines.append(f"_no reel: {r.note}_")
        lines.append("")
        lines.append("```")
        lines.append(r.text)
        lines.append("```")
        tags = [f["features"][0].get("tag") for f in facets(r.text)
                if f["features"][0]["$type"].endswith("#tag")]
        if tags:
            lines.append(f"tags: {', '.join('#' + t for t in tags)}")
        if r.url:
            lines.append(f"posted: {r.url}")
        if r.error:
            lines.append(f"**failed: {r.error}**")
        lines.append("")
    ok = sum(1 for r in results if r.ok)
    vids = sum(1 for r in results if r.kind == "video")
    lines.append(f"**{vids}/{len(results)} with video"
                 + (f", {ok}/{len(results)} posted.**" if not dry_run else ".**"))
    stuck = {r.note for r in results if r.note}
    if stuck:
        lines.append("")
        lines.append("Why the rest are cards: " + "; ".join(sorted(stuck)))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# video
# --------------------------------------------------------------------------

def service_auth(session: Session, aud: str, lxm: str,
                 minutes: int = 30) -> str:
    """A short-lived token scoped to one audience and one method.

    The session's own JWT is not accepted by the video service: it is issued
    for the PDS, and video.bsky.app is a different party. This asks the PDS
    to mint a token that names exactly who may present it and exactly which
    method it may call, which is why the `lxm` for an upload is
    `com.atproto.repo.uploadBlob` and not `uploadVideo` -- the video service
    is being authorised to write a blob into this account's repo.
    """
    exp = int(time.time()) + minutes * 60
    out = _query(session, "com.atproto.server.getServiceAuth",
                 {"aud": aud, "lxm": lxm, "exp": exp})
    return out["token"]


def upload_limits(session: Session) -> dict:
    """What this account is allowed to upload today.

    Asked before every upload rather than assumed, because the two things
    that stop a video -- an unverified email, and the daily quota -- both
    report here in a sentence a human can act on, and both otherwise
    surface as an opaque failure after the file has been sent.
    """
    token = service_auth(session, VIDEO_DID, "app.bsky.video.getUploadLimits")
    return _query(session, "app.bsky.video.getUploadLimits", {},
                  host=VIDEO_HOST, token=token)


def upload_video(session: Session, data: bytes, name: str) -> dict:
    """Hand the file to the video service. Returns a jobStatus, not a blob."""
    if len(data) > VIDEO_MAX:
        raise BlueskyError(f"video is {len(data)/1e6:.1f} MB; the limit is "
                           f"{VIDEO_MAX/1e6:.0f} MB")
    if not session.pds_did:
        raise BlueskyError("cannot find this account's PDS in its DID document")
    token = service_auth(session, session.pds_did, "com.atproto.repo.uploadBlob")
    url = (f"{VIDEO_HOST}/xrpc/app.bsky.video.uploadVideo"
           f"?{urllib.parse.urlencode({'did': session.did, 'name': name})}")
    out = _call(url, data=data, timeout=600,
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "video/mp4"})
    return out.get("jobStatus", out)


def job_status(session: Session, job_id: str) -> dict:
    token = service_auth(session, VIDEO_DID, "app.bsky.video.getJobStatus")
    out = _query(session, "app.bsky.video.getJobStatus", {"jobId": job_id},
                 host=VIDEO_HOST, token=token)
    return out.get("jobStatus", out)


def job_done(status: dict) -> bool:
    return str(status.get("state", "")).upper().endswith("COMPLETED")


def job_failed(status: dict) -> bool:
    state = str(status.get("state", "")).upper()
    return "FAILED" in state or bool(status.get("error"))


def await_video(session: Session, job_id: str, *, timeout: float = 420,
                poll: float = 3.0) -> dict:
    """Wait for the transcode and return the blob it produced.

    Transcoding is somebody else's queue, so this waits rather than assumes.
    A job that fails says so in `error`/`message`; a job that never finishes
    times out rather than hanging the run behind an unbounded loop.
    """
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = job_status(session, job_id)
        if job_done(last):
            blob = last.get("blob")
            if not blob:
                raise BlueskyError("the job completed but returned no blob")
            return blob
        if job_failed(last):
            raise BlueskyError(f"video job failed: "
                               f"{last.get('error') or ''} "
                               f"{last.get('message') or ''}".strip())
        time.sleep(poll)
    raise BlueskyError(f"video job {job_id} still "
                       f"{last.get('state', 'unknown')} after {timeout:.0f}s")


def video_embed(blob: dict, alt: str, size: tuple[int, int]) -> dict:
    """app.bsky.embed.video. alt is capped at 1000 graphemes by the lexicon."""
    w, h = size
    embed = {"$type": "app.bsky.embed.video", "video": blob,
             "aspectRatio": {"width": int(w), "height": int(h)}}
    if alt:
        embed["alt"] = alt[:1000]
    return embed
