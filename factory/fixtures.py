"""Synthetic test media so the pipeline can be verified without Muse/VoiceStudio (not committed)."""

from __future__ import annotations

from pathlib import Path

from .media import run_ffmpeg


def make_clip(path: Path, width: int, height: int, seconds: float, source: str = "testsrc2") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            f"{source}=size={width}x{height}:rate=25",
            "-t",
            f"{seconds}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ]
    )
    return path


def make_tone(path: Path, seconds: float, freq: int = 300) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}", "-ac", "2", str(path)])
    return path


def _still(path: Path, source: str, vf: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    jpeg = path.suffix.lower() in (".jpg", ".jpeg")
    chain = ",".join(x for x in (vf, "format=yuvj420p" if jpeg else None) if x)
    run_ffmpeg(["-f", "lavfi", "-i", source, *(["-vf", chain] if chain else []), "-frames:v", "1", str(path)])
    return path


def make_image(path: Path, width: int = 1600, height: int = 900) -> Path:
    """Still image; the format follows the extension (png, jpg, webp when the ffmpeg build has libwebp)."""
    return _still(path, f"mandelbrot=size={width}x{height}")


def make_circle_image(path: Path, width: int, height: int, radius: int) -> Path:
    """White disc centred on black: any non-uniform scaling shows up as a non-circular disc."""
    disc = f"geq=lum='if(lt(hypot(X-W/2,Y-H/2),{radius}),255,0)':cb=128:cr=128"
    return _still(path, f"color=c=black:s={width}x{height}", disc)


def make_corrupt(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not really an mp4")
    return path


def make_set(folder: Path) -> dict[str, Path]:
    """Portrait/landscape/mixed clips of different lengths + stills + music + corrupt files."""
    return {
        "landscape_short": make_clip(folder / "landscape_3s.mp4", 1280, 720, 3),
        "portrait_long": make_clip(folder / "portrait_14s.mp4", 1080, 1920, 14, "testsrc"),
        "small_4x3": make_clip(folder / "small_640x480_4s.mp4", 640, 480, 4, "smptebars"),
        "square": make_clip(folder / "square_8s.mp4", 1080, 1080, 8, "rgbtestsrc"),
        "landscape_long": make_clip(folder / "landscape_12s.mp4", 1920, 1080, 12),
        "portrait_short": make_clip(folder / "portrait_2s.mp4", 720, 1280, 2, "testsrc"),
        "image": make_image(folder / "still.png"),
        "portrait_image": make_image(folder / "portrait_800x1200.jpg", 800, 1200),
        "landscape_image": make_image(folder / "landscape_1600x900.jpg", 1600, 900),
        "music": make_tone(folder / "music.mp3", 40, 180),
        "corrupt": make_corrupt(folder / "corrupt.mp4"),
        "corrupt_image": make_corrupt(folder / "corrupt.jpg"),
    }
