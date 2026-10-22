"""P0-03: create / reload / list / edit / restart."""

from __future__ import annotations

import pytest

from factory.config import get_settings
from factory.pipeline import Factory
from factory.planner import MockPlanner
from factory.store import NotFound, ProjectStore


def test_create_reload_three_projects_after_restart():
    f = Factory()
    ids = [f.create_project(f"Topic {i}", 30, "vi").id for i in range(3)]
    for pid in ids:
        f.plan(pid, MockPlanner())
    restarted = ProjectStore(get_settings().projects_dir)  # new instance = app restart
    listed = restarted.list()
    assert {p.id for p in listed} == set(ids)
    for pid in ids:
        p = restarted.get(pid)
        assert p.status == "planned" and p.generation_count == 1 and p.planner_provider == "mock"
        assert restarted.get_storyboard(pid) is not None
        for sub in ("scenes", "audio", "subtitles", "renders", "logs"):
            assert (restarted.dir(pid) / sub).is_dir()


def test_edit_persists_and_marks_audio_stale():
    f = Factory()
    pid = f.create_project("Edit me", 30).id
    f.plan(pid, MockPlanner())
    f.generate_voice(pid, [1])
    f.edit_scene(
        pid, 1, narration="Câu mới hoàn toàn.", video_prompt="A brand new prompt for the scene, vertical 9:16."
    )
    sb = Factory().storyboard(pid)
    s = sb.scene(1)
    assert s.narration == "Câu mới hoàn toàn." and s.video_prompt.startswith("A brand new")
    assert s.audio_stale


def test_edit_metadata():
    f = Factory()
    pid = f.create_project("Meta", 30).id
    f.plan(pid, MockPlanner())
    f.edit_metadata(pid, "New title", "New desc", ["a", "#b", " "])
    sb = f.storyboard(pid)
    assert (sb.title, sb.hashtags) == ("New title", ["#a", "#b"])


def test_rejects_bad_ids_and_path_escape():
    store = ProjectStore(get_settings().projects_dir)
    with pytest.raises(NotFound):
        store.get("../etc")
    pid = Factory().create_project("Safe", 30).id
    with pytest.raises(NotFound):
        store.path(pid, "../../outside.txt")


def test_corrupt_project_folder_is_skipped():
    f = Factory()
    pid = f.create_project("Good", 30).id
    bad = get_settings().projects_dir / "20260101-broken-aaaaaa"
    bad.mkdir(parents=True)
    (bad / "project.json").write_text("{not json")
    assert [p.id for p in f.store.list()] == [pid]


def test_replan_clears_old_scene_assets():
    f = Factory()
    pid = f.create_project("Replan", 30).id
    f.plan(pid, MockPlanner())
    f.generate_voice(pid, [1])
    assert any(f.store.path(pid, "audio").glob("scene_*"))
    f.plan(pid, MockPlanner())
    assert not any(f.store.path(pid, "audio").glob("scene_*"))
    assert f.store.get(pid).generation_count == 2
