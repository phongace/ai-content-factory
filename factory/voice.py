"""Per-scene voice (tracker P0-06, P0-07). Each scene -> audio/scene_XX.wav; ffprobe duration is the
source of truth for rendering."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import requests

from . import media
from .config import Settings, get_settings
from .planner import narration_seconds


class VoiceError(Exception):
    pass


@dataclass
class VoiceRequest:
    text: str
    language: str
    output: Path  # final .wav path


class VoiceProvider(Protocol):
    name: str

    def generate(self, req: VoiceRequest) -> None: ...


class MockVoiceProvider:
    """Quiet tone with a realistic length for the narration. For pipeline tests only."""

    name = "mock"

    def generate(self, req: VoiceRequest) -> None:
        secs = narration_seconds(req.text, req.language)
        media.run_ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=220:sample_rate=48000:duration={secs}",
                "-af",
                "volume=0.15",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(req.output),
            ]
        )


class EdgeVoiceProvider:
    """Microsoft Edge read-aloud via MPT. Free, has Vietnamese voices, but no clear commercial
    license -> DEV/TEST ONLY, do not publish monetized videos with it."""

    name = "edge"

    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()

    def generate(self, req: VoiceRequest) -> None:
        from .mpt_bridge import voice_module

        voice = voice_module()
        name = self.s.edge_voice_vi if req.language == "vi" else self.s.edge_voice_en
        with tempfile.TemporaryDirectory() as tmp:
            mp3 = Path(tmp) / "voice.mp3"
            if (
                voice.tts(text=req.text, voice_name=name, voice_rate=1.0, voice_file=str(mp3)) is None
                or not mp3.is_file()
            ):
                raise VoiceError(f"Edge TTS failed for voice {name} (network or service error)")
            media.to_wav(mp3, req.output)


class VoiceStudioProvider:
    """VoiceStudio local server, OpenAI-compatible /v1/audio/speech. Reuses MPT's
    `_openai_compatible_tts` transport (same one MPT uses for Kokoro/Chatterbox)."""

    name = "voicestudio"

    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()

    def ping(self) -> None:
        try:
            requests.get(self.s.voicestudio_base_url, timeout=3)
        except requests.RequestException as e:
            raise VoiceError(
                f"VoiceStudio not reachable at {self.s.voicestudio_base_url}. "
                "Start VoiceStudio (or its backend) and check VOICESTUDIO_BASE_URL, then retry."
            ) from e

    def generate(self, req: VoiceRequest) -> None:
        self.ping()
        from .mpt_bridge import voice_module

        voice = voice_module()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "voice.mp3"
            sub = voice._openai_compatible_tts(
                "voicestudio",
                self.s.voicestudio_base_url,
                self.s.voicestudio_api_key,
                self.s.voicestudio_model,
                self.s.voicestudio_voice,
                req.text,
                1.0,
                str(out),
            )
            # MPT returns None on failure instead of raising; turn it into a clear stage error (TC-10).
            if sub is None or not out.is_file() or out.stat().st_size == 0:
                raise VoiceError(
                    f"VoiceStudio returned no audio (voice={self.s.voicestudio_voice}, "
                    f"model={self.s.voicestudio_model}). Check the voice id/engine in VoiceStudio, then retry."
                )
            media.to_wav(out, req.output)


PROVIDERS = {"mock": MockVoiceProvider, "edge": EdgeVoiceProvider, "voicestudio": VoiceStudioProvider}


def get_voice_provider(name: str | None = None, settings: Settings | None = None) -> VoiceProvider:
    s = settings or get_settings()
    key = (name or s.voice_provider).lower()
    if key not in PROVIDERS:
        raise VoiceError(f"unknown voice provider '{key}' (use one of: {', '.join(PROVIDERS)}, or upload audio)")
    cls = PROVIDERS[key]
    return cls() if key == "mock" else cls(s)


def import_audio(src: str | Path, output: Path) -> None:
    """Manual voice (exported from VoiceStudio UI or recorded): validate + normalize to wav."""
    media.audio_duration(src)  # raises MediaError for corrupt/no-audio files
    if Path(src).suffix.lower() == ".wav":
        tmp = output.with_suffix(".tmp.wav")
        shutil.copyfile(src, tmp)
        media.to_wav(tmp, output)
        tmp.unlink(missing_ok=True)
    else:
        media.to_wav(src, output)
