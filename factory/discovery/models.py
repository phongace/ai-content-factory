"""Discovery data models: a reference candidate, its rule-based score, and the analyzed content opportunity."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..schema import now_iso

CandidateSource = Literal["manual_url", "manual_content", "youtube"]
Score = float  # 0-10, higher is better

# Hook sub-score weights; overall = weighted mean (explainable, no model judgement on the final pick)
HOOK_WEIGHTS = {"curiosity": 0.25, "clarity": 0.2, "tension": 0.2, "novelty": 0.15, "payoff": 0.2}
# Opportunity overall; viral_signal only counts when the candidate has metrics
OPPORTUNITY_WEIGHTS = {
    "viral_signal": 0.15,
    "hook_potential": 0.25,
    "novelty": 0.15,
    "channel_fit": 0.15,
    "visual_feasibility": 0.15,
    "production_efficiency": 0.15,
}


class CandidateScore(BaseModel):
    viral_score: Score
    signals: dict[str, float]  # 0-10 per signal
    raw: dict[str, float]  # inputs behind the signals (age_days, views_per_day, engagement_rate, ...)
    scored_at: str = Field(default_factory=now_iso)


class DiscoveryCandidate(BaseModel):
    id: str
    source: CandidateSource
    url: str | None = None
    platform: str | None = None  # youtube / tiktok / facebook / other
    external_id: str | None = None  # e.g. YouTube video id
    topic: str | None = None
    title: str | None = None
    description: str | None = None
    author: str | None = None
    author_id: str | None = None
    published_at: str | None = None
    duration_s: float | None = None
    metrics: dict[str, int] = Field(default_factory=dict)  # views / likes / comments when known
    transcript: str | None = None
    notes: str | None = None
    niche: str | None = None
    query: str | None = None  # the YouTube search that found it
    enrich_error: str | None = None  # why metadata enrichment was skipped/failed (the URL is still saved)
    score: CandidateScore | None = None
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    @property
    def label(self) -> str:
        return self.title or self.topic or self.url or self.id


class HookCandidate(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    curiosity: Score = Field(ge=0, le=10)
    clarity: Score = Field(ge=0, le=10)
    tension: Score = Field(ge=0, le=10)
    novelty: Score = Field(ge=0, le=10)
    payoff: Score = Field(ge=0, le=10)

    @field_validator("text")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("hook text must not be empty")
        return v

    @property
    def overall(self) -> Score:
        return round(sum(getattr(self, k) * w for k, w in HOOK_WEIGHTS.items()), 2)


class OpportunityScores(BaseModel):
    viral_signal: Score | None = None  # from the candidate's rule-based score, never from the LLM
    hook_potential: Score = Field(ge=0, le=10)
    novelty: Score = Field(ge=0, le=10)
    channel_fit: Score = Field(ge=0, le=10)
    visual_feasibility: Score = Field(ge=0, le=10)
    production_efficiency: Score = Field(ge=0, le=10)
    overall: Score = 0.0

    def recompute(self) -> None:
        parts = {k: getattr(self, k) for k in OPPORTUNITY_WEIGHTS}
        known = {k: v for k, v in parts.items() if v is not None}
        weight = sum(OPPORTUNITY_WEIGHTS[k] for k in known)
        self.overall = round(sum(v * OPPORTUNITY_WEIGHTS[k] for k, v in known.items()) / weight, 2)


class AnalysisDraft(BaseModel):
    """What an analyzer produces; validated before it becomes a stored ContentOpportunity."""

    topic: str = Field(min_length=3, max_length=300)
    angle: str = Field(min_length=1, max_length=600)
    hook_pattern: str = Field(min_length=1, max_length=300)
    hooks: list[HookCandidate] = Field(min_length=3, max_length=7)
    format_id: str
    story_structure: list[str] = Field(min_length=3, max_length=8)
    why_it_may_work: list[str] = Field(min_length=1, max_length=6)
    novelty: Score = Field(ge=0, le=10)
    channel_fit: Score = Field(ge=0, le=10)
    visual_feasibility: Score = Field(ge=0, le=10)
    production_efficiency: Score = Field(ge=0, le=10)

    @field_validator("format_id")
    @classmethod
    def _known_format(cls, v: str) -> str:
        from .formats import FORMATS

        v = v.strip().upper()
        if v not in FORMATS:
            raise ValueError(f"unknown format_id {v!r}; use one of {', '.join(FORMATS)}")
        return v


class ContentOpportunity(BaseModel):
    id: str
    candidate_id: str
    analyzer: str
    topic: str
    angle: str
    hook_pattern: str
    hooks: list[HookCandidate]
    selected_hook: int = 0  # index into hooks; defaults to the best overall
    format_id: str  # analyzer's pick, user can override
    analyzed_format_id: str | None = None  # the analyzer's pick, whose beats story_structure follows
    story_structure: list[str]
    why_it_may_work: list[str]
    scores: OpportunityScores
    language: str = "vi"
    cost_usd: float = 0.0
    project_ids: list[str] = Field(default_factory=list)
    saved_pattern: bool = False
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    @property
    def hook(self) -> HookCandidate:
        return self.hooks[self.selected_hook]


class FormatTemplate(BaseModel):
    id: str
    name: str
    description: str
    hook_type: str
    story_beats: list[str]
    target_duration: tuple[int, int]
    pacing: Literal["fast", "medium", "slow"]
