"""Data models. Rules come from tracker 01_Flow / P0-04 / TC-01 / TC-02."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

MIN_SCENES, MAX_SCENES = 4, 6
MIN_SCENE_TARGET, MAX_SCENE_TARGET = 5.0, 9.0  # tracker: target 5–9s, never >9s
MIN_TOTAL, MAX_TOTAL = 20, 45
TOTAL_TOLERANCE = 0.25  # planned total must be within ±25% of the requested duration

Language = Literal["vi", "en"]
Status = Literal["draft", "planned", "assets", "rendering", "rendered", "published", "error"]
Stage = Literal["created", "planning", "storyboard", "voice", "clips", "subtitle", "render", "qc", "publish"]


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


class PlannerInput(BaseModel):
    topic: str = Field(min_length=3, max_length=300)
    target_duration: int = Field(default=30, ge=MIN_TOTAL, le=MAX_TOTAL)
    language: Language = "vi"
    visual_style: str = Field(default="cinematic, realistic, soft natural light", max_length=200)


class PlannedScene(BaseModel):
    order: int = Field(ge=1)
    target_duration: float = Field(ge=MIN_SCENE_TARGET, le=MAX_SCENE_TARGET)
    narration: str = Field(min_length=1)
    video_prompt: str = Field(min_length=20)

    @field_validator("narration", "video_prompt")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be empty")
        return v


class Storyboard(BaseModel):
    title: str = Field(min_length=1, max_length=100)  # YouTube title limit
    description: str = Field(min_length=1, max_length=4500)
    hashtags: list[str] = Field(min_length=1, max_length=15)
    hook: str = Field(min_length=1)
    scenes: list[PlannedScene] = Field(min_length=MIN_SCENES, max_length=MAX_SCENES)

    @field_validator("hashtags")
    @classmethod
    def _hashtags(cls, tags: list[str]) -> list[str]:
        out = []
        for t in tags:
            t = "#" + t.strip().lstrip("#").replace(" ", "")
            if len(t) < 2:
                raise ValueError("empty hashtag")
            out.append(t)
        return out

    @model_validator(mode="after")
    def _orders(self) -> Storyboard:
        for i, s in enumerate(self.scenes, start=1):
            if s.order != i:
                raise ValueError(f"scene orders must be 1..n in sequence (got {s.order} at position {i})")
        return self

    @property
    def total_target(self) -> float:
        return sum(s.target_duration for s in self.scenes)

    def check_total(self, requested: int) -> None:
        lo, hi = requested * (1 - TOTAL_TOLERANCE), requested * (1 + TOTAL_TOLERANCE)
        if not lo <= self.total_target <= hi:
            raise ValueError(
                f"total target {self.total_target:.1f}s is too far from requested {requested}s "
                f"(allowed {lo:.0f}–{hi:.0f}s)"
            )


class SceneState(BaseModel):
    order: int
    target_duration: float
    narration: str
    video_prompt: str
    clip_file: str | None = None  # relative to project dir
    clip_duration: float | None = None
    clip_width: int | None = None
    clip_height: int | None = None
    audio_file: str | None = None
    actual_audio_duration: float | None = None  # source of truth for render timing
    audio_source: str | None = None
    audio_narration: str | None = None  # narration text the audio was made from

    @property
    def audio_stale(self) -> bool:
        return self.audio_file is not None and self.audio_narration != self.narration


class StoryboardFile(BaseModel):
    title: str
    description: str
    hashtags: list[str]
    hook: str
    scenes: list[SceneState]

    @classmethod
    def from_plan(cls, sb: Storyboard) -> StoryboardFile:
        return cls(
            title=sb.title,
            description=sb.description,
            hashtags=sb.hashtags,
            hook=sb.hook,
            scenes=[
                SceneState(
                    order=s.order,
                    target_duration=s.target_duration,
                    narration=s.narration,
                    video_prompt=s.video_prompt,
                )
                for s in sb.scenes
            ],
        )

    def scene(self, order: int) -> SceneState:
        for s in self.scenes:
            if s.order == order:
                return s
        raise KeyError(f"scene {order} not found")


class Cost(BaseModel):
    claude_estimated_cost: float = 0.0
    muse_estimated_cost: float = 0.0
    voice_estimated_cost: float = 0.0
    render_duration_ms: int = 0
    total_estimated_cost: float = 0.0

    def recompute(self) -> None:
        self.total_estimated_cost = round(
            self.claude_estimated_cost + self.muse_estimated_cost + self.voice_estimated_cost, 6
        )


class RenderInfo(BaseModel):
    status: Literal["idle", "rendering", "done", "error"] = "idle"
    file: str | None = None
    duration: float | None = None
    file_size: int | None = None
    width: int | None = None
    height: int | None = None
    error: str | None = None
    rendered_at: str | None = None
    render_count: int = 0
    reused_stages: int = 0


class Project(BaseModel):
    id: str
    topic: str
    title: str
    target_duration: int
    language: Language
    visual_style: str
    voice_preset: str = "default"
    status: Status = "draft"
    current_stage: Stage = "created"
    error: str | None = None
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    estimated_cost: float = 0.0
    generation_count: int = 0
    planner_provider: str | None = None
    cost: Cost = Field(default_factory=Cost)
    muse_clip_count: int = 0
    assets_changed_at: str | None = None  # last edit to narration/clip/voice; render is stale if newer
    render: RenderInfo = Field(default_factory=RenderInfo)
    youtube_video_id: str | None = None
    youtube_url: str | None = None
    publish_status: Literal["none", "uploading", "uploaded", "error"] = "none"
    published_at: str | None = None
    publish_privacy: Literal["private", "unlisted"] | None = None
    publish_error: str | None = None

    @property
    def render_stale(self) -> bool:
        r = self.render
        return bool(r.file and self.assets_changed_at and r.rendered_at and self.assets_changed_at > r.rendered_at)
