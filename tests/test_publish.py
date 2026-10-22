"""TC-09 with a mock publisher + YouTube not-configured behaviour (P0-14, P0-15)."""

from __future__ import annotations

import pytest

from factory.pipeline import Factory
from factory.planner import MockPlanner
from factory.publish import youtube
from factory.publish.base import MockPublisher, NotConfigured, build_description
from factory.schema import now_iso


def fake_rendered(f: Factory) -> str:
    pid = f.create_project("Publish", 30).id
    f.plan(pid, MockPlanner())
    out = f.store.path(pid, "renders/final-short.mp4")
    out.write_bytes(b"fake mp4 bytes")

    def done(p):
        p.render.status, p.render.file, p.render.rendered_at = "done", "renders/final-short.mp4", now_iso()
        p.status = "rendered"

    f.store.update(pid, done)
    return pid


def test_tc09_mock_upload_persists_ids():
    f = Factory()
    pid = fake_rendered(f)
    pub = MockPublisher()
    p = f.publish(pid, pub, "unlisted")
    assert p.youtube_video_id == "mock0000001" and p.youtube_url.endswith("mock0000001")
    assert (p.publish_status, p.publish_privacy, p.status) == ("uploaded", "unlisted", "published")
    assert p.published_at
    assert pub.uploads[0].title == f.storyboard(pid).title


def test_refuses_unrendered_and_stale():
    f = Factory()
    pid = f.create_project("No render", 30).id
    f.plan(pid, MockPlanner())
    with pytest.raises(ValueError, match="render the video"):
        f.publish(pid, MockPublisher())
    pid2 = fake_rendered(f)
    f.store.update(pid2, lambda p: setattr(p, "assets_changed_at", "9999-01-01T00:00:00+00:00"))
    with pytest.raises(ValueError, match="render again"):
        f.publish(pid2, MockPublisher())


def test_public_is_refused():
    f = Factory()
    pid = fake_rendered(f)
    with pytest.raises(Exception, match="private or unlisted"):
        f.publish(pid, MockPublisher(), "public")
    assert f.store.get(pid).publish_status == "error"


def test_youtube_not_configured():
    assert youtube.status() == "not_configured"
    with pytest.raises(NotConfigured, match="YouTube not configured"):
        youtube.YouTubePublisher()


def test_youtube_configured_but_not_connected(monkeypatch):
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "id")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "secret")
    assert youtube.status() == "not_connected"
    f = Factory()
    pid = fake_rendered(f)
    with pytest.raises(NotConfigured, match="not connected"):
        f.publish(pid, youtube.YouTubePublisher())


def test_description_has_hashtags():
    assert build_description("Hi", ["#a", "b"]).endswith("#a #b")
