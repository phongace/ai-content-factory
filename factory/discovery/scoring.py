"""Rule-based viral score: cheap, explainable ranking before any LLM call. A heuristic, not a prediction.

Signals (each 0-10, components persisted on the candidate so a ranking can be debugged):
- view_velocity: views per day since publishing, log-scaled; VELOCITY_REF views/day = 10
- engagement: (likes + 2 * comments) / views; ENGAGEMENT_REF = 10
- recency: halves every RECENCY_HALF_LIFE_DAYS
- reach: absolute views, log-scaled; REACH_REF views = 10
Age is floored at MIN_AGE_DAYS so a video a few minutes old does not get an absurd velocity.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from .models import CandidateScore, DiscoveryCandidate

WEIGHTS = {"view_velocity": 0.45, "engagement": 0.2, "recency": 0.15, "reach": 0.2}
VELOCITY_REF = 100_000.0
ENGAGEMENT_REF = 0.08
RECENCY_HALF_LIFE_DAYS = 14.0
REACH_REF = 10_000_000.0
MIN_AGE_DAYS = 0.25


def _clamp(v: float) -> float:
    return round(max(0.0, min(10.0, v)), 2)


def _log_scale(value: float, ref: float) -> float:
    return _clamp(10 * math.log10(1 + max(value, 0.0)) / math.log10(1 + ref))


def age_days(published_at: str | None, now: datetime | None = None) -> float | None:
    if not published_at:
        return None
    try:
        published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if published.tzinfo is None:
        published = published.replace(tzinfo=UTC)
    now = now or datetime.now(UTC)
    return max((now - published).total_seconds() / 86400, MIN_AGE_DAYS)


def score_candidate(c: DiscoveryCandidate, now: datetime | None = None) -> CandidateScore | None:
    """None when the candidate has no view count or publish date (manual references usually have neither)."""
    views = c.metrics.get("views")
    age = age_days(c.published_at, now)
    if views is None or age is None:
        return None
    likes = c.metrics.get("likes", 0)
    comments = c.metrics.get("comments", 0)
    velocity = views / age
    engagement = (likes + 2 * comments) / views if views > 0 else 0.0
    signals = {
        "view_velocity": _log_scale(velocity, VELOCITY_REF),
        "engagement": _clamp(10 * engagement / ENGAGEMENT_REF),
        "recency": _clamp(10 * 0.5 ** (age / RECENCY_HALF_LIFE_DAYS)),
        "reach": _log_scale(views, REACH_REF),
    }
    viral = round(sum(signals[k] * w for k, w in WEIGHTS.items()), 2)
    raw = {
        "views": float(views),
        "likes": float(likes),
        "comments": float(comments),
        "age_days": round(age, 3),
        "views_per_day": round(velocity, 1),
        "engagement_rate": round(engagement, 5),
    }
    return CandidateScore(viral_score=viral, signals=signals, raw=raw)


def rank(candidates: list[DiscoveryCandidate]) -> list[DiscoveryCandidate]:
    """Scored candidates first by viral score, unscored ones after them (newest first)."""
    scored = sorted((c for c in candidates if c.score), key=lambda c: c.score.viral_score if c.score else 0.0)
    unscored = sorted((c for c in candidates if not c.score), key=lambda c: c.created_at)
    return [*reversed(scored), *reversed(unscored)]
