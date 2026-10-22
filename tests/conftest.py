from __future__ import annotations

from pathlib import Path

import pytest

from factory import config as config_mod
from factory.fixtures import make_set

FIXTURE_DIR = Path(__file__).parent / ".fixtures"


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
    marker = FIXTURE_DIR / ".complete"
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
        "music": FIXTURE_DIR / "music.mp3",
        "corrupt": FIXTURE_DIR / "corrupt.mp4",
    }
