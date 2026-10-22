# POC Runbook

## 1. Cài đặt (1 lần)

```bash
brew install ffmpeg uv            # macOS, ffmpeg Homebrew đã có libass (cần cho phụ đề)
git checkout feature/poc-video-factory-v1
uv sync                           # tạo .venv, cài mpt==1.3.8 + deps (lần đầu vài phút)
cp .env.example .env.local
uv run factory preflight          # phải ra "problems": []
```

## 2. Env

Mọi thứ đều optional, thiếu thì app tự chạy chế độ mock:

| Biến | Thiếu thì sao |
|---|---|
| `ANTHROPIC_API_KEY` | Planner dùng MockPlanner (kịch bản mẫu) |
| `VOICE_PROVIDER=voicestudio` + `VOICESTUDIO_*` | Voice dùng mock (tiếng tone) hoặc upload audio tay |
| `YOUTUBE_CLIENT_ID/SECRET` | Tab Publish báo "YouTube not configured", render vẫn chạy |
| `BGM_FILE` | Không có nhạc nền, hoặc upload nhạc ngay trong UI |

## 3. Chạy app

```bash
uv run streamlit run ui/app.py    # http://localhost:8501
```

## 4–10. Flow trong UI

1. **Tạo project** (sidebar): topic, thời lượng 20–45s, ngôn ngữ, visual style.
2. **Generate plan** (tab Storyboard): ra 4–6 scene, mỗi scene 5–9s. Sửa narration/prompt rồi bấm "Lưu scene". Sửa title/description/hashtag ở "YouTube metadata".
3. **Voice**: chọn provider rồi "Generate voice cho TẤT CẢ scene", hoặc từng scene. Hoặc upload audio (export từ VoiceStudio UI). Mỗi scene hiện duration thật. Có cảnh báo nếu >10s hoặc narration đã sửa sau khi tạo voice.
4. **Muse**: bấm nút copy ở khung prompt → dán vào Muse → generate → tải mp4 → upload vào đúng scene ("Gắn clip"). Clip ngang/dọc/vuông đều được, app tự crop 9:16. Không có clip thì upload ảnh tĩnh tạm (app tự zoom chậm).
5. **Render** (tab Render & QC): upload nhạc nền nếu muốn, chỉnh volume, bấm Render. Ra `data/projects/<id>/renders/final-short.mp4` 1080x1920, H.264/AAC, 30fps, -14 LUFS, phụ đề đã burn.
6. **QC**: xem preview, duration, size, cảnh báo. Scene nào xấu: chọn scene ở mục "Replace scene → Render again", upload clip mới, bấm nút. Chỉ scene đó render lại, planner và voice không chạy lại.
7. **Chi phí Muse**: nhập tổng chi phí Muse thật của video ở cuối tab Render & QC.
8. **Publish** (tab Publish): "Connect YouTube" (lần đầu), chọn private/unlisted, "Upload lên YouTube". Link hiện ngay sau khi xong.

## CLI tương đương

```bash
uv run factory new "Kỷ luật quan trọng hơn động lực" --duration 30 --language vi
uv run factory plan <project_id>
uv run factory voice <project_id> [--scene 2] [--provider voicestudio]
uv run factory voice <project_id> --scene 2 --file voice2.mp3      # upload tay
uv run factory clip <project_id> 1 ~/Downloads/muse_scene1.mp4
uv run factory render <project_id> [--bgm music.mp3]
uv run factory youtube-connect
uv run factory publish <project_id> --privacy private
uv run factory status <project_id>
uv run factory list
```

## Kiểm tra chất lượng

```bash
scripts/check.sh            # ruff + pyright + pytest + preflight (~4 phút, phần render spawn ffmpeg)
uv run pytest -m "not media" # bản nhanh, bỏ test render
```

## Dữ liệu

`data/projects/<id>/`: `project.json` (status, stage, error, cost, YouTube ID), `storyboard.json` (scene, prompt, clip, voice, duration thật), `scenes/`, `audio/`, `subtitles/final.ass|srt`, `renders/final-short.mp4`, `renders/engine/` (cache render của MPT, xoá được), `logs/events.log`.
