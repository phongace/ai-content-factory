"""Manual Muse hand-off helpers: one prompt sheet per project out, many downloaded assets in.

Muse itself stays manual (no API, no browser automation): these only cut the copy/paste and per-scene uploads.
"""

from __future__ import annotations

import re
from pathlib import Path

from .schema import StoryboardFile, asset_type_for
from .store import scene_name

# "scene_01.mp4", "Scene 2.png", "canh-3.jpg", "s04.mov", "05_muse.mp4"; the number must not run into more digits
_NAMED = re.compile(r"(?:^|[^a-z0-9])(?:scene|canh|s)[ _-]?0*(\d{1,2})(?!\d)")
_LEADING = re.compile(r"^0*(\d{1,2})(?!\d)")


def scene_from_name(path: str | Path) -> int | None:
    stem = Path(path).stem.lower()
    m = _NAMED.search(stem) or _LEADING.match(stem)
    return int(m.group(1)) if m else None


def asset_files(paths: list[Path]) -> list[Path]:
    """Video/image files among `paths`; a folder contributes its direct children (hidden files skipped)."""
    out: list[Path] = []
    for p in paths:
        children = sorted(p.iterdir()) if p.is_dir() else [p]
        out += [c for c in children if c.is_file() and not c.name.startswith(".") and asset_type_for(c)]
    return out


def match_by_name(files: list[Path], orders: list[int]) -> tuple[dict[int, Path], list[Path]]:
    """Scene -> file from the number in each file name; the newest file wins when several name the same scene."""
    matched: dict[int, Path] = {}
    unmatched: list[Path] = []
    for f in sorted(files, key=lambda f: f.stat().st_mtime):
        order = scene_from_name(f)
        if order in orders:
            matched[order] = f
        else:
            unmatched.append(f)
    return matched, unmatched


def match_by_time(files: list[Path], missing: list[int]) -> dict[int, Path]:
    """The newest len(missing) files, oldest first, onto the scenes that have no asset yet, in scene order.

    Fits generating scenes in Muse one after another and downloading each as it finishes."""
    newest = sorted(files, key=lambda f: f.stat().st_mtime)[-len(missing) :] if missing else []
    return dict(zip(missing, newest, strict=False))


def muse_sheet(pid: str, title: str, sb: StoryboardFile) -> str:
    lines = [f"# Muse prompts: {title}", "", f"Project: {pid}", ""]
    if sb.visual_bible:
        lines += [
            "Visual bible (đã nằm ở đầu mọi prompt, giữ nguyên để nhân vật/bối cảnh giống nhau giữa các clip):",
            sb.visual_bible,
            "",
            "Nếu Muse cho thêm ảnh tham chiếu: dùng CÙNG MỘT ảnh nhân vật cho mọi scene.",
            "",
        ]
    lines += [
        "Tải từng clip về, đặt tên theo 'lưu thành' (hoặc tải lần lượt scene 1, 2, 3... rồi thêm --by-time):",
        f"  uv run factory import-assets {pid} ~/Downloads",
        "",
    ]
    for s in sb.scenes:
        duration = f"voice {s.actual_audio_duration:.1f}s" if s.actual_audio_duration else f"~{s.target_duration:.0f}s"
        status = f" · ĐÃ CÓ {s.asset_type} (bỏ qua nếu giữ)" if s.asset_path else ""
        head = f"## Scene {s.order} · {duration} · lưu thành {scene_name(s.order, 'mp4')}{status}"
        lines += [head, s.video_prompt, ""]
    return "\n".join(lines)
