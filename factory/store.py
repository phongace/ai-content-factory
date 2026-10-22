"""Local filesystem project store (tracker P0-03). No database in the POC.

data/projects/{id}/
  project.json  storyboard.json
  scenes/  audio/  subtitles/  renders/  logs/
"""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import threading
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .schema import PlannerInput, Project, StoryboardFile, now_iso

SUBDIRS = ("scenes", "audio", "subtitles", "renders", "logs")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,80}$")
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class NotFound(Exception):
    pass


def _lock(key: str) -> threading.RLock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def slugify(text: str) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:40].strip("-") or "project"


def atomic_write_text(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def scene_name(order: int, ext: str) -> str:
    return f"scene_{order:02d}.{ext.lstrip('.')}"


class ProjectStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ---------- paths ----------
    def dir(self, project_id: str) -> Path:
        if not ID_RE.match(project_id):
            raise NotFound(f"invalid project id: {project_id}")
        return self.root / project_id

    def path(self, project_id: str, rel: str) -> Path:
        base = self.dir(project_id).resolve()
        p = (base / rel).resolve()
        if p != base and base not in p.parents:
            raise NotFound(f"path escapes project: {rel}")
        return p

    # ---------- project ----------
    def create(self, inp: PlannerInput, voice_preset: str = "default") -> Project:
        stamp = datetime.now(UTC).strftime("%Y%m%d")
        pid = f"{stamp}-{slugify(inp.topic)}-{secrets.token_hex(3)}"
        d = self.dir(pid)
        for sub in SUBDIRS:
            (d / sub).mkdir(parents=True, exist_ok=True)
        project = Project(
            id=pid,
            topic=inp.topic,
            title=inp.topic,
            target_duration=inp.target_duration,
            language=inp.language,
            visual_style=inp.visual_style,
            voice_preset=voice_preset,
        )
        self._write_project(project)
        self.log(pid, f"created topic={inp.topic!r}")
        return project

    def get(self, project_id: str) -> Project:
        f = self.dir(project_id) / "project.json"
        if not f.is_file():
            raise NotFound(f"project not found: {project_id}")
        return Project.model_validate_json(f.read_text(encoding="utf-8"))

    def list(self) -> list[Project]:
        out = []
        for d in self.root.iterdir() if self.root.exists() else []:
            if d.is_dir() and ID_RE.match(d.name) and (d / "project.json").is_file():
                try:
                    out.append(self.get(d.name))
                except Exception:  # corrupt folder must not break the list
                    continue
        return sorted(out, key=lambda p: p.created_at, reverse=True)

    def update(self, project_id: str, fn: Callable[[Project], None]) -> Project:
        with _lock(f"p:{project_id}"):
            p = self.get(project_id)
            fn(p)
            p.cost.recompute()
            p.estimated_cost = p.cost.total_estimated_cost
            p.updated_at = now_iso()
            self._write_project(Project.model_validate(p.model_dump()))
            return p

    def _write_project(self, p: Project) -> None:
        atomic_write_text(self.dir(p.id) / "project.json", p.model_dump_json(indent=2) + "\n")

    # ---------- storyboard ----------
    def get_storyboard(self, project_id: str) -> StoryboardFile | None:
        f = self.dir(project_id) / "storyboard.json"
        if not f.is_file():
            return None
        return StoryboardFile.model_validate_json(f.read_text(encoding="utf-8"))

    def save_storyboard(self, project_id: str, sb: StoryboardFile) -> StoryboardFile:
        with _lock(f"s:{project_id}"):
            sb = StoryboardFile.model_validate(sb.model_dump())
            atomic_write_text(self.dir(project_id) / "storyboard.json", sb.model_dump_json(indent=2) + "\n")
            return sb

    def update_storyboard(self, project_id: str, fn: Callable[[StoryboardFile], None]) -> StoryboardFile:
        with _lock(f"s:{project_id}"):
            sb = self.get_storyboard(project_id)
            if sb is None:
                raise NotFound(f"project {project_id} has no storyboard yet")
            fn(sb)
            return self.save_storyboard(project_id, sb)

    # ---------- logs ----------
    def log(self, project_id: str, message: str) -> None:
        f = self.dir(project_id) / "logs" / "events.log"
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a", encoding="utf-8") as fh:
            fh.write(f"{now_iso()} {message}\n")

    def write_json(self, project_id: str, rel: str, data: dict) -> None:
        atomic_write_text(self.path(project_id, rel), json.dumps(data, indent=2, ensure_ascii=False) + "\n")
