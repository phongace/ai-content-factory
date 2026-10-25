"""Regression tests for the review-fix batch: scene edits, persisted BGM, bundled fonts, attach validation,
YouTube text limits, inspect cache, UI load errors. No network and no real render."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from factory import media
from factory import render as render_mod
from factory.pipeline import Factory
from factory.planner import MockPlanner
from factory.publish import youtube
from factory.publish.base import build_description, clean_title
from factory.render import FONTS_DIR, RenderError, RenderOutput
from factory.schema import SceneState, now_iso

APP = str(Path(__file__).resolve().parent.parent / "ui" / "app.py")


def mark_rendered(f: Factory, pid: str) -> None:
    def done(p) -> None:
        p.status = "rendered"
        p.render.status, p.render.file, p.render.rendered_at = "done", "renders/final-short.mp4", now_iso()

    f.store.update(pid, done)


def planned(title: str = "Batch") -> tuple[Factory, str]:
    f = Factory()
    pid = f.create_project(title, 30).id
    f.plan(pid, MockPlanner())
    return f, pid


def test_prompt_only_edit_keeps_render_fresh_but_narration_change_marks_stale():
    f, pid = planned()
    mark_rendered(f, pid)
    before = f.store.get(pid)
    narration = f.storyboard(pid).scene(1).narration

    f.edit_scene(pid, 1, narration=f"  {narration}\n", video_prompt="A different visual prompt, vertical 9:16.")
    after = f.store.get(pid)
    assert f.storyboard(pid).scene(1).video_prompt.startswith("A different visual prompt")
    assert (after.status, after.assets_changed_at, after.render_stale) == ("rendered", before.assets_changed_at, False)

    f.edit_scene(pid, 1, narration="Một câu hoàn toàn mới.")
    assert (f.store.get(pid).status, f.store.get(pid).render_stale) == ("assets", True)


def test_bgm_is_copied_reused_removable_and_part_of_staleness(tmp_path, monkeypatch):
    f, pid = planned("Bgm")
    f.generate_voice(pid, [1])
    song, other = tmp_path / "song.wav", tmp_path / "other.wav"
    shutil.copy(f.store.path(pid, "audio/scene_01.wav"), song)
    shutil.copy(song, other)
    other.write_bytes(other.read_bytes() + b"\0\0")
    calls: list[tuple[Path | None, float]] = []
    fail = False

    def fake_render(project_id, project_dir, language, sb, bgm=None, bgm_volume=0.25):
        calls.append((bgm, bgm_volume))
        if fail:
            raise RenderError("boom")
        return RenderOutput(project_dir / "renders" / "final-short.mp4", 6.0, 1, 1080, 1920, 1, 0)

    monkeypatch.setattr("factory.pipeline.render_video", fake_render)
    stored = f.store.path(pid, "renders/bgm.wav")

    p = f.render(pid, song, 0.3)
    assert calls[-1] == (stored, 0.3) and stored.is_file()
    assert p.bgm == p.render.bgm and p.bgm is not None
    assert (p.bgm.file, p.bgm.sha256, p.bgm.volume) == ("renders/bgm.wav", media.file_sha256(song), 0.3)
    assert not p.render_stale

    f.render(pid)
    assert calls[-1] == (stored, 0.3)
    p = f.render(pid, bgm_volume=0.4)
    assert calls[-1] == (stored, 0.4) and p.bgm is not None and p.bgm.volume == 0.4

    fail = True
    with pytest.raises(RenderError):
        f.render(pid, other)
    assert f.store.get(pid).render_stale
    fail = False
    p = f.render(pid)
    assert calls[-1] == (stored, 0.4) and p.bgm is not None and p.bgm.sha256 == media.file_sha256(other)
    assert not p.render_stale

    with pytest.raises(ValueError, match="either"):
        f.render(pid, song, no_bgm=True)
    p = f.render(pid, no_bgm=True)
    assert calls[-1][0] is None and p.bgm is None and p.render.bgm is None and not p.render_stale
    assert not list(f.store.path(pid, "renders").glob("bgm.*"))
    f.render(pid)
    assert calls[-1][0] is None

    monkeypatch.setenv("BGM_FILE", str(song))
    g = Factory()
    fresh = g.create_project("Env default", 30).id
    g.plan(fresh, MockPlanner())
    assert g.render(fresh).bgm is not None
    g.render(fresh, no_bgm=True)
    assert g.render(fresh).bgm is None


def test_fonts_dir_ships_only_be_vietnam_pro_bold_and_its_licence():
    assert sorted(x.name for x in FONTS_DIR.iterdir()) == ["BeVietnamPro-Bold.ttf", "OFL.txt"]
    assert "SIL OPEN FONT LICENSE Version 1.1" in (FONTS_DIR / "OFL.txt").read_text(encoding="utf-8")


def test_invalid_upload_keeps_status_and_same_file_attach_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSE_COST_PER_CLIP", "0.5")
    f, pid = planned("Attach")
    mark_rendered(f, pid)
    image = tmp_path / "scene.png"
    image.write_bytes(b"not really a png")

    def unreadable(path):
        raise media.MediaError("not a readable image")

    monkeypatch.setattr(media, "probe_image", unreadable)
    with pytest.raises(media.MediaError):
        f.attach_asset(pid, 1, image)
    p = f.store.get(pid)
    assert (p.status, p.error, p.render_stale) == ("rendered", None, False)
    assert not list(f.store.path(pid, "scenes").iterdir())

    monkeypatch.setattr(media, "probe_image", lambda path: media.ImageInfo(2, 2))
    f.attach_asset(pid, 1, image, "muse")
    first = f.store.get(pid)
    assert (first.status, first.muse_clip_count, first.cost.muse_estimated_cost) == ("assets", 1, 0.5)

    f.attach_asset(pid, 1, image, "muse")
    again = f.store.get(pid)
    assert (again.muse_clip_count, again.cost.muse_estimated_cost) == (1, 0.5)
    assert again.assets_changed_at == first.assets_changed_at
    assert not list(f.store.path(pid, "scenes").glob(".incoming*"))


def test_youtube_text_limits_count_utf8_bytes_and_drop_angle_brackets():
    assert clean_title("<b>" + "x" * 150) == "b" + "x" * 99
    text = build_description("ế" * 3000 + "<script>", ["#a"])
    assert len(text.encode("utf-8")) < 5000 and "<" not in text and ">" not in text
    assert text == "ế" * len(text) and len(text.encode("utf-8")) == 4998
    assert build_description("Hi", ["#a", "b"]) == "Hi\n\n#a #b"

    f, pid = planned("Meta")
    f.edit_metadata(pid, "<T>itle " + "y" * 200, "d<>esc " + "ế" * 3000, ["a"])
    sb = f.storyboard(pid)
    assert sb.title == "Title " + "y" * 94
    assert sb.description.startswith("desc ") and len(sb.description.encode("utf-8")) < 5000


def test_inspect_asset_caches_clean_results_by_file_version_but_never_problems(tmp_path, monkeypatch):
    render_mod._INSPECTED_OK.clear()
    probes: list[Path] = []
    broken = False

    def fake_probe(path):
        probes.append(Path(path))
        if broken:
            raise media.MediaError("garbage")
        return media.ImageInfo(2, 2)

    monkeypatch.setattr(media, "probe_image", fake_probe)
    (tmp_path / "scenes").mkdir()
    asset = tmp_path / "scenes" / "scene_01.png"
    asset.write_bytes(b"v1")
    scene = SceneState(
        order=1,
        target_duration=6,
        narration="n",
        video_prompt="p",
        asset_type="image",
        asset_path="scenes/scene_01.png",
    )

    assert render_mod.inspect_asset(tmp_path, scene) is None
    assert render_mod.inspect_asset(tmp_path, scene) is None
    assert len(probes) == 1
    asset.write_bytes(b"version two")
    assert render_mod.inspect_asset(tmp_path, scene) is None
    assert len(probes) == 2

    broken = True
    asset.write_bytes(b"x")
    assert (render_mod.inspect_asset(tmp_path, scene) or "").startswith("bad image")
    assert (render_mod.inspect_asset(tmp_path, scene) or "").startswith("bad image")
    assert len(probes) == 4


def test_ui_reports_corrupt_project_files_and_asks_youtube_status_once(monkeypatch):
    f, pid = planned("Corrupt")
    st.cache_data.clear()
    status_calls: list[str] = []

    def fake_status(settings=None):
        status_calls.append("call")
        return "not_configured"

    monkeypatch.setattr(youtube, "status", fake_status)
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["pid"] = pid
    at.run()
    at.run()
    assert not at.exception and status_calls == ["call"]

    f.store.path(pid, "storyboard.json").write_text("{not json", encoding="utf-8")
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["pid"] = pid
    at.run()
    assert not at.exception
    assert any("storyboard.json" in e.value for e in at.error)
    assert not at.tabs

    (f.store.dir(pid) / "project.json").write_text("[]", encoding="utf-8")
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["pid"] = pid
    at.run()
    assert not at.exception
    assert any("project.json" in e.value for e in at.error)
