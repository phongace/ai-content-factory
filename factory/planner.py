"""Content planner (tracker P0-04, P0-05): topic -> validated storyboard JSON.

ClaudePlanner calls the Anthropic Messages API directly with a forced tool call, so the output is
JSON by construction and token usage (-> cost) is exact. MockPlanner needs no key.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Protocol

import requests
from pydantic import ValidationError

from .config import Settings, get_settings
from .schema import MAX_SCENE_TARGET, MAX_SCENES, MIN_SCENE_TARGET, MIN_SCENES, PlannerInput, Storyboard

# Speaking rate used to size narration (words per second). Vietnamese counts syllables.
WORDS_PER_SECOND = {"vi": 3.6, "en": 2.5}


class PlannerError(Exception):
    pass


@dataclass
class PlanResult:
    storyboard: Storyboard
    provider: str
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    attempts: int = 1


class ContentPlanner(Protocol):
    name: str

    def generate(self, inp: PlannerInput) -> PlanResult: ...


def scene_plan(target: int) -> list[float]:
    """Split a total duration into 4–6 scene targets of 5–9s each."""
    n = min(MAX_SCENES, max(MIN_SCENES, round(target / 7)))
    while n < MAX_SCENES and target / n > MAX_SCENE_TARGET:
        n += 1
    while n > MIN_SCENES and target / n < MIN_SCENE_TARGET:
        n -= 1
    base = max(MIN_SCENE_TARGET, min(MAX_SCENE_TARGET, target / n))
    return [round(base, 1)] * n


def validate(raw: dict, inp: PlannerInput) -> Storyboard:
    sb = Storyboard.model_validate(raw)
    sb.check_total(inp.target_duration)
    return sb


# ---------------------------------------------------------------- mock
_MOCK = {
    "vi": {
        "hook": "Bạn đang làm sai điều này mỗi ngày mà không biết.",
        "lines": [
            "Bạn đang làm sai điều này mỗi ngày mà không hề biết, và nó đang lấy đi kết quả bạn xứng đáng có.",
            "Phần lớn mọi người chỉ bắt đầu khi có cảm hứng, rồi bỏ cuộc ngay khi cảm hứng biến mất vài ngày sau.",
            "Người thành công thì khác, họ xây những thói quen thật nhỏ và lặp lại đều đặn mỗi ngày, kể cả khi lười.",
            "Chỉ cần mười phút mỗi sáng, sau đúng một tháng bạn sẽ thấy sự khác biệt rất rõ ràng trong công việc.",
            "Vậy nên đừng chờ động lực nữa, hãy để kỷ luật kéo bạn đi từng bước nhỏ, ngay từ hôm nay.",
            "Theo dõi kênh để nhận thêm một mẹo ngắn mỗi ngày, giúp bạn sống kỷ luật và hiệu quả hơn.",
        ],
        "desc": "Video ngắn về chủ đề: {topic}.",
        "tags": ["#shorts", "#kynang", "#thoiquen"],
    },
    "en": {
        "hook": "You are doing this wrong every single day.",
        "lines": [
            "You are doing this wrong every single day, and it is quietly stealing the results you deserve.",
            "Most people only start when they feel motivated, then quit the moment that feeling fades away.",
            "Successful people do it differently. They build tiny habits and repeat them daily, even when lazy.",
            "Just ten minutes every morning, and in one month you will see a clear difference in your work.",
            "So stop waiting for motivation. Let discipline carry you forward, one small step at a time, today.",
            "Follow for one short, practical tip every day to build discipline and get more done.",
        ],
        "desc": "A short video about: {topic}.",
        "tags": ["#shorts", "#habits", "#selfimprovement"],
    },
}


class MockPlanner:
    name = "mock"

    def generate(self, inp: PlannerInput) -> PlanResult:
        m = _MOCK[inp.language]
        targets = scene_plan(inp.target_duration)
        scenes = [
            {
                "order": i + 1,
                "target_duration": t,
                "narration": m["lines"][i],
                "video_prompt": (
                    f"Vertical 9:16 shot, {inp.visual_style}. Scene {i + 1} about '{inp.topic}': "
                    "a person in a modern apartment, natural movement, shallow depth of field, no text on screen."
                ),
            }
            for i, t in enumerate(targets)
        ]
        raw = {
            "title": inp.topic[:90],
            "description": m["desc"].format(topic=inp.topic),
            "hashtags": m["tags"],
            "hook": m["hook"],
            "scenes": scenes,
        }
        return PlanResult(storyboard=validate(raw, inp), provider=self.name)


# ---------------------------------------------------------------- claude
STORYBOARD_TOOL = {
    "name": "submit_storyboard",
    "description": "Submit the finished YouTube Shorts storyboard.",
    "input_schema": {
        "type": "object",
        "required": ["title", "description", "hashtags", "hook", "scenes"],
        "properties": {
            "title": {"type": "string", "description": "YouTube title, max 90 characters, no hashtags"},
            "description": {"type": "string", "description": "YouTube description, 1-3 short sentences"},
            "hashtags": {"type": "array", "items": {"type": "string"}, "description": "3-6 hashtags incl. #shorts"},
            "hook": {"type": "string", "description": "the opening line that stops the scroll"},
            "scenes": {
                "type": "array",
                "minItems": MIN_SCENES,
                "maxItems": MAX_SCENES,
                "items": {
                    "type": "object",
                    "required": ["order", "target_duration", "narration", "video_prompt"],
                    "properties": {
                        "order": {"type": "integer"},
                        "target_duration": {"type": "number", "minimum": MIN_SCENE_TARGET, "maximum": MAX_SCENE_TARGET},
                        "narration": {"type": "string"},
                        "video_prompt": {"type": "string"},
                    },
                },
            },
        },
    },
}

SYSTEM_PROMPT = """You plan faceless YouTube Shorts for monetized channels.
Rules (hard):
- {n_min}-{n_max} scenes, order 1..n. Each scene target_duration between {s_min} and {s_max} seconds. Never above {s_max}.
- Sum of target_duration close to the requested total.
- Narration is spoken voice-over in the requested language, natural and punchy. Size it to the scene:
  about {wps} words per second of target_duration (Vietnamese: count syllables). No emojis, no stage directions.
- Scene 1 narration IS the hook.
- video_prompt is in ENGLISH for a text-to-video model: vertical 9:16, one continuous shot, concrete subject,
  action, setting, camera move, lighting, the requested visual style. No on-screen text, no logos,
  no real people or brands. 25-70 words.
- Title/description/hashtags in the requested language, ready for YouTube. Include #shorts.
Return ONLY by calling the submit_storyboard tool."""


class ClaudePlanner:
    name = "claude"
    url = "https://api.anthropic.com/v1/messages"

    def __init__(self, settings: Settings | None = None, max_attempts: int = 3, session=None):
        self.s = settings or get_settings()
        if not self.s.anthropic_api_key:
            raise PlannerError("ANTHROPIC_API_KEY is not set")
        self.max_attempts = max_attempts
        self.http = session or requests.Session()

    def _call(self, system: str, messages: list[dict]) -> dict:
        try:
            r = self.http.post(
                self.url,
                headers={
                    "x-api-key": self.s.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.s.anthropic_model,
                    "max_tokens": 2500,
                    "system": system,
                    "tools": [STORYBOARD_TOOL],
                    "tool_choice": {"type": "tool", "name": "submit_storyboard"},
                    "messages": messages,
                },
                timeout=120,
            )
        except requests.RequestException as e:
            raise PlannerError(f"Claude request failed: {e}") from e
        if r.status_code != 200:
            raise PlannerError(f"Claude API {r.status_code}: {r.text[:300]}")
        return r.json()

    def generate(self, inp: PlannerInput) -> PlanResult:
        targets = scene_plan(inp.target_duration)
        system = SYSTEM_PROMPT.format(
            n_min=MIN_SCENES,
            n_max=MAX_SCENES,
            s_min=int(MIN_SCENE_TARGET),
            s_max=int(MAX_SCENE_TARGET),
            wps=WORDS_PER_SECOND[inp.language],
        )
        lang = "Vietnamese" if inp.language == "vi" else "English"
        user = (
            f"Topic: {inp.topic}\nLanguage: {lang}\nTotal duration: {inp.target_duration}s "
            f"(suggested {len(targets)} scenes of ~{targets[0]}s)\nVisual style: {inp.visual_style}"
        )
        messages: list[dict] = [{"role": "user", "content": user}]
        in_tok = out_tok = 0
        last_error = ""
        for attempt in range(1, self.max_attempts + 1):
            data = self._call(system, messages)
            usage = data.get("usage", {})
            in_tok += int(usage.get("input_tokens", 0))
            out_tok += int(usage.get("output_tokens", 0))
            block = next((b for b in data.get("content", []) if b.get("type") == "tool_use"), None)
            try:
                if block is None:
                    raise ValueError("model did not call submit_storyboard")
                sb = validate(block.get("input") or {}, inp)
                cost = (in_tok * self.s.anthropic_input_price + out_tok * self.s.anthropic_output_price) / 1e6
                return PlanResult(sb, self.name, round(cost, 6), in_tok, out_tok, attempt)
            except (ValidationError, ValueError) as e:
                last_error = str(e)[:1500]
                if block is None:
                    messages.append({"role": "user", "content": "Call submit_storyboard now."})
                    continue
                messages += [
                    {"role": "assistant", "content": [block]},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": block["id"],
                                "is_error": True,
                                "content": f"Invalid storyboard, fix and call submit_storyboard again:\n{last_error}",
                            }
                        ],
                    },
                ]
        raise PlannerError(f"planner output invalid after {self.max_attempts} attempts: {last_error}")


def get_planner(settings: Settings | None = None) -> ContentPlanner:
    s = settings or get_settings()
    if s.planner == "mock" or (s.planner == "auto" and not s.anthropic_api_key):
        return MockPlanner()
    return ClaudePlanner(s)


def narration_seconds(text: str, language: str) -> float:
    """Rough spoken length, used by the mock voice and for UI warnings."""
    words = len(text.split())
    return max(1.0, math.ceil(words / WORDS_PER_SECOND.get(language, 2.5) * 10) / 10)


def dumps(sb: Storyboard) -> str:
    return json.dumps(sb.model_dump(), ensure_ascii=False, indent=2)
