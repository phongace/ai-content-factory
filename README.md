# AI Content Factory (POC)

Internal tool: Topic → storyboard → voice → Muse clips → subtitles → render → QC → YouTube Shorts.
Python + Streamlit, rendering on top of MoneyPrinterTurbo (`mpt==1.3.8`, MIT).

```bash
brew install uv                 # macOS (FFmpeg: see the libass note below)
uv sync
cp .env.example .env.local      # fill in what you have, everything is optional
uv run factory preflight
uv run factory demo --music     # offline end-to-end with synthetic media
uv run streamlit run ui/studio.py  # UI at http://localhost:8501
```

FFmpeg must be built with libass (the `ass` filter burns the captions). Homebrew core `ffmpeg` may lack it
(check: `ffmpeg -hide_banner -filters | grep ' ass '`). Use a libass-enabled build, for example a static build
for macOS (https://ffmpeg.martin-riedl.de) or `brew install ffmpeg-full` (keg-only), and set `FFMPEG_PATH` /
`FFPROBE_PATH` in `.env.local`. Both our code and MoneyPrinterTurbo then run those binaries, and `render`
fails fast with a clear message if the `ass` filter is missing. Tests do not read `.env.local`: export the two
variables in your shell, or run `scripts/check.sh`, which takes them from `.env.local`.

Docs: `docs/POC_RUNBOOK.md` (how to run), `docs/MANUAL_SETUP.md` (what needs a human),
`docs/IMPLEMENTATION_STATUS.md` (task status), `CLAUDE.md` (scope guardrails), `THIRD_PARTY_NOTICES.md`.
