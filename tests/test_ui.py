"""Smoke test: the Streamlit page renders for empty, planned and rendered-looking projects."""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from factory.pipeline import Factory
from factory.planner import MockPlanner

APP = str(Path(__file__).resolve().parent.parent / "ui" / "studio.py")


def test_empty_state():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert any("Tạo project" in i.value for i in at.info)


def test_planned_project_page_and_youtube_not_configured():
    f = Factory()
    pid = f.create_project("UI smoke", 30).id
    f.plan(pid, MockPlanner())
    f.generate_voice(pid, [1])
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["pid"] = pid
    at.run()
    assert not at.exception
    assert at.header[0].value == "UI smoke"
    assert len(at.text_area) >= 8  # narration + prompt per scene
    assert any("YouTube not configured" in i.value for i in at.info)
    assert any("missing visual asset" in w.value for w in at.warning)


def test_generate_plan_button():
    f = Factory()
    pid = f.create_project("Button plan", 30).id
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["pid"] = pid
    at.run()
    next(b for b in at.button if b.label == "Generate plan").click().run()
    assert not at.exception
    assert f.store.get_storyboard(pid) is not None
    assert f.store.get(pid).status == "planned"
