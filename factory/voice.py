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


def _api_error(r: requests.Response) -> str:
    """VoiceStudio answers with OpenAI's {"error": {"message": ...}} shape; fall back to the raw body."""
    try:
        body = r.json()
    except ValueError:
        return r.text[:300].strip()
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict) and isinstance(err.get("message"), str):
        return err["message"]
    return str(body)[:300]


class VoiceStudioProvider:
    """VoiceStudio local server, OpenAI-compatible POST /v1/audio/speech, called directly so the
    scene language is sent and server errors (401, 403 licence, 409 ambiguous voice, 503 loading)
    reach the user instead of a generic failure."""

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
        what = f"voice={self.s.voicestudio_voice}, model={self.s.voicestudio_model}, language={req.language}"
        try:
            r = requests.post(
                f"{self.s.voicestudio_base_url}/audio/speech",
                headers={"Authorization": f"Bearer {self.s.voicestudio_api_key}"},
                json={
                    "model": self.s.voicestudio_model,
                    "voice": self.s.voicestudio_voice,
                    "input": req.text,
                    "response_format": "wav",
                    "language": req.language,
                },
                timeout=self.s.voicestudio_timeout,
            )
        except requests.Timeout as e:
            raise VoiceError(
                f"VoiceStudio did not answer within {self.s.voicestudio_timeout:.0f}s ({what}). "
                "CPU synthesis is slow: raise VOICESTUDIO_TIMEOUT or use a smaller engine, then retry."
            ) from e
        except requests.RequestException as e:
            raise VoiceError(f"VoiceStudio request failed ({what}): {type(e).__name__}, retry") from e
        if r.status_code == 401:
            raise VoiceError("VoiceStudio rejected the API key (401): set VOICESTUDIO_API_KEY in .env.local")
        if r.status_code != 200:
            raise VoiceError(f"VoiceStudio error {r.status_code} ({what}): {_api_error(r)}")
        if not r.content:
            raise VoiceError(f"VoiceStudio returned no audio ({what}), retry")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "voice.wav"
            out.write_bytes(r.content)
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
