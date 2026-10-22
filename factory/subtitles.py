"""Scene-level subtitles (tracker P0-09, P0-12). Timing = narration text + actual audio duration
per scene. No Whisper / word-level alignment in the POC (Phase 2)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

MAX_WORDS = {"vi": 8, "en": 6}  # per caption line, readable on a phone
MIN_CAPTION = 0.6  # seconds


@dataclass
class Cue:
    start: float
    end: float
    text: str


def split_caption_text(text: str, language: str = "vi") -> list[str]:
    max_words = MAX_WORDS.get(language, 7)
    parts = [p.strip() for p in re.split(r"(?<=[.!?…;:,])\s+", text.strip()) if p.strip()]
    chunks: list[str] = []
    for part in parts:
        words = part.split()
        n_chunks = max(1, -(-len(words) // max_words))
        size = -(-len(words) // n_chunks)  # balance chunk sizes
        chunks += [" ".join(words[i : i + size]) for i in range(0, len(words), size)]
    return chunks


def scene_cues(text: str, start: float, duration: float, language: str = "vi") -> list[Cue]:
    chunks = split_caption_text(text, language)
    if not chunks or duration <= 0:
        return []
    # Merge chunks until every cue is long enough to read.
    while len(chunks) > 1 and duration / len(chunks) < MIN_CAPTION:
        chunks[-2] = f"{chunks[-2]} {chunks[-1]}"
        chunks.pop()
    weights = [max(1, len(c)) for c in chunks]
    total = sum(weights)
    cues, t = [], start
    for i, (c, w) in enumerate(zip(chunks, weights, strict=True)):
        end = start + duration if i == len(chunks) - 1 else t + duration * w / total
        cues.append(Cue(round(t, 3), round(end, 3), c.rstrip(",;:")))
        t = end
    return cues


def build_cues(scenes: list[tuple[str, float]], language: str = "vi") -> list[Cue]:
    """scenes: [(narration, actual_audio_duration)] in order. Cues never overlap and never cross a
    scene boundary."""
    cues, offset = [], 0.0
    for text, dur in scenes:
        cues += scene_cues(text, offset, dur, language)
        offset += dur
    return cues


def _ts_ass(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ts_srt(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Short,Be Vietnam Pro,72,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,5,1,2,90,90,560,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def write_ass(cues: list[Cue], path: Path) -> None:
    lines = [ASS_HEADER]
    for c in cues:
        lines.append(f"Dialogue: 0,{_ts_ass(c.start)},{_ts_ass(c.end)},Short,,0,0,0,,{_ass_escape(c.text)}\n")
    path.write_text("".join(lines), encoding="utf-8")


def write_srt(cues: list[Cue], path: Path) -> None:
    out = [f"{i}\n{_ts_srt(c.start)} --> {_ts_srt(c.end)}\n{c.text}\n" for i, c in enumerate(cues, 1)]
    path.write_text("\n".join(out), encoding="utf-8")
