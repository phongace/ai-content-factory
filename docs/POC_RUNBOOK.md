# POC Runbook

## 1. Cài đặt (1 lần)

```bash
brew install uv                   # macOS
git checkout feature/poc-video-factory-v1
uv sync                           # tạo .venv, cài mpt==1.3.8 + deps (lần đầu vài phút)
cp .env.example .env.local        # điền FFMPEG_PATH / FFPROBE_PATH nếu cần (xem bên dưới)
uv run factory preflight          # phải ra "problems": []
```

**FFmpeg phải có libass** (filter `ass`, dùng để burn phụ đề). `brew install ffmpeg` (Homebrew core) **có thể không có libass**
(ví dụ ffmpeg 9.0.2 trên máy dev thiếu `ass`/`subtitles`). Kiểm tra: `ffmpeg -hide_banner -filters | grep ' ass '`, không ra dòng nào là thiếu.
Nếu thiếu, cài bản có libass rồi trỏ `.env.local` tới đúng binary:

- Static build cho macOS (ví dụ https://ffmpeg.martin-riedl.de), hoặc `brew install ffmpeg-full` (keg-only, binary nằm ở `$(brew --prefix ffmpeg-full)/bin`).
- `FFMPEG_PATH=/đường/dẫn/ffmpeg` và `FFPROBE_PATH=/đường/dẫn/ffprobe` trong `.env.local`. Cả code của mình lẫn MoneyPrinterTurbo (scene render) đều dùng đúng cặp binary này.
- `uv run factory preflight` báo rõ binary nào thiếu `ass`; `render` cũng tự kiểm tra trước khi chạy và báo lỗi tương tự.
- Test render (`pytest -m media`) không đọc `.env.local`: export `FFMPEG_PATH`/`FFPROBE_PATH` trong shell, hoặc chạy `scripts/check.sh` (script tự lấy hai biến này từ `.env.local`). Không có ffmpeg đủ libass thì test render bị skip kèm lý do.

## 2. Env

Mọi thứ đều optional, thiếu thì app tự chạy chế độ mock:

| Biến | Thiếu thì sao |
|---|---|
| `ANTHROPIC_API_KEY` | Planner dùng MockPlanner (kịch bản mẫu) |
| `VOICE_PROVIDER=voicestudio` + `VOICESTUDIO_*` | Voice dùng mock (tiếng tone) hoặc upload audio tay |
| `data/secrets/youtube_client_secret.json` (hoặc `YOUTUBE_CLIENT_SECRET_FILE`; fallback `YOUTUBE_CLIENT_ID/SECRET`) | Tab Publish báo "YouTube not configured", render vẫn chạy |
| `BGM_FILE` | Không có nhạc nền, hoặc upload nhạc ngay trong UI |

## 3. Chạy app

```bash
uv run streamlit run ui/app.py    # http://localhost:8501
```

## 4–10. Flow trong UI

1. **Tạo project** (sidebar): topic, thời lượng 20–45s, ngôn ngữ, visual style.
2. **Generate plan** (tab Storyboard): ra 4–6 scene, mỗi scene 5–9s. Sửa narration/prompt rồi bấm "Lưu scene". Sửa title/description/hashtag ở "YouTube metadata".
3. **Voice**: chọn provider rồi "Generate voice cho TẤT CẢ scene", hoặc từng scene. Hoặc upload audio (export từ VoiceStudio UI). Mỗi scene hiện duration thật. Có cảnh báo nếu >10s hoặc narration đã sửa sau khi tạo voice.
4. **Visual Asset** (mỗi scene dùng video hoặc ảnh, trộn tuỳ ý, ví dụ Short 4 scene: video / ảnh / video / ảnh để giảm chi phí Muse). Ở khung "Visual Asset" của scene chọn "Video" hoặc "Image", upload, chọn Source rồi bấm "Attach Asset" (đã có asset thì nút là "Replace Asset", kèm "Remove Asset"). Sau khi gắn, scene hiện Type, Source, tên file, kích thước và preview.
   - **Cách A, video từ Muse**: bấm nút copy ở "Copy Muse Prompt" → dán vào Muse → generate → tải mp4 → "Upload Video" vào đúng scene (Source `muse`). Clip ngang/dọc/vuông đều được, app tự crop 9:16, clip ngắn hơn voice thì loop, dài hơn thì cắt.
   - **Cách B, ảnh**: chuẩn bị hoặc tạo ảnh ở ngoài → "Upload Image" (`.jpg`, `.jpeg`, `.png`, `.webp`, Source `upload` hoặc `generated`). Không có AI image provider trong app, không có Muse API.
   - Cả hai cách đi vào cùng một renderer. Ảnh được chuẩn hoá lúc Render thành clip 1080x1920: cover-scale + crop giữa (không méo), zoom chậm 10%, dài đúng bằng voice của scene (làm tròn lên theo frame 30fps, render cắt đúng theo voice). Clip này cache trong `scenes/normalized/` theo (nội dung ảnh, số frame, version), nên ảnh không đổi thì scene được reuse khi render lại.
   - Chi phí Muse chỉ tính cho asset có Source `muse` (video upload mặc định là `muse`, ảnh mặc định là `upload`).
5. **Render** (tab Render & QC): upload nhạc nền nếu muốn, chỉnh volume, bấm Render. Ra `data/projects/<id>/renders/final-short.mp4` 1080x1920, H.264/AAC, 30fps, -14 LUFS, phụ đề đã burn.
6. **QC**: xem preview, duration, size, cảnh báo (scene chưa có asset, file asset mất/hỏng/sai đuôi, asset đổi sau lần render cuối, clip loop...). Scene nào xấu: chọn scene ở mục "Replace scene → Render again", upload asset mới (video hoặc ảnh, đổi ảnh sang video và ngược lại đều được), bấm nút. Chỉ scene đó render lại, planner và voice không chạy lại.
7. **Chi phí Muse**: nhập tổng chi phí Muse thật của video ở cuối tab Render & QC.
8. **Publish** (tab Publish): "Connect YouTube" (lần đầu, xem `docs/MANUAL_SETUP.md` §4), chọn private/unlisted, "Upload lên YouTube". Link hiện ngay sau khi xong.

## CLI tương đương

```bash
uv run factory new "Kỷ luật quan trọng hơn động lực" --duration 30 --language vi
uv run factory plan <project_id>
uv run factory voice <project_id> [--scene 2] [--provider voicestudio]
uv run factory voice <project_id> --scene 2 --file voice2.mp3      # upload tay
uv run factory clip <project_id> 1 ~/Downloads/muse_scene1.mp4     # video (Source mặc định: muse)
uv run factory clip <project_id> 2 ~/Downloads/scene2.jpg            # ảnh (Source mặc định: upload)
uv run factory clip <project_id> 2 scene2.webp --source generated    # muse | upload | generated | mock
uv run factory render <project_id> [--bgm music.mp3]
uv run factory youtube-connect       # OAuth một lần: mở trình duyệt, chờ tối đa 300s, kiểm tra scope, lưu token
uv run factory youtube-status        # connected | not_connected | not_configured + scope + hạn token (exit 0 chỉ khi connected)
uv run factory publish <project_id> --privacy private
uv run factory publish <project_id> --privacy private --title "Test private upload" --description "Kiểm tra OAuth"
uv run factory status <project_id>
uv run factory list
uv run factory demo --music          # offline: 4 scene video / ảnh / video / ảnh + voice mock + phụ đề + nhạc
```

## Lỗi YouTube

Mọi lỗi dưới đây hiện dạng `ERROR: ...` ở CLI (hoặc thanh lỗi trong UI) và lưu vào `publish_error` của project. Lỗi nào cũng chạy lại được sau khi xử lý.

| Thông báo (rút gọn) | Nghĩa là | Xử lý |
|---|---|---|
| `YouTube not configured: OAuth client file not found at ...` | Thiếu file client JSON và thiếu cặp env fallback | Lưu JSON Desktop app vào đường dẫn được in ra (MANUAL_SETUP §4) |
| `... is not valid JSON` / `found type: web` / `missing client_secret` | File hỏng, sai loại client (`web` thay vì `installed`), hoặc thiếu trường | Tạo client loại Desktop app và tải JSON lại |
| `authorization denied or cancelled (access_denied)` | Bấm Cancel/Deny, hoặc Google chặn account | Chạy lại `youtube-connect`, bấm Continue / Allow; account phải nằm trong Test users |
| `timed out after 300s waiting for the Google authorization callback` | Không hoàn tất consent trong 5 phút, hoặc trình duyệt không vào được `http://localhost` | Chạy lại và hoàn tất consent; kiểm tra trình duyệt/máy cùng chạy lệnh |
| `Google authorization callback failed (invalid_client ...)` | Client JSON không còn hợp lệ hoặc thuộc project khác | Tải JSON mới từ đúng project |
| `Google did not return a refresh token` | Account đã cấp quyền trước đó mà Google không phát refresh token mới | Gỡ app tại https://myaccount.google.com/permissions rồi connect lại |
| `YouTube token lacks the youtube.upload permission` / `insufficient permissions` | Token không có scope upload (bỏ tick quyền ở màn hình consent) | Chạy lại `youtube-connect` và giữ tick quyền upload |
| `YouTube token expired or revoked (invalid_grant ...)` | Refresh token hết hạn (app Testing: khoảng 7 ngày) hoặc bị thu hồi | Chạy lại `youtube-connect` |
| `YouTube Data API v3 is not enabled ...` | Project của OAuth client chưa bật API (`accessNotConfigured` / `SERVICE_DISABLED`) | Bật YouTube Data API v3, chờ khoảng 1 phút, chạy lại |
| `YouTube quota or upload limit reached (quotaExceeded / uploadLimitExceeded / rateLimitExceeded)` | Hết quota API hoặc giới hạn upload của kênh. Không tự thử lại | Đợi quota reset (nửa đêm giờ Pacific) rồi chạy lại |
| `network error ...` / `gave up after 5 attempts` | Mất mạng hoặc YouTube trả 5xx; upload resumable đã tự thử lại 5 lần liên tiếp | Kiểm tra mạng và chạy lại lệnh publish |
| `already uploaded to YouTube (...)` | Project đã có video | Thêm `--force` nếu thật sự muốn tạo bản mới |

## Kiểm tra chất lượng

```bash
scripts/check.sh            # ruff + pyright + pytest + preflight (~4 phút, phần render spawn ffmpeg)
uv run pytest -m "not media" # bản nhanh, bỏ test render
```

## Dữ liệu

`data/projects/<id>/`: `project.json` (status, stage, error, cost, YouTube ID), `storyboard.json` (scene, prompt, `asset_type`/`asset_path`/`asset_source`, voice, duration thật; project cũ chỉ có `clip_file` vẫn load được, tự suy ra video), `scenes/` (file video/ảnh gốc của từng scene, `scenes/normalized/` là cache clip từ ảnh, xoá được), `audio/`, `subtitles/final.ass|srt`, `renders/final-short.mp4`, `renders/engine/` (cache render của MPT, xoá được), `logs/events.log`.
