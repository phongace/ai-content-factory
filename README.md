# AI Content Factory (POC)

Internal tool: Topic → storyboard → voice → Muse clips → subtitles → render → QC → YouTube Shorts.
Python + Streamlit, rendering on top of MoneyPrinterTurbo (`mpt==1.3.8`, MIT).

```bash
brew install ffmpeg uv          # macOS
uv sync
cp .env.example .env.local      # fill in what you have, everything is optional
uv run factory preflight
uv run factory demo --music     # offline end-to-end with synthetic media
uv run streamlit run ui/app.py  # UI at http://localhost:8501
```

Docs: `docs/POC_RUNBOOK.md` (how to run), `docs/MANUAL_SETUP.md` (what needs a human),
`docs/IMPLEMENTATION_STATUS.md` (task status), `CLAUDE.md` (scope guardrails), `THIRD_PARTY_NOTICES.md`.
