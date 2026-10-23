"""Single place where MoneyPrinterTurbo (package `mpt`, MIT) is imported.

MPT keeps its config.toml and runtime files under MPT_HOME; point it inside our data/ dir so it
never writes to the user's home folder. Pinned to mpt==1.3.8 because we use two private helpers.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .config import get_settings

_TOOLS = ("ffmpeg", "ffprobe")


def _prepare() -> None:
    home = get_settings().mpt_home
    home.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPT_HOME", str(home))


def same_file(a: str | None, b: str | None) -> bool:
    return a is not None and b is not None and os.path.realpath(a) == os.path.realpath(b)


def mpt_tool_paths() -> dict[str, str | None]:
    """What MPT's hard-coded bare `ffmpeg` / `ffprobe` resolve to through PATH right now."""
    return {tool: shutil.which(tool) for tool in _TOOLS}


def _link(shim: Path, name: str, target: str) -> None:
    link = shim / name
    if link.is_symlink() and os.readlink(link) == target:
        return
    tmp = shim / f".{name}.{os.getpid()}"
    tmp.unlink(missing_ok=True)
    tmp.symlink_to(target)
    os.replace(tmp, link)


def use_configured_ffmpeg() -> None:
    """Make MPT run the same ffmpeg/ffprobe as our own calls (FFMPEG_PATH / FFPROBE_PATH).

    MPT 1.3.8 resolves bare `ffmpeg`/`ffprobe` from PATH in `create_revision` (toolchain fingerprint)
    and as the `render()` defaults, with no way to override the fingerprint. So when a configured binary
    is not what PATH already resolves to, a shim dir (symlinks named ffmpeg/ffprobe) goes first on PATH.
    Idempotent and best effort: `media.preflight()` verifies the outcome and reports a mismatch.
    """
    s = get_settings()
    wanted = {"ffmpeg": shutil.which(s.ffmpeg), "ffprobe": shutil.which(s.ffprobe)}
    current = mpt_tool_paths()
    stale = {tool: path for tool, path in wanted.items() if path and not same_file(path, current[tool])}
    if not stale:
        return
    shim = s.mpt_home / "bin"
    try:
        shim.mkdir(parents=True, exist_ok=True)
        for tool, path in stale.items():
            _link(shim, tool, os.path.abspath(path))
    except OSError:
        return
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if not parts or parts[0] != str(shim):
        os.environ["PATH"] = os.pathsep.join([str(shim), *parts])


def video_project_module():
    # stdlib-only module, no config side effects (apart from pointing ffmpeg/ffprobe on PATH)
    use_configured_ffmpeg()
    from app.services import video_project

    return video_project


def voice_module():
    _prepare()
    from app.services import voice  # imports app.config -> creates MPT_HOME/config.toml

    return voice


def fonts_dir() -> Path:
    """Bundled fonts dir of the installed mpt wheel (Be Vietnam Pro is OFL licensed)."""
    import app

    return Path(app.__file__).resolve().parent / "_assets" / "resource" / "fonts"
