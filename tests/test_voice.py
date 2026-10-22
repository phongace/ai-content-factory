"""TC-03, TC-10 + manual import (P0-06, P0-07)."""

from __future__ import annotations

import pytest

from factory import media
from factory.pipeline import Factory
from factory.planner import MockPlanner
from factory.voice import VoiceError, VoiceStudioProvider, get_voice_provider


@pytest.fixture
def planned():
    f = Factory()
    pid = f.create_project("Voice test", 30, "vi").id
    f.plan(pid, MockPlanner())
    return f, pid


def test_tc03_every_scene_gets_wav_and_duration(planned):
    f, pid = planned
    f.generate_voice(pid)
    sb = f.storyboard(pid)
    for s in sb.scenes:
        path = f.store.path(pid, s.audio_file)
        assert path.name == f"scene_{s.order:02d}.wav"
        assert s.actual_audio_duration == pytest.approx(media.audio_duration(path), abs=0.01)
        assert s.audio_source == "mock" and not s.audio_stale
    assert f.store.get(pid).status == "assets"


def test_tc10_voicestudio_down_fails_clearly_and_retry_works(planned):
    f, pid = planned
    with pytest.raises(RuntimeError, match="VoiceStudio not reachable"):
        f.generate_voice(pid, [1], provider="voicestudio")
    p = f.store.get(pid)
    assert p.status == "error" and p.error.startswith("voice:") and p.current_stage == "voice"
    f.generate_voice(pid, [1], provider="mock")  # retry with another provider
    p = f.store.get(pid)
    assert p.status == "assets" and p.error is None


def test_voicestudio_ping_raises_voice_error():
    with pytest.raises(VoiceError):
        VoiceStudioProvider().ping()


def test_unknown_provider():
    with pytest.raises(VoiceError, match="unknown voice provider"):
        get_voice_provider("nope")


def test_manual_import_normalizes_audio(planned, media_set):
    f, pid = planned
    f.import_voice(pid, 2, media_set["music"])  # stereo mp3 -> mono 48k wav
    s = f.storyboard(pid).scene(2)
    info = media.probe(f.store.path(pid, s.audio_file))
    assert s.audio_source == "manual" and info.audio_codec == "pcm_s16le"
    assert s.actual_audio_duration == pytest.approx(40, abs=0.1)


def test_manual_import_rejects_corrupt(planned, tmp_path):
    f, pid = planned
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"nope")
    with pytest.raises(media.MediaError):
        f.import_voice(pid, 1, bad)
