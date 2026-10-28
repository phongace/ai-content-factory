"""CLI: `uv run factory <command>`. Same service layer as the UI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import media
from .config import get_settings
from .pipeline import Factory
from .qc import qc_warnings
from .schema import ASSET_SOURCES


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def cmd_preflight(_a, _f: Factory) -> int:
    r = media.preflight()
    from .publish import youtube

    s = get_settings()
    r["planner"] = "claude" if s.anthropic_api_key and s.planner != "mock" else "mock"
    r["voice_provider"] = s.voice_provider
    r["youtube"] = youtube.status(s)
    r["data_dir"] = str(s.data_dir)
    _print(r)
    return 0 if not r["problems"] else 1


def cmd_new(a, f: Factory) -> int:
    p = f.create_project(a.topic, a.duration, a.language, a.style, visual_bible=a.bible or "")
    print(p.id)
    return 0


def cmd_list(_a, f: Factory) -> int:
    for p in f.store.list():
        print(f"{p.id}\t{p.status}\t{p.current_stage}\t${p.estimated_cost:.4f}\t{p.title}")
    return 0


def cmd_plan(a, f: Factory) -> int:
    sb = f.plan(a.project)
    _print(sb.model_dump())
    return 0


def cmd_voice(a, f: Factory) -> int:
    if a.file:
        f.import_voice(a.project, a.scene, a.file)
    else:
        f.generate_voice(a.project, [a.scene] if a.scene else None, a.provider)
    sb = f.storyboard(a.project)
    _print([{"scene": s.order, "audio": s.audio_file, "duration": s.actual_audio_duration} for s in sb.scenes])
    return 0


def cmd_clip(a, f: Factory) -> int:
    f.attach_asset(a.project, a.scene, a.file, a.source)
    s = f.storyboard(a.project).scene(a.scene)
    print(f"scene {a.scene}: {s.asset_type} asset attached (source: {s.asset_source})")
    return 0


def cmd_bible(a, f: Factory) -> int:
    f.edit_visual_bible(a.project, a.text)
    print(f.storyboard(a.project).visual_bible or "(visual bible cleared)")
    return 0


def cmd_muse_prompts(a, f: Factory) -> int:
    out = f.muse_sheet(a.project)
    print(out.read_text(encoding="utf-8"))
    print(f"saved: {out}", file=sys.stderr)
    return 0


def cmd_import_assets(a, f: Factory) -> int:
    report = f.import_assets(a.project, a.paths, by_time=a.by_time, source=a.source, dry_run=a.dry_run)
    _print(report)
    return 1 if any(r["result"].startswith("error") for r in report) else 0


def cmd_render(a, f: Factory) -> int:
    p = f.render(a.project, a.bgm, a.bgm_volume, no_bgm=a.no_bgm)
    _print({**p.render.model_dump(), "file": str(f.store.path(p.id, p.render.file or "renders/final-short.mp4"))})
    return 0


def cmd_status(a, f: Factory) -> int:
    _print(
        {
            "project": f.store.get(a.project).model_dump(),
            "storyboard": (sb.model_dump() if (sb := f.store.get_storyboard(a.project)) else None),
        }
    )
    return 0


def cmd_youtube_connect(_a, _f: Factory) -> int:
    from .publish import youtube

    s = get_settings()
    info = youtube.connect(s)
    _print({"state": "connected", "token_file": str(s.youtube_token_file), **info.as_dict()})
    return 0


def cmd_youtube_status(_a, _f: Factory) -> int:
    from .publish import youtube

    report = youtube.describe(get_settings())
    _print(report)
    return 0 if report["state"] == "connected" else 1


def cmd_publish(a, f: Factory) -> int:
    from .publish.youtube import YouTubePublisher

    p = f.publish(a.project, YouTubePublisher(), a.privacy, force=a.force, title=a.title, description=a.description)
    print(p.youtube_url)
    print(f"video_id: {p.youtube_video_id}")
    print(f"privacy: {p.publish_privacy}")
    print(f"upload_status: {p.publish_status}")
    print(f"published_at: {p.published_at}")
    return 0


def cmd_demo(a, f: Factory) -> int:
    """Topic -> mock plan -> mock voice -> synthetic video and image scenes -> render. Proves the pipeline offline."""
    from .fixtures import make_set
    from .planner import MockPlanner

    fx = make_set(get_settings().data_dir / "fixtures")
    p = f.create_project(a.topic, 30, a.language)
    f.plan(p.id, MockPlanner())
    f.generate_voice(p.id, provider=a.provider)
    visuals = [
        fx["landscape_short"],
        fx["portrait_image"],
        fx["portrait_long"],
        fx["image"],
        fx["square"],
        fx["landscape_image"],
    ]
    for s in f.storyboard(p.id).scenes:
        f.attach_asset(p.id, s.order, visuals[(s.order - 1) % len(visuals)], "mock")
    p = f.render(p.id, fx["music"] if a.music else None)
    _print(
        {
            "project": p.id,
            **p.render.model_dump(),
            "file": str(f.store.path(p.id, p.render.file or "renders/final-short.mp4")),
            "assets": [
                {"scene": s.order, "type": s.asset_type, "source": s.asset_source, "path": s.asset_path}
                for s in f.storyboard(p.id).scenes
            ],
            "qc_warnings": qc_warnings(f.store.get(p.id), f.storyboard(p.id), f.store.dir(p.id)),
        }
    )
    return 0


# ---------------------------------------------------------------- discovery
def _discovery(f: Factory):
    from .discovery.service import Discovery

    return Discovery(f)


def _candidate_row(c) -> dict:
    return {
        "id": c.id,
        "source": c.source,
        "title": c.label,
        "author": c.author,
        "url": c.url,
        "views": c.metrics.get("views"),
        "views_per_day": c.score.raw.get("views_per_day") if c.score else None,
        "viral_score": c.score.viral_score if c.score else None,
        "signals": c.score.signals if c.score else None,
        "enrich_error": c.enrich_error,
    }


def cmd_discover_url(a, f: Factory) -> int:
    c = _discovery(f).add_url(a.url, a.title, _text(a.transcript, a.transcript_file), a.notes, a.niche)
    _print(_candidate_row(c))
    return 0


def _text(value: str | None, file: Path | None) -> str | None:
    return file.read_text(encoding="utf-8") if file else value


def cmd_discover_text(a, f: Factory) -> int:
    c = _discovery(f).add_content(a.topic, a.title, _text(a.transcript, a.transcript_file), a.notes, a.url, a.niche)
    _print(_candidate_row(c))
    return 0


def cmd_discover_youtube(a, f: Factory) -> int:
    found, cached = _discovery(f).search_youtube(a.niche, a.keywords, a.days, a.max, a.language)
    print(f"{len(found)} candidates ({'cache' if cached else 'YouTube API'}), top {a.top}:", file=sys.stderr)
    _print([_candidate_row(c) for c in found[: a.top]])
    return 0


def cmd_candidates(a, f: Factory) -> int:
    _print([_candidate_row(c) for c in _discovery(f).candidates()[: a.top]])
    return 0


def _opportunity_view(o) -> dict:
    return {
        **o.model_dump(exclude={"hooks"}),
        "hooks": [{"index": i, "overall": h.overall, **h.model_dump()} for i, h in enumerate(o.hooks)],
    }


def cmd_analyze(a, f: Factory) -> int:
    _print(_opportunity_view(_discovery(f).analyze(a.candidate, a.language, a.niche)))
    return 0


def cmd_opportunity(a, f: Factory) -> int:
    _print(_opportunity_view(_discovery(f).store.get_opportunity(a.opportunity)))
    return 0


def cmd_generate(a, f: Factory) -> int:
    p = _discovery(f).generate_video(a.opportunity, a.duration, a.style, hook_index=a.hook, format_id=a.format)
    print(p.id)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="factory", description="AI Content Factory POC")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("preflight", help="check ffmpeg/filters/config")
    n = sub.add_parser("new", help="create a project")
    n.add_argument("topic")
    n.add_argument("--duration", type=int, default=30)
    n.add_argument("--language", choices=["vi", "en"], default="vi")
    n.add_argument("--style", default="cinematic, realistic, soft natural light")
    n.add_argument("--bible", help="fixed look of the recurring character/world, prepended to every Muse prompt")
    sub.add_parser("list")
    for name in ("plan", "status"):
        sub.add_parser(name).add_argument("project")
    v = sub.add_parser("voice", help="generate (or --file import) scene voice")
    v.add_argument("project")
    v.add_argument("--scene", type=int)
    v.add_argument("--provider", choices=["mock", "edge", "voicestudio"])
    v.add_argument("--file", type=Path)
    c = sub.add_parser("clip", help="attach a video (Muse clip) or an image (jpg/png/webp) to a scene")
    c.add_argument("project")
    c.add_argument("scene", type=int)
    c.add_argument("file", type=Path)
    c.add_argument("--source", choices=ASSET_SOURCES, help="default: muse for a video, upload for an image")
    b = sub.add_parser("bible", help="set (or clear with '') the visual bible prefix of every scene's Muse prompt")
    b.add_argument("project")
    b.add_argument("text")
    sub.add_parser("muse-prompts", help="all scene prompts in one sheet (also saved as muse_prompts.md)").add_argument(
        "project"
    )
    ia = sub.add_parser("import-assets", help="attach many downloaded clips/images at once (scene number in name)")
    ia.add_argument("project")
    ia.add_argument("paths", nargs="+", type=Path, help="files and/or folders (folders: direct children only)")
    ia.add_argument("--by-time", action="store_true", help="newest files, oldest first, onto scenes missing an asset")
    ia.add_argument("--source", choices=ASSET_SOURCES, help="default: muse for a video, upload for an image")
    ia.add_argument("--dry-run", action="store_true", help="show the mapping without attaching")
    r = sub.add_parser("render", help="render the Short; background music is kept from the previous render")
    r.add_argument("project")
    music = r.add_mutually_exclusive_group()
    music.add_argument("--bgm", type=Path, help="use this music (copied into the project, reused by later renders)")
    music.add_argument("--no-bgm", action="store_true", help="remove the project's background music")
    r.add_argument("--bgm-volume", type=float, help="music volume 0-1 (default: the project's stored volume)")
    sub.add_parser("youtube-connect", help="one-time OAuth in the browser, then verifies the token scope")
    sub.add_parser("youtube-status", help="connected / not_connected / not_configured + granted scopes and expiry")
    pb = sub.add_parser("publish")
    pb.add_argument("project")
    pb.add_argument("--privacy", choices=["private", "unlisted"], default="private")
    pb.add_argument("--title", help="YouTube title for this upload (default: storyboard title, max 100 chars)")
    pb.add_argument("--description", help="YouTube description for this upload (default: storyboard description)")
    pb.add_argument("--force", action="store_true", help="upload again even if the project already has a YouTube video")
    d = sub.add_parser("demo", help="offline end-to-end run with synthetic media")
    d.add_argument("--topic", default="Kỷ luật quan trọng hơn động lực")
    d.add_argument("--language", choices=["vi", "en"], default="vi")
    d.add_argument("--provider", choices=["mock", "edge", "voicestudio"], default="mock")
    d.add_argument("--music", action="store_true")
    du = sub.add_parser("discover-url", help="save a reference URL (YouTube metadata fetched when YOUTUBE_API_KEY set)")
    du.add_argument("url")
    dt = sub.add_parser("discover-text", help="save a reference you describe yourself (topic/title/transcript)")
    dt.add_argument("--topic")
    dt.add_argument("--url")
    for cmd in (du, dt):
        cmd.add_argument("--title")
        cmd.add_argument("--transcript")
        cmd.add_argument("--transcript-file", type=Path)
        cmd.add_argument("--notes")
        cmd.add_argument("--niche")
    dy = sub.add_parser("discover-youtube", help="search recent YouTube videos and rank them (needs YOUTUBE_API_KEY)")
    dy.add_argument("niche")
    dy.add_argument("--keywords", default="")
    dy.add_argument("--days", type=int, default=30, help="lookback window (default 30)")
    dy.add_argument("--max", type=int, default=20, help="results to fetch, max 50 (default 20)")
    dy.add_argument("--language", choices=["vi", "en"])
    dy.add_argument("--top", type=int, default=10)
    cl = sub.add_parser("candidates", help="saved references, ranked by viral score")
    cl.add_argument("--top", type=int, default=20)
    an = sub.add_parser("analyze", help="candidate -> opportunity (angle, 5 scored hooks, format)")
    an.add_argument("candidate")
    an.add_argument("--language", choices=["vi", "en"], default="vi")
    an.add_argument("--niche")
    sub.add_parser("opportunity", help="show an opportunity").add_argument("opportunity")
    g = sub.add_parser("generate", help="opportunity -> project with a planned storyboard (existing pipeline)")
    g.add_argument("opportunity")
    g.add_argument("--hook", type=int, help="hook index to use (default: best scored)")
    g.add_argument("--format", help="override the format id")
    g.add_argument("--duration", type=int, help="target seconds 20-45 (default: middle of the format's range)")
    g.add_argument("--style", default="cinematic, realistic, soft natural light")
    a = ap.parse_args(argv)
    handler = globals()[f"cmd_{a.cmd.replace('-', '_')}"]
    try:
        return handler(a, Factory())
    except Exception as e:  # concise error for humans; details are in logs/events.log
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
