"""QC checks shown next to the preview (tracker P0-13). Warnings, not blockers."""

from __future__ import annotations

from pathlib import Path

from .render import inspect_asset
from .schema import MAX_TOTAL, MIN_TOTAL, Project, StoryboardFile

HARD_SCENE_LIMIT = 10.0


def qc_warnings(p: Project, sb: StoryboardFile | None, project_dir: Path | None = None) -> list[str]:
    """`project_dir` enables the file checks (missing, unsupported or corrupt visual asset)."""
    out: list[str] = []
    if sb is None:
        return out
    durations = [s.actual_audio_duration for s in sb.scenes if s.actual_audio_duration]
    for s in sb.scenes:
        if s.actual_audio_duration and s.actual_audio_duration > HARD_SCENE_LIMIT:
            out.append(f"Scene {s.order}: voice is {s.actual_audio_duration:.1f}s (>10s), shorten the narration")
        if s.audio_stale:
            out.append(f"Scene {s.order}: narration edited after voice generation")
        if not s.asset_path:
            out.append(f"Scene {s.order}: no visual asset yet")
        elif project_dir is not None and (why := inspect_asset(project_dir, s)):
            out.append(f"Scene {s.order}: {why}")
        if s.asset_duration and s.actual_audio_duration and s.asset_duration < s.actual_audio_duration * 0.5:
            out.append(f"Scene {s.order}: clip ({s.asset_duration:.1f}s) is much shorter than voice, it will loop")
    if durations and len(durations) == len(sb.scenes):
        total = sum(durations)
        if not MIN_TOTAL <= total <= MAX_TOTAL:
            out.append(f"Total voice {total:.1f}s is outside {MIN_TOTAL}–{MAX_TOTAL}s")
        elif abs(total - p.target_duration) > p.target_duration * 0.3:
            out.append(f"Total voice {total:.1f}s is far from target {p.target_duration}s")
    r = p.render
    if r.status == "done":
        if (r.width, r.height) != (1080, 1920):
            out.append(f"Output is {r.width}x{r.height}, expected 1080x1920")
        if p.render_stale:
            out.append("Assets changed after the last render, render again")
    return out
