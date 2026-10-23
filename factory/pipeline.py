"""Service layer used by both the Streamlit UI and the CLI. Every stage persists to disk and records
status/stage/error so a failure says where it happened (tracker P0-16, 01_Flow step 10)."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from . import media
from .config import Settings, get_settings
from .planner import ContentPlanner, PlannerError, get_planner
from .publish.base import Publisher, PublishRequest
from .render import render as render_video
from .schema import PlannerInput, Project, StoryboardFile, now_iso
from .store import ProjectStore, scene_name
from .voice import VoiceRequest, get_voice_provider, import_audio

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}


class Factory:
    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()
        self.store = ProjectStore(self.s.projects_dir)

    # ------------------------------------------------------------ helpers
    @contextmanager
    def stage(self, pid: str, name: str) -> Iterator[None]:
        def start(p: Project) -> None:
            p.current_stage = name  # type: ignore[assignment]
            p.error = None

        self.store.update(pid, start)
        self.store.log(pid, f"stage {name}: start")
        try:
            yield
        except Exception as e:
            msg = f"{name}: {e}"

            def fail(p: Project) -> None:
                p.status = "error"
                p.error = msg

            self.store.update(pid, fail)
            self.store.log(pid, f"stage {name}: ERROR {e}")
            raise
        self.store.log(pid, f"stage {name}: ok")

    def _touch_assets(self, p: Project) -> None:
        p.assets_changed_at = now_iso()
        if p.status in ("planned", "rendered", "published", "error", "draft"):
            p.status = "assets"
        p.error = None

    def storyboard(self, pid: str) -> StoryboardFile:
        sb = self.store.get_storyboard(pid)
        if sb is None:
            raise ValueError("generate the plan first")
        return sb

    # ------------------------------------------------------------ create / plan
    def create_project(
        self,
        topic: str,
        target_duration: int = 30,
        language: str = "vi",
        visual_style: str = "cinematic, realistic, soft natural light",
        voice_preset: str = "default",
    ) -> Project:
        inp = PlannerInput(
            topic=topic,
            target_duration=target_duration,
            language=language,  # type: ignore[arg-type]
            visual_style=visual_style,
        )
        return self.store.create(inp, voice_preset)

    def plan(self, pid: str, planner: ContentPlanner | None = None) -> StoryboardFile:
        p = self.store.get(pid)
        planner = planner or get_planner(self.s)
        inp = PlannerInput(
            topic=p.topic, target_duration=p.target_duration, language=p.language, visual_style=p.visual_style
        )
        with self.stage(pid, "planning"):
            try:
                result = planner.generate(inp)
            except PlannerError as e:
                if e.cost_usd:
                    failed_cost = e.cost_usd

                    def spent(p: Project) -> None:
                        p.cost.claude_estimated_cost = round(p.cost.claude_estimated_cost + failed_cost, 6)

                    self.store.update(pid, spent)
                    self.store.log(
                        pid,
                        f"plan failed after {e.attempts} attempts: "
                        f"tokens={e.input_tokens}/{e.output_tokens}, cost=${e.cost_usd}",
                    )
                raise
            sb = self.store.save_storyboard(pid, StoryboardFile.from_plan(result.storyboard))
            for d in ("audio", "scenes"):  # a new plan invalidates old per-scene assets
                for f in self.store.path(pid, d).glob("scene_*"):
                    f.unlink()

            def done(p: Project) -> None:
                p.title = result.storyboard.title
                p.planner_provider = result.provider
                p.generation_count += 1
                p.cost.claude_estimated_cost = round(p.cost.claude_estimated_cost + result.cost_usd, 6)
                p.status, p.current_stage = "planned", "storyboard"
                p.assets_changed_at = now_iso()

            self.store.update(pid, done)
            self.store.log(
                pid,
                f"plan by {result.provider}: {len(sb.scenes)} scenes, attempts={result.attempts}, "
                f"tokens={result.input_tokens}/{result.output_tokens}, cost=${result.cost_usd}",
            )
        return sb

    # ------------------------------------------------------------ edits
    def edit_scene(self, pid: str, order: int, narration: str | None = None, video_prompt: str | None = None) -> None:
        def fn(sb: StoryboardFile) -> None:
            s = sb.scene(order)
            if narration is not None:
                if not narration.strip():
                    raise ValueError("narration cannot be empty")
                s.narration = narration.strip()
            if video_prompt is not None:
                s.video_prompt = video_prompt.strip()

        self.store.update_storyboard(pid, fn)
        if narration is not None:
            self.store.update(pid, self._touch_assets)

    def edit_metadata(self, pid: str, title: str, description: str, hashtags: list[str]) -> None:
        def fn(sb: StoryboardFile) -> None:
            sb.title, sb.description = title.strip()[:100], description.strip()
            sb.hashtags = ["#" + h.strip().lstrip("#") for h in hashtags if h.strip().lstrip("#")]

        self.store.update_storyboard(pid, fn)

    def set_muse_cost(self, pid: str, usd: float) -> None:
        def fn(p: Project) -> None:
            p.cost.muse_estimated_cost = max(0.0, float(usd))

        self.store.update(pid, fn)

    # ------------------------------------------------------------ clips (manual Muse import)
    def attach_clip(self, pid: str, order: int, src: str | Path) -> None:
        src = Path(src)
        ext = src.suffix.lower()
        with self.stage(pid, "clips"):
            self.storyboard(pid).scene(order)  # validates order
            dst = self.store.path(pid, f"scenes/{scene_name(order, 'mp4')}")
            tmp = dst.with_name(f".incoming{ext or '.bin'}")
            shutil.copyfile(src, tmp)
            try:
                if ext in IMAGE_EXT:
                    media.image_to_clip(tmp, dst.with_name(".converted.mp4"))
                    tmp.unlink()
                    tmp = dst.with_name(".converted.mp4")
                elif ext not in VIDEO_EXT:
                    raise ValueError(f"unsupported clip type '{ext}' (use mp4/mov/webm or an image)")
                info = media.probe(tmp)
                if not info.has_video:
                    raise ValueError("file has no video stream")
                tmp.replace(dst)
            finally:
                tmp.unlink(missing_ok=True)

            def fn(sb: StoryboardFile) -> None:
                s = sb.scene(order)
                s.clip_file = f"scenes/{dst.name}"
                s.clip_duration, s.clip_width, s.clip_height = info.duration, info.width, info.height

            self.store.update_storyboard(pid, fn)

            def proj(p: Project) -> None:
                p.muse_clip_count += 1
                p.cost.muse_estimated_cost = round(p.cost.muse_estimated_cost + self.s.muse_cost_per_clip, 6)
                self._touch_assets(p)

            self.store.update(pid, proj)

    # ------------------------------------------------------------ voice
    def generate_voice(self, pid: str, orders: list[int] | None = None, provider: str | None = None) -> None:
        p = self.store.get(pid)
        sb = self.storyboard(pid)
        prov = get_voice_provider(provider, self.s)
        targets = [s for s in sb.scenes if orders is None or s.order in orders]
        with self.stage(pid, "voice"):
            for s in targets:
                out = self.store.path(pid, f"audio/{scene_name(s.order, 'wav')}")
                tmp = out.with_name(f".gen_{out.name}")
                try:
                    prov.generate(VoiceRequest(text=s.narration, language=p.language, output=tmp))
                    duration = media.audio_duration(tmp)
                    tmp.replace(out)
                except Exception as e:
                    raise RuntimeError(f"scene {s.order}: {e}") from e
                finally:
                    tmp.unlink(missing_ok=True)
                self._save_audio(pid, s.order, out, duration, prov.name, s.narration)

    def import_voice(self, pid: str, order: int, src: str | Path) -> None:
        if Path(src).suffix.lower() not in AUDIO_EXT:
            raise ValueError("unsupported audio type (use wav/mp3/m4a/aac/flac/ogg)")
        with self.stage(pid, "voice"):
            s = self.storyboard(pid).scene(order)
            out = self.store.path(pid, f"audio/{scene_name(order, 'wav')}")
            import_audio(src, out)
            self._save_audio(pid, order, out, media.audio_duration(out), "manual", s.narration)

    def _save_audio(self, pid: str, order: int, out: Path, duration: float, source: str, narration: str) -> None:
        def fn(sb: StoryboardFile) -> None:
            sc = sb.scene(order)
            sc.audio_file = f"audio/{out.name}"
            sc.actual_audio_duration = duration
            sc.audio_source = source
            sc.audio_narration = narration

        self.store.update_storyboard(pid, fn)

        def proj(p: Project) -> None:
            p.cost.voice_estimated_cost = round(
                p.cost.voice_estimated_cost + duration * self.s.voice_cost_per_second, 6
            )
            self._touch_assets(p)

        self.store.update(pid, proj)
        self.store.log(pid, f"voice scene {order}: {source} {duration:.2f}s")

    # ------------------------------------------------------------ render / QC
    def render(self, pid: str, bgm: str | Path | None = None, bgm_volume: float | None = None) -> Project:
        p = self.store.get(pid)
        sb = self.storyboard(pid)
        music = Path(bgm) if bgm else (Path(self.s.bgm_file) if self.s.bgm_file else None)

        def start(p: Project) -> None:
            p.status = "rendering"
            p.render.status, p.render.error = "rendering", None

        self.store.update(pid, start)
        try:
            with self.stage(pid, "render"):
                out = render_video(
                    pid,
                    self.store.dir(pid),
                    p.language,
                    sb,
                    music,
                    self.s.bgm_volume if bgm_volume is None else bgm_volume,
                )
        except Exception as e:
            err = str(e)

            def failed(p: Project) -> None:
                p.render.status, p.render.error = "error", err

            self.store.update(pid, failed)
            raise

        def done(p: Project) -> None:
            r = p.render
            r.status, r.error, r.rendered_at = "done", None, now_iso()
            r.file = "renders/final-short.mp4"
            r.duration, r.file_size, r.width, r.height = out.duration, out.size, out.width, out.height
            r.render_count += 1
            r.reused_stages = out.reused_stages
            p.cost.render_duration_ms = out.elapsed_ms
            p.status, p.current_stage = "rendered", "qc"

        p = self.store.update(pid, done)
        self.store.log(
            pid, f"render ok {out.duration:.2f}s {out.size}B in {out.elapsed_ms}ms reused={out.reused_stages}"
        )
        return p

    # ------------------------------------------------------------ publish
    def publish(self, pid: str, publisher: Publisher, privacy: str = "private", force: bool = False) -> Project:
        p = self.store.get(pid)
        if p.youtube_video_id and not force:
            raise ValueError(f"already uploaded to YouTube ({p.youtube_url}); pass force to upload a new copy")
        if p.render.status != "done" or not p.render.file:
            raise ValueError("render the video before publishing")
        if p.render_stale:
            raise ValueError("assets changed after the last render, render again before publishing")
        sb = self.storyboard(pid)
        req = PublishRequest(
            file=self.store.path(pid, p.render.file),
            title=sb.title,
            description=sb.description,
            hashtags=sb.hashtags,
            privacy=privacy,  # type: ignore[arg-type]
            language=p.language,
        )

        def start(p: Project) -> None:
            p.publish_status, p.publish_error = "uploading", None

        self.store.update(pid, start)
        try:
            with self.stage(pid, "publish"):
                res = publisher.upload(req)
        except Exception as e:
            err = str(e)

            def failed(p: Project) -> None:
                p.publish_status, p.publish_error = "error", err

            self.store.update(pid, failed)
            raise

        actual_privacy = res.privacy or req.privacy

        def done(p: Project) -> None:
            p.youtube_video_id, p.youtube_url = res.video_id, res.url
            p.publish_status, p.published_at, p.publish_privacy = "uploaded", now_iso(), actual_privacy
            p.status = "published"

        p = self.store.update(pid, done)
        self.store.log(pid, f"published {res.url} (requested {req.privacy}, actual {actual_privacy})")
        return p
