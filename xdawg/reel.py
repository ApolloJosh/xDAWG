"""Card plus clip, out the other side as one vertical video.

The card is a 1080x1920 PNG with a transparent rectangle in it. This module
lays the clip *underneath* that hole and flattens the two together, so the
card can draw a frame around the video and the video can never be
composited over artwork.

The hole's position is passed in, never assumed. xdawg.card measures it
after layout for exactly this reason: it moves whenever a block above it
changes height, and a stale rectangle here is a clip half-hidden behind
the stat strip, which nobody notices until they watch the finished file.

Everything is one ffmpeg invocation per output. Intermediate files are a
tax on a job that already spends most of its wall clock waiting on MLB.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

FPS = 30
BG = "white"             # the card is on paper; the canvas behind it must be too
FADE = 0.25              # seconds of fade at each end of the finished reel
FADE_TO = "white"        # not black: a black dip on a white card reads as a fault


class FFmpegError(RuntimeError):
    pass


def have_ffmpeg() -> bool:
    """Is there an ffmpeg to shell out to?

    Worth asking up front rather than discovering it as a FileNotFoundError
    three layers down, where it looks like any other reason a clip did not
    turn into a reel. A CI runner is the case that matters: Chromium gets
    installed on purpose and ffmpeg does not come along with it.
    """
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _run(args: list[str]) -> str:
    try:
        p = subprocess.run(args, capture_output=True, text=True)
    except FileNotFoundError:
        raise FFmpegError(
            f"{args[0]} is not installed. Reels need ffmpeg and ffprobe on "
            f"PATH; a CI runner has neither unless the job installs them."
        ) from None
    if p.returncode != 0:
        # ffmpeg's real complaint is always in the last few lines; the rest
        # is a banner nobody has ever read.
        tail = "\n".join((p.stderr or "").strip().splitlines()[-12:])
        raise FFmpegError(f"{shlex.join(args[:3])} ... failed:\n{tail}")
    return p.stdout


@dataclass
class Probe:
    duration: float = 0.0
    width: int = 0
    height: int = 0
    audio: bool = False

    @property
    def ok(self) -> bool:
        return self.duration > 0 and self.width > 0


def probe(path: str | Path) -> Probe:
    """What ffprobe knows about a file. A dud returns an empty Probe."""
    try:
        raw = _run(["ffprobe", "-v", "error", "-print_format", "json",
                    "-show_format", "-show_streams", str(path)])
    except FFmpegError:
        return Probe()
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        return Probe()
    out = Probe()
    for s in d.get("streams", []):
        if s.get("codec_type") == "video" and not out.width:
            out.width = int(s.get("width") or 0)
            out.height = int(s.get("height") or 0)
        if s.get("codec_type") == "audio":
            out.audio = True
    try:
        out.duration = float(d.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError):
        out.duration = 0.0
    return out


def fit_filter(box: tuple[int, int, int, int], label_in: str,
               label_out: str) -> str:
    """Scale a clip to fill the window, cropping the overflow.

    Fill, not letterbox. Savant serves 16:9 and the window is 16:9, so in
    the ordinary case this crops nothing at all -- but a clip that arrives
    4:3 or vertical would otherwise put black bars inside a frame the card
    has already drawn a border around, which looks like a mistake even
    though it is technically the faithful thing to do.
    """
    _, _, w, h = box
    return (f"[{label_in}]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},fps={FPS},setsar=1[{label_out}]")


def build_command(card: str | Path, box: tuple[int, int, int, int],
                  clips: list[str | Path], out: str | Path, *,
                  duration: float, canvas: tuple[int, int] = (1080, 1920),
                  fade: float = FADE) -> list[str]:
    """The single ffmpeg call that makes one reel. Pure -- builds, runs nothing."""
    x, y, _, _ = box
    cw, ch = canvas
    n = len(clips)
    if n == 0:
        raise ValueError("build_command needs at least one clip")

    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={BG}:s={cw}x{ch}:r={FPS}",
            "-loop", "1", "-i", str(card)]
    for c in clips:
        args += ["-i", str(c)]

    # Inputs: 0 canvas, 1 card, 2..n+1 clips.
    chain = [fit_filter(box, f"{i + 2}:v", f"c{i}") for i in range(n)]
    if n == 1:
        chain.append("[c0]null[clip]")
    else:
        # Straight cuts. A crossfade between two different plays reads as a
        # replay of one play, which is worse than an honest hard cut.
        chain.append("".join(f"[c{i}]" for i in range(n)) +
                     f"concat=n={n}:v=1:a=0[clip]")
    chain.append(f"[0:v][clip]overlay=x={x}:y={y}:shortest=0[bg]")
    chain.append(f"[bg][1:v]overlay=0:0:format=auto[flat]")
    if fade > 0:
        fo = max(duration - fade, 0)
        chain.append(f"[flat]fade=t=in:st=0:d={fade}:color={FADE_TO},"
                     f"fade=t=out:st={fo:.3f}:d={fade}:color={FADE_TO}[v]")
    else:
        chain.append("[flat]null[v]")

    args += ["-filter_complex", ";".join(chain), "-map", "[v]"]
    # Audio is taken from the first clip only. Concatenated broadcast audio
    # across two plays is a jump cut in the commentary, and muting the whole
    # thing throws away the crowd, which is most of why a highlight works.
    if n >= 1 and probe(clips[0]).audio:
        args += ["-map", "2:a?", "-c:a", "aac", "-b:a", "128k", "-ac", "2"]
    args += ["-t", f"{duration:.3f}",
             "-c:v", "libx264", "-preset", "medium", "-crf", "20",
             "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.0",
             "-movflags", "+faststart", "-r", str(FPS), str(out)]
    return args


def compose(card: str | Path, box: tuple[int, int, int, int],
            clips: list[str | Path], out: str | Path, *,
            canvas: tuple[int, int] = (1080, 1920),
            max_seconds: float = 60.0, tail: float = 0.6) -> Path:
    """Burn one card and its clips into a finished vertical video."""
    # Asked before probing. probe() swallows its own failures and returns an
    # empty Probe, so without this a missing ffmpeg reports as "none of the
    # clips are readable video" -- which is false, and points the next
    # person at Savant instead of at the runner.
    if not have_ffmpeg():
        raise FFmpegError("ffmpeg and ffprobe are not on PATH; cannot build "
                          "a reel")
    usable = [c for c in clips if probe(c).ok]
    if not usable:
        raise FFmpegError("none of the clips are readable video")
    total = sum(probe(c).duration for c in usable) + tail
    total = min(total, max_seconds)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(build_command(card, box, usable, out, duration=total, canvas=canvas))
    return out


def still(card: str | Path, out: str | Path, *, seconds: float = 6.0,
          canvas: tuple[int, int] = (1080, 1920)) -> Path:
    """The card alone, as video.

    For the nights when Savant has no clip for the winner's playId. A post
    that goes out as a still beats a post that does not go out, and a
    silent six seconds is a legitimate feed object rather than an error.
    """
    if not have_ffmpeg():
        raise FFmpegError("ffmpeg is not on PATH; cannot render a still")
    cw, ch = canvas
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
          "-f", "lavfi", "-i", f"color=c={BG}:s={cw}x{ch}:r={FPS}",
          "-loop", "1", "-i", str(card),
          "-filter_complex",
          f"[0:v][1:v]overlay=0:0:format=auto,"
          f"fade=t=in:st=0:d={FADE}:color={FADE_TO},"
          f"fade=t=out:st={seconds - FADE:.3f}:d={FADE}:color={FADE_TO}[v]",
          "-map", "[v]", "-t", f"{seconds:.3f}",
          "-c:v", "libx264", "-preset", "medium", "-crf", "20",
          "-pix_fmt", "yuv420p", "-movflags", "+faststart",
          "-r", str(FPS), str(out)])
    return out
