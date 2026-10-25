"""Scene visual assets (video or image): model, backward compatibility and failure reporting. No rendering."""

from __future__ import annotations

import json

import pytest

from factory.pipeline import Factory
from factory.planner import MockPlanner
from factory.qc import qc_warnings
from factory.render import check_ready
from factory.schema import StoryboardFile


@pytest.fixture
def planned():
    f = Factory()
    pid = f.create_project("Assets", 30).id
    f.plan(pid, MockPlanner())
    return f, pid


def test_legacy_clip_fields_load_as_video_asset(planned):
    f, pid = planned
    path = f.store.dir(pid) / "storyboard.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["scenes"][0].update(clip_file="scenes/scene_01.mp4", clip_duration=3.0, clip_width=1280, clip_height=720)
    data["scenes"][1].update(clip_file="scenes/scene_02.webp")
    path.write_text(json.dumps(data), encoding="utf-8")

    sb = Factory().storyboard(pid)
    s1, s2 = sb.scene(1), sb.scene(2)
    assert (s1.asset_type, s1.asset_path, s1.asset_source) == ("video", "scenes/scene_01.mp4", None)
    assert (s1.asset_duration, s1.asset_width, s1.asset_height) == (3.0, 1280, 720)
    assert (s2.asset_type, s2.asset_path) == ("image", "scenes/scene_02.webp")
    assert sb.scene(3).asset_path is None and sb.scene(3).asset_type is None

    f.store.save_storyboard(pid, sb)
    assert "clip_" not in path.read_text(encoding="utf-8")


def test_new_asset_fields_round_trip():
    raw = {
        "title": "t",
        "description": "d",
        "hashtags": ["#a"],
        "hook": "h",
        "scenes": [
            {
                "order": 1,
                "target_duration": 6,
                "narration": "n",
                "video_prompt": "p",
                "asset_type": "image",
                "asset_path": "scenes/scene_01.jpg",
                "asset_source": "generated",
                "asset_width": 800,
                "asset_height": 1200,
            }
        ],
    }
    s = StoryboardFile.model_validate_json(StoryboardFile.model_validate(raw).model_dump_json()).scene(1)
    assert (s.asset_type, s.asset_source, s.asset_duration, s.asset_width) == ("image", "generated", None, 800)


@pytest.mark.parametrize("name", ["still.gif", "notes.txt", "photo.heic", "frame.bmp"])
def test_attach_rejects_unsupported_extension(planned, tmp_path, name):
    f, pid = planned
    src = tmp_path / name
    src.write_bytes(b"x")
    with pytest.raises(ValueError, match="unsupported asset type"):
        f.attach_asset(pid, 1, src)
    assert f.storyboard(pid).scene(1).asset_path is None
    assert not any(f.store.path(pid, "scenes").iterdir())
    assert (f.store.get(pid).status, f.store.get(pid).error) == ("planned", None)


def test_attach_rejects_unknown_source(planned, tmp_path):
    f, pid = planned
    src = tmp_path / "a.png"
    src.write_bytes(b"x")
    with pytest.raises(ValueError, match="unknown asset source 'tiktok'"):
        f.attach_asset(pid, 1, src, "tiktok")
    assert f.storyboard(pid).scene(1).asset_path is None


def test_missing_unsupported_mismatched_and_deleted_assets_are_reported(planned):
    f, pid = planned

    def fn(sb: StoryboardFile) -> None:
        sb.scene(2).asset_path = "scenes/scene_02.gif"
        sb.scene(3).asset_type, sb.scene(3).asset_path = "video", "scenes/scene_03.mp4"
        sb.scene(4).asset_type, sb.scene(4).asset_path = "image", "scenes/scene_04.mp4"

    sb = f.store.update_storyboard(pid, fn)
    pdir = f.store.dir(pid)

    problems = check_ready(pdir, sb)
    assert "Scene 1: missing visual asset (upload a video or an image)" in problems
    assert any(p.startswith("Scene 2: unsupported asset type '.gif'") for p in problems)
    assert "Scene 3: visual asset file is missing (scene_03.mp4)" in problems
    assert "Scene 4: asset type 'image' does not match its file extension '.mp4'" in problems

    warnings = qc_warnings(f.store.get(pid), sb, pdir)
    assert "Scene 1: no visual asset yet" in warnings
    assert any(w.startswith("Scene 2: unsupported asset type '.gif'") for w in warnings)
    assert "Scene 3: visual asset file is missing (scene_03.mp4)" in warnings
    assert any(w.startswith("Scene 4: asset type 'image' does not match") for w in warnings)
    assert qc_warnings(f.store.get(pid), sb) == ["Scene 1: no visual asset yet"]  # file checks need the project dir
