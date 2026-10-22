"""Single place where MoneyPrinterTurbo (package `mpt`, MIT) is imported.

MPT keeps its config.toml and runtime files under MPT_HOME; point it inside our data/ dir so it
never writes to the user's home folder. Pinned to mpt==1.3.8 because we use two private helpers.
"""

from __future__ import annotations

import os
from pathlib import Path

from .config import get_settings


def _prepare() -> None:
    home = get_settings().mpt_home
    home.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPT_HOME", str(home))


def video_project_module():
    # stdlib-only module, no config side effects
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
