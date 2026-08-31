"""The ffmpeg call, checked without watching a video.

`build_command` is pure on purpose: it assembles the invocation and runs
nothing, so the filter graph can be asserted on directly. The parts that
genuinely need ffmpeg build tiny synthetic clips and check the geometry by
sampling pixels out of the finished frame -- which is the only honest way
to answer "did the clip land in the hole".
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from xdawg import reel

BOX = (0, 718, 1080, 608)
has_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="no ffmpeg")


# --------------------------------------------------------------------------
# the command
# --------------------------------------------------------------------------

def _graph(cmd: list[str]) -> str:
    return cmd[cmd.index("-filter_complex") + 1]


def test_the_clip_is_placed_at_the_measured_offset():
    cmd = reel.build_command("c.png", BOX, ["a.mp4"], "o.mp4", duration=8)
    assert "overlay=x=0:y=718" in _graph(cmd)


def test_the_card_goes_over_the_clip_not_under_it():
    g = _graph(reel.build_command("c.png", BOX, ["a.mp4"], "o.mp4", duration=8))
    assert g.index("[clip]overlay") < g.index("[1:v]overlay")


def test_the_clip_fills_the_window_rather_than_letterboxing_inside_it():
    g = _graph(reel.build_command("c.png", BOX, ["a.mp4"], "o.mp4", duration=8))
    assert "force_original_aspect_ratio=increase" in g
    assert "crop=1080:608" in g


def test_two_clips_are_concatenated_with_a_hard_cut():
    g = _graph(reel.build_command("c.png", BOX, ["a.mp4", "b.mp4"], "o.mp4",
                                  duration=12))
    assert "concat=n=2:v=1:a=0" in g
    # A crossfade between two different plays reads as a replay of one.
    assert "xfade" not in g


def test_one_clip_is_not_run_through_concat():
    g = _graph(reel.build_command("c.png", BOX, ["a.mp4"], "o.mp4", duration=8))
    assert "concat" not in g


def test_the_reel_opens_on_the_play_with_no_fade_in():
    # Half a second of white before the card and the play is a viewer's
    # thumb already moving. The tail still fades: it says the clip is over
    # rather than looping mid-swing.
    g = _graph(reel.build_command("c.png", BOX, ["a.mp4"], "o.mp4",
                                  duration=10))
    assert "fade=t=in" not in g
    assert "fade=t=out:st=9.750:d=0.25" in g


def test_a_fade_in_can_still_be_asked_for():
    g = _graph(reel.build_command("c.png", BOX, ["a.mp4"], "o.mp4",
                                  duration=10, fade_in=0.5))
    assert "fade=t=in:st=0:d=0.5" in g


def test_the_still_does_not_open_on_white_either():
    cmd = reel.build_command("c.png", BOX, ["a.mp4"], "o.mp4", duration=8)
    assert "fade=t=in" not in _graph(cmd)


def test_the_fade_is_to_white_not_black():
    # The card is on paper. A dip to black reads as a fault in the file
    # rather than as a transition.
    g = _graph(reel.build_command("c.png", BOX, ["a.mp4"], "o.mp4", duration=8))
    assert g.count("color=white") == 1      # the tail only
    # And the canvas the card sits on is paper too, so a card narrower than
    # the frame would show white margins rather than black ones.
    cmd = reel.build_command("c.png", BOX, ["a.mp4"], "o.mp4", duration=8)
    assert any(a.startswith("color=c=white:s=") for a in cmd)


def test_fade_can_be_turned_off():
    g = _graph(reel.build_command("c.png", BOX, ["a.mp4"], "o.mp4",
                                  duration=10, fade=0))
    assert "fade=" not in g


def test_the_output_is_encoded_for_a_phone():
    cmd = reel.build_command("c.png", BOX, ["a.mp4"], "o.mp4", duration=8)
    assert "yuv420p" in cmd            # or half the world sees green
    assert "+faststart" in cmd         # or it will not start until fully loaded
    assert "libx264" in cmd


def test_no_clips_is_a_programming_error_not_a_silent_empty_video():
    with pytest.raises(ValueError):
        reel.build_command("c.png", BOX, [], "o.mp4", duration=8)


# --------------------------------------------------------------------------
# with a real ffmpeg
# --------------------------------------------------------------------------

def _clip(path: Path, *, seconds: int = 2, size: str = "1280x720",
          color: str = "red", audio: bool = False) -> Path:
    args = ["ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={color}:s={size}:r=30:d={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=400:duration={seconds}",
                 "-c:a", "aac"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(path)]
    subprocess.run(args, check=True, capture_output=True)
    return path


PAPER = (255, 255, 255)


def _card(path: Path) -> Path:
    """A stand-in card: opaque paper, with a transparent rectangle at BOX."""
    from PIL import Image
    im = Image.new("RGBA", reel_size := (1080, 1920), PAPER + (255,))
    x, y, w, h = BOX
    im.paste((0, 0, 0, 0), (x, y, x + w, y + h))
    assert im.size == reel_size
    im.save(path)
    return path


def _pixel(video: Path, at: float, xy: tuple[int, int]):
    from PIL import Image
    out = video.with_name("frame.png")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(at),
                    "-i", str(video), "-frames:v", "1", str(out)],
                   check=True, capture_output=True)
    return Image.open(out).convert("RGB").getpixel(xy)


@has_ffmpeg
def test_probe_reads_a_real_file(tmp_path):
    p = reel.probe(_clip(tmp_path / "a.mp4", seconds=2, audio=True))
    assert p.ok and p.audio
    assert 1.5 < p.duration < 2.6
    assert (p.width, p.height) == (1280, 720)


@has_ffmpeg
def test_probe_returns_empty_for_something_that_is_not_video(tmp_path):
    junk = tmp_path / "junk.mp4"
    junk.write_text("not a video")
    assert not reel.probe(junk).ok


@has_ffmpeg
def test_the_clip_lands_in_the_hole_and_nowhere_else(tmp_path):
    card = _card(tmp_path / "card.png")
    clip = _clip(tmp_path / "a.mp4", seconds=3, color="red")
    out = reel.compose(card, BOX, [clip], tmp_path / "reel.mp4")
    x, y, w, h = BOX
    r, g, b = _pixel(out, 1.5, (x + w // 2, y + h // 2))
    assert r > 150 and g < 90 and b < 90            # the clip is visible
    # Approximate, not exact: the h264 round trip through YUV moves every
    # channel by a point or two, and a test that demands the byte back is a
    # test that fails on a different ffmpeg build for no reason.
    for above_or_below in (y - 60, y + h + 60):
        px = _pixel(out, 1.5, (w // 2, above_or_below))
        assert min(px) > 215, f"the card is not opaque at y={above_or_below}"


@has_ffmpeg
def test_the_finished_reel_is_the_right_shape_and_length(tmp_path):
    out = reel.compose(_card(tmp_path / "c.png"), BOX,
                       [_clip(tmp_path / "a.mp4", seconds=3)],
                       tmp_path / "reel.mp4", tail=0.6)
    p = reel.probe(out)
    assert (p.width, p.height) == (1080, 1920)
    assert 3.2 < p.duration < 4.2


@has_ffmpeg
def test_a_long_clip_is_capped_rather_than_posted_at_full_length(tmp_path):
    out = reel.compose(_card(tmp_path / "c.png"), BOX,
                       [_clip(tmp_path / "a.mp4", seconds=8)],
                       tmp_path / "reel.mp4", max_seconds=4)
    assert reel.probe(out).duration <= 4.3


@has_ffmpeg
def test_two_clips_play_one_after_the_other(tmp_path):
    card = _card(tmp_path / "c.png")
    a = _clip(tmp_path / "a.mp4", seconds=3, color="red")
    b = _clip(tmp_path / "b.mp4", seconds=3, color="blue")
    out = reel.compose(card, BOX, [a, b], tmp_path / "reel.mp4")
    x, y, w, h = BOX
    mid = (x + w // 2, y + h // 2)
    first = _pixel(out, 1.5, mid)
    second = _pixel(out, 4.5, mid)
    assert first[0] > 150 and second[2] > 150


@has_ffmpeg
def test_a_vertical_clip_fills_the_window_instead_of_leaving_bars(tmp_path):
    # A clip that is not 16:9 must be cropped, not letterboxed: black bars
    # inside a frame the card has drawn a border around look like a bug.
    card = _card(tmp_path / "c.png")
    clip = _clip(tmp_path / "v.mp4", seconds=2, size="720x1280", color="red")
    out = reel.compose(card, BOX, [clip], tmp_path / "reel.mp4")
    x, y, w, h = BOX
    for px in (x + 20, x + w // 2, x + w - 20):
        r, g, b = _pixel(out, 1.0, (px, y + h // 2))
        assert r > 150, f"black bar at x={px}"


@has_ffmpeg
def test_an_unreadable_clip_is_an_error_rather_than_an_empty_file(tmp_path):
    junk = tmp_path / "junk.mp4"
    junk.write_text("nope")
    with pytest.raises(reel.FFmpegError):
        reel.compose(_card(tmp_path / "c.png"), BOX, [junk], tmp_path / "r.mp4")


@has_ffmpeg
def test_the_card_alone_is_still_a_postable_video(tmp_path):
    # The fallback for a night Savant has no clip. A still that goes out
    # beats a post that does not.
    out = reel.still(_card(tmp_path / "c.png"), tmp_path / "s.mp4", seconds=5)
    p = reel.probe(out)
    assert (p.width, p.height) == (1080, 1920)
    assert 4.5 < p.duration < 5.5


# --------------------------------------------------------------------------
# a missing ffmpeg
# --------------------------------------------------------------------------

def test_have_ffmpeg_answers_honestly(monkeypatch):
    monkeypatch.setattr(reel.shutil, "which", lambda n: "/usr/bin/" + n)
    assert reel.have_ffmpeg() is True
    monkeypatch.setattr(reel.shutil, "which", lambda n: None)
    assert reel.have_ffmpeg() is False


def test_a_missing_ffmpeg_says_so_rather_than_blaming_the_clip(tmp_path,
                                                              monkeypatch):
    # probe() swallows its own failures and returns an empty Probe, so
    # without the explicit check a missing binary reported as "none of the
    # clips are readable video" -- false, and it points the next person at
    # Savant instead of at the runner.
    monkeypatch.setattr(reel.shutil, "which", lambda n: None)
    with pytest.raises(reel.FFmpegError, match="not on PATH"):
        reel.compose("c.png", BOX, ["a.mp4"], tmp_path / "r.mp4")
    with pytest.raises(reel.FFmpegError, match="not on PATH"):
        reel.still("c.png", tmp_path / "s.mp4")


def test_a_missing_binary_names_itself(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError(2, "No such file or directory", "ffmpeg")
    monkeypatch.setattr(reel.subprocess, "run", boom)
    with pytest.raises(reel.FFmpegError, match="is not installed"):
        reel._run(["ffmpeg", "-version"])
