"""Discovery Engine v0: manual/YouTube discovery, rule-based scoring, opportunity analysis, hand-off to the planner."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from factory.config import get_settings
from factory.discovery import analyzer as analyzer_mod
from factory.discovery.analyzer import (
    OPPORTUNITY_TOOL,
    ClaudeOpportunityAnalyzer,
    MockOpportunityAnalyzer,
    candidate_prompt,
    get_analyzer,
    system_prompt,
)
from factory.discovery.formats import FORMATS, get_format
from factory.discovery.models import AnalysisDraft, DiscoveryCandidate
from factory.discovery.scoring import rank, score_candidate
from factory.discovery.service import Discovery, youtube_candidate_id
from factory.discovery.youtube import DiscoveryError, parse_duration, video_id_from_url
from factory.pipeline import Factory
from factory.planner import ClaudePlanner, MockPlanner, PlannerError, brief_prompt
from factory.schema import ContentBrief, PlannerInput

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------- fakes
class FakeResponse:
    def __init__(self, payload, status=200, headers=None):
        self.payload, self.status_code, self.text, self.headers = payload, status, str(payload), headers or {}

    def json(self):
        return self.payload


class FakeYouTube:
    """Answers search.list / videos.list from a fixed catalogue and records every call."""

    def __init__(self, videos: dict[str, dict], status: int = 200, error: dict | None = None):
        self.videos, self.status, self.error, self.calls = videos, status, error, []

    def get(self, url, params, timeout):
        endpoint = url.rsplit("/", 1)[-1]
        self.calls.append((endpoint, dict(params)))
        if self.status != 200:
            return FakeResponse({"error": self.error or {}}, status=self.status)
        if endpoint == "search":
            ids = list(self.videos)[: int(params["maxResults"])]
            return FakeResponse({"items": [{"id": {"kind": "youtube#video", "videoId": i}} for i in ids]})
        wanted = params["id"].split(",")
        return FakeResponse({"items": [{"id": i, **self.videos[i]} for i in wanted if i in self.videos]})


def yt_video(title: str, days_old: float, views: int, likes: int | None = None, comments: int | None = None) -> dict:
    stats = {"viewCount": str(views)}
    if likes is not None:
        stats["likeCount"] = str(likes)
    if comments is not None:
        stats["commentCount"] = str(comments)
    published = (datetime.now(UTC) - timedelta(days=days_old)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "snippet": {
            "title": title,
            "description": f"about {title}",
            "channelId": "UC123",
            "channelTitle": "Some Channel",
            "publishedAt": published,
        },
        "statistics": stats,
        "contentDetails": {"duration": "PT45S"},
    }


CATALOGUE = {
    "AAAAAAAAAA1": yt_video("Old slow video", 25, 20_000, 300, 10),
    "BBBBBBBBBB2": yt_video("Fresh rocket", 2, 900_000, 60_000, 2_000),
    "CCCCCCCCCC3": yt_video("Hidden likes", 5, 50_000),
}


def discovery(monkeypatch, session=None, key: str | None = "test-key") -> Discovery:
    if key:
        monkeypatch.setenv("YOUTUBE_API_KEY", key)
    return Discovery(Factory(), youtube_session=session)


def cand(views=None, likes=None, comments=None, days=None, **kw) -> DiscoveryCandidate:
    metrics = {k: v for k, v in {"views": views, "likes": likes, "comments": comments}.items() if v is not None}
    if days is not None:
        kw["published_at"] = (NOW - timedelta(days=days)).isoformat()
    return DiscoveryCandidate(id="cand-test", source="youtube", metrics=metrics, **kw)


# ---------------------------------------------------------------- discovery: manual modes
def test_manual_url_saves_without_credentials_and_reloads(monkeypatch):
    d = discovery(monkeypatch, key=None)
    c = d.add_url("https://www.tiktok.com/@someone/video/7400000000000000000", title="Viral trick")
    assert c.source == "manual_url" and c.platform == "tiktok"
    assert "manual" in (c.enrich_error or "")
    again = Discovery(Factory()).store.get_candidate(c.id)  # fresh store = reload after restart
    assert again.url == c.url and again.title == "Viral trick" and again.score is None


def test_manual_youtube_url_without_key_still_saved(monkeypatch):
    d = discovery(monkeypatch, key=None)
    c = d.add_url("https://youtube.com/shorts/BBBBBBBBBB2")
    assert c.external_id == "BBBBBBBBBB2"
    assert "not configured" in (c.enrich_error or "")


def test_manual_youtube_url_enriched_with_key(monkeypatch):
    yt = FakeYouTube(CATALOGUE)
    c = discovery(monkeypatch, yt).add_url("https://youtu.be/BBBBBBBBBB2", notes="great pacing")
    assert c.title == "Fresh rocket" and c.metrics["views"] == 900_000 and c.notes == "great pacing"
    assert c.score is not None and c.enrich_error is None
    assert [e for e, _ in yt.calls] == ["videos"]  # 1 unit, no search


def test_manual_url_enrich_api_error_is_recorded_not_raised(monkeypatch):
    yt = FakeYouTube(CATALOGUE, status=403, error={"errors": [{"reason": "quotaExceeded"}], "message": "quota"})
    c = discovery(monkeypatch, yt).add_url("https://www.youtube.com/watch?v=BBBBBBBBBB2")
    assert "quota" in (c.enrich_error or "") and c.url


def test_manual_url_rejects_non_url(monkeypatch):
    with pytest.raises(ValueError, match="URL"):
        discovery(monkeypatch, key=None).add_url("not a url")


def test_manual_content_saves_and_reloads(monkeypatch):
    d = discovery(monkeypatch, key=None)
    c = d.add_content(topic="Ngủ 8 tiếng là lầm tưởng", transcript="Câu một. Câu hai.", url="https://fb.watch/x")
    again = d.store.get_candidate(c.id)
    assert again.source == "manual_content" and again.transcript == "Câu một. Câu hai."
    assert again.platform == "facebook"
    with pytest.raises(ValueError, match="at least"):
        d.add_content(notes="only notes")


def test_edit_candidate_adds_transcript(monkeypatch):
    d = discovery(monkeypatch, key=None)
    c = d.add_url("https://www.facebook.com/reel/1")
    d.edit_candidate(c.id, title="Reel title", transcript="text")
    assert d.store.get_candidate(c.id).transcript == "text"


@pytest.mark.parametrize(
    "url,vid",
    [
        ("https://www.youtube.com/watch?v=BBBBBBBBBB2&t=3", "BBBBBBBBBB2"),
        ("https://youtu.be/BBBBBBBBBB2", "BBBBBBBBBB2"),
        ("https://m.youtube.com/shorts/BBBBBBBBBB2?feature=share", "BBBBBBBBBB2"),
        ("https://www.youtube.com/@channel", None),
        ("https://www.tiktok.com/@x/video/1", None),
    ],
)
def test_video_id_from_url(url, vid):
    assert video_id_from_url(url) == vid


def test_parse_duration():
    assert parse_duration("PT45S") == 45 and parse_duration("PT1M5S") == 65 and parse_duration("P0D") is None


# ---------------------------------------------------------------- discovery: YouTube
def test_youtube_not_configured(monkeypatch):
    d = discovery(monkeypatch, key=None)
    assert not d.youtube_configured
    with pytest.raises(DiscoveryError, match="Manual URL/content discovery still works"):
        d.search_youtube("productivity")


def test_youtube_search_ranks_and_batches(monkeypatch):
    yt = FakeYouTube(CATALOGUE)
    found, cached = discovery(monkeypatch, yt).search_youtube("productivity", "habits", 30, 20, "vi")
    assert not cached
    assert [e for e, _ in yt.calls] == ["search", "videos"]  # one batched videos.list, no N+1
    search = yt.calls[0][1]
    assert search["q"] == "productivity habits" and search["order"] == "viewCount"
    assert search["publishedAfter"].endswith("Z") and search["relevanceLanguage"] == "vi"
    assert yt.calls[1][1]["id"] == "AAAAAAAAAA1,BBBBBBBBBB2,CCCCCCCCCC3"
    assert found[0].title == "Fresh rocket"
    hidden = next(c for c in found if c.title == "Hidden likes")
    assert hidden.metrics == {"views": 50_000} and hidden.score is not None
    assert found[0].id == youtube_candidate_id("BBBBBBBBBB2")


def test_youtube_search_cache_avoids_api_calls(monkeypatch):
    yt = FakeYouTube(CATALOGUE)
    d = discovery(monkeypatch, yt)
    first, _ = d.search_youtube("productivity")
    calls = len(yt.calls)
    second, cached = d.search_youtube("productivity")
    assert cached and len(yt.calls) == calls
    assert [c.id for c in second] == [c.id for c in first]
    d.search_youtube("productivity", lookback_days=7)  # different query -> real call
    assert len(yt.calls) > calls


def test_youtube_cache_expires(monkeypatch):
    yt = FakeYouTube(CATALOGUE)
    d = discovery(monkeypatch, yt)
    d.search_youtube("productivity")
    params = {"q": "productivity", "days": 30, "max": 20, "lang": ""}
    assert d.store.cached_search(params) is not None
    assert d.store.cached_search(params, now=datetime.now(UTC) + timedelta(hours=13)) is None


def test_repeat_search_updates_same_record_and_keeps_notes(monkeypatch):
    d = discovery(monkeypatch, FakeYouTube(CATALOGUE))
    found, _ = d.search_youtube("productivity")
    d.edit_candidate(found[0].id, notes="my note")
    d.search_youtube("productivity", "new words")
    assert d.store.get_candidate(found[0].id).notes == "my note"
    assert len(d.store.list_candidates()) == 3


@pytest.mark.parametrize(
    "status,error,match",
    [
        (403, {"errors": [{"reason": "quotaExceeded"}], "message": "q"}, "quota exceeded"),
        (400, {"errors": [{"reason": "badRequest"}], "message": "API key not valid."}, "key"),
        (500, {"message": "backend"}, "500"),
    ],
)
def test_youtube_api_errors(monkeypatch, status, error, match):
    d = discovery(monkeypatch, FakeYouTube(CATALOGUE, status=status, error=error))
    with pytest.raises(DiscoveryError, match=match):
        d.search_youtube("productivity")


def test_youtube_error_never_leaks_key(monkeypatch):
    class Boom:
        def get(self, url, params, timeout):
            import requests

            raise requests.ConnectionError(f"failed {url}?key={params['key']}")

    with pytest.raises(DiscoveryError) as e:
        discovery(monkeypatch, Boom(), key="secret-key-123").search_youtube("x")
    assert "secret-key-123" not in str(e.value)


# ---------------------------------------------------------------- scoring
def test_new_high_velocity_ranks_above_old_low_velocity():
    new = cand(views=500_000, likes=30_000, comments=800, days=2)
    old = cand(views=40_000, likes=1_000, comments=50, days=28)
    s_new, s_old = score_candidate(new, NOW), score_candidate(old, NOW)
    assert s_new and s_old and s_new.viral_score > s_old.viral_score
    assert s_new.signals["view_velocity"] > s_old.signals["view_velocity"]
    assert set(s_new.signals) == {"view_velocity", "engagement", "recency", "reach"}
    assert s_new.raw["views_per_day"] == 250_000


def test_zero_views_and_zero_likes_no_division_error():
    s = score_candidate(cand(views=0, likes=0, comments=0, days=3), NOW)
    assert s is not None and s.signals["engagement"] == 0 and s.signals["view_velocity"] == 0
    s2 = score_candidate(cand(views=1_000, likes=0, days=3), NOW)
    assert s2 is not None and s2.signals["engagement"] == 0


def test_missing_metrics_unscored_and_ranked_last():
    assert score_candidate(cand(days=1), NOW) is None
    assert score_candidate(cand(views=1000), NOW) is None  # no publish date
    unscored = cand(title="manual")
    scored = cand(views=10, days=1).model_copy(update={"id": "cand-b"})
    scored.score = score_candidate(scored, NOW)
    assert [c.id for c in rank([unscored, scored])] == ["cand-b", "cand-test"]


def test_very_new_video_age_is_floored():
    s = score_candidate(cand(views=10_000, days=0.001), NOW)
    assert s is not None and s.raw["age_days"] == 0.25 and s.signals["view_velocity"] <= 10


def test_old_viral_video_keeps_reach_loses_recency():
    s = score_candidate(cand(views=50_000_000, likes=2_000_000, days=700), NOW)
    assert s is not None and s.signals["reach"] == 10 and s.signals["recency"] == 0
    assert all(0 <= v <= 10 for v in s.signals.values()) and 0 <= s.viral_score <= 10


def test_bad_publish_date_unscored():
    assert score_candidate(cand(views=100, published_at="yesterday"), NOW) is None


# ---------------------------------------------------------------- opportunity analyzer
def opportunity_raw(**over) -> dict:
    raw = {
        "topic": "Ngủ ít mà vẫn tỉnh",
        "angle": "Chất lượng giấc ngủ quan trọng hơn số giờ, và có cách đo đơn giản.",
        "hook_pattern": "contrarian claim + personal stake",
        "hooks": [
            {"text": f"Hook số {i}", "curiosity": 5 + i, "clarity": 7, "tension": 6, "novelty": 5, "payoff": 7}
            for i in range(5)
        ],
        "format_id": "MYTH_BUST",
        "story_structure": ["hook", "myth", "evidence", "truth", "takeaway"],
        "why_it_may_work": ["Phản trực giác", "Áp dụng được ngay"],
        "novelty": 7,
        "channel_fit": 8,
        "visual_feasibility": 9,
        "production_efficiency": 8,
    }
    raw.update(over)
    return raw


class FakeClaude:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def post(self, url, headers, json, timeout):
        self.calls.append(copy.deepcopy(json))
        return self.responses.pop(0)


def opp_reply(raw, tid="t1"):
    return FakeResponse(
        {
            "content": [{"type": "tool_use", "id": tid, "name": "submit_opportunity", "input": raw}],
            "usage": {"input_tokens": 2000, "output_tokens": 800},
        }
    )


def claude_analyzer(monkeypatch, responses):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    session = FakeClaude(responses)
    return ClaudeOpportunityAnalyzer(get_settings(), session=session, sleep=lambda _s: None), session


def test_claude_structured_output_to_opportunity(monkeypatch):
    an, session = claude_analyzer(monkeypatch, [opp_reply(opportunity_raw())])
    d = discovery(monkeypatch, FakeYouTube(CATALOGUE))
    found, _ = d.search_youtube("sleep")
    o = d.analyze(found[0].id, "vi", "health", an)
    assert o.analyzer == "claude" and o.format_id == "MYTH_BUST" and len(o.hooks) == 5
    assert o.selected_hook == 4  # highest weighted overall, chosen in code
    assert o.scores.hook_potential == o.hooks[4].overall
    assert o.scores.viral_signal == found[0].score.viral_score if found[0].score else False
    assert 0 < o.scores.overall <= 10 and o.cost_usd > 0
    body = session.calls[0]
    assert body["tools"][0]["name"] == "submit_opportunity" and body["tool_choice"] == {"type": "auto"}
    assert "Fresh rocket" in body["messages"][0]["content"]
    assert d.store.get_opportunity(o.id).hooks[0].text == "Hook số 0"  # persisted


def test_claude_invalid_response_retried_then_ok(monkeypatch):
    bad = opportunity_raw(format_id="NOT_A_FORMAT")
    an, session = claude_analyzer(monkeypatch, [opp_reply(bad, "a"), opp_reply(opportunity_raw(), "b")])
    r = an.analyze(cand(title="x"), "vi")
    assert r.attempts == 2
    feedback = session.calls[1]["messages"][-1]["content"][0]
    assert feedback["is_error"] and "format_id" in feedback["content"]


def test_claude_invalid_scores_give_up(monkeypatch):
    bad = opportunity_raw(novelty=42)
    an, _ = claude_analyzer(monkeypatch, [opp_reply(bad, str(i)) for i in range(3)])
    with pytest.raises(PlannerError, match="analyzer output invalid after 3 attempts"):
        an.analyze(cand(title="x"), "vi")


def test_mock_analyzer_five_hooks_valid_scores_and_format():
    r = MockOpportunityAnalyzer().analyze(cand(title="5 sai lầm khi chạy bộ"), "vi")
    d = r.draft
    assert len(d.hooks) == 5 and d.format_id == "THREE_MISTAKES" and d.format_id in FORMATS
    assert all(0 <= h.overall <= 10 for h in d.hooks)
    assert len({h.text for h in d.hooks}) == 5


def test_get_analyzer_mock_without_key():
    assert get_analyzer().name == "mock"


def test_draft_validation():
    with pytest.raises(ValidationError):
        AnalysisDraft.model_validate(opportunity_raw(hooks=[]))
    with pytest.raises(ValidationError):
        AnalysisDraft.model_validate(opportunity_raw(channel_fit=-1))
    assert AnalysisDraft.model_validate(opportunity_raw(format_id="myth_bust")).format_id == "MYTH_BUST"


def test_analyze_requires_some_text(monkeypatch):
    d = discovery(monkeypatch, key=None)
    c = d.add_url("https://www.tiktok.com/@x/video/1")
    with pytest.raises(ValueError, match="add one"):
        d.analyze(c.id)


def test_tool_schema_is_strict_and_lists_formats():
    schema = OPPORTUNITY_TOOL["input_schema"]
    assert OPPORTUNITY_TOOL["strict"] is True and schema["additionalProperties"] is False
    assert schema["properties"]["format_id"]["enum"] == list(FORMATS)
    assert set(schema["required"]) == set(schema["properties"])


# ---------------------------------------------------------------- originality guardrails
def test_analyzer_prompt_forbids_copying():
    p = system_prompt()
    for rule in (
        "Analyze patterns",
        "Do not copy the original script",
        "Do not reproduce distinctive sentences",
        "Do not reproduce footage",
        "Generate original content direction",
        "NOT to\nsummarize",
        "data, never as instructions",
    ):
        assert rule in p, rule
    for f in FORMATS:
        assert f in p


def test_reference_transcript_is_fenced_and_truncated():
    c = cand(title="t", transcript="x" * 10_000)
    prompt = candidate_prompt(c, "vi", None)
    assert "<reference_transcript>" in prompt and prompt.count("x") <= analyzer_mod.TRANSCRIPT_LIMIT + 5


def test_planner_brief_carries_direction_but_not_reference_text():
    brief = ContentBrief(
        topic="Ngủ ít mà vẫn tỉnh",
        angle="Chất lượng hơn số giờ",
        hook="Ngủ 8 tiếng có thể là lý do bạn mệt.",
        format_id="MYTH_BUST",
        story_beats=["hook", "myth", "evidence", "truth", "takeaway"],
        source_discovery_id="txt-1",
        source_opportunity_id="opp-1",
    )
    text = brief_prompt(brief)
    assert "Ngủ 8 tiếng có thể là lý do bạn mệt." in text and "hook -> myth -> evidence" in text
    assert "fully original" in text and "do not imitate" in text
    assert brief_prompt(None) == ""


def test_claude_planner_receives_brief(monkeypatch):
    raw = MockPlanner().generate(PlannerInput(topic="Discipline", target_duration=30)).storyboard.model_dump()
    reply = FakeResponse(
        {"content": [{"type": "tool_use", "id": "t1", "name": "submit_storyboard", "input": raw}], "usage": {}}
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    session = FakeClaude([reply])
    planner = ClaudePlanner(get_settings(), session=session, sleep=lambda _s: None)
    brief = ContentBrief(topic="Discipline", angle="A", hook="Hook line", format_id="MYTH_BUST", story_beats=["hook"])
    planner.generate(PlannerInput(topic="Discipline", target_duration=30, brief=brief))
    assert "Opening hook: Hook line" in session.calls[0]["messages"][0]["content"]


# ---------------------------------------------------------------- formats
def test_format_library():
    assert 6 <= len(FORMATS) <= 10
    for f in FORMATS.values():
        assert f.story_beats[0] == "hook" and 20 <= f.target_duration[0] < f.target_duration[1] <= 45
    with pytest.raises(ValueError, match="unknown format_id"):
        get_format("NOPE")


# ---------------------------------------------------------------- integration
def test_candidate_to_opportunity_to_project(monkeypatch):
    d = discovery(monkeypatch, FakeYouTube(CATALOGUE))
    found, _ = d.search_youtube("productivity")
    o = d.analyze(found[0].id, "vi", analyzer=MockOpportunityAnalyzer())
    p = d.generate_video(o.id, planner=MockPlanner(), hook_index=1, format_id="POV_STORY")
    assert p.status == "planned" and p.brief is not None
    assert p.brief.source_discovery_id == found[0].id and p.brief.source_opportunity_id == o.id
    assert p.brief.format_id == "POV_STORY" and p.brief.story_beats == FORMATS["POV_STORY"].story_beats
    assert p.brief.hook == o.hooks[1].text and p.brief.reference_url == found[0].url
    sb = d.factory.storyboard(p.id)
    assert sb.hook == o.hooks[1].text and sb.scenes[0].narration == o.hooks[1].text
    assert 4 <= len(sb.scenes) <= 6
    reloaded = d.store.get_opportunity(o.id)
    assert reloaded.project_ids == [p.id] and reloaded.selected_hook == 1
    assert Factory().store.get(p.id).brief == p.brief  # survives reload


def test_manual_content_to_project_default_duration(monkeypatch):
    d = discovery(monkeypatch, key=None)
    c = d.add_content(topic="Kỷ luật hơn động lực", transcript="ghi chú")
    o = d.analyze(c.id)
    p = d.generate_video(o.id, planner=MockPlanner())
    fmt = FORMATS[o.format_id]
    assert p.target_duration == round(sum(fmt.target_duration) / 2)
    assert p.brief and p.brief.story_beats == o.story_structure


def test_save_pattern(monkeypatch):
    d = discovery(monkeypatch, key=None)
    o = d.analyze(d.add_content(topic="Thói quen buổi sáng").id)
    assert d.save_pattern(o.id).saved_pattern
    assert d.store.patterns.file(o.id).is_file()


def test_choose_validates(monkeypatch):
    d = discovery(monkeypatch, key=None)
    o = d.analyze(d.add_content(topic="Thói quen buổi sáng").id)
    with pytest.raises(ValueError, match="hook index"):
        d.choose(o.id, hook_index=9)
    with pytest.raises(ValueError, match="unknown format_id"):
        d.choose(o.id, format_id="NOPE")


def test_plain_topic_flow_unchanged():
    f = Factory()
    p = f.create_project("Kỷ luật quan trọng hơn động lực", 30)
    sb = f.plan(p.id, MockPlanner())
    assert p.brief is None and f.store.get(p.id).brief is None
    assert sb.hook == "Bạn đang làm sai điều này mỗi ngày mà không biết."


def test_old_project_json_without_brief_loads():
    f = Factory()
    p = f.create_project("Legacy project", 30)
    path = f.store.dir(p.id) / "project.json"
    data = path.read_text(encoding="utf-8").replace('"brief": null,', "")
    assert '"brief"' not in data
    path.write_text(data, encoding="utf-8")
    assert f.store.get(p.id).brief is None


# ---------------------------------------------------------------- UI smoke
def test_discover_page_renders(monkeypatch):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    d = discovery(monkeypatch, key=None)
    c = d.add_content(topic="Thói quen buổi sáng", transcript="ghi chú")
    o = d.analyze(c.id)
    app = str(Path(__file__).resolve().parent.parent / "ui" / "studio.py")
    at = AppTest.from_file(app, default_timeout=60)
    at.session_state["page"] = "Discover"
    at.session_state["cid"] = c.id
    at.session_state["oid"] = o.id
    at.run()
    assert not at.exception
    assert at.header[0].value == "Discover"
    assert any("not configured" in i.value for i in at.info)
    assert any(b.label == "Generate Video" for b in at.button)
