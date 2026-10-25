"""Publisher abstraction (tracker P0-14, P0-15). POC: YouTube only."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

Privacy = Literal["private", "unlisted"]  # never public by default (Public is a manual step)


class PublishError(Exception):
    pass


class NotConfigured(PublishError):
    pass


@dataclass
class PublishRequest:
    file: Path
    title: str
    description: str
    hashtags: list[str]
    privacy: Privacy = "private"
    language: str = "vi"


@dataclass
class PublishResult:
    video_id: str
    url: str
    raw: dict = field(default_factory=dict)
    privacy: Privacy | None = None  # what the platform actually applied, which can differ from the request


class Publisher(Protocol):
    name: str

    def upload(self, req: PublishRequest) -> PublishResult: ...


TITLE_MAX_CHARS = 100
DESCRIPTION_MAX_BYTES = 4999  # YouTube allows 5000 UTF-8 bytes; stay strictly under


def strip_angle_brackets(text: str) -> str:
    return text.replace("<", "").replace(">", "")


def clean_title(title: str) -> str:
    return strip_angle_brackets(title).strip()[:TITLE_MAX_CHARS]


def clean_description(description: str) -> str:
    data = strip_angle_brackets(description).strip().encode("utf-8")
    return data[:DESCRIPTION_MAX_BYTES].decode("utf-8", errors="ignore")


def build_description(description: str, hashtags: list[str]) -> str:
    tags = " ".join(h if h.startswith("#") else f"#{h}" for h in hashtags)
    return clean_description(f"{description.strip()}\n\n{tags}")


class MockPublisher:
    """Records uploads instead of calling YouTube. Used by tests and dry runs."""

    name = "mock"

    def __init__(self) -> None:
        self.uploads: list[PublishRequest] = []

    def upload(self, req: PublishRequest) -> PublishResult:
        if req.privacy not in ("private", "unlisted"):
            raise PublishError("privacy must be private or unlisted")
        if not req.file.is_file():
            raise PublishError(f"file not found: {req.file}")
        self.uploads.append(req)
        vid = f"mock{len(self.uploads):07d}"
        return PublishResult(vid, f"https://youtube.com/shorts/{vid}", privacy=req.privacy)
