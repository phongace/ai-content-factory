"""Settings from environment (+ optional .env.local). Read lazily so tests can override env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_ENV_LOADED = False


def load_env_file(path: Path | None = None) -> None:
    """Minimal .env.local loader (KEY=VALUE lines). Real env vars always win."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    for candidate in [path] if path else [ROOT / ".env.local", ROOT / ".env"]:
        if not candidate or not candidate.is_file():
            continue
        for raw in candidate.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip().strip('"').strip("'")
            if value:
                os.environ.setdefault(key.strip(), value)


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or default


def _num(name: str, default: float) -> float:
    try:
        return float(_env(name) or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    ffmpeg: str
    ffprobe: str
    anthropic_api_key: str | None
    anthropic_model: str
    anthropic_input_price: float
    anthropic_output_price: float
    planner: str
    voice_provider: str
    edge_voice_vi: str
    edge_voice_en: str
    voicestudio_base_url: str
    voicestudio_voice: str
    voicestudio_model: str
    voicestudio_api_key: str
    voicestudio_timeout: float
    voice_cost_per_second: float
    muse_cost_per_clip: float
    bgm_file: str | None
    bgm_volume: float
    youtube_client_id: str | None
    youtube_client_secret: str | None
    youtube_client_secret_file: Path
    youtube_api_key: str | None
    discovery_cache_hours: float

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def discovery_dir(self) -> Path:
        return self.data_dir / "discoveries"

    @property
    def opportunities_dir(self) -> Path:
        return self.data_dir / "opportunities"

    @property
    def patterns_dir(self) -> Path:
        return self.data_dir / "patterns"

    @property
    def mpt_home(self) -> Path:
        return self.data_dir / "mpt"

    @property
    def youtube_token_file(self) -> Path:
        return self.data_dir / "secrets" / "youtube_token.json"

    @property
    def youtube_client_from_env(self) -> bool:
        return bool(self.youtube_client_id and self.youtube_client_secret)

    @property
    def youtube_configured(self) -> bool:
        """An OAuth client is available: the Desktop-app JSON file (primary) or the env ID/secret pair."""
        return self.youtube_client_secret_file.is_file() or self.youtube_client_from_env


def get_settings() -> Settings:
    load_env_file()
    data_dir = Path(_env("DATA_DIR") or ROOT / "data").expanduser().resolve()
    secret_file = _env("YOUTUBE_CLIENT_SECRET_FILE")
    return Settings(
        data_dir=data_dir,
        ffmpeg=_env("FFMPEG_PATH", "ffmpeg") or "ffmpeg",
        ffprobe=_env("FFPROBE_PATH", "ffprobe") or "ffprobe",
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        anthropic_model=_env("ANTHROPIC_MODEL", "claude-sonnet-5-5") or "claude-sonnet-5-5",
        anthropic_input_price=_num("ANTHROPIC_INPUT_PRICE_PER_MTOK", 2.0),
        anthropic_output_price=_num("ANTHROPIC_OUTPUT_PRICE_PER_MTOK", 10.0),
        planner=(_env("PLANNER", "auto") or "auto").lower(),
        voice_provider=(_env("VOICE_PROVIDER", "mock") or "mock").lower(),
        edge_voice_vi=_env("EDGE_VOICE_VI", "vi-VN-HoaiMyNeural") or "vi-VN-HoaiMyNeural",
        edge_voice_en=_env("EDGE_VOICE_EN", "en-US-AndrewNeural") or "en-US-AndrewNeural",
        voicestudio_base_url=(_env("VOICESTUDIO_BASE_URL", "http://127.0.0.1:3900/v1") or "").rstrip("/"),
        voicestudio_voice=_env("VOICESTUDIO_VOICE", "default") or "default",
        voicestudio_model=_env("VOICESTUDIO_MODEL", "tts-1") or "tts-1",
        voicestudio_api_key=_env("VOICESTUDIO_API_KEY", "local") or "local",
        # CPU-only backends synthesize ~30x slower than real time, so one scene can take minutes
        voicestudio_timeout=_num("VOICESTUDIO_TIMEOUT", 900.0),
        voice_cost_per_second=_num("VOICE_COST_PER_SECOND", 0.0),
        muse_cost_per_clip=_num("MUSE_COST_PER_CLIP", 0.0),
        bgm_file=_env("BGM_FILE"),
        bgm_volume=_num("BGM_VOLUME", 0.25),
        youtube_client_id=_env("YOUTUBE_CLIENT_ID"),
        youtube_client_secret=_env("YOUTUBE_CLIENT_SECRET"),
        # relative paths resolve against the repo root; the default sits next to the token file
        youtube_client_secret_file=(
            ROOT / Path(secret_file).expanduser()
            if secret_file
            else data_dir / "secrets" / "youtube_client_secret.json"
        ),
        # public search/videos.list for discovery; the upload OAuth token only has the youtube.upload scope
        youtube_api_key=_env("YOUTUBE_API_KEY"),
        discovery_cache_hours=_num("DISCOVERY_CACHE_HOURS", 12.0),
    )
