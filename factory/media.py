"""ffmpeg/ffprobe helpers (tracker P0-02). Argument lists only, never a shell."""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import mpt_bridge
from .config import get_settings


class MediaError(Exception):
    pass


def _tail(text: str, n: int = 6) -> str:
    return "\n".join(text.strip().splitlines()[-n:])


def run_ffmpeg(args: list[str], timeout: float = 900, cwd: str | Path | None = None, strict: bool = False) -> None:
    s = get_settings()
    cmd = [s.ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except FileNotFoundError as e:
        raise MediaError(f"ffmpeg not found ({s.ffmpeg}). Install it or set FFMPEG_PATH.") from e
    except subprocess.TimeoutExpired as e:
        raise MediaError(f"ffmpeg timed out after {timeout}s") from e
    if r.returncode != 0 or (strict and r.stderr.strip()):
        raise MediaError(f"ffmpeg failed: {_tail(r.stderr)}")


@dataclass
class Probe:
    duration: float
    has_video: bool
    has_audio: bool
    width: int | None
    height: int | None
    size: int | None
    video_codec: str | None
    audio_codec: str | None
    fps: float | None


def _ffprobe(p: Path) -> dict:
    s = get_settings()
    if not p.is_file():
        raise MediaError(f"file not found: {p.name}")
    cmd = [s.ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(p)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError as e:
        raise MediaError(f"ffprobe not found ({s.ffprobe}). Install it or set FFPROBE_PATH.") from e
    if r.returncode != 0:
        raise MediaError(f"unreadable media file {p.name}: {_tail(r.stderr, 2) or 'ffprobe error'}")
    return json.loads(r.stdout or "{}")


def _display_size(v: dict | None) -> tuple[int | None, int | None]:
    """Width/height as displayed, i.e. swapped when the stream carries a 90/270 degree rotation."""
    w, h = (v or {}).get("width"), (v or {}).get("height")
    rot = 0
    if v:
        rot = abs(int(float((v.get("tags") or {}).get("rotate", 0) or 0)))
        for sd in v.get("side_data_list") or []:
            if "rotation" in sd:
                rot = abs(int(sd["rotation"]))
    if rot in (90, 270) and w and h:
        w, h = h, w
    return w, h


def probe(path: str | Path) -> Probe:
    p = Path(path)
    data = _ffprobe(p)
    streams = data.get("streams", [])
    v = next((x for x in streams if x.get("codec_type") == "video"), None)
    a = next((x for x in streams if x.get("codec_type") == "audio"), None)
    try:
        duration = float(data.get("format", {}).get("duration") or (v or a or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        raise MediaError(f"media file has no readable duration: {p.name}")
    w, h = _display_size(v)
    fps = None
    if v and v.get("avg_frame_rate") and "/" in v["avg_frame_rate"]:
        num, den = v["avg_frame_rate"].split("/")
        fps = float(num) / float(den) if float(den) else None
    size = data.get("format", {}).get("size")
    return Probe(
        duration=round(duration, 3),
        has_video=v is not None,
        has_audio=a is not None,
        width=w,
        height=h,
        size=int(size) if size else None,
        video_codec=(v or {}).get("codec_name"),
        audio_codec=(a or {}).get("codec_name"),
        fps=fps,
    )


def audio_duration(path: str | Path) -> float:
    p = probe(path)
    if not p.has_audio:
        raise MediaError(f"no audio stream in {Path(path).name}")
    return p.duration


def to_wav(src: str | Path, dst: str | Path) -> None:
    """Normalize any audio to 48 kHz mono PCM wav (stable input for the renderer)."""
    run_ffmpeg(["-i", str(src), "-vn", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(dst)])


@dataclass
class ImageInfo:
    width: int
    height: int


def probe_image(path: str | Path) -> ImageInfo:
    """Size of a still image, after decoding it once: garbage and truncated files are rejected."""
    p = Path(path)
    w, h = _display_size(next((x for x in _ffprobe(p).get("streams", []) if x.get("codec_type") == "video"), None))
    if not w or not h:
        raise MediaError(f"not a readable image: {p.name}")
    try:
        run_ffmpeg(["-i", str(p), "-frames:v", "1", "-f", "null", "-"], timeout=60, strict=True)
    except MediaError as e:
        raise MediaError(f"corrupt or truncated image {p.name}: {e}") from e
    return ImageInfo(w, h)


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# Bump whenever image_to_clip changes what it writes: the version is part of the normalized-clip cache key.
IMAGE_CLIP_VERSION = "img2clip-v1"
IMAGE_ZOOM = 0.10  # total slow zoom-in across the clip, 10%


def clip_frames(seconds: float, fps: int) -> int:
    """Whole frames that cover `seconds` (the clip is at most one frame longer than the voice)."""
    return max(1, math.ceil(round(seconds * fps, 3)))


def image_to_clip(
    image: str | Path, dst: str | Path, frames: int, width: int = 1080, height: int = 1920, fps: int = 30
) -> None:
    """Still image -> mp4 of exactly `frames` frames at `width`x`height`.

    Cover-scale + centre-crop (never stretch), then a slow centred zoom-in. The image is first scaled to 2x so
    zoompan crops at sub-pixel precision instead of stepping whole pixels.
    """
    w2, h2 = width * 2, height * 2
    vf = (
        f"scale={w2}:{h2}:force_original_aspect_ratio=increase,crop={w2}:{h2},"
        f"zoompan=z='1+{IMAGE_ZOOM}*on/{frames}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d={frames}:s={width}x{height}:fps={fps},"
        "setsar=1,format=yuv420p"
    )
    run_ffmpeg(
        ["-i", str(image), "-vf", vf, "-frames:v", str(frames), "-an"]
        + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-r", str(fps), "-movflags", "+faststart", str(dst)]
    )


def preflight() -> dict:
    """Check binaries and the filters/encoders the POC needs, for the ffmpeg MPT will actually run."""
    s = get_settings()
    mpt_bridge.use_configured_ffmpeg()
    result: dict = {
        "ffmpeg": False,
        "ffprobe": False,
        "libx264": False,
        "aac": False,
        "ass": False,
        "sidechaincompress": False,
        "loudnorm": False,
        "version": None,
        "ffmpeg_path": shutil.which(s.ffmpeg),
        "ffprobe_path": shutil.which(s.ffprobe),
        "mpt_ffmpeg_path": None,
        "mpt_ffprobe_path": None,
        "problems": [],
    }
    mpt_paths = mpt_bridge.mpt_tool_paths()
    result["mpt_ffmpeg_path"], result["mpt_ffprobe_path"] = mpt_paths["ffmpeg"], mpt_paths["ffprobe"]
    try:
        v = subprocess.run([s.ffmpeg, "-version"], capture_output=True, text=True, timeout=20)
        result["ffmpeg"] = v.returncode == 0
        result["version"] = v.stdout.splitlines()[0] if v.stdout else None
        enc = subprocess.run([s.ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=20)
        flt = subprocess.run([s.ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=20)
        result["libx264"] = " libx264 " in enc.stdout
        result["aac"] = " aac " in enc.stdout
        for name in ("ass", "sidechaincompress", "loudnorm"):
            result[name] = f" {name} " in flt.stdout
    except FileNotFoundError:
        pass
    with contextlib.suppress(FileNotFoundError):
        result["ffprobe"] = subprocess.run([s.ffprobe, "-version"], capture_output=True, timeout=20).returncode == 0
    for key in ("ffmpeg", "ffprobe", "libx264", "aac", "sidechaincompress", "loudnorm"):
        if not result[key]:
            result["problems"].append(f"missing: {key}")
    if result["ffmpeg"] and not result["ass"]:
        result["problems"].append(
            f"ffmpeg at {result['ffmpeg_path'] or s.ffmpeg} lacks libass (ass filter); "
            "install a libass-enabled FFmpeg and set FFMPEG_PATH"
        )
    for tool in ("ffmpeg", "ffprobe"):
        ours, theirs = result[f"{tool}_path"], result[f"mpt_{tool}_path"]
        if ours and not mpt_bridge.same_file(ours, theirs):
            result["problems"].append(
                f"MoneyPrinterTurbo would run {tool} at {theirs or 'nothing on PATH'} instead of {ours}; "
                f"put the {tool} you want first on PATH or set {tool.upper()}_PATH"
            )
    return result


_PREFLIGHT_OK: dict[tuple[str, str, str], dict] = {}


def preflight_cached() -> dict:
    """`preflight()` memoized per (ffmpeg, ffprobe, PATH). Only a clean result is cached, so a fix is picked up."""
    s = get_settings()
    mpt_bridge.use_configured_ffmpeg()
    key = (s.ffmpeg, s.ffprobe, os.environ.get("PATH", ""))
    if key in _PREFLIGHT_OK:
        return _PREFLIGHT_OK[key]
    result = preflight()
    if not result["problems"]:
        _PREFLIGHT_OK[key] = result
    return result


def filter_escape(value: str) -> str:
    """Escape a path used as an ffmpeg filter option value."""
    return value.replace("\\", "/").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,")
