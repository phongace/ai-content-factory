from __future__ import annotations

import functools
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from factory import config as config_mod
from factory.fixtures import make_set

FIXTURE_DIR = Path(__file__).parent / ".fixtures"
_SKIPPED_MEDIA = pytest.StashKey[int]()


def pytest_configure(config: pytest.Config) -> None:
    # Tests never read the developer's .env.local, not even from module/session fixtures that run before
    # the function-scoped isolated_env. ffmpeg comes from the real process env (FFMPEG_PATH / FFPROBE_PATH).
    config_mod._ENV_LOADED = True
    config.stash[_SKIPPED_MEDIA] = 0


@functools.cache
def _media_skip_reason() -> str | None:
    """Why `media` tests cannot run here (None = ffmpeg with the ass filter and ffprobe are available)."""
    ffmpeg = shutil.which(os.environ.get("FFMPEG_PATH", "").strip() or "ffmpeg")
    ffprobe = shutil.which(os.environ.get("FFPROBE_PATH", "").strip() or "ffprobe")
    if not ffmpeg or not ffprobe:
        return "ffmpeg/ffprobe not found; set FFMPEG_PATH and FFPROBE_PATH to a libass-enabled FFmpeg"
    try:
        filters = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return f"ffmpeg at {ffmpeg} does not run; set FFMPEG_PATH and FFPROBE_PATH to a libass-enabled FFmpeg"
    if " ass " not in filters.stdout:
        return f"ffmpeg at {ffmpeg} lacks libass (ass filter); set FFMPEG_PATH and FFPROBE_PATH to a libass build"
    return None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    media_items = [item for item in items if item.get_closest_marker("media")]
    reason = _media_skip_reason() if media_items else None
    if reason:
        for item in media_items:
            item.add_marker(pytest.mark.skip(reason=reason))
        config.stash[_SKIPPED_MEDIA] = len(media_items)


def pytest_terminal_summary(terminalreporter, config: pytest.Config) -> None:
    if config.stash[_SKIPPED_MEDIA]:
        terminalreporter.write_line(f"media tests skipped: {_media_skip_reason()}")


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Every test gets its own DATA_DIR and no real credentials."""
    monkeypatch.setattr(config_mod, "_ENV_LOADED", True)  # never read the developer's .env.local
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MPT_HOME", str(tmp_path / "data" / "mpt"))
    for key in ("ANTHROPIC_API_KEY", "YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "BGM_FILE", "PLANNER"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("VOICE_PROVIDER", "mock")
    monkeypatch.setenv("VOICESTUDIO_BASE_URL", "http://127.0.0.1:9/v1")  # closed port
    yield


@pytest.fixture(scope="session")
def media_set() -> dict[str, Path]:
    marker = FIXTURE_DIR / ".complete-v2"
    if not marker.exists():
        make_set(FIXTURE_DIR)
        marker.write_text("ok")
    return {
        "landscape_short": FIXTURE_DIR / "landscape_3s.mp4",
        "portrait_long": FIXTURE_DIR / "portrait_14s.mp4",
        "small_4x3": FIXTURE_DIR / "small_640x480_4s.mp4",
        "square": FIXTURE_DIR / "square_8s.mp4",
        "landscape_long": FIXTURE_DIR / "landscape_12s.mp4",
        "portrait_short": FIXTURE_DIR / "portrait_2s.mp4",
        "image": FIXTURE_DIR / "still.png",
        "portrait_image": FIXTURE_DIR / "portrait_800x1200.jpg",
        "landscape_image": FIXTURE_DIR / "landscape_1600x900.jpg",
        "music": FIXTURE_DIR / "music.mp3",
        "corrupt": FIXTURE_DIR / "corrupt.mp4",
        "corrupt_image": FIXTURE_DIR / "corrupt.jpg",
    }
