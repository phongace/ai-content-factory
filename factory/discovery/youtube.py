"""YouTube Data API v3 discovery with an API key: one search.list, then one batched videos.list (no N+1).

search.list draws on its own small daily quota, so identical searches are cached by the discovery store.
No scraping, no transcripts, public data only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import requests

API = "https://www.googleapis.com/youtube/v3"
HTTP_TIMEOUT_S = 30
MAX_PAGE = 50  # search.list and videos.list both cap at 50 per call

NOT_CONFIGURED = "YouTube discovery not configured (set YOUTUBE_API_KEY). Manual URL/content discovery still works."

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_DURATION_RE = re.compile(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


class DiscoveryError(Exception):
    pass


class DiscoveryNotConfigured(DiscoveryError):
    pass


@dataclass
class YouTubeVideo:
    video_id: str
    title: str
    description: str
    channel_id: str
    channel_title: str
    published_at: str
    duration_s: float | None
    views: int | None
    likes: int | None
    comments: int | None

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"

    def metrics(self) -> dict[str, int]:
        m = {"views": self.views, "likes": self.likes, "comments": self.comments}
        return {k: v for k, v in m.items() if v is not None}


def video_id_from_url(url: str) -> str | None:
    """watch?v=, youtu.be/, /shorts/, /embed/, /live/ forms; None for anything else."""
    try:
        u = urlparse(url.strip())
    except ValueError:
        return None
    host = (u.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    candidate = None
    if host == "youtu.be":
        candidate = u.path.strip("/").split("/")[0]
    elif host in ("youtube.com", "music.youtube.com"):
        if u.path == "/watch":
            candidate = (parse_qs(u.query).get("v") or [""])[0]
        else:
            parts = u.path.strip("/").split("/")
            if len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v"):
                candidate = parts[1]
    return candidate if candidate and _VIDEO_ID_RE.match(candidate) else None


def platform_of(url: str) -> str:
    host = (urlparse(url.strip()).hostname or "").lower()
    for name, marks in {
        "youtube": ("youtube.com", "youtu.be"),
        "tiktok": ("tiktok.com",),
        "facebook": ("facebook.com", "fb.watch"),
        "instagram": ("instagram.com",),
    }.items():
        if any(host == m or host.endswith("." + m) for m in marks):
            return name
    return "other"


def parse_duration(iso: str | None) -> float | None:
    m = _DURATION_RE.match(iso or "")
    if not m or not any(m.groups()):
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    total = ((d * 24 + h) * 60 + mi) * 60 + s
    return float(total) if total else None  # live streams report P0D


def _int(v: object) -> int | None:
    try:
        return int(str(v))
    except (TypeError, ValueError):
        return None


def _api_error(r: requests.Response) -> str:
    try:
        err = r.json().get("error", {})
        reason = (err.get("errors") or [{}])[0].get("reason", "")
        message = err.get("message", "")
    except ValueError:
        reason, message = "", r.text[:200]
    if reason == "quotaExceeded":
        return "YouTube API quota exceeded for today; cached searches and manual discovery still work"
    if r.status_code in (400, 403) and ("API key" in message or reason in ("keyInvalid", "forbidden")):
        return f"YouTube API rejected the key ({reason or r.status_code}): check YOUTUBE_API_KEY and its restrictions"
    return f"YouTube API {r.status_code} {reason}: {message}".strip()


class YouTubeDiscovery:
    def __init__(self, api_key: str | None, session=None):
        if not api_key:
            raise DiscoveryNotConfigured(NOT_CONFIGURED)
        self.key = api_key
        self.http = session or requests.Session()

    def _get(self, endpoint: str, params: dict) -> dict:
        try:
            r = self.http.get(f"{API}/{endpoint}", params={**params, "key": self.key}, timeout=HTTP_TIMEOUT_S)
        except requests.RequestException as e:
            raise DiscoveryError(f"YouTube request failed: {type(e).__name__}") from e
        if r.status_code != 200:
            raise DiscoveryError(_api_error(r))
        return r.json()

    def search(
        self,
        query: str,
        lookback_days: int = 30,
        max_results: int = 20,
        language: str | None = None,
        shorts_only: bool = True,
        now: datetime | None = None,
    ) -> list[YouTubeVideo]:
        if not query.strip():
            raise ValueError("enter a niche or keywords to search")
        after = (now or datetime.now(UTC)) - timedelta(days=lookback_days)
        params: dict[str, str | int] = {
            "part": "snippet",
            "type": "video",
            "q": query.strip(),
            "order": "viewCount",
            "publishedAfter": after.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "maxResults": max(1, min(max_results, MAX_PAGE)),
        }
        if shorts_only:
            params["videoDuration"] = "short"  # under 4 minutes
        if language:
            params["relevanceLanguage"] = language
        data = self._get("search", params)
        ids = [i["id"]["videoId"] for i in data.get("items", []) if i.get("id", {}).get("videoId")]
        return self.videos(ids)

    def videos(self, ids: list[str]) -> list[YouTubeVideo]:
        out: list[YouTubeVideo] = []
        for start in range(0, len(ids), MAX_PAGE):
            batch = ids[start : start + MAX_PAGE]
            data = self._get("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(batch)})
            by_id = {i["id"]: i for i in data.get("items", [])}
            for vid in batch:  # keep search order; skip ids that vanished (deleted/private)
                item = by_id.get(vid)
                if item is None:
                    continue
                sn, st = item.get("snippet", {}), item.get("statistics", {})
                out.append(
                    YouTubeVideo(
                        video_id=vid,
                        title=sn.get("title", ""),
                        description=sn.get("description", ""),
                        channel_id=sn.get("channelId", ""),
                        channel_title=sn.get("channelTitle", ""),
                        published_at=sn.get("publishedAt", ""),
                        duration_s=parse_duration(item.get("contentDetails", {}).get("duration")),
                        views=_int(st.get("viewCount")),
                        likes=_int(st.get("likeCount")),  # hidden like counts are simply absent
                        comments=_int(st.get("commentCount")),
                    )
                )
        return out
