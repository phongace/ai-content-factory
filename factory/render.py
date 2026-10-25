"""Render (tracker P0-10, P0-11, P0-12).

0. Each scene's visual asset is normalized to a clip: a video passes through, an image becomes a clip of
   exactly the voice duration (cached by content, so an unchanged image keeps its scene render cache hit).
1. MoneyPrinterTurbo `VideoProject` renders each scene (cover-crop to 1080x1920, loop/trim clip to the
   scene's audio, 30fps, H.264/AAC) and concatenates. Unchanged scenes are reused between revisions.
2. Our final pass burns the ASS captions, mixes optional background music with sidechain ducking
   under the voice, and loudness-normalizes to -14 LUFS.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

from . import media, subtitles
from .mpt_bridge import video_project_module
from .schema import AssetType, SceneState, StoryboardFile, asset_type_for, supported_asset_types

WIDTH, HEIGHT, FPS = 1080, 1920, 30
FONTS_DIR = Path(__file__).resolve().parent / "assets" / "fonts"  # Be Vietnam Pro Bold (SIL OFL), nothing else


class RenderError(Exception):
    pass


@dataclass
class RenderOutput:
    file: Path
    duration: float
    size: int
    width: int
    height: int
    elapsed_ms: int
    reused_stages: int


@dataclass
class NormalizedSceneVisual:
    """What the scene renderer consumes, whatever the asset was: `path` is always a video file."""

    path: Path
    asset_type: AssetType
    reused: bool = False  # cached normalized clip reused (images only)


def asset_problem(project_dir: Path, s: SceneState) -> str | None:
    """Cheap check (no ffprobe) of why a scene's visual asset cannot be used, None = looks usable."""
    if not s.asset_path:
        return "missing visual asset (upload a video or an image)"
    kind = asset_type_for(s.asset_path)
    ext = Path(s.asset_path).suffix.lower()
    if kind is None:
        return f"unsupported asset type '{ext}' ({supported_asset_types()})"
    if s.asset_type and s.asset_type != kind:
        return f"asset type '{s.asset_type}' does not match its file extension '{ext}'"
    if not (project_dir / s.asset_path).is_file():
        return f"visual asset file is missing ({Path(s.asset_path).name})"
    return None


_INSPECTED_OK: dict[tuple[str, int, int], bool] = {}
_INSPECTED_OK_MAX = 256


def _probe_asset(path: Path) -> str | None:
    if asset_type_for(path) == "image":
        try:
            media.probe_image(path)
        except media.MediaError as e:
            return f"bad image ({e})"
        return None
    try:
        has_video = media.probe(path).has_video
    except media.MediaError as e:
        return f"bad video clip ({e})"
    return None if has_video else "clip has no video stream"


def inspect_asset(project_dir: Path, s: SceneState) -> str | None:
    """`asset_problem` plus a real decode/probe, so a corrupt file is caught. None = renderable.

    Clean results are cached by (resolved path, mtime_ns, size) so the QC panel does not re-decode every
    scene on each UI rerun. Problems are never cached, so a fixed file or a newly installed ffprobe is picked up.
    """
    why = asset_problem(project_dir, s)
    if why:
        return why
    path = project_dir / str(s.asset_path)
    stat = path.stat()
    key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    if key in _INSPECTED_OK:
        return None
    problem = _probe_asset(path)
    if problem is None:
        if len(_INSPECTED_OK) >= _INSPECTED_OK_MAX:
            del _INSPECTED_OK[next(iter(_INSPECTED_OK))]
        _INSPECTED_OK[key] = True
    return problem


def normalize_scene_visual(project_dir: Path, s: SceneState) -> NormalizedSceneVisual:
    """Scene asset -> clip for the scene renderer.

    A video is used as is (the renderer crops, trims or loops it). An image becomes a 1080x1920 clip of exactly
    ceil(voice seconds * fps) frames, stored under scenes/normalized/ keyed by (image content hash, frame count,
    encode version). Same image + same voice duration = same file, so the renderer sees identical bytes and
    keeps its scene-stage cache hit. A different image, or a switch between image and video, changes the bytes
    the renderer snapshots, which invalidates exactly that scene.
    """
    why = inspect_asset(project_dir, s)
    if why:
        raise RenderError(f"Scene {s.order}: {why}")
    src = project_dir / str(s.asset_path)
    if asset_type_for(src) != "image":
        return NormalizedSceneVisual(src, "video")
    frames = media.clip_frames(float(s.actual_audio_duration or 0), FPS)
    spec = f"{media.IMAGE_CLIP_VERSION}|{media.file_sha256(src)}|{frames}|{WIDTH}x{HEIGHT}@{FPS}"
    key = hashlib.sha256(spec.encode()).hexdigest()[:16]
    folder = project_dir / "scenes" / "normalized"
    out = folder / f"scene_{s.order:02d}-{key}.mp4"
    if out.is_file() and out.stat().st_size > 0:
        return NormalizedSceneVisual(out, "image", reused=True)
    folder.mkdir(parents=True, exist_ok=True)
    for stale in folder.glob(f"scene_{s.order:02d}-*.mp4"):
        stale.unlink()
    tmp = folder / f".scene_{s.order:02d}-{key}.tmp.mp4"
    try:
        media.image_to_clip(src, tmp, frames, WIDTH, HEIGHT, FPS)
        tmp.replace(out)
    except media.MediaError as e:
        raise RenderError(f"Scene {s.order}: image to clip failed ({e})") from e
    finally:
        tmp.unlink(missing_ok=True)
    return NormalizedSceneVisual(out, "image")


def check_ready(project_dir: Path, sb: StoryboardFile) -> list[str]:
    """Human-readable reasons the project cannot render yet (empty list = ready)."""
    problems = []
    for s in sb.scenes:
        why = asset_problem(project_dir, s)
        if why:
            problems.append(f"Scene {s.order}: {why}")
        if not s.audio_file or not (project_dir / s.audio_file).is_file() or not s.actual_audio_duration:
            problems.append(f"Scene {s.order}: missing voice audio")
        elif s.audio_stale:
            problems.append(f"Scene {s.order}: narration changed after voice was generated, regenerate voice")
    return problems


def _final_pass(src: Path, ass_file: Path, out: Path, bgm: Path | None, bgm_volume: float) -> None:
    sub = f"ass={media.filter_escape(ass_file.name)}:fontsdir={media.filter_escape(str(FONTS_DIR))}"
    norm = "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"
    args = ["-i", str(src)]
    if bgm:
        args += ["-stream_loop", "-1", "-i", str(bgm)]
        graph = (
            f"[0:v]{sub}[v];"
            f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={bgm_volume:.3f}[m];"
            "[0:a]aformat=sample_rates=48000:channel_layouts=stereo,asplit=2[voice][key];"
            "[m][key]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350[duck];"
            f"[voice][duck]amix=inputs=2:duration=first:normalize=0,{norm}[a]"
        )
    else:
        graph = f"[0:v]{sub}[v];[0:a]{norm}[a]"
    args += [
        "-filter_complex",
        graph,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(FPS),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        "-t",
        f"{media.probe(src).duration:.3f}",
        str(out),
    ]
    media.run_ffmpeg(args, cwd=ass_file.parent)


def render(
    project_id: str,
    project_dir: Path,
    language: str,
    sb: StoryboardFile,
    bgm: Path | None = None,
    bgm_volume: float = 0.25,
) -> RenderOutput:
    problems = check_ready(project_dir, sb)
    if problems:
        raise RenderError("; ".join(problems))
    toolchain = media.preflight_cached()["problems"]
    if toolchain:
        raise RenderError("; ".join(toolchain))
    started = time.monotonic()
    audios = {s.order: project_dir / str(s.audio_file) for s in sb.scenes}

    # Validate and normalize inputs up front so a corrupt upload names its scene (TC-11).
    visuals = {s.order: normalize_scene_visual(project_dir, s) for s in sb.scenes}
    if bgm and not bgm.is_file():
        raise RenderError(f"background music not found: {bgm}")

    # 1) subtitles from actual audio durations
    cues = subtitles.build_cues([(s.narration, float(s.actual_audio_duration or 0)) for s in sb.scenes], language)
    ass_file = project_dir / "subtitles" / "final.ass"
    subtitles.write_ass(cues, ass_file)
    subtitles.write_srt(cues, project_dir / "subtitles" / "final.srt")

    # 2) scene render + concat via MoneyPrinterTurbo (revisioned, reuses unchanged scenes)
    vp_mod = video_project_module()
    engine = vp_mod.VideoProject(project_dir / "renders" / "engine")
    manifest = {
        "project_id": project_id[:64],
        "settings": {"width": WIDTH, "height": HEIGHT, "fps": FPS, "narration_volume": 1.0, "bgm_volume": 0.0},
        "scenes": [
            {
                "id": f"scene_{s.order:02d}",
                "footage": str(visuals[s.order].path.resolve()),
                "audio": str(audios[s.order].resolve()),
                "narration": s.narration,
            }
            for s in sb.scenes
        ],
    }
    try:
        rev = engine.create_revision(manifest, relative_to=project_dir)
        status = engine.render(rev["revision_id"])
    except vp_mod.ProjectError as e:
        raise RenderError(f"scene render failed: {e}") from e
    if status.get("state") != "succeeded":
        raise RenderError(f"scene render ended in state {status.get('state')}")
    assembled = Path(status["export"]["path"])
    reused = sum(1 for v in (status.get("stages") or {}).values() if isinstance(v, dict) and v.get("reused"))

    # 3) captions + music + loudness
    out = project_dir / "renders" / "final-short.mp4"
    tmp = out.with_name("final-short.tmp.mp4")
    try:
        _final_pass(assembled, ass_file, tmp, bgm, bgm_volume)
        tmp.replace(out)
    except media.MediaError as e:
        tmp.unlink(missing_ok=True)
        raise RenderError(f"final pass failed: {e}") from e

    p = media.probe(out)
    return RenderOutput(
        out,
        p.duration,
        p.size or out.stat().st_size,
        p.width or 0,
        p.height or 0,
        int((time.monotonic() - started) * 1000),
        reused,
    )
