"""Local JSON store for discovery (same atomic-write pattern as the project store, no database).

data/discoveries/{candidate_id}.json
data/discoveries/_search_cache/{query_hash}.json   YouTube search results, reused within DISCOVERY_CACHE_HOURS
data/opportunities/{opportunity_id}.json
data/patterns/{opportunity_id}.json                 "Save Pattern" references
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel

from ..schema import now_iso
from ..store import NotFound, atomic_write_text, slugify
from .models import ContentOpportunity, DiscoveryCandidate

RECORD_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,100}$")


def new_id(prefix: str, text: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    return f"{prefix}-{stamp}-{slugify(text)}-{secrets.token_hex(3)}"


class _JsonDir:
    def __init__(self, root: Path):
        self.root = Path(root)

    def file(self, record_id: str) -> Path:
        if not RECORD_ID_RE.match(record_id):
            raise NotFound(f"invalid id: {record_id}")
        return self.root / f"{record_id}.json"

    def write(self, record_id: str, model: BaseModel) -> None:
        atomic_write_text(self.file(record_id), model.model_dump_json(indent=2) + "\n")

    def read_text(self, record_id: str) -> str:
        f = self.file(record_id)
        if not f.is_file():
            raise NotFound(f"not found: {record_id}")
        return f.read_text(encoding="utf-8")

    def ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return [f.stem for f in self.root.glob("*.json") if RECORD_ID_RE.match(f.stem)]


class DiscoveryStore:
    def __init__(self, discoveries: Path, opportunities: Path, patterns: Path, cache_hours: float = 12.0):
        self.candidates = _JsonDir(discoveries)
        self.opportunities = _JsonDir(opportunities)
        self.patterns = _JsonDir(patterns)
        self.cache_dir = Path(discoveries) / "_search_cache"
        self.cache_hours = cache_hours

    # ---------- candidates ----------
    def save_candidate(self, c: DiscoveryCandidate) -> DiscoveryCandidate:
        c.updated_at = now_iso()
        c = DiscoveryCandidate.model_validate(c.model_dump())
        self.candidates.write(c.id, c)
        return c

    def get_candidate(self, cid: str) -> DiscoveryCandidate:
        return DiscoveryCandidate.model_validate_json(self.candidates.read_text(cid))

    def find_candidate(self, cid: str) -> DiscoveryCandidate | None:
        try:
            return self.get_candidate(cid)
        except NotFound:
            return None

    def update_candidate(self, cid: str, fn: Callable[[DiscoveryCandidate], None]) -> DiscoveryCandidate:
        c = self.get_candidate(cid)
        fn(c)
        return self.save_candidate(c)

    def list_candidates(self) -> list[DiscoveryCandidate]:
        out = []
        for cid in self.candidates.ids():
            try:
                out.append(self.get_candidate(cid))
            except (ValueError, OSError):  # a corrupt record must not break the list
                continue
        return out

    # ---------- opportunities ----------
    def save_opportunity(self, o: ContentOpportunity) -> ContentOpportunity:
        o.updated_at = now_iso()
        o = ContentOpportunity.model_validate(o.model_dump())
        self.opportunities.write(o.id, o)
        return o

    def get_opportunity(self, oid: str) -> ContentOpportunity:
        return ContentOpportunity.model_validate_json(self.opportunities.read_text(oid))

    def update_opportunity(self, oid: str, fn: Callable[[ContentOpportunity], None]) -> ContentOpportunity:
        o = self.get_opportunity(oid)
        fn(o)
        return self.save_opportunity(o)

    def list_opportunities(self, candidate_id: str | None = None) -> list[ContentOpportunity]:
        out = []
        for oid in self.opportunities.ids():
            try:
                o = self.get_opportunity(oid)
            except (ValueError, OSError):
                continue
            if candidate_id is None or o.candidate_id == candidate_id:
                out.append(o)
        return sorted(out, key=lambda o: o.created_at, reverse=True)

    def save_pattern(self, o: ContentOpportunity) -> Path:
        data = {
            "opportunity_id": o.id,
            "candidate_id": o.candidate_id,
            "format_id": o.format_id,
            "hook_pattern": o.hook_pattern,
            "angle": o.angle,
            "story_structure": o.story_structure,
            "saved_at": now_iso(),
        }
        f = self.patterns.file(o.id)
        atomic_write_text(f, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        return f

    # ---------- YouTube search cache ----------
    @staticmethod
    def cache_key(params: dict) -> str:
        return hashlib.sha256(json.dumps(params, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]

    def cached_search(self, params: dict, now: datetime | None = None) -> list[str] | None:
        """Candidate ids of an identical search made within `cache_hours`, else None."""
        f = self.cache_dir / f"{self.cache_key(params)}.json"
        if not f.is_file():
            return None
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            fetched = datetime.fromisoformat(data["fetched_at"])
        except (ValueError, KeyError, OSError):
            return None
        if (now or datetime.now(UTC)) - fetched > timedelta(hours=self.cache_hours):
            return None
        return list(data["candidate_ids"])

    def cache_search(self, params: dict, candidate_ids: list[str]) -> None:
        data = {"params": params, "fetched_at": now_iso(), "candidate_ids": candidate_ids}
        f = self.cache_dir / f"{self.cache_key(params)}.json"
        atomic_write_text(f, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
