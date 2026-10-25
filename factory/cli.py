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
    p = f.create_project(a.topic, a.duration, a.language, a.style)
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="factory", description="AI Content Factory POC")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("preflight", help="check ffmpeg/filters/config")
    n = sub.add_parser("new", help="create a project")
    n.add_argument("topic")
    n.add_argument("--duration", type=int, default=30)
    n.add_argument("--language", choices=["vi", "en"], default="vi")
    n.add_argument("--style", default="cinematic, realistic, soft natural light")
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
    a = ap.parse_args(argv)
    handler = globals()[f"cmd_{a.cmd.replace('-', '_')}"]
    try:
        return handler(a, Factory())
    except Exception as e:  # concise error for humans; details are in logs/events.log
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
