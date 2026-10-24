"""Image-or-video scene assets through the real renderer. Spawns ffmpeg (slow, ~2-3 min).

Needs a libass-enabled FFmpeg (FFMPEG_PATH / FFPROBE_PATH from the process env); skipped by conftest otherwise.
"""

from __future__ import annotations

import contextlib
import math
import shutil
import subprocess
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from factory import media
from factory.config import get_settings
from factory.fixtures import make_circle_image, make_image
from factory.mpt_bridge import video_project_module
from factory.pipeline import Factory
from factory.planner import MockPlanner
from factory.qc import qc_warnings
from factory.render import RenderError, normalize_scene_visual
from factory.schema import SceneState

pytestmark = pytest.mark.media

APP = str(Path(__file__).resolve().parent.parent / "ui" / "app.py")
FPS = 30


def scene(order: int, asset_path: str, voice_seconds: float) -> SceneState:
    return SceneState(
        order=order,
        target_duration=6,
        narration="x",
        video_prompt="p",
        asset_path=asset_path,
        actual_audio_duration=voice_seconds,
    )


def project_with_image(tmp_path: Path, src: Path, name: str = "scene_01") -> Path:
    project = tmp_path / "project"
    (project / "scenes").mkdir(parents=True)
    shutil.copyfile(src, project / "scenes" / f"{name}{src.suffix}")
    return project


def gray_frame(clip: Path, index: int) -> bytes:
    cmd = [get_settings().ffmpeg, "-v", "error", "-i", str(clip), "-vf", f"select=eq(n\\,{index}),format=gray"]
    return subprocess.run([*cmd, "-frames:v", "1", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout


def disc_size(frame: bytes, width: int = 1080, height: int = 1920) -> tuple[int, int]:
    rows = [r for r in range(height) if max(frame[r * width : (r + 1) * width]) > 128]
    cols = [c for c in range(width) if max(frame[c::width]) > 128]
    return cols[-1] - cols[0] + 1, rows[-1] - rows[0] + 1


def stage_report(f: Factory, pid: str) -> tuple[set[str], set[str]]:
    """(reused, rebuilt) engine stages of the latest render."""
    engine = video_project_module().VideoProject(f.store.path(pid, "renders/engine"))
    stages = engine.status().get("stages") or {}
    return {k for k, v in stages.items() if v.get("reused")}, {k for k, v in stages.items() if not v.get("reused")}


# ---------------------------------------------------------------- normalization


@pytest.fixture(scope="module")
def stills(media_set, tmp_path_factory) -> dict[str, Path]:
    out = {"portrait_jpg": media_set["portrait_image"], "landscape_jpg": media_set["landscape_image"]}
    out["png"] = media_set["image"]
    with contextlib.suppress(media.MediaError):  # an ffmpeg build without libwebp cannot make the fixture
        out["webp"] = make_image(tmp_path_factory.mktemp("webp") / "still.webp", 1280, 720)
    return out


@pytest.mark.parametrize("kind", ["portrait_jpg", "landscape_jpg", "png", "webp"])
def test_each_image_format_becomes_an_exact_1080x1920_clip(kind, stills, tmp_path):
    if kind not in stills:
        pytest.skip("this ffmpeg build cannot encode webp")
    src = stills[kind]
    project = project_with_image(tmp_path, src)
    voice = 6.37
    visual = normalize_scene_visual(project, scene(1, f"scenes/scene_01{src.suffix}", voice))
    info = media.probe(visual.path)
    assert visual.asset_type == "image" and visual.path.parent.name == "normalized"
    assert (info.width, info.height) == (1080, 1920)
    assert info.fps == pytest.approx(FPS) and info.video_codec == "h264" and not info.has_audio
    frames = math.ceil(voice * FPS)
    assert info.duration == pytest.approx(frames / FPS, abs=0.002)
    assert 0 <= info.duration - voice < 1 / FPS + 0.001


@pytest.mark.parametrize(("width", "height", "radius"), [(1600, 900, 200), (800, 1200, 300), (1000, 1000, 200)])
def test_image_is_cover_cropped_never_stretched(width, height, radius, tmp_path):
    src = make_circle_image(tmp_path / "disc.png", width, height, radius)
    project = project_with_image(tmp_path, src)
    seconds = 4.0
    clip = normalize_scene_visual(project, scene(1, "scenes/scene_01.png", seconds)).path
    first_w, first_h = disc_size(gray_frame(clip, 0))
    expected = 2 * radius * max(1080 / width, 1920 / height)  # uniform cover scale, centre crop
    assert abs(first_w - first_h) <= 3
    assert first_h == pytest.approx(expected, abs=4)
    last_w, last_h = disc_size(gray_frame(clip, int(seconds * FPS) - 1))
    assert abs(last_w - last_h) <= 3  # the slow zoom keeps the aspect ratio
    assert last_h > first_h


def test_normalized_clip_is_cached_by_content_and_voice_frames(media_set, tmp_path):
    project = project_with_image(tmp_path, media_set["landscape_image"])
    asset = "scenes/scene_01.jpg"
    first = normalize_scene_visual(project, scene(1, asset, 6.37))
    assert not first.reused
    stamp = first.path.stat().st_mtime_ns

    again = normalize_scene_visual(project, scene(1, asset, 6.37))
    assert again.reused and again.path == first.path and again.path.stat().st_mtime_ns == stamp

    same_frames = normalize_scene_visual(project, scene(1, asset, 6.39))  # 6.37s and 6.39s are both 192 frames
    assert same_frames.reused and same_frames.path == first.path

    longer = normalize_scene_visual(project, scene(1, asset, 6.41))  # 193 frames: a different clip
    assert not longer.reused and longer.path != first.path
    assert not first.path.exists()

    shutil.copyfile(media_set["portrait_image"], project / asset)  # same name, different content
    other = normalize_scene_visual(project, scene(1, asset, 6.41))
    assert not other.reused and other.path != longer.path
    assert len(list((project / "scenes" / "normalized").glob("scene_01-*.mp4"))) == 1


# ---------------------------------------------------------------- mixed render, state, cache invalidation


@pytest.fixture(scope="module")
def mixed(media_set, tmp_path_factory):
    """Landscape video, portrait image, portrait video, landscape image: rendered once, shared below."""
    mp = pytest.MonkeyPatch()
    data = tmp_path_factory.mktemp("mixed") / "data"
    mp.setenv("DATA_DIR", str(data))
    mp.setenv("MPT_HOME", str(data / "mpt"))
    mp.setenv("VOICE_PROVIDER", "mock")
    f = Factory()
    pid = f.create_project("Mixed visuals", 30, "vi").id
    f.plan(pid, MockPlanner())
    f.generate_voice(pid)
    for order, key in enumerate(["landscape_short", "portrait_image", "portrait_long", "image"], start=1):
        f.attach_asset(pid, order, media_set[key])
    p = f.render(pid)
    yield f, pid, p
    mp.undo()


def test_mixed_video_and_image_scenes_render_1080x1920(mixed):
    f, pid, p = mixed
    info = media.probe(f.store.path(pid, p.render.file))
    assert (info.width, info.height) == (1080, 1920)
    assert info.video_codec == "h264" and info.audio_codec == "aac" and info.fps == pytest.approx(FPS, abs=0.01)
    sb = f.storyboard(pid)
    assert p.render.duration == pytest.approx(sum(s.actual_audio_duration for s in sb.scenes), abs=0.25)


def test_image_clips_last_exactly_the_voice_and_never_loop(mixed):
    f, pid, p = mixed
    sb = f.storyboard(pid)
    for order in (2, 4):
        clips = list(f.store.path(pid, "scenes/normalized").glob(f"scene_{order:02d}-*.mp4"))
        assert len(clips) == 1
        voice = sb.scene(order).actual_audio_duration
        assert 0 <= media.probe(clips[0]).duration - voice < 1 / FPS + 0.001
    assert qc_warnings(p, sb, f.store.dir(pid)) == ["Scene 1: clip (3.0s) is much shorter than voice, it will loop"]


def test_asset_state_survives_reload(mixed):
    f, pid, _ = mixed
    sb = Factory(f.s).storyboard(pid)  # a new service instance over the same data dir, as after an app restart
    assert [s.asset_type for s in sb.scenes] == ["video", "image", "video", "image"]
    assert [s.asset_source for s in sb.scenes] == ["muse", "upload", "muse", "upload"]
    assert [Path(str(s.asset_path)).suffix for s in sb.scenes] == [".mp4", ".jpg", ".mp4", ".png"]
    image = sb.scene(2)
    assert (image.asset_width, image.asset_height, image.asset_duration) == (800, 1200, None)
    assert sb.scene(1).asset_duration == pytest.approx(3.0, abs=0.05)


def test_muse_cost_counts_only_muse_assets(media_set, monkeypatch):
    monkeypatch.setenv("MUSE_COST_PER_CLIP", "0.5")
    f = Factory()
    pid = f.create_project("Cost", 30).id
    f.plan(pid, MockPlanner())
    f.attach_asset(pid, 1, media_set["portrait_short"])
    f.attach_asset(pid, 2, media_set["portrait_image"])
    f.attach_asset(pid, 3, media_set["portrait_short"], "upload")
    p = f.store.get(pid)
    assert p.muse_clip_count == 1 and p.cost.muse_estimated_cost == 0.5


def test_unchanged_rerender_reuses_every_stage_and_normalized_clip(mixed):
    f, pid, first = mixed
    folder = f.store.path(pid, "scenes/normalized")
    before = {x.name: x.stat().st_mtime_ns for x in folder.glob("*.mp4")}
    assert len(before) == 2
    p = f.render(pid)
    reused, rebuilt = stage_report(f, pid)
    assert not rebuilt and len(reused) == 14  # 4 scenes x (audio, timing, scene) + assembly + export
    assert p.render.reused_stages == 14 and p.render.render_count == first.render.render_count + 1
    assert {x.name: x.stat().st_mtime_ns for x in folder.glob("*.mp4")} == before


def test_replacing_an_image_rebuilds_only_that_scene(mixed, media_set):
    f, pid, _ = mixed
    f.attach_asset(pid, 2, media_set["landscape_image"])
    assert f.store.get(pid).render_stale
    assert "Assets changed after the last render, render again" in qc_warnings(
        f.store.get(pid), f.storyboard(pid), f.store.dir(pid)
    )
    assert not list(f.store.path(pid, "scenes/normalized").glob("scene_02-*"))  # stale cache dropped at replace
    p = f.render(pid)
    reused, rebuilt = stage_report(f, pid)
    assert rebuilt == {"scene_02:scene", "assembly", "export"} and len(reused) == 11
    assert len(list(f.store.path(pid, "scenes/normalized").glob("scene_02-*.mp4"))) == 1
    assert not p.render_stale and (p.render.width, p.render.height) == (1080, 1920)


def test_switching_between_image_and_video_rebuilds_only_those_scenes(mixed, media_set):
    f, pid, _ = mixed
    f.attach_asset(pid, 2, media_set["landscape_short"])  # image -> video
    f.attach_asset(pid, 3, media_set["portrait_image"])  # video -> image
    sb = f.storyboard(pid)
    assert (sb.scene(2).asset_type, sb.scene(3).asset_type) == ("video", "image")
    names = sorted(x.name for x in f.store.path(pid, "scenes").glob("scene_0[23].*"))
    assert names == ["scene_02.mp4", "scene_03.jpg"]  # the old file of the other type is gone
    p = f.render(pid)
    reused, rebuilt = stage_report(f, pid)
    assert rebuilt == {"scene_02:scene", "scene_03:scene", "assembly", "export"} and len(reused) == 10
    assert (p.render.width, p.render.height) == (1080, 1920)


def test_remove_asset_clears_state_and_files(mixed, media_set):
    f, pid, _ = mixed
    f.attach_asset(pid, 4, media_set["image"], "generated")
    assert f.storyboard(pid).scene(4).asset_source == "generated"
    f.remove_asset(pid, 4)
    s = f.storyboard(pid).scene(4)
    assert (s.asset_type, s.asset_path, s.asset_source, s.asset_width) == (None, None, None, None)
    assert not list(f.store.path(pid, "scenes").glob("scene_04.*"))
    assert not list(f.store.path(pid, "scenes/normalized").glob("scene_04-*"))
    assert f.store.get(pid).render_stale
    with pytest.raises(RenderError, match="Scene 4: missing visual asset"):
        f.render(pid)


def test_corrupt_and_truncated_images_are_rejected_on_upload(mixed, media_set, tmp_path):
    f, pid, _ = mixed
    truncated = tmp_path / "truncated.jpg"
    whole = media_set["landscape_image"].read_bytes()
    truncated.write_bytes(whole[: len(whole) // 2])
    before = f.storyboard(pid).scene(3).asset_path
    for bad in (media_set["corrupt_image"], truncated):
        with pytest.raises(media.MediaError, match="not a readable image|corrupt or truncated"):
            f.attach_asset(pid, 3, bad)
        assert f.storyboard(pid).scene(3).asset_path == before
    assert f.store.get(pid).error.startswith("clips:")


def test_deleted_or_corrupt_image_on_disk_fails_render_and_qc_naming_the_scene(mixed, media_set):
    f, pid, _ = mixed
    f.attach_asset(pid, 4, media_set["image"])
    pdir = f.store.dir(pid)
    image = f.store.path(pid, "scenes/scene_04.png")
    original = image.read_bytes()
    try:
        image.unlink()
        with pytest.raises(RenderError, match=r"Scene 4: visual asset file is missing \(scene_04.png\)"):
            f.render(pid)
        assert "Scene 4: visual asset file is missing (scene_04.png)" in qc_warnings(
            f.store.get(pid), f.storyboard(pid), pdir
        )

        image.write_bytes(b"garbage")
        with pytest.raises(RenderError, match="Scene 4: bad image"):
            f.render(pid)
        assert any(w.startswith("Scene 4: bad image") for w in qc_warnings(f.store.get(pid), f.storyboard(pid), pdir))
        assert f.store.get(pid).render.status == "error"
    finally:
        image.write_bytes(original)
    assert f.render(pid).render.status == "done"


# ---------------------------------------------------------------- UI


def test_storyboard_ui_shows_type_source_preview_and_removes_assets(mixed, monkeypatch):
    f, pid, _ = mixed
    monkeypatch.setenv("DATA_DIR", str(f.s.data_dir))
    monkeypatch.setenv("MPT_HOME", str(f.s.mpt_home))
    at = AppTest.from_file(APP, default_timeout=120)
    at.session_state["pid"] = pid
    at.run()
    assert not at.exception
    captions = [c.value for c in at.caption]
    assert any(c.startswith("Type: Video · Source: muse · scene_01.mp4") for c in captions)
    assert any(c.startswith("Type: Image · Source: upload · scene_03.jpg · 800x1200") for c in captions)
    assert len([b for b in at.button if b.label == "Remove Asset"]) == 4

    at.button(key="rm1").click().run()
    assert not at.exception
    assert f.storyboard(pid).scene(1).asset_path is None
    assert not list(f.store.path(pid, "scenes").glob("scene_01.*"))
