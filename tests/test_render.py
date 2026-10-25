"""TC-04, TC-05, TC-06, TC-08, TC-11 + P0-10..13. Spawns ffmpeg (slow, ~1 min).

Needs a libass-enabled FFmpeg (FFMPEG_PATH / FFPROBE_PATH from the process env); skipped by conftest otherwise.
"""

from __future__ import annotations

import hashlib

import pytest

from factory import media
from factory.pipeline import Factory
from factory.planner import MockPlanner
from factory.qc import qc_warnings
from factory.render import RenderError

pytestmark = pytest.mark.media


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def rendered(media_set, short_voice, tmp_path_factory):
    """One project, mixed sources, rendered with music. Shared by the assertions below."""
    mp = pytest.MonkeyPatch()
    data = tmp_path_factory.mktemp("render") / "data"
    mp.setenv("DATA_DIR", str(data))
    mp.setenv("MPT_HOME", str(data / "mpt"))
    mp.setenv("VOICE_PROVIDER", "mock")
    f = Factory()
    pid = f.create_project("Render test", 25, "vi").id
    f.plan(pid, MockPlanner())
    short_voice(f, pid)
    f.generate_voice(pid)
    clips = [media_set["landscape_short"], media_set["portrait_long"], media_set["small_4x3"], media_set["image"]]
    for s in f.storyboard(pid).scenes:
        f.attach_asset(pid, s.order, clips[s.order - 1])
    p = f.render(pid, media_set["music"])
    yield f, pid, p
    mp.undo()


def test_tc05_output_spec_mixed_sources(rendered):
    f, pid, p = rendered
    info = media.probe(f.store.path(pid, p.render.file))
    assert (info.width, info.height) == (1080, 1920)
    assert info.video_codec == "h264" and info.audio_codec == "aac"
    assert info.fps == pytest.approx(30, abs=0.01)
    assert p.render.status == "done" and p.status == "rendered"


def test_tc06_timing_follows_audio(rendered):
    f, pid, p = rendered
    sb = f.storyboard(pid)
    total = sum(s.actual_audio_duration for s in sb.scenes)
    assert p.render.duration == pytest.approx(total, abs=0.25)
    # scene 1 clip (3s) shorter than its audio -> looped, scene 2 clip (14s) longer -> trimmed
    assert sb.scene(1).asset_duration < sb.scene(1).actual_audio_duration
    assert sb.scene(2).asset_duration > sb.scene(2).actual_audio_duration


def test_subtitles_written(rendered):
    f, pid, _ = rendered
    ass = f.store.path(pid, "subtitles/final.ass").read_text(encoding="utf-8")
    assert ass.count("Dialogue:") >= 4


def test_cost_and_log_recorded(rendered):
    f, pid, p = rendered
    assert p.cost.render_duration_ms > 0
    assert "render ok" in f.store.path(pid, "logs/events.log").read_text()
    # only expected warning: scene 1 uses a 3s clip under ~6s of voice, so it loops
    assert qc_warnings(p, f.storyboard(pid)) == ["Scene 1: clip (3.0s) is much shorter than voice, it will loop"]


def test_tc04_tc08_replace_one_scene_and_rerender(rendered, media_set):
    f, pid, first = rendered
    audio_before = {s.order: sha(f.store.path(pid, s.audio_file)) for s in f.storyboard(pid).scenes}
    final_before = sha(f.store.path(pid, first.render.file))
    f.attach_asset(pid, 2, media_set["square"])
    assert f.store.get(pid).render_stale
    p = f.render(pid, media_set["music"])
    sb = f.storyboard(pid)
    assert sb.scene(2).asset_width == 1080 and sb.scene(2).asset_height == 1080  # only scene 2 changed
    assert {s.order: sha(f.store.path(pid, s.audio_file)) for s in sb.scenes} == audio_before
    assert p.render.render_count == 2 and p.render.reused_stages >= 9  # 3 scenes x 3 stages reused
    assert sha(f.store.path(pid, p.render.file)) != final_before
    assert not p.render_stale


def test_tc11_corrupt_clip_rejected_on_upload(rendered, media_set):
    f, pid, _ = rendered
    before = f.store.get(pid)
    with pytest.raises(Exception, match="corrupt.mp4|unreadable"):
        f.attach_asset(pid, 1, media_set["corrupt"])
    after = f.store.get(pid)  # a rejected upload leaves the project untouched: no error state, same status
    assert after.error is None and after.status == before.status


def test_tc11_corrupt_clip_on_disk_fails_render_with_scene(rendered):
    f, pid, _ = rendered
    clip = f.store.path(pid, f.storyboard(pid).scene(3).asset_path)
    original = clip.read_bytes()
    clip.write_bytes(b"broken")
    try:
        with pytest.raises(RenderError, match="Scene 3: bad video clip"):
            f.render(pid)
        assert f.store.get(pid).render.status == "error"
    finally:
        clip.write_bytes(original)


def test_render_refuses_missing_assets():
    f = Factory()
    pid = f.create_project("Not ready", 30).id
    f.plan(pid, MockPlanner())
    with pytest.raises(RenderError, match="Scene 1: missing visual asset"):
        f.render(pid)
