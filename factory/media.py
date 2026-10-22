"""ffmpeg/ffprobe helpers (tracker P0-02). Argument lists only, never a shell."""

from __future__ import annotations

import contextlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import get_settings


class MediaError(Exception):
    pass


def _tail(text: str, n: int = 6) -> str:
    return "\n".join(text.strip().splitlines()[-n:])


def run_ffmpeg(args: list[str], timeout: float = 900, cwd: str | Path | None = None) -> None:
    s = get_settings()
    cmd = [s.ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except FileNotFoundError as e:
        raise MediaError(f"ffmpeg not found ({s.ffmpeg}). Install it or set FFMPEG_PATH.") from e
    except subprocess.TimeoutExpired as e:
        raise MediaError(f"ffmpeg timed out after {timeout}s") from e
    if r.returncode != 0:
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


def probe(path: str | Path) -> Probe:
    s = get_settings()
    p = Path(path)
    if not p.is_file():
        raise MediaError(f"file not found: {p.name}")
    cmd = [s.ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(p)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError as e:
        raise MediaError(f"ffprobe not found ({s.ffprobe}). Install it or set FFPROBE_PATH.") from e
    if r.returncode != 0:
        raise MediaError(f"unreadable media file {p.name}: {_tail(r.stderr, 2) or 'ffprobe error'}")
    data = json.loads(r.stdout or "{}")
    streams = data.get("streams", [])
    v = next((x for x in streams if x.get("codec_type") == "video"), None)
    a = next((x for x in streams if x.get("codec_type") == "audio"), None)
    try:
        duration = float(data.get("format", {}).get("duration") or (v or a or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        raise MediaError(f"media file has no readable duration: {p.name}")
    w, h = (v or {}).get("width"), (v or {}).get("height")
    rot = 0
    if v:
        rot = abs(int(float((v.get("tags") or {}).get("rotate", 0) or 0)))
        for sd in v.get("side_data_list") or []:
            if "rotation" in sd:
                rot = abs(int(sd["rotation"]))
    if rot in (90, 270) and w and h:
        w, h = h, w
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


def image_to_clip(image: str | Path, dst: str | Path, seconds: float = 6.0) -> None:
    """Fallback asset (01_Flow step 5): still image -> short mp4 with slow zoom."""
    frames = int(seconds * 30)
    vf = (
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        f"zoompan=z='min(zoom+0.0008,1.12)':d={frames}:s=1080x1920:fps=30,format=yuv420p"
    )
    run_ffmpeg(["-loop", "1", "-i", str(image), "-t", f"{seconds:.2f}", "-vf", vf, "-c:v", "libx264", str(dst)])


def preflight() -> dict:
    """Check binaries and the filters/encoders the POC needs."""
    s = get_settings()
    result: dict = {
        "ffmpeg": False,
        "ffprobe": False,
        "libx264": False,
        "aac": False,
        "ass": False,
        "sidechaincompress": False,
        "loudnorm": False,
        "version": None,
        "problems": [],
    }
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
    for key in ("ffmpeg", "ffprobe", "libx264", "aac", "ass", "sidechaincompress", "loudnorm"):
        if not result[key]:
            result["problems"].append(f"missing: {key}")
    return result


def filter_escape(value: str) -> str:
    """Escape a path used as an ffmpeg filter option value."""
    return value.replace("\\", "/").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,")
