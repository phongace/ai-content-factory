# CLAUDE.md — guardrails for every session on this repo

This is an INTERNAL AI content factory POC that produces YouTube Shorts for monetized channels.
It is NOT a SaaS. Source of truth for scope: `docs/AI_Content_Factory_POC_Tracker.xlsx` (read-only).
Status of every task: `docs/IMPLEMENTATION_STATUS.md` (keep it updated).

## Pipeline (POC)
Topic → Claude storyboard JSON → per-scene voice → manual Muse clips → scene-level subtitles →
render (MoneyPrinterTurbo `VideoProject` + our final FFmpeg pass) → QC / replace one scene → YouTube (private/unlisted).

## Discovery Engine v0 (2026-10-27, `factory/discovery/`)
Reference (manual URL / manual content / YouTube search) → rule-based viral score → opportunity analyzer (angle,
5 scored hooks, format from the built-in library) → `ContentBrief` → the SAME `Factory.create_project` + `plan`.
- Thin layer in front of the pipeline, never a second pipeline. `Project.brief` (optional) keeps discovery/opportunity
  ids, hook and format for later analytics; a project without a brief plans exactly as before.
- Rank with code first (`scoring.py`), send only the chosen candidate to Claude. The best hook is picked in code from
  weighted sub-scores; `viral_signal` comes from metrics, never from the LLM.
- Originality: the analyzer prompt forbids copying script, distinctive phrasing or footage; the planner only ever
  sees the brief (angle, our hook, beats), never the reference transcript.
- YouTube discovery uses `YOUTUBE_API_KEY` (public data, search cached on disk). The upload OAuth token has only the
  `youtube.upload` scope: do not widen it for discovery. No scraping, no TikTok/Facebook crawling, no transcript fetch.
- Storage: `data/discoveries/`, `data/opportunities/`, `data/patterns/` (JSON, atomic writes via `factory/store.py`).

## Stack decisions (2026-10-22)
- Python 3.11+ with `uv`. UI = Streamlit (`ui/studio.py`). CLI = `factory` (`factory/cli.py`). Next.js was dropped.
- Media engine = `mpt==1.3.8` (MoneyPrinterTurbo, MIT). Pinned on purpose. All imports go through
  `factory/mpt_bridge.py` or `factory/voice.py`. Never import `app.*` anywhere else.
- Our package is `factory/` (MPT already owns the top-level `app` package name). Never name any of our
  modules or scripts `app` (the Streamlit UI was `ui/app.py` until 2026-10-25: Streamlit puts `ui/` on
  `sys.path`, so `import app.services...` resolved to the UI script and every render from the UI failed).
- Storage = local filesystem `data/projects/{id}/` (project.json, storyboard.json, scenes/, audio/, subtitles/, renders/, logs/).

## Hard rules
- Local-first. No database, no queue, no cloud storage, no background workers.
- Provider abstractions stay thin: `ContentPlanner`, `VoiceProvider`, `Publisher`. Mock implementations must keep working without any credentials.
- Voice is generated PER SCENE. `actual_audio_duration` (ffprobe) is the source of truth for timing.
- Planner: 4–6 scenes, each target 5–9s, never above 9s (TC-02). Total 20–45s.
- Muse is MANUAL in the POC (copy prompt → generate → upload clip). No Muse API.
- YouTube uploads default to `private`, `unlisted` allowed, `public` refused in code.
- Secrets never committed. `.env.local` is gitignored, `.env.example` holds placeholders only. `data/` is gitignored (contains the YouTube token).
- Do not use MPT's bundled songs or proprietary fonts (see THIRD_PARTY_NOTICES.md).

## Out of scope (tracker 08_Phase2_Deferred) — do NOT build
n8n, cron, Redis, BullMQ, Supabase/Postgres, R2/S3, TikTok/Facebook publishing or crawling, analytics loop
(YouTube Analytics, winner detection, comment mining), trend research beyond Discovery v0, visual beats,
feedback loop, batch generation/queue, Muse automation, word-level/Whisper subtitles, multi-user, auth, billing, Kubernetes.
MPT ships some of these (AI video providers, Whisper, Upload-Post, Redis state): leave them unused.

## Before marking work complete
Run `scripts/check.sh` (ruff lint + format check, pyright, pytest, ffmpeg preflight). Fix what you broke.
No blanket `# noqa`, no skipped tests, no `Any` sprinkling without a written reason.
