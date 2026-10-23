"""TC-01, TC-02 + planner retry/validation (P0-04, P0-05)."""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from factory.config import get_settings
from factory.planner import ClaudePlanner, MockPlanner, PlannerError, get_planner, scene_plan, validate
from factory.schema import PlannerInput, Storyboard


def good_raw(n=5, each=6.0):
    return {
        "title": "Kỷ luật thắng động lực",
        "description": "Mẹo ngắn.",
        "hashtags": ["#shorts", "kyluat"],
        "hook": "Bạn đang sai.",
        "scenes": [
            {
                "order": i + 1,
                "target_duration": each,
                "narration": f"Câu {i + 1}.",
                "video_prompt": "Vertical 9:16 shot of a runner at dawn, slow push-in, warm light.",
            }
            for i in range(n)
        ],
    }


@pytest.mark.parametrize("lang", ["vi", "en"])
@pytest.mark.parametrize("target", [20, 30, 45])
def test_tc01_mock_plan_valid(lang, target):
    r = MockPlanner().generate(PlannerInput(topic="Productivity tips", target_duration=target, language=lang))
    sb = r.storyboard
    assert 4 <= len(sb.scenes) <= 6
    assert all(5 <= s.target_duration <= 9 for s in sb.scenes)
    assert [s.order for s in sb.scenes] == list(range(1, len(sb.scenes) + 1))
    assert all(h.startswith("#") for h in sb.hashtags)


@pytest.mark.parametrize("target", range(20, 46))
def test_scene_plan_respects_limits(target):
    plan = scene_plan(target)
    assert 4 <= len(plan) <= 6
    assert all(5 <= t <= 9 for t in plan)
    assert abs(sum(plan) - target) <= target * 0.25


def test_tc02_scene_over_9s_rejected():
    raw = good_raw()
    raw["scenes"][2]["target_duration"] = 9.5
    with pytest.raises(ValidationError):
        Storyboard.model_validate(raw)


@pytest.mark.parametrize("n", [3, 7])
def test_scene_count_enforced(n):
    with pytest.raises(ValidationError):
        Storyboard.model_validate(good_raw(n=n))


def test_total_must_match_request():
    with pytest.raises(ValueError, match="too far"):
        validate(good_raw(n=4, each=5), PlannerInput(topic="abc", target_duration=45))


def test_hashtags_normalized_and_order_checked():
    sb = Storyboard.model_validate(good_raw())
    assert sb.hashtags == ["#shorts", "#kyluat"]
    raw = good_raw()
    raw["scenes"][0]["order"] = 2
    with pytest.raises(ValidationError):
        Storyboard.model_validate(raw)


def test_get_planner_falls_back_to_mock_without_key():
    assert get_planner().name == "mock"


class FakeResponse:
    def __init__(self, payload, status=200, headers=None):
        self.payload, self.status_code, self.text, self.headers = payload, status, str(payload), headers or {}

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def post(self, url, headers, json, timeout):
        self.calls.append(copy.deepcopy(json))
        return self.responses.pop(0)


def tool_reply(raw, tid="t1"):
    return FakeResponse(
        {
            "content": [{"type": "tool_use", "id": tid, "name": "submit_storyboard", "input": raw}],
            "usage": {"input_tokens": 1000, "output_tokens": 500},
        }
    )


def claude(monkeypatch, responses):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    session = FakeSession(responses)
    return ClaudePlanner(get_settings(), session=session, sleep=lambda _seconds: None), session


def test_claude_retries_invalid_then_succeeds(monkeypatch):
    bad = good_raw()
    bad["scenes"][0]["target_duration"] = 12
    planner, session = claude(monkeypatch, [tool_reply(bad, "a"), tool_reply(good_raw(), "b")])
    r = planner.generate(PlannerInput(topic="Discipline", target_duration=30))
    assert r.attempts == 2 and r.provider == "claude"
    assert r.input_tokens == 2000 and r.output_tokens == 1000
    assert r.cost_usd == pytest.approx((2000 * 2 + 1000 * 10) / 1e6)
    retry_msg = session.calls[1]["messages"][-1]["content"][0]
    assert retry_msg["type"] == "tool_result" and retry_msg["is_error"]
    assert session.calls[0]["tool_choice"] == {"type": "auto"}


def test_claude_gives_up_with_clear_error(monkeypatch):
    bad = good_raw(n=2)
    planner, _ = claude(monkeypatch, [tool_reply(bad, str(i)) for i in range(3)])
    with pytest.raises(PlannerError, match="invalid after 3 attempts"):
        planner.generate(PlannerInput(topic="Discipline", target_duration=30))


def test_claude_http_error(monkeypatch):
    planner, session = claude(monkeypatch, [FakeResponse({"error": "overloaded"}, status=529)] * 4)
    with pytest.raises(PlannerError, match="529"):
        planner.generate(PlannerInput(topic="Discipline", target_duration=30))
    assert len(session.calls) == 4
