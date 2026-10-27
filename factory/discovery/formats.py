"""Built-in short-form format library (no editor in v0). The analyzer picks one; the user can override it."""

from __future__ import annotations

from .models import FormatTemplate

_ALL = [
    FormatTemplate(
        id="CONTRARIAN_EXPLAINER",
        name="Contrarian explainer",
        description="Flip a belief most viewers hold, then explain why the opposite is true.",
        hook_type="contrarian_statement",
        story_beats=["hook", "common_belief", "contradiction", "explanation", "payoff"],
        target_duration=(25, 40),
        pacing="fast",
    ),
    FormatTemplate(
        id="MYTH_BUST",
        name="Myth bust",
        description="Name a popular myth, show the evidence against it, give the real rule.",
        hook_type="myth_callout",
        story_beats=["hook", "myth", "evidence", "truth", "takeaway"],
        target_duration=(25, 40),
        pacing="fast",
    ),
    FormatTemplate(
        id="WHAT_IF_ESCALATION",
        name="What-if escalation",
        description="A hypothetical that gets more extreme each step, ending on a surprising consequence.",
        hook_type="hypothetical_question",
        story_beats=["hook", "setup", "escalation_1", "escalation_2", "payoff"],
        target_duration=(25, 45),
        pacing="fast",
    ),
    FormatTemplate(
        id="THREE_MISTAKES",
        name="Three mistakes",
        description="Three common mistakes, each with a one-line fix.",
        hook_type="warning",
        story_beats=["hook", "mistake_1", "mistake_2", "mistake_3", "payoff"],
        target_duration=(25, 40),
        pacing="fast",
    ),
    FormatTemplate(
        id="DARK_FACT",
        name="Dark fact",
        description="One unsettling, verifiable fact, its context and why it matters to the viewer.",
        hook_type="shocking_fact",
        story_beats=["hook", "context", "reveal", "implication"],
        target_duration=(20, 35),
        pacing="medium",
    ),
    FormatTemplate(
        id="PROBLEM_SOLUTION",
        name="Problem / solution",
        description="A relatable pain point, why the usual fix fails, the better fix.",
        hook_type="pain_point",
        story_beats=["hook", "problem", "failed_fix", "solution", "payoff"],
        target_duration=(25, 40),
        pacing="fast",
    ),
    FormatTemplate(
        id="BEFORE_AFTER",
        name="Before / after",
        description="Show the before state, the single change, and the after state.",
        hook_type="transformation_promise",
        story_beats=["hook", "before", "change", "after", "takeaway"],
        target_duration=(20, 35),
        pacing="fast",
    ),
    FormatTemplate(
        id="MINI_DOCUMENTARY",
        name="Mini documentary",
        description="A compact true story with a turning point and a lesson.",
        hook_type="story_tease",
        story_beats=["hook", "setting", "conflict", "turning_point", "resolution"],
        target_duration=(30, 45),
        pacing="medium",
    ),
    FormatTemplate(
        id="COUNTDOWN",
        name="Countdown",
        description="Top 3 list counting down to the most surprising item.",
        hook_type="ranking_tease",
        story_beats=["hook", "item_3", "item_2", "item_1", "payoff"],
        target_duration=(25, 45),
        pacing="fast",
    ),
    FormatTemplate(
        id="POV_STORY",
        name="POV story",
        description="Second-person scenario the viewer lives through, ending on a twist or lesson.",
        hook_type="pov_scenario",
        story_beats=["hook", "situation", "tension", "twist", "lesson"],
        target_duration=(20, 40),
        pacing="fast",
    ),
]

FORMATS: dict[str, FormatTemplate] = {f.id: f for f in _ALL}
DEFAULT_FORMAT = "CONTRARIAN_EXPLAINER"


def get_format(format_id: str) -> FormatTemplate:
    try:
        return FORMATS[format_id.strip().upper()]
    except KeyError:
        raise ValueError(f"unknown format_id {format_id!r}; use one of {', '.join(FORMATS)}") from None
